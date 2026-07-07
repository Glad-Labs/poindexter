"""Roundtrip tests for ``services.qa_trend.get_qa_pass_trend`` against the
Postgres test DB (``db_pool``). Seeds ``audit_log`` qa_pass_completed rows, then
asserts the epoch-bucketed pass-rate series + the clamp. Mirrors
``tests/unit/services/test_findings_read.py``."""

from __future__ import annotations

import json

import pytest

from services.qa_trend import _clamp, get_qa_pass_trend

# db_pool is loop_scope="session"; tests must share that loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_clamp_bounds_bucket_count():
    # tiny step over a huge range must not exceed ~1000 buckets
    rng, step = _clamp(604800, 1)
    assert rng == 604800
    assert step >= 604800 / 1000
    # range floored to 60s minimum; step floored to 15
    assert _clamp(5, 5) == (60, 15)


async def _seed_qa(conn, *, approved, terminal=True):
    await conn.execute(
        "INSERT INTO audit_log (event_type, source, severity, details) "
        "VALUES ('qa_pass_completed', 'qa.aggregate', 'info', $1::jsonb)",
        json.dumps({"approved": approved, "terminal": terminal, "final_score": 80}),
    )


async def _reset(conn):
    await conn.execute("DELETE FROM audit_log WHERE event_type = 'qa_pass_completed'")


async def test_pass_rate_buckets_and_gaps(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        # one bucket (~now): 1 pass + 1 fail → 50%
        await _seed_qa(conn, approved=True)
        await _seed_qa(conn, approved=False)
    try:
        out = await get_qa_pass_trend(db_pool, range_seconds=3600, step_seconds=900)
        assert out["series"][0]["label"] == "pass %"
        pts = out["series"][0]["points"]
        vals = [v for _, v in pts if v is not None]
        assert 50.0 in vals  # populated bucket = 50%
        assert any(v is None for _, v in pts)  # empty buckets → null (honest gap)
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
