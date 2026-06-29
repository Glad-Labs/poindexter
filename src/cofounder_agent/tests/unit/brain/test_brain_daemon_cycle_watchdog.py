"""Unit tests — brain_daemon cycle watchdog + DB command_timeout.

2026-06-29 follow-up (sibling of the docker_port_forward alert-only fix). A
stuck ``await`` inside ``run_cycle`` — a DB query on a wedged Docker host-port
proxy — parked the daemon's only thread in ``epoll_wait`` for ~37 min. The
cycle's ``try/except`` catches *exceptions*, but a hang raises nothing, so the
heartbeat went stale, the dead-man's switch fired, and the brain never
recovered on its own.

These pin the two guards that make the cycle hang-proof:

* ``_create_brain_pool`` sets an asyncpg ``command_timeout`` so every query is
  bounded *client-side* — the timer fires even when the wedged proxy means the
  server never sees the query (the exact 2026-06-29 mechanism).
* ``_run_cycle_with_watchdog`` wraps the cycle in ``asyncio.wait_for``,
  converting a hang into a ``TimeoutError`` the main loop can account for and
  recover from (cancel the cycle, page if persistent, retry next cycle).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_silent_failures.py.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402


@pytest.mark.unit
@pytest.mark.asyncio
class TestCreateBrainPoolSetsCommandTimeout:
    async def test_pool_created_with_command_timeout(self, monkeypatch):
        captured: dict = {}

        async def fake_create_pool(dsn, **kwargs):
            captured["dsn"] = dsn
            captured["kwargs"] = kwargs
            return MagicMock(name="pool")

        monkeypatch.setattr(bd.asyncpg, "create_pool", fake_create_pool)

        pool = await bd._create_brain_pool("postgresql://x/y")

        assert pool is not None
        # The whole point: a per-query timeout so a wedged connection can't park
        # the daemon's only thread forever (2026-06-29).
        ct = captured["kwargs"].get("command_timeout")
        assert ct == bd.BRAIN_DB_COMMAND_TIMEOUT_SECONDS
        assert ct and ct > 0
        # Existing pool sizing preserved.
        assert captured["kwargs"].get("min_size") == 1
        assert captured["kwargs"].get("max_size") == 3
        assert captured["dsn"] == "postgresql://x/y"


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunCycleWithWatchdog:
    async def test_hung_cycle_raises_timeout_not_hang(self):
        """A run_cycle that never returns must raise TimeoutError within the
        watchdog window — NOT park forever (the 2026-06-29 failure mode)."""
        cancelled = {"v": False}

        async def hung_cycle(_pool):
            try:
                await asyncio.sleep(60)  # never completes in the 0.05s window
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise

        with pytest.raises(TimeoutError):
            await bd._run_cycle_with_watchdog(
                MagicMock(), cycle_timeout=0.05, run_cycle_fn=hung_cycle,
            )
        # The stuck cycle was actually torn down, not left detached — that's
        # what frees the daemon's thread to run the next cycle.
        assert cancelled["v"] is True

    async def test_normal_cycle_returns(self):
        ran = {"v": False}

        async def ok_cycle(_pool):
            ran["v"] = True

        await bd._run_cycle_with_watchdog(
            MagicMock(), cycle_timeout=5, run_cycle_fn=ok_cycle,
        )
        assert ran["v"] is True

    async def test_cycle_exception_propagates(self):
        """A real error inside the cycle must propagate unchanged so the main
        loop's existing ``except Exception`` accounting still fires — the
        watchdog adds a timeout, it does not swallow errors."""
        async def boom_cycle(_pool):
            raise ValueError("monitor exploded")

        with pytest.raises(ValueError, match="monitor exploded"):
            await bd._run_cycle_with_watchdog(
                MagicMock(), cycle_timeout=5, run_cycle_fn=boom_cycle,
            )

    async def test_defaults_to_module_run_cycle(self, monkeypatch):
        """With no fn injected it runs the module's ``run_cycle`` — so the
        production call site needs no extra argument."""
        called = {"v": False}

        async def fake_run_cycle(_pool):
            called["v"] = True

        monkeypatch.setattr(bd, "run_cycle", fake_run_cycle)
        await bd._run_cycle_with_watchdog(MagicMock(), cycle_timeout=5)
        assert called["v"] is True


# ---------------------------------------------------------------------------
# 2026-06-29 hardening deltas — three guards layered on top of the merged
# cycle-watchdog (#1991). Each closes a failure mode the watchdog alone can't:
#   * server-side statement_timeout — a query that REACHES Postgres but runs
#     forever (lock/seq-scan); command_timeout only covers the wedged-socket
#     case where the server never sees the query.
#   * faulthandler hang-dump — a sync C-level freeze parks the single thread so
#     asyncio.wait_for's own TimeoutError can never be delivered; only an
#     OS-thread timer can dump the stuck frame.
#   * independent liveness heartbeat — decouples the dead-man's-switch row from
#     cycle completion, so a hung/cancelled cycle can't starve the switch while
#     the loop is still alive and recovering.
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal asyncpg-pool stand-in: records ``execute`` calls and serves
    ``fetchval`` from a dict. ``on_execute`` lets a test trip a shutdown event
    or raise mid-write."""

    def __init__(self, *, values=None, on_execute=None, execute_raises=None):
        self.execute_calls: list[tuple] = []
        self._values = values or {}
        self._on_execute = on_execute
        self._execute_raises = execute_raises

    async def execute(self, *args):
        self.execute_calls.append(args)
        if self._on_execute is not None:
            self._on_execute()
        if self._execute_raises is not None:
            raise self._execute_raises
        return "INSERT 0 1"

    async def fetchval(self, _sql, key):
        return self._values.get(key)


@pytest.mark.unit
@pytest.mark.asyncio
class TestCreateBrainPoolSetsStatementTimeout:
    async def test_pool_created_with_server_statement_timeout(self, monkeypatch):
        captured: dict = {}

        async def fake_create_pool(dsn, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock(name="pool")

        monkeypatch.setattr(bd.asyncpg, "create_pool", fake_create_pool)

        await bd._create_brain_pool("postgresql://x/y")

        # Server-side bound: Postgres itself cancels a long query (frees the
        # backend), complementing the client-side command_timeout. Postgres
        # expects milliseconds as a string in server_settings.
        server_settings = captured["kwargs"].get("server_settings") or {}
        assert server_settings.get("statement_timeout") == str(
            bd.BRAIN_DB_STATEMENT_TIMEOUT_MS
        )
        assert int(server_settings["statement_timeout"]) > 0
        # The client-side guard must still be present — they are layered, not
        # one-or-the-other.
        assert captured["kwargs"].get("command_timeout") == (
            bd.BRAIN_DB_COMMAND_TIMEOUT_SECONDS
        )


@pytest.mark.unit
class TestHangWatchdog:
    """faulthandler hang diagnostics — mirrors the worker's TestHangWatchdog.
    The brain's single thread can be parked by a sync C-level call so the
    asyncio cycle-watchdog's cancellation never fires; only faulthandler's own
    thread can then dump the stuck frame. Diagnostic-only, must never raise."""

    def test_arm_schedules_dump(self):
        with patch.object(bd, "faulthandler") as fh:
            fh.is_enabled.return_value = False
            bd._arm_hang_watchdog(300)
        fh.enable.assert_called_once()
        fh.dump_traceback_later.assert_called_once()
        assert fh.dump_traceback_later.call_args.args[0] == 300

    def test_arm_disabled_when_zero(self):
        with patch.object(bd, "faulthandler") as fh:
            bd._arm_hang_watchdog(0)
        fh.dump_traceback_later.assert_not_called()

    def test_arm_never_raises(self):
        """A faulthandler failure (e.g. no stderr fileno under captured output)
        must be swallowed — diagnostics can't break the daemon."""
        with patch.object(bd, "faulthandler") as fh:
            fh.is_enabled.return_value = True
            fh.dump_traceback_later.side_effect = RuntimeError("no fileno")
            bd._arm_hang_watchdog(300)  # must not raise

    def test_disarm_cancels(self):
        with patch.object(bd, "faulthandler") as fh:
            bd._disarm_hang_watchdog()
        fh.cancel_dump_traceback_later.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
class TestWriteCycleHeartbeat:
    async def test_writes_cycle_heartbeat_row_with_stats(self):
        pool = _FakePool()
        await bd.write_cycle_heartbeat(
            pool,
            probes_run=7,
            probes_failed=1,
            internal_issues=2,
            external_issues=0,
            probe_status={"site": "ok", "api": "issue"},
            kind="cycle",
        )
        assert len(pool.execute_calls) == 1
        args = pool.execute_calls[0]
        # event_type drives the Prometheus dead-man's-switch gauge.
        assert args[1] == "brain.cycle_heartbeat"
        assert args[2] == "brain.brain_daemon"
        details = json.loads(args[3])
        assert details["probes_run"] == 7
        assert details["probes_failed"] == 1
        assert details["heartbeat_kind"] == "cycle"
        assert details["probe_status"] == {"site": "ok", "api": "issue"}

    async def test_liveness_default_kind(self):
        pool = _FakePool()
        await bd.write_cycle_heartbeat(pool)
        details = json.loads(pool.execute_calls[0][3])
        assert details["heartbeat_kind"] == "liveness"
        assert details["probes_run"] == 0

    async def test_db_error_is_swallowed(self):
        """A failed heartbeat write must never propagate — the loop that calls
        it is the one keeping the dead-man's switch fresh."""
        pool = _FakePool(execute_raises=RuntimeError("db wedged"))
        await bd.write_cycle_heartbeat(pool, kind="liveness")  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
class TestHeartbeatLoop:
    async def test_one_tick_writes_touches_and_arms(self):
        shutdown = asyncio.Event()
        touch = MagicMock()
        # Trip shutdown on the first heartbeat write so the loop exits after one
        # tick (the write happens after arm + touch).
        pool = _FakePool(on_execute=shutdown.set)

        with patch.object(bd, "faulthandler") as fh:
            fh.is_enabled.return_value = True
            await bd.heartbeat_loop(
                pool, shutdown,
                interval=0.01, hang_dump_seconds=300, touch_file=touch,
            )

        # Liveness row written, file touched, watchdog (re)armed this tick, and
        # disarmed on loop exit.
        assert any(c[1] == "brain.cycle_heartbeat" for c in pool.execute_calls)
        touch.assert_called()
        fh.dump_traceback_later.assert_called()
        fh.cancel_dump_traceback_later.assert_called_once()

    async def test_already_shutdown_writes_nothing(self):
        shutdown = asyncio.Event()
        shutdown.set()
        pool = _FakePool()
        with patch.object(bd, "faulthandler"):
            await bd.heartbeat_loop(
                pool, shutdown, interval=0.01, hang_dump_seconds=300,
            )
        assert pool.execute_calls == []

    async def test_db_error_does_not_kill_loop(self):
        """A wedged DB must not crash the liveness loop — it logs and keeps
        ticking. ``touch_file`` trips shutdown so the test terminates."""
        shutdown = asyncio.Event()
        pool = _FakePool(execute_raises=RuntimeError("db wedged"))
        with patch.object(bd, "faulthandler"):
            await bd.heartbeat_loop(
                pool, shutdown,
                interval=0.01, hang_dump_seconds=300,
                touch_file=shutdown.set,
            )
        # Attempted the write despite the DB being down, then exited cleanly.
        assert len(pool.execute_calls) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestSettingReaders:
    async def test_hang_dump_default(self):
        assert await bd._hang_dump_seconds(_FakePool()) == (
            bd.BRAIN_HANG_DUMP_DEFAULT_SECONDS
        )

    async def test_hang_dump_from_setting(self):
        pool = _FakePool(values={"brain_hang_dump_seconds": "120"})
        assert await bd._hang_dump_seconds(pool) == 120

    async def test_heartbeat_interval_default(self):
        assert await bd._heartbeat_interval_seconds(_FakePool()) == (
            bd.BRAIN_HEARTBEAT_INTERVAL_DEFAULT_SECONDS
        )

    async def test_heartbeat_interval_from_setting(self):
        pool = _FakePool(values={"brain_heartbeat_interval_seconds": "30"})
        assert await bd._heartbeat_interval_seconds(pool) == 30
