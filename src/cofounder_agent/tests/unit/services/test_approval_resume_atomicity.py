"""Regression tests: atomic approve+resume — service-layer halves (b + c2).

``poindexter pipeline resume`` recorded the gate approval (clearing
``awaiting_gate`` + writing an ``approved`` ``pipeline_gate_history`` row)
BEFORE re-invoking the graph, with no rollback if the resume failed. That
left a task in corrupt limbo: not re-resumable (``awaiting_gate`` cleared),
and a stale ``approved`` row that — after the stale-inprogress sweep reset
the task to ``pending`` — could auto-pass a *fresh* re-run's gate and
republish regenerated content with NO operator review.

These tests pin the two service-layer halves of the fix:

* ``approve()`` tags each ``approved`` row with the task's current
  ``retry_count`` (so the gate atom can reject a *stale* approval once a
  sweep bumps the count — see the atom tests), and returns the new
  ``pipeline_gate_history.id`` so a failed resume can target the exact row.
* ``rollback_resume_approval()`` is the compensating action: in one
  transaction it restores the paused-at-gate columns AND deletes the
  dangling approval row.

Issue: Glad-Labs/glad-labs-stack pipeline-resume atomicity.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import services.approval_service as svc
from tests.unit.services._gate_fakes import FakeConn, FakePool, executed_sql

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _silence_audit():
    with patch.object(svc, "audit_log_bg") as m:
        yield m


def _insert_metadata(conn: FakeConn) -> dict:
    """Return the parsed ``metadata`` jsonb arg of the gate_history INSERT."""
    for sql, args in conn.executed:
        if "pipeline_gate_history" in sql and "INSERT" in sql.upper():
            # metadata is the final positional ($5/$6) — a JSON string.
            return json.loads(args[-1])
    raise AssertionError("no pipeline_gate_history INSERT was recorded")


# ---------------------------------------------------------------------------
# (c2) approve() tags the approval with the task's current retry_count
# ---------------------------------------------------------------------------


class TestApproveTagsRetryCount:
    async def test_metadata_records_current_retry_count(self):
        row = {
            "id": "t1", "status": "awaiting_gate",
            "awaiting_gate": "draft_gate", "retry_count": 3,
        }
        conn = FakeConn(fetchrow_result=row, fetchval_result=1)
        pool = FakePool(conn)
        await svc.approve(task_id="t1", site_config=None, pool=pool)
        assert _insert_metadata(conn).get("approved_at_retry_count") == 3

    async def test_missing_retry_count_defaults_to_zero(self):
        # Pre-retry_count rows / fakes that omit the column must not crash
        # and must record a concrete 0 (the column's DB default).
        row = {"id": "t1", "status": "awaiting_gate", "awaiting_gate": "g"}
        conn = FakeConn(fetchrow_result=row, fetchval_result=1)
        pool = FakePool(conn)
        await svc.approve(task_id="t1", site_config=None, pool=pool)
        assert _insert_metadata(conn).get("approved_at_retry_count") == 0


# ---------------------------------------------------------------------------
# (b) approve() returns the new gate_history row id for precise rollback
# ---------------------------------------------------------------------------


class TestApproveReturnsGateHistoryId:
    async def test_returns_inserted_row_id(self):
        row = {
            "id": "t1", "status": "awaiting_gate",
            "awaiting_gate": "g", "retry_count": 0,
        }
        conn = FakeConn(fetchrow_result=row, fetchval_result=987)
        pool = FakePool(conn)
        out = await svc.approve(task_id="t1", site_config=None, pool=pool)
        assert out["gate_history_id"] == 987


# ---------------------------------------------------------------------------
# (b) rollback_resume_approval() — the compensating action
# ---------------------------------------------------------------------------


class TestRollbackResumeApproval:
    async def test_restores_paused_columns_and_deletes_row(self):
        conn = FakeConn()
        pool = FakePool(conn)
        out = await svc.rollback_resume_approval(
            task_id="t1",
            gate_name="draft_gate",
            gate_history_id=987,
            artifact={"title": "X"},
            paused_at=None,
            pool=pool,
        )
        assert out["ok"] is True
        sql = executed_sql(conn)
        # 1. The paused-at-gate state is restored so the task is re-resumable.
        assert "UPDATE pipeline_tasks" in sql
        assert "awaiting_gate" in sql
        assert "status = 'awaiting_gate'" in sql
        # 2. The dangling approval row is deleted, targeted by its id.
        assert "DELETE FROM pipeline_gate_history" in sql
        delete_args = [
            args for s, args in conn.executed
            if "DELETE FROM pipeline_gate_history" in s
        ]
        assert delete_args and 987 in delete_args[0]

    async def test_restored_gate_name_is_the_original(self):
        conn = FakeConn()
        pool = FakePool(conn)
        await svc.rollback_resume_approval(
            task_id="t1",
            gate_name="draft_gate",
            gate_history_id=1,
            artifact=None,
            paused_at=None,
            pool=pool,
        )
        update_args = [
            args for s, args in conn.executed if "UPDATE pipeline_tasks" in s
        ]
        assert update_args and "draft_gate" in update_args[0]

    async def test_none_id_skips_the_delete(self):
        # Defensive: if the caller never captured an id (e.g. a legacy
        # approval), we still restore the pause but issue no blind DELETE.
        conn = FakeConn()
        pool = FakePool(conn)
        await svc.rollback_resume_approval(
            task_id="t1",
            gate_name="g",
            gate_history_id=None,
            artifact=None,
            paused_at=None,
            pool=pool,
        )
        assert "DELETE FROM pipeline_gate_history" not in executed_sql(conn)


# ---------------------------------------------------------------------------
# (c1) latest_approved_gate — names the gate a stranded task already passed
# ---------------------------------------------------------------------------


class TestLatestApprovedGate:
    async def test_returns_gate_name_when_approved_row_exists(self):
        conn = FakeConn(fetchrow_result={"gate_name": "draft_gate"})
        gate = await svc.latest_approved_gate(FakePool(conn), "t1")
        assert gate == "draft_gate"

    async def test_returns_none_when_no_approval(self):
        conn = FakeConn(fetchrow_result=None)
        gate = await svc.latest_approved_gate(FakePool(conn), "t1")
        assert gate is None
