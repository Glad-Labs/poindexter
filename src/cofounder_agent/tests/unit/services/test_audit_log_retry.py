"""A transient audit-write failure must not become a lost finding.

The 2026-09-09 pool-exhaustion burst lasted seconds; findings raised inside it
were dropped ("finding lost on audit write"). The writer now retries briefly
before declaring the row lost; the loud logging and out-of-band page stay
exactly as they were for a failure that persists.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.audit_log import AuditLogger


def _pool():
    pool = MagicMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(AuditLogger, "RETRY_DELAYS", (0.0, 0.0))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_failure_is_retried_and_lands():
    pool = _pool()
    pool.execute = AsyncMock(side_effect=[RuntimeError("pool exhausted"), None])
    await AuditLogger(pool).log("finding", "scheduler.dispatch", {"kind": "x"}, severity="warn")
    assert pool.execute.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persistent_failure_still_pages_after_all_attempts():
    pool = _pool()
    pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
    audit = AuditLogger(pool)
    with patch.object(audit, "_page_operator_out_of_band", new=AsyncMock()) as page:
        await audit.log("finding", "scheduler.dispatch", {"kind": "x"}, severity="critical")
    assert pool.execute.await_count == 1 + len(AuditLogger.RETRY_DELAYS)
    page.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_on_first_attempt_writes_once():
    pool = _pool()
    await AuditLogger(pool).log("job_run", "scheduler", {"ok": True})
    assert pool.execute.await_count == 1
