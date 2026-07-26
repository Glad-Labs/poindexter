"""brain/service_restart.py — the poller's claim/execute/record logic
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
from brain import service_restart as sr

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

    def __init__(self, claim_rows: list[dict], sweep_rows: list[dict] | None = None):
        self.conn = _FakeConn(claim_rows)
        self.pool_executed: list[tuple[str, tuple]] = []
        # Rows the stale-claim sweep's UPDATE ... RETURNING hands back.
        self._sweep_rows = sweep_rows or []
        self.pool_fetched: list[tuple[str, tuple]] = []

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
    (Glad-Labs/glad-labs-stack#2505).
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
