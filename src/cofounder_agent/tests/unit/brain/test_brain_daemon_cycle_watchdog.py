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

The last section drives the real ``run_cycle`` end to end (every stage
stubbed) to pin its cycle-end probe tally: the heartbeat's ``probes_failed``,
the "Cycle complete" brain_decisions row and the "=== Cycle end" log line must
count every probe the same heartbeat's ``probe_status`` reports.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_silent_failures.py.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from poindexter.brain import brain_daemon as bd  # noqa: E402
from poindexter.brain import cycle_stage, operator_notifier  # noqa: E402


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


# ---------------------------------------------------------------------------
# Cycle-end probe tally (2026-09-28). ``probe_failures`` used to be counted
# straight after ``run_health_probes`` returned, before any of the gated probes
# that follow it had been added to ``probe_results``. Their failures never
# reached the heartbeat's ``probes_failed``, the "Cycle complete"
# brain_decisions row or the cycle-end log line, while the same heartbeat's
# ``probe_status`` listed them. Prod, 2026-09-27 21:45 UTC: the log read
# "30 probes (0 failed)" beside probe_status.scheduled_workflow_watch="issue".
# ---------------------------------------------------------------------------

# A nested def's awaits run only if something calls it, e.g. the
# ``_read_app_setting`` inside ``_BrainSecretReader.get_secret``.
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _run_cycle_ast() -> ast.AsyncFunctionDef:
    node = ast.parse(inspect.getsource(bd.run_cycle)).body[0]
    assert isinstance(node, ast.AsyncFunctionDef) and node.name == "run_cycle"
    return node


def _awaited_names(node: ast.AST) -> list[str]:
    """Plain functions ``node`` awaits, in source order, skipping nested defs."""
    found: list[str] = []

    def visit(parent: ast.AST) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, _NESTED_SCOPES):
                continue
            if (
                isinstance(child, ast.Await)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Name)
            ):
                found.append(child.value.func.id)
            visit(child)

    visit(node)
    return found


def _probe_results_writes(node: ast.AST) -> tuple[bool, str | None]:
    """(adds to probe_results?, the literal key it assigns, if any). The
    business-probes block merges a whole dict in with ``.update`` instead."""
    writes, key = False, None
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "probe_results"
                ):
                    writes, key = True, ast.literal_eval(target.slice)
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "update"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "probe_results"
        ):
            writes = True
    return writes, key


def _gated_ifs() -> list[ast.If]:
    return [
        node
        for node in ast.walk(_run_cycle_ast())
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id.startswith("_HAS_")
    ]


# Derived from run_cycle's source, never hand-listed, so a probe added later is
# covered the day it lands. (flag, runner, probe_results key or None).
_GATED_PROBE_BLOCKS = [
    (node.test.id, _awaited_names(node)[0], _probe_results_writes(node)[1])
    for node in _gated_ifs()
    if _probe_results_writes(node)[0]
]
_RUN_CYCLE_FLAGS = sorted({node.test.id for node in _gated_ifs()})

# What a runner returns when its probe fails, for the three whose block builds
# the probe_results entry itself. Every other runner returns a summary whose
# own ``ok`` the block stores.
_FAILING_RESULT = {
    # Returns {probe_name: result}; the block merges it in.
    "run_business_probes": {"business_probe": {"ok": False, "detail": "boom"}},
    # ok = no URL failures and no tailscale drift.
    "maybe_run_operator_url_probe": {"url_failures": 1, "tailscale_drift_count": 0},
    # ok = no secret write came back "error:...".
    "write_prometheus_secrets": {"uptime_kuma_api_key": "error: boom"},
}
_DEFAULT_FAILING_RESULT = {"ok": False, "detail": "boom"}

_TALLY = re.compile(r"(\d+) probes \((\d+) failed\)")


@dataclass
class _CycleRecord:
    heartbeat: dict
    decision_counts: tuple[int, int]
    decision_context: dict
    log_counts: tuple[int, int]


def _counts(text: str) -> tuple[int, int]:
    match = _TALLY.search(text)
    assert match, f"no 'N probes (M failed)' in {text!r}"
    return int(match.group(1)), int(match.group(2))


async def _run_cycle(monkeypatch, caplog, *, early: dict, enabled: dict) -> _CycleRecord:
    """Run the real ``run_cycle`` with every stage it awaits stubbed out.

    ``early`` is what ``run_health_probes`` returns. ``enabled`` maps a gated
    runner to the result it returns; every other ``_HAS_`` gate in run_cycle
    is switched off. ``write_cycle_heartbeat`` runs for real against a fake
    pool, so the test reads the heartbeat row exactly as written.
    """
    for name in set(_awaited_names(_run_cycle_ast())) - {"write_cycle_heartbeat"}:
        monkeypatch.setattr(bd, name, AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(bd, "monitor_services", AsyncMock(return_value=[]))
    monkeypatch.setattr(bd, "monitor_external_services", AsyncMock(return_value=[]))
    monkeypatch.setattr(bd, "_read_app_setting", AsyncMock(return_value="30"))
    monkeypatch.setattr(bd, "run_health_probes", AsyncMock(return_value=dict(early)))
    monkeypatch.setattr(operator_notifier, "set_page_cooldown_seconds", MagicMock())
    # Restored at teardown; run_cycle stamps the stage as it goes.
    monkeypatch.setattr(cycle_stage, "_current", cycle_stage.get_stage())
    for flag in _RUN_CYCLE_FLAGS:
        monkeypatch.setattr(bd, flag, False)
    flag_for = {runner: flag for flag, runner, _key in _GATED_PROBE_BLOCKS}
    for runner, result in enabled.items():
        monkeypatch.setattr(bd, flag_for[runner], True)
        monkeypatch.setattr(bd, runner, AsyncMock(return_value=result), raising=False)

    pool = _FakePool()
    caplog.set_level(logging.INFO, logger="brain")
    await bd.run_cycle(pool)

    heartbeats = [c for c in pool.execute_calls if c[1] == "brain.cycle_heartbeat"]
    decisions = [c for c in pool.execute_calls if "INSERT INTO brain_decisions" in c[0]]
    cycle_ends = [
        r.getMessage() for r in caplog.records if "=== Cycle end:" in r.getMessage()
    ]
    assert len(heartbeats) == len(decisions) == len(cycle_ends) == 1
    return _CycleRecord(
        heartbeat=json.loads(heartbeats[0][3]),
        decision_counts=_counts(decisions[0][1]),
        decision_context=json.loads(decisions[0][3]),
        log_counts=_counts(cycle_ends[0]),
    )


@pytest.mark.unit
def test_the_gated_probe_blocks_are_found():
    """Floor for the derivation: if run_cycle's probes stop being
    ``if _HAS_<X>:`` blocks, the parametrized test below collects nothing and
    reports a skip, not a failure. Fail here instead, and re-derive for the
    new shape."""
    assert len(_GATED_PROBE_BLOCKS) >= 20
    assert "run_scheduled_workflow_watch" in {r for _f, r, _k in _GATED_PROBE_BLOCKS}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "runner", "key"),
    _GATED_PROBE_BLOCKS,
    ids=[key or runner for _flag, runner, key in _GATED_PROBE_BLOCKS],
)
async def test_a_failing_gated_probe_counts_as_failed_everywhere(
    flag, runner, key, monkeypatch, caplog,
):
    result = _FAILING_RESULT.get(runner, _DEFAULT_FAILING_RESULT)
    failed = key or next(iter(result))

    cycle = await _run_cycle(
        monkeypatch, caplog, early={"site": {"ok": True}}, enabled={runner: result},
    )

    assert cycle.heartbeat["probe_status"] == {"site": "ok", failed: "issue"}
    assert cycle.heartbeat["probes_run"] == 2
    assert cycle.heartbeat["probes_failed"] == 1
    assert cycle.decision_context["probe_failures"] == [failed]
    assert cycle.decision_counts == (2, 1)
    assert cycle.log_counts == (2, 1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_tally_matches_probe_status_across_early_and_gated_probes(
    monkeypatch, caplog,
):
    """The 2026-09-27 cycle, plus an early failure: a health probe down, the
    scheduled-workflow watch reporting stale workflows, another gated probe
    fine. All three tallies must name the two failures probe_status lists."""
    cycle = await _run_cycle(
        monkeypatch,
        caplog,
        early={"site": {"ok": True}, "ollama": {"ok": False, "detail": "down"}},
        enabled={
            "run_scheduled_workflow_watch": {
                "ok": False, "detail": "2 stale scheduled workflow(s)",
            },
            "run_clock_skew_probe": {"ok": True, "detail": "skew 0.29s"},
        },
    )

    status = cycle.heartbeat["probe_status"]
    issues = sorted(name for name, s in status.items() if s == "issue")
    assert issues == ["ollama", "scheduled_workflow_watch"]
    assert cycle.heartbeat["probes_run"] == len(status) == 4
    assert cycle.heartbeat["probes_failed"] == 2
    assert sorted(cycle.decision_context["probe_failures"]) == issues
    assert cycle.decision_counts == (4, 2)
    assert cycle.log_counts == (4, 2)
