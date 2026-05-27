"""Unit tests for ``services/topic_sources/runner.py``.

Covers the aggregation + error-isolation contract — the runner iterates
every registered TopicSource, handles per-source failures without
aborting the rest, applies per-source config from PluginConfig, and
returns a RunnerSummary.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import DiscoveredTopic
from services.topic_sources.runner import RunnerSummary, SourceStats, run_all


class _StubSource:
    def __init__(self, name: str, result=None, error: Exception | None = None):
        self.name = name
        self._result = result or []
        self._error = error
        self.extract_calls: list[dict[str, Any]] = []

    async def extract(self, pool: Any, config: dict[str, Any]) -> list[DiscoveredTopic]:
        self.extract_calls.append(config)
        if self._error is not None:
            raise self._error
        return self._result


def _mk_topic(title: str, source: str = "x") -> DiscoveredTopic:
    return DiscoveredTopic(title=title, category="technology", source=source)


def _mock_config(enabled: bool = True, cfg: dict[str, Any] | None = None):
    """Build a PluginConfig-shaped async return value."""
    m = MagicMock()
    m.enabled = enabled
    m.config = cfg or {}
    return m


class TestRunAll:
    @pytest.mark.asyncio
    async def test_aggregates_topics_from_all_enabled_sources(self):
        a = _StubSource("a", [_mk_topic("t1", "a"), _mk_topic("t2", "a")])
        b = _StubSource("b", [_mk_topic("t3", "b")])

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[a, b],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=AsyncMock(return_value=_mock_config()),
        ):
            summary = await run_all(pool=None)

        assert summary.total == 3
        assert len(summary.per_source) == 2
        assert {s.name for s in summary.per_source} == {"a", "b"}
        assert all(s.error is None for s in summary.per_source)

    @pytest.mark.asyncio
    async def test_disabled_source_skipped(self):
        a = _StubSource("a", [_mk_topic("t1", "a")])
        disabled = _StubSource("disabled", [_mk_topic("nope", "disabled")])

        async def cfg_load(pool, plugin_type, name):
            return _mock_config(enabled=(name != "disabled"))

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[a, disabled],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=cfg_load,
        ):
            summary = await run_all(pool=None)

        assert summary.total == 1
        # The disabled source should be in the stats list but marked disabled.
        disabled_stats = next(s for s in summary.per_source if s.name == "disabled")
        assert disabled_stats.enabled is False
        assert disabled_stats.topics_returned == 0
        # And extract was never invoked on the disabled source.
        assert disabled.extract_calls == []

    @pytest.mark.asyncio
    async def test_source_error_isolated(self):
        good = _StubSource("good", [_mk_topic("t1", "good")])
        bad = _StubSource("bad", error=RuntimeError("boom"))

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[good, bad],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=AsyncMock(return_value=_mock_config()),
        ):
            summary = await run_all(pool=None)

        # Good source's topics still returned.
        assert summary.total == 1
        bad_stats = next(s for s in summary.per_source if s.name == "bad")
        assert bad_stats.error is not None
        assert "boom" in bad_stats.error

    @pytest.mark.asyncio
    async def test_name_dedup_across_entry_points_and_core_samples(self):
        """A source registered in both entry_points AND core samples should
        only run once — otherwise it'd double-count topics."""
        same_name_1 = _StubSource("duplicated", [_mk_topic("t1")])
        same_name_2 = _StubSource("duplicated", [_mk_topic("t2")])

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[same_name_1],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": [same_name_2]},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=AsyncMock(return_value=_mock_config()),
        ):
            summary = await run_all(pool=None)

        # Exactly one source ran (the first-seen instance from entry_points).
        assert len(summary.per_source) == 1
        assert summary.total == 1

    @pytest.mark.asyncio
    async def test_config_threaded_into_extract(self):
        a = _StubSource("a", [])

        async def cfg_load(pool, plugin_type, name):
            return _mock_config(cfg={"custom_flag": "xyz"})

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[a],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=cfg_load,
        ):
            await run_all(pool=None)

        # The config dict reached the source's extract() unchanged.
        assert a.extract_calls == [{"custom_flag": "xyz"}]

    @pytest.mark.asyncio
    async def test_empty_registry_returns_empty_summary(self):
        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ):
            summary = await run_all(pool=None)

        assert summary.total == 0
        assert summary.per_source == []


class _HangingSource:
    """Stub source that hangs on extract until cancelled — proxy for an
    upstream that never responds (e.g. HackerNews API stall, DevTo proxy
    block). The runner's per-source timeout is what saves us."""

    def __init__(self, name: str = "hanger"):
        self.name = name
        self.extract_calls = 0

    async def extract(self, pool: Any, config: dict[str, Any]):
        import asyncio
        self.extract_calls += 1
        # Sleep way past the test's 1s timeout to prove the wait_for
        # cancels it. If the runner forgets to bound this, the test
        # itself times out instead of asserting the expected behavior.
        await asyncio.sleep(30)
        return []


class TestPerSourceTimeout:
    """Pins the 2026-05-27 timeout fix (#254). Before: any source that
    hangs blocks ``asyncio.gather`` indefinitely, starving the whole
    discovery pass. After: each source is wrapped in ``asyncio.wait_for``
    with a global default (60s) and per-source override via
    ``plugin.topic_source.<name>.config.timeout_s``."""

    @pytest.mark.asyncio
    async def test_hanging_source_isolated_by_timeout(self):
        """The runner returns within the timeout window, the hanger is
        recorded as errored, and OTHER sources still get their topics."""
        good = _StubSource("good", [_mk_topic("t1", "good")])
        hanger = _HangingSource("hanger")

        async def cfg_load(pool, plugin_type, name):
            # Tight global timeout for fast test.
            if name == "_runner":
                return _mock_config(cfg={"per_source_timeout_s": 0.5})
            return _mock_config()

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[good, hanger],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=cfg_load,
        ):
            summary = await run_all(pool=None)

        # The good source's topic still made it through.
        assert any(t.title == "t1" for t in summary.topics)
        hanger_stats = next(s for s in summary.per_source if s.name == "hanger")
        assert hanger_stats.error is not None
        assert "timed out" in hanger_stats.error
        assert hanger_stats.topics_returned == 0

    @pytest.mark.asyncio
    async def test_per_source_timeout_override(self):
        """A per-source ``timeout_s`` in PluginConfig.config overrides the
        global default. Operators tune slow sources (web_search) up while
        keeping the floor tight for the rest."""
        hanger = _HangingSource("hanger")

        per_source_call_counts: dict[str, int] = {"_runner": 0, "hanger": 0}

        async def cfg_load(pool, plugin_type, name):
            per_source_call_counts[name] = per_source_call_counts.get(name, 0) + 1
            if name == "_runner":
                return _mock_config(cfg={"per_source_timeout_s": 30.0})  # global lax
            if name == "hanger":
                return _mock_config(cfg={"timeout_s": 0.3})  # tight override
            return _mock_config()

        with patch(
            "plugins.registry.get_topic_sources",
            return_value=[hanger],
        ), patch(
            "plugins.registry.get_core_samples",
            return_value={"topic_sources": []},
        ), patch(
            "plugins.config.PluginConfig.load",
            new=cfg_load,
        ):
            summary = await run_all(pool=None)

        # Lookup must have hit the per-source row, not just the global.
        assert per_source_call_counts.get("hanger", 0) >= 1
        # Hung extract was cut at the 0.3s override, NOT the 30s global.
        hanger_stats = next(s for s in summary.per_source if s.name == "hanger")
        assert hanger_stats.error is not None
        assert "timed out" in hanger_stats.error
        # Total wall time must be far less than the 30s global — empirical
        # generous bound to keep CI stable.
        assert hanger_stats.duration_s < 5.0


class TestSourceStats:
    def test_defaults(self):
        s = SourceStats(name="x")
        assert s.enabled is True
        assert s.topics_returned == 0
        assert s.error is None


class TestRunnerSummary:
    def test_total_counts_topics(self):
        s = RunnerSummary()
        s.topics.append(_mk_topic("a"))
        s.topics.append(_mk_topic("b"))
        assert s.total == 2
