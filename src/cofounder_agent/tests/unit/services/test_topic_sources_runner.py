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
