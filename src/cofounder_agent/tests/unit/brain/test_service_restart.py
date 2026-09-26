"""poindexter/brain/service_restart.py — the poller's claim/execute/record logic
(poindexter#909). SKIP LOCKED concurrency-safety itself is proven against a
real Postgres in tests/integration_db/test_service_restart_requests.py — a
fake connection can't demonstrate real row-locking, so this file covers the
control flow around it: empty-queue no-op, success/failure status mapping,
audit-log shape, and the brain_daemon-unavailable degrade path.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from poindexter.brain import service_restart as sr

pytestmark = pytest.mark.asyncio


class _FakeConn:
    def __init__(self, claim_rows: list[dict]):
        self._claim_rows = claim_rows
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _NullCtx()

    async def fetch(self, _sql: str, *_args: Any) -> list:
        return self._claim_rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _AcquireCtx:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc):
        return False


class _FakePool:
    """Supports BOTH the acquire()-scoped claim transaction AND the flat
    pool.execute() calls the per-row update/audit-write use — mirrors real
    asyncpg.Pool, which proxies execute() by acquiring its own connection."""

    def __init__(
        self, claim_rows: list[dict], sweep_rows: list[dict] | None = None,
        settings: dict[str, str] | None = None,
    ):
        self.conn = _FakeConn(claim_rows)
        self.pool_executed: list[tuple[str, tuple]] = []
        # Rows the stale-claim sweep's UPDATE ... RETURNING hands back.
        self._sweep_rows = sweep_rows or []
        self.pool_fetched: list[tuple[str, tuple]] = []
        # app_settings the footprint guard reads via pool.fetchval.
        self._settings = settings or {}

    async def fetchval(self, _sql: str, *args: Any) -> Any:
        return self._settings.get(args[0]) if args else None

    def acquire(self):
        return _AcquireCtx(self.conn)

    async def execute(self, sql: str, *args: Any) -> str:
        self.pool_executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list:
        self.pool_fetched.append((sql, args))
        return self._sweep_rows


class _FakeBrainDaemon:
    def __init__(self, result: tuple[bool, str]):
        self._result = result
        self.calls: list[tuple[str, Any]] = []

    async def docker_restart_container(self, container: str, *, pool=None):
        self.calls.append((container, pool))
        return self._result


async def test_empty_queue_is_a_noop(monkeypatch):
    pool = _FakePool(claim_rows=[])
    daemon = _FakeBrainDaemon((True, "restarted"))
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

    await sr.poll_and_execute_restart_requests(pool)

    assert daemon.calls == []
    assert pool.pool_executed == []


async def test_successful_restart_marks_done_and_audits(monkeypatch):
    rid = uuid.uuid4()
    pool = _FakePool(claim_rows=[{"id": rid, "container": "poindexter-pyroscope"}])
    daemon = _FakeBrainDaemon((True, "restarted poindexter-pyroscope"))
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

    await sr.poll_and_execute_restart_requests(pool)

    assert daemon.calls == [("poindexter-pyroscope", pool)]
    # Claim UPDATE happened on the transaction connection.
    claim_sql = pool.conn.executed[0][0]
    assert "status = 'claimed'" in claim_sql
    # Outcome UPDATE + audit INSERT happened via pool.execute (its own conn).
    outcome_sql, outcome_args = pool.pool_executed[0]
    assert "status = $1" in outcome_sql
    assert outcome_args[0] == "done"
    audit_sql, audit_args = pool.pool_executed[1]
    assert "INSERT INTO audit_log" in audit_sql
    assert audit_args[0] == "service_restart_completed"
    assert audit_args[4] == "info"  # severity


async def test_failed_restart_marks_failed_with_warning_severity(monkeypatch):
    rid = uuid.uuid4()
    pool = _FakePool(claim_rows=[{"id": rid, "container": "poindexter-ghost"}])
    daemon = _FakeBrainDaemon((False, "container poindexter-ghost not found"))
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

    await sr.poll_and_execute_restart_requests(pool)

    outcome_sql, outcome_args = pool.pool_executed[0]
    assert outcome_args[0] == "failed"
    assert "not found" in outcome_args[1]
    _audit_sql, audit_args = pool.pool_executed[1]
    assert audit_args[4] == "warning"


async def test_docker_restart_raising_is_caught_as_failed(monkeypatch):
    """One bad row must not kill the batch (docstring contract) — an
    exception from the restart call itself still resolves to a 'failed' row,
    never an unhandled raise into the poll loop."""
    rid = uuid.uuid4()
    pool = _FakePool(claim_rows=[{"id": rid, "container": "poindexter-worker"}])

    class _RaisingDaemon:
        async def docker_restart_container(self, *_a, **_k):
            raise RuntimeError("docker daemon unreachable")

    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: _RaisingDaemon())

    await sr.poll_and_execute_restart_requests(pool)  # must not raise

    outcome_args = pool.pool_executed[0][1]
    assert outcome_args[0] == "failed"
    assert "docker daemon unreachable" in outcome_args[1]


async def test_brain_daemon_unavailable_leaves_queue_unclaimed(monkeypatch):
    """The lazy-import guard mirrors alert_dispatcher's degrade path — no
    brain_daemon module resolvable means rows accumulate pending, not crash."""
    pool = _FakePool(claim_rows=[{"id": uuid.uuid4(), "container": "poindexter-x"}])
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: None)

    await sr.poll_and_execute_restart_requests(pool)

    assert pool.conn.executed == []  # never even reached the claim query
    assert pool.pool_executed == []


async def test_multiple_claimed_rows_all_execute(monkeypatch):
    rows = [
        {"id": uuid.uuid4(), "container": "poindexter-loki"},
        {"id": uuid.uuid4(), "container": "poindexter-tempo"},
    ]
    pool = _FakePool(claim_rows=rows)
    daemon = _FakeBrainDaemon((True, "ok"))
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

    await sr.poll_and_execute_restart_requests(pool)

    assert {c for c, _p in daemon.calls} == {"poindexter-loki", "poindexter-tempo"}
    # 2 rows x (outcome UPDATE + audit INSERT) = 4 pool.execute calls.
    assert len(pool.pool_executed) == 4


class TestStaleClaimSweep:
    """A row is marked `claimed` in one transaction and finalized only after the
    restart returns. If brain dies in between, nothing reclaims it — the claim
    query filters `status='pending'` — so it strands in `claimed` forever and
    the console reports a permanent "still in progress". The sweep closes that
    (Glad-Labs/poindexter#2505).
    """

    async def test_sweep_runs_before_claiming(self, monkeypatch):
        pool = _FakePool(claim_rows=[])
        daemon = _FakeBrainDaemon((True, "ok"))
        monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

        await sr.poll_and_execute_restart_requests(pool)

        assert len(pool.pool_fetched) == 1
        sql = pool.pool_fetched[0][0]
        assert "status = 'claimed'" in sql
        assert "SET status = 'failed'" in sql

    async def test_sweep_runs_even_when_brain_daemon_unavailable(self, monkeypatch):
        """The degrade path returns early — but an orphaned row must still be
        swept, or a brain-image problem freezes the queue on BOTH axes."""
        pool = _FakePool(claim_rows=[])
        monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: None)

        await sr.poll_and_execute_restart_requests(pool)

        assert len(pool.pool_fetched) == 1

    async def test_swept_rows_are_audited(self, monkeypatch):
        rid = uuid.uuid4()
        pool = _FakePool(
            claim_rows=[],
            sweep_rows=[{"id": rid, "container": "poindexter-loki"}],
        )
        daemon = _FakeBrainDaemon((True, "ok"))
        monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

        await sr.poll_and_execute_restart_requests(pool)

        audits = [
            args for sql, args in pool.pool_executed if "audit_log" in sql
        ]
        assert len(audits) == 1
        assert "service_restart_orphaned" in audits[0]

    async def test_sweep_failure_never_blocks_the_poll(self, monkeypatch):
        """Sweep is maintenance; a DB hiccup there must not stop real restarts."""
        rid = uuid.uuid4()
        pool = _FakePool(claim_rows=[{"id": rid, "container": "poindexter-tempo"}])

        async def _boom(*_a, **_kw):
            raise RuntimeError("sweep query failed")

        pool.fetch = _boom  # type: ignore[method-assign]
        daemon = _FakeBrainDaemon((True, "restarted"))
        monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

        await sr.poll_and_execute_restart_requests(pool)

        assert [c for c, _p in daemon.calls] == ["poindexter-tempo"]



# --- the footprint guard (2026-09-17) ----------------------------------------
#
# Rows the reclaim ladder queues (requested_by='gpu_vram_reclaim') mean "this
# sidecar declined to unload while the card was short" — a blind inference.
# 35 restarts in 3 h, all of idle sidecars, because the card was full of
# someone ELSE's work. Brain can see docker + the per-pid exporter metric, so
# it measures before it bounces.

_RECLAIM_ROW = {"id": uuid.uuid4(), "container": "poindexter-wan-server", "requested_by": "gpu_vram_reclaim"}


async def _run_with_footprint(monkeypatch, footprint, *, row=None, settings=None, daemon_result=(True, "restarted")):
    pool = _FakePool(claim_rows=[dict(row or _RECLAIM_ROW)], settings=settings)
    daemon = _FakeBrainDaemon(daemon_result)
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

    async def _fake_footprint(_container, _pool):
        return footprint

    monkeypatch.setattr(sr, "container_gpu_footprint_gb", _fake_footprint)
    await sr.poll_and_execute_restart_requests(pool)
    return pool, daemon


async def test_idle_sidecar_below_the_squat_floor_is_not_bounced(monkeypatch):
    pool, daemon = await _run_with_footprint(monkeypatch, 0.49)

    assert daemon.calls == [], "an idle sidecar must not be restarted"
    outcome_sql, outcome_args = pool.pool_executed[0]
    assert "status = $1" in outcome_sql
    assert outcome_args[0] == "done"  # the status CHECK has no 'skipped'
    assert outcome_args[1].startswith("skipped —")
    assert "0.49 GB" in outcome_args[1] and "1.0 GB" in outcome_args[1]
    _audit_sql, audit_args = pool.pool_executed[1]
    assert audit_args[0] == "service_restart_skipped"
    assert audit_args[4] == "info"


async def test_real_squatter_is_still_restarted(monkeypatch):
    pool, daemon = await _run_with_footprint(monkeypatch, 10.96)  # stable-audio, poindexter#999

    assert daemon.calls == [("poindexter-wan-server", pool)]
    _sql, args = pool.pool_executed[0]
    assert args[0] == "done" and args[1] == "restarted"
    assert pool.pool_executed[1][1][0] == "service_restart_completed"


async def test_unknown_footprint_never_bounces_blind(monkeypatch):
    pool, daemon = await _run_with_footprint(monkeypatch, None)

    assert daemon.calls == []
    assert "unknown" in pool.pool_executed[0][1][1]


async def test_operator_requests_bypass_the_guard(monkeypatch):
    """A console click is deliberate — the guard is for the ladder's guesses."""
    row = {**_RECLAIM_ROW, "requested_by": "console"}
    pool, daemon = await _run_with_footprint(monkeypatch, 0.0, row=row)

    assert daemon.calls == [("poindexter-wan-server", pool)]


async def test_guard_can_be_switched_off(monkeypatch):
    pool, daemon = await _run_with_footprint(
        monkeypatch, 0.0,
        settings={"vram_reclaim_restart_footprint_guard_enabled": "false"},
    )
    assert daemon.calls == [("poindexter-wan-server", pool)]


async def test_squat_floor_is_the_ladders_setting(monkeypatch):
    """The same knob the scheduler uses (vram_reclaim_min_freed_gb) — one floor."""
    pool, daemon = await _run_with_footprint(
        monkeypatch, 2.5, settings={"vram_reclaim_min_freed_gb": "3.0"},
    )
    assert daemon.calls == []
    assert "3.0 GB" in pool.pool_executed[0][1][1]


async def test_guard_raising_skips_rather_than_bouncing_or_stranding(monkeypatch):
    pool = _FakePool(claim_rows=[dict(_RECLAIM_ROW)])
    daemon = _FakeBrainDaemon((True, "restarted"))
    monkeypatch.setattr(sr, "_resolve_brain_daemon_module", lambda: daemon)

    async def _boom(_c, _p):
        raise RuntimeError("docker socket gone")

    monkeypatch.setattr(sr, "container_gpu_footprint_gb", _boom)
    await sr.poll_and_execute_restart_requests(pool)

    assert daemon.calls == []
    assert pool.pool_executed[0][1][0] == "done"
    assert "guard raised" in pool.pool_executed[0][1][1]


# async only because the module-level pytestmark marks every test asyncio;
# a sync test under it draws a PytestWarning on every run.
async def test_parse_process_memory_mib_reads_the_exporter_text():
    body = (
        "# HELP nvidia_gpu_process_memory_mib Per-process GPU memory (compute apps)\n"
        "# TYPE nvidia_gpu_process_memory_mib gauge\n"
        'nvidia_gpu_process_memory_mib{gpu="0",pid="1137114",process="python"} 498.0\n'
        'nvidia_gpu_process_memory_mib{gpu="1",pid="349838",process="llama-server"} 21384\n'
        'nvidia_gpu_process_memory_mib{gpu="0",pid="1137114",process="python"} 2.0\n'
        'nvidia_gpu_memory_used_mib{gpu="0"} 27000\n'
        "garbage line\n"
    )
    assert sr.parse_process_memory_mib(body) == {1137114: 500.0, 349838: 21384.0}


async def test_footprint_sums_only_the_containers_pids(monkeypatch):
    async def _pids(_container):
        return [1137114, 4242]

    async def _text(_url):
        return (
            'nvidia_gpu_process_memory_mib{gpu="0",pid="1137114",process="python"} 512.0\n'
            'nvidia_gpu_process_memory_mib{gpu="0",pid="4242",process="python"} 512.0\n'
            'nvidia_gpu_process_memory_mib{gpu="0",pid="1662201",process="python"} 20480.0\n'
        )

    monkeypatch.setattr(sr, "_container_pids", _pids)
    monkeypatch.setattr(sr, "_fetch_exporter_text", _text)
    pool = _FakePool(claim_rows=[])
    assert await sr.container_gpu_footprint_gb("poindexter-wan-server", pool) == 1.0


async def test_footprint_is_unknown_when_docker_or_exporter_fail(monkeypatch):
    async def _no_pids(_container):
        return None

    monkeypatch.setattr(sr, "_container_pids", _no_pids)
    assert await sr.container_gpu_footprint_gb("poindexter-x", _FakePool(claim_rows=[])) is None

    async def _pids(_container):
        return [1]

    async def _no_text(_url):
        return None

    monkeypatch.setattr(sr, "_container_pids", _pids)
    monkeypatch.setattr(sr, "_fetch_exporter_text", _no_text)
    assert await sr.container_gpu_footprint_gb("poindexter-x", _FakePool(claim_rows=[])) is None
