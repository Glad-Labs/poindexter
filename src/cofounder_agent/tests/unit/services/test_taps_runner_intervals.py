"""Per-tap interval scheduling (poindexter#998).

Every Tap declared `interval_seconds` and nothing read it: the runner walked
every enabled tap on every invocation, so `github_issues` (6h) ran hourly
and HelloTap's `interval_seconds = 0` ("on-demand only") was ignored. With
two hourly callers — RunTapsJob in the worker and the auto-embed sidecar —
each tap actually ran twice an hour from two processes.

The sharp edge is the interaction with zero-yield detection: a not-due tap
reports 0 embedded / 0 skipped / 0 failed, which is byte-identical to the
"source went dark" signal. If those are conflated, every throttled cycle
pages. That is the first thing pinned below.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.taps.runner import (
    TapStats,
    is_tap_due,
    is_zero_yield,
    mark_tap_run,
    resolve_interval_seconds,
)


class _Tap:
    def __init__(self, name: str = "t", interval: int = 0) -> None:
        self.name = name
        self.interval_seconds = interval


class _Cfg:
    def __init__(self, interval: int = 0) -> None:
        self.interval_seconds = interval


class _Conn:
    def __init__(self, due: Any = None, raises: bool = False) -> None:
        self._due = due
        self._raises = raises
        self.executed: list[tuple[Any, ...]] = []

    async def fetchrow(self, _sql: str, *args: Any):
        if self._raises:
            raise RuntimeError("relation \"tap_run_state\" does not exist")
        return None if self._due is None else {"due": self._due}

    async def execute(self, _sql: str, *args: Any) -> str:
        if self._raises:
            raise RuntimeError("write failed")
        self.executed.append(args)
        return "INSERT 0 1"


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._c = conn

    async def __aenter__(self) -> _Conn:
        return self._c

    async def __aexit__(self, *_e: Any) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._c = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self._c)


class TestZeroYieldInteraction:
    """The regression this feature could most easily cause."""

    def test_not_due_is_not_a_dark_tap(self):
        stats = TapStats(name="github_issues", due=False)
        assert stats.embedded == stats.skipped == stats.failed == 0
        assert is_zero_yield(stats) is False, (
            "a throttled tap reports 0/0/0 — treating that as zero-yield "
            "would page on every skipped cycle"
        )

    def test_a_due_tap_yielding_nothing_still_fires(self):
        """The detection must keep working for taps that DID run."""
        assert is_zero_yield(TapStats(name="memory", due=True)) is True

    def test_disabled_still_excluded(self):
        assert is_zero_yield(TapStats(name="hello", enabled=False)) is False


class TestResolveInterval:
    def test_class_attribute_is_the_default(self):
        assert resolve_interval_seconds(_Tap(interval=21600), _Cfg()) == 21600

    def test_operator_config_overrides_the_class(self):
        assert resolve_interval_seconds(_Tap(interval=21600), _Cfg(3600)) == 3600

    def test_zero_config_falls_through_to_the_class(self):
        """PluginConfig defaults the field to 0 when app_settings omits it."""
        assert resolve_interval_seconds(_Tap(interval=7200), _Cfg(0)) == 7200

    def test_zero_everywhere_means_no_throttle(self):
        """0 must mean 'every cycle', never 'never' — otherwise every tap
        without an explicit interval silently stops."""
        assert resolve_interval_seconds(_Tap(interval=0), _Cfg(0)) == 0

    def test_negative_is_clamped(self):
        assert resolve_interval_seconds(_Tap(interval=-5), _Cfg()) == 0


class TestIsTapDue:
    @pytest.mark.asyncio
    async def test_zero_interval_always_due_without_touching_the_db(self):
        conn = _Conn(raises=True)  # would explode if queried
        assert await is_tap_due(_Pool(conn), "t", 0, grace_seconds=300) is True

    @pytest.mark.asyncio
    async def test_no_row_means_never_run_means_due(self):
        assert await is_tap_due(_Pool(_Conn(due=None)), "t", 3600, grace_seconds=0) is True

    @pytest.mark.asyncio
    async def test_respects_the_db_verdict(self):
        assert await is_tap_due(_Pool(_Conn(due=False)), "t", 3600, grace_seconds=0) is False
        assert await is_tap_due(_Pool(_Conn(due=True)), "t", 3600, grace_seconds=0) is True

    @pytest.mark.asyncio
    async def test_db_failure_fails_open(self):
        """Pre-migration or a DB hiccup must run the tap, not starve it.

        Under-running is the failure mode that hides for weeks; over-running
        costs one dedup pass.
        """
        assert await is_tap_due(_Pool(_Conn(raises=True)), "t", 3600, grace_seconds=0) is True

    @pytest.mark.asyncio
    async def test_grace_is_subtracted_from_the_interval(self):
        """An hourly tap on an hourly cycle must not become 2-hourly."""
        conn = _Conn(due=True)
        captured: list[float] = []

        async def fetchrow(_sql: str, *args: Any):
            captured.append(args[1])
            return {"due": True}

        conn.fetchrow = fetchrow  # type: ignore[method-assign]
        await is_tap_due(_Pool(conn), "memory", 3600, grace_seconds=300)
        assert captured == [3300.0]

    @pytest.mark.asyncio
    async def test_grace_never_makes_the_window_negative(self):
        conn = _Conn(due=True)
        captured: list[float] = []

        async def fetchrow(_sql: str, *args: Any):
            captured.append(args[1])
            return {"due": True}

        conn.fetchrow = fetchrow  # type: ignore[method-assign]
        await is_tap_due(_Pool(conn), "t", 60, grace_seconds=99999)
        assert captured == [0.0]


class TestMarkTapRun:
    @pytest.mark.asyncio
    async def test_records_name_and_status(self):
        conn = _Conn()
        await mark_tap_run(_Pool(conn), "github_issues", "ok")
        assert conn.executed == [("github_issues", "ok")]

    @pytest.mark.asyncio
    async def test_write_failure_is_swallowed(self):
        """Bookkeeping must never fail an otherwise good ingest run."""
        await mark_tap_run(_Pool(_Conn(raises=True)), "t", "ok")  # must not raise


class TestRealTapIntervals:
    """The declared cadences this feature exists to honour."""

    @pytest.mark.parametrize(
        ("module", "cls_name", "expected"),
        [
            ("services.taps.github_issues", "GitHubIssuesTap", 21600),
            ("services.taps.claude_code_sessions", "ClaudeCodeSessionsTap", 7200),
            ("services.taps.memory", "MemoryFilesTap", 3600),
            ("services.taps.audit", "AuditTap", 1800),
        ],
    )
    def test_declared_intervals_resolve(self, module: str, cls_name: str, expected: int):
        import importlib

        tap = getattr(importlib.import_module(module), cls_name)()
        assert resolve_interval_seconds(tap, _Cfg()) == expected
