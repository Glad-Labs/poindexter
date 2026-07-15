"""Roundtrip tests for ``services.findings_read.get_findings_trend`` against the
Postgres test DB (``db_pool``). Mirrors ``test_findings_read.py``."""

from __future__ import annotations

import json

import pytest

from services.findings_read import get_findings_trend

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed(conn, *, severity, kind="x"):
    await conn.execute(
        "INSERT INTO audit_log (event_type, source, severity, details) "
        "VALUES ('finding', 'probe', $1, $2::jsonb)",
        severity,
        json.dumps({"kind": kind, "title": "t", "body": "b"}),
    )


async def _reset(conn):
    await conn.execute("DELETE FROM audit_log WHERE event_type = 'finding'")


async def test_one_series_per_severity_counts(db_pool):
    """"warning" rows (the drifted spelling — see utils.findings.emit_finding)
    must coalesce into the "warn" series, not fragment into their own line."""
    async with db_pool.acquire() as conn:
        await _reset(conn)
        await _seed(conn, severity="warning")
        await _seed(conn, severity="warning")
        await _seed(conn, severity="critical")
    try:
        out = await get_findings_trend(db_pool, range_seconds=3600, step_seconds=900)
        labels = sorted(s["label"] for s in out["series"])
        assert "warn" in labels and "critical" in labels
        assert "warning" not in labels
        warn = next(s for s in out["series"] if s["label"] == "warn")
        counts = [v for _, v in warn["points"]]
        # populated bucket = 2; empty buckets are 0 (a count), never null
        assert 2 in counts and all(v is not None for v in counts)
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
