"""Unit tests for the external_taps framework (Phase C / GH-103)."""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import registry as registry_module
from services.integrations import tap_runner
from services.integrations.handlers import (
    tap_builtin_topic_source,
    tap_singer_subprocess,
)


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "UPDATE 1"

    async def fetch(self, query, *args):
        return self._pool.next_fetch

    async def fetchrow(self, query, *args):
        return self._pool.next_fetchrow

    async def fetchval(self, query, *args):
        return self._pool.next_fetchval


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.next_fetch: list[dict[str, Any]] = []
        self.next_fetchrow: dict[str, Any] | None = None
        self.next_fetchval: Any = None

    def acquire(self):
        return _FakeConn(self)


def _tap_row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000030",
        "name": "hackernews",
        "handler_name": "builtin_topic_source",
        "tap_type": "hackernews",
        "target_table": "content_tasks",
        "record_handler": None,
        "schedule": "every 1 hour",
        "config": {},
        "state": {},
        "enabled": True,
        "metadata": {},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolation():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    # Register real handlers so runner tests can dispatch to them.
    registry_module._REGISTRY["tap.builtin_topic_source"] = (
        tap_builtin_topic_source.builtin_topic_source
    )
    registry_module._REGISTRY["tap.singer_subprocess"] = (
        tap_singer_subprocess.singer_subprocess
    )
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# singer_subprocess (stub)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_singer_subprocess_validates_required_config():
    """The handler is no longer a stub (shipped 2026-04-25, GH-103);
    it now ships a real subprocess runner. End-to-end tests live in
    tests/unit/services/test_tap_singer_subprocess.py. This test just
    confirms the handler validates required config up-front."""
    with pytest.raises(ValueError, match="command"):
        await tap_singer_subprocess.singer_subprocess(
            None, site_config=None, row=_tap_row(), pool=_FakePool(),
        )


# ---------------------------------------------------------------------------
# builtin_topic_source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builtin_topic_source_requires_tap_type():
    with pytest.raises(ValueError, match="tap_type"):
        await tap_builtin_topic_source.builtin_topic_source(
            None, site_config=None, row=_tap_row(tap_type=""), pool=_FakePool(),
        )


@pytest.mark.asyncio
async def test_builtin_topic_source_requires_pool():
    with pytest.raises(RuntimeError, match="pool unavailable"):
        await tap_builtin_topic_source.builtin_topic_source(
            None, site_config=None, row=_tap_row(), pool=None,
        )


@pytest.mark.asyncio
async def test_builtin_topic_source_delegates_to_topic_runner(monkeypatch):
    """The handler shells out to services.topic_sources.runner.run_all
    and filters the summary to this tap's source name."""
    from dataclasses import dataclass

    @dataclass
    class _SourceStats:
        name: str
        topics_returned: int = 0
        error: str | None = None

    @dataclass
    class _Summary:
        per_source: list[_SourceStats]
        topics: list[Any]

        @property
        def total(self) -> int:
            return len(self.topics)

    async def _fake_run_all(pool):
        # Signature matches services.topic_sources.runner.run_all after
        # the fix in PR #134 (handler calls run_all(pool) — site_config
        # was dropped because each TopicSource plugin reads its own
        # config from the plugin registry).
        return _Summary(
            per_source=[
                _SourceStats(name="hackernews", topics_returned=5),
                _SourceStats(name="devto", topics_returned=3),
            ],
            topics=[],
        )

    # Patch the import the handler does lazily.
    import services.topic_sources.runner as _tsr
    monkeypatch.setattr(_tsr, "run_all", _fake_run_all)

    result = await tap_builtin_topic_source.builtin_topic_source(
        None,
        site_config=None,
        row=_tap_row(tap_type="hackernews"),
        pool=_FakePool(),
    )
    assert result == {"records": 5, "source": "hackernews"}


@pytest.mark.asyncio
async def test_builtin_topic_source_raises_on_source_error(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class _SourceStats:
        name: str
        topics_returned: int = 0
        error: str | None = None

    @dataclass
    class _Summary:
        per_source: list[_SourceStats]
        topics: list[Any]

        @property
        def total(self) -> int:
            return len(self.topics)

    async def _fake_run_all(pool):
        # Signature matches the handler's run_all(pool) call (see comment
        # in the sibling test for context — fixed alongside PR #134).
        return _Summary(
            per_source=[_SourceStats(name="hackernews", error="429 rate limited")],
            topics=[],
        )

    import services.topic_sources.runner as _tsr
    monkeypatch.setattr(_tsr, "run_all", _fake_run_all)

    with pytest.raises(RuntimeError, match="429"):
        await tap_builtin_topic_source.builtin_topic_source(
            None,
            site_config=None,
            row=_tap_row(tap_type="hackernews"),
            pool=_FakePool(),
        )


# ---------------------------------------------------------------------------
# tap_runner
# ---------------------------------------------------------------------------


class _StubHandler:
    def __init__(self, returning: dict[str, Any] | None = None, raises: Exception | None = None):
        self.returning = returning or {}
        self.raises = raises
        self.calls = 0

    async def __call__(self, payload, *, site_config, row, pool):
        self.calls += 1
        if self.raises:
            raise self.raises
        return dict(self.returning)


class TestTapRunAll:
    @pytest.mark.asyncio
    async def test_empty_when_no_enabled_rows(self):
        pool = _FakePool()
        pool.next_fetch = []
        summary = await tap_runner.run_all(pool)
        assert summary.taps == []
        assert summary.total_records == 0

    @pytest.mark.asyncio
    async def test_aggregates_per_tap_records(self):
        stub = _StubHandler(returning={"records": 42})
        registry_module._REGISTRY["tap.builtin_topic_source"] = stub

        pool = _FakePool()
        pool.next_fetch = [_tap_row(name="a"), _tap_row(name="b")]

        summary = await tap_runner.run_all(pool)
        assert len(summary.taps) == 2
        assert stub.calls == 2
        assert summary.total_records == 84
        assert all(t.ok for t in summary.taps)

    @pytest.mark.asyncio
    async def test_isolates_per_tap_failures(self):
        good = _StubHandler(returning={"records": 10})
        bad = _StubHandler(raises=RuntimeError("source down"))
        registry_module._REGISTRY["tap.builtin_topic_source"] = good
        registry_module._REGISTRY["tap.singer_subprocess"] = bad

        pool = _FakePool()
        pool.next_fetch = [
            _tap_row(name="ok_tap", handler_name="builtin_topic_source"),
            _tap_row(name="bad_tap", handler_name="singer_subprocess"),
        ]

        summary = await tap_runner.run_all(pool)
        assert summary.total_records == 10
        assert summary.total_failed == 1
        ok_names = [t.name for t in summary.taps if t.ok]
        assert ok_names == ["ok_tap"]

    @pytest.mark.asyncio
    async def test_only_names_filters(self):
        pool = _FakePool()
        pool.next_fetch = [_tap_row(name="only_this")]
        summary = await tap_runner.run_all(pool, only_names=["only_this"])
        assert len(summary.taps) == 1
