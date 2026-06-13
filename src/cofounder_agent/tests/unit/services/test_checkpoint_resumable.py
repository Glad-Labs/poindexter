"""Tests for ``services.template_runner.has_resumable_checkpoint`` (c1).

When a resume fails AFTER the gate was already passed (e.g. a downstream node
like ``content.republish_post`` raised), ``approve`` has already cleared
``awaiting_gate`` and the runner's exception handler returns a halted summary
WITHOUT re-raising — so the task is stranded ``in_progress`` with an intact
LangGraph checkpoint *past* the gate. ``poindexter pipeline resume``'s guard
(awaiting_gate IS NOT NULL) would refuse to re-resume it. This helper lets the
CLI detect "there's a durable checkpoint to continue from" so the operator can
resume again instead of waiting ~30 min for the stale sweep to recycle it.
"""

from __future__ import annotations

import pytest

from services.template_runner import has_resumable_checkpoint
from tests.unit.services._gate_fakes import FakeConn, FakePool

pytestmark = pytest.mark.unit


def _fake_pool(*, table_present: bool, row_exists: bool) -> FakePool:
    def _fetchval(sql: str, args):
        if "to_regclass" in sql:
            return table_present
        if "EXISTS" in sql or "checkpoints" in sql:
            return row_exists
        raise AssertionError(f"unexpected fetchval SQL: {sql[:80]}")

    return FakePool(FakeConn(fetchval_result=_fetchval))


class TestHasResumableCheckpoint:
    async def test_true_when_checkpoint_row_exists(self):
        pool = _fake_pool(table_present=True, row_exists=True)
        assert await has_resumable_checkpoint(pool, "task-123") is True

    async def test_false_when_no_checkpoint_row(self):
        pool = _fake_pool(table_present=True, row_exists=False)
        assert await has_resumable_checkpoint(pool, "task-123") is False

    async def test_false_when_checkpoint_table_absent(self):
        # Fresh install / checkpointer off → the table doesn't exist. Must not
        # raise (to_regclass returns NULL), just report "not resumable".
        pool = _fake_pool(table_present=False, row_exists=False)
        assert await has_resumable_checkpoint(pool, "task-123") is False

    async def test_false_and_swallows_errors(self):
        # Any DB error → conservative False (the at-gate path is unaffected;
        # this only gates the extra continue-resume convenience).
        class _BoomConn(FakeConn):
            async def fetchval(self, sql, *args):
                raise RuntimeError("db down")

        assert await has_resumable_checkpoint(FakePool(_BoomConn()), "t") is False
