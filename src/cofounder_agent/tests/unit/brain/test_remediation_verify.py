import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from brain.remediation import engine as E
from tests.unit.brain._remediation_fakes import FakePool

LOG = logging.getLogger("t")
CFG = {"verify_after_seconds": 120}


def _pending_row(run_id, fingerprint, acted_at, verify_after=120):
    return {
        "id": 1, "timestamp": acted_at,
        "details": json.dumps({
            "remediation_run_id": run_id, "fingerprint": fingerprint,
            "alertname": "WorkerDown", "action_name": "restart_container",
            "verify_after_seconds": verify_after, "source": "rule",
        }),
    }


@pytest.mark.asyncio
async def test_resolved_writes_verify_and_does_not_page():
    acted = datetime.now(UTC) - timedelta(seconds=200)  # past grace
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r1", "fp1", acted)])
    # dedup_state.last_seen_at BEFORE we acted -> not re-fired -> resolved
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted - timedelta(seconds=5)})
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["resolved"] == 1 and out["still_firing"] == 0
    assert paged == []
    verify_rows = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert any(v.get("result") == "resolved" for v in verify_rows)


@pytest.mark.asyncio
async def test_still_firing_pages_and_writes_verify():
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r2", "fp2", acted)])
    # last_seen_at AFTER we acted -> re-fired -> still firing
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted + timedelta(seconds=30)})
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["still_firing"] == 1 and out["resolved"] == 0
    assert len(paged) == 1 and "still firing" in paged[0]
    verify_rows = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert any(v.get("result") == "still_firing" for v in verify_rows)


@pytest.mark.asyncio
async def test_not_yet_due_is_skipped():
    acted = datetime.now(UTC) - timedelta(seconds=10)  # inside grace
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r3", "fp3", acted)])
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=None)
    assert out == {"verified": 0, "resolved": 0, "still_firing": 0}


@pytest.mark.asyncio
async def test_still_firing_check_db_error_logs_warning_and_pages(caplog):
    # A DB read failure during the still-firing check must be VISIBLE (logged)
    # and fail toward paging — never a silent swallow.
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r4", "fp4", acted)])

    def _boom(sql, args):
        raise RuntimeError("dedup_state read failed")

    pool.set_fetchrow(_boom)
    paged = []

    async def notify(msg, critical=False):
        paged.append(msg)

    with caplog.at_level(logging.WARNING):
        out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)

    assert out["still_firing"] == 1 and out["resolved"] == 0
    assert len(paged) == 1
    assert any("still-firing check failed" in r.getMessage() for r in caplog.records)
