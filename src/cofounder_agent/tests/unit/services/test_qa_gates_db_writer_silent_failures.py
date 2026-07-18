"""Unit tests — qa_gates counter-update visibility.

Silent-excepts OLD-debt burn-down (batch 20). ``record_chain_run`` bumps the
``qa_gates`` counters (``total_runs`` / ``total_rejections`` / ``last_run_at`` /
``last_run_status``) that power the QA Rails dashboard. One ``try`` wraps the
WHOLE transaction — the per-gate loop is inside it — so a failure loses EVERY
gate's counters for that chain run, not just one. Swallowed at DEBUG only, that
made the QA Rails board silently stop incrementing while the pipeline looked
healthy: the router-learning / stats gap class (batches 10-13).

``qa_gates`` is not ``audit_log``, so ``emit_finding`` is the correct signal
here (contrast batch 17/18/19, where the failing write WAS audit_log and a
finding would have vanished in the same outage).
"""

from __future__ import annotations

from typing import Any

import pytest

from services.qa_gates_db_writer import record_chain_run

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _Review:
    def __init__(self, reviewer: str, approved: bool = True, advisory: bool = False):
        self.reviewer = reviewer
        self.approved = approved
        self.advisory = advisory


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a) -> bool:
        return False


class _Conn:
    def __init__(self, execute_raises: bool) -> None:
        self._raises = execute_raises
        self.executed: list[Any] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def execute(self, *args: Any) -> str:
        if self._raises:
            raise RuntimeError("qa_gates update boom")
        self.executed.append(args)
        return "UPDATE 1"


class _AcquireCtx:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_a) -> bool:
        return False


class _Pool:
    def __init__(self, execute_raises: bool = False) -> None:
        self.conn = _Conn(execute_raises)

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)


async def test_counter_update_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)

    # Never raises — counter bookkeeping must not break a QA chain run.
    await record_chain_run(_Pool(execute_raises=True), [
        _Review("programmatic_validator", approved=False),
    ])

    findings = [c for c in calls if c["kind"] == "qa_gates_counter_update_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "qa_gates_counter_update_failed" in findings[0]["dedup_key"]


async def test_counter_update_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _Pool(execute_raises=False)

    await record_chain_run(pool, [_Review("programmatic_validator", approved=True)])

    assert pool.conn.executed, "expected the qa_gates UPDATE to run"
    assert calls == []
