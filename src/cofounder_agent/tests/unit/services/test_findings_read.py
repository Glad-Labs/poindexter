"""Roundtrip tests for ``services.findings_read.read_findings``.

Exercises the real SQL against the Postgres test DB via the ``db_pool``
fixture: seeds ``audit_log`` finding rows + the delivery-policy / route-watermark
``app_settings``, then asserts the structured summary (emitted/pending counts,
by-kind / by-severity rollups, per-finding routed/PENDING/log-only status, and
the resolved delivery policy). The HTTP contract is covered separately by
``tests/unit/routes/test_findings_routes.py`` (mocked).
"""

from __future__ import annotations

import json

import pytest

from services.findings_read import read_findings

# The db_pool fixture is loop_scope="session"; tests must share that loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_finding(conn, *, kind, severity, title, source="probe_x"):
    row = await conn.fetchrow(
        "INSERT INTO audit_log (event_type, source, severity, details) "
        "VALUES ('finding', $1, $2, $3::jsonb) RETURNING id",
        source,
        severity,
        json.dumps({"kind": kind, "title": title, "body": "b"}),
    )
    return row["id"]


async def _set_setting(conn, key, value):
    await conn.execute(
        "INSERT INTO app_settings (key, value, category, description) "
        "VALUES ($1, $2, 'testing', 'findings_read test') "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key,
        value,
    )


async def _reset(conn):
    await conn.execute("DELETE FROM audit_log WHERE event_type = 'finding'")
    await _set_setting(conn, "findings_alert_route_watermark", "")


async def test_rollup_status_and_delivery(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        await _set_setting(conn, "findings.broken_external_link.delivery", "discord")
        warn_id = await _seed_finding(
            conn, kind="broken_external_link", severity="warn", title="Dead link"
        )
        crit_id = await _seed_finding(
            conn, kind="quality_regression", severity="critical", title="Score dropped"
        )
        await _seed_finding(conn, kind="note", severity="info", title="FYI")
        # Watermark at the warn row: warn -> routed (id <= wm); critical above
        # -> PENDING; info -> log-only (not a routable severity).
        await _set_setting(conn, "findings_alert_route_watermark", str(warn_id))

    try:
        out = await read_findings(db_pool, hours=168, limit=50)

        assert out["counts"]["emitted"] == 3
        assert out["counts"]["pending"] == 1  # only the critical
        assert out["watermark"] == warn_id

        by_kind = {f["kind"]: f for f in out["findings"]}
        assert by_kind["broken_external_link"]["status"] == "routed"
        assert by_kind["broken_external_link"]["delivery"] == "discord"
        assert by_kind["quality_regression"]["status"] == "PENDING"
        assert by_kind["quality_regression"]["id"] == crit_id
        assert by_kind["note"]["status"] == "log-only"

        kind_counts = {r["kind"]: r["count"] for r in out["by_kind"]}
        assert kind_counts == {
            "broken_external_link": 1,
            "quality_regression": 1,
            "note": 1,
        }
        sev_counts = {r["severity"]: r["count"] for r in out["by_severity"]}
        assert sev_counts == {"warn": 1, "critical": 1, "info": 1}
        assert out["delivery_by_kind"]["broken_external_link"] == "discord"
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)


async def test_kind_filter_and_pending_only(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        await _seed_finding(conn, kind="broken_external_link", severity="warn", title="A")
        await _seed_finding(conn, kind="broken_external_link", severity="critical", title="B")
        await _seed_finding(conn, kind="anomaly", severity="warn", title="C")
        # No watermark set (defaults to 0) → every routable finding is above it.

    try:
        # kind filter narrows everything to the two broken_external_link rows.
        only_links = await read_findings(db_pool, kind="broken_external_link")
        assert only_links["counts"]["emitted"] == 2
        assert {f["kind"] for f in only_links["findings"]} == {"broken_external_link"}

        # pending_only narrows the detail rows to routable findings above the
        # watermark (all 3 here, since wm=0 and all are warn/critical).
        pending = await read_findings(db_pool, pending_only=True)
        assert len(pending["findings"]) == 3
        assert all(f["status"] == "PENDING" for f in pending["findings"])
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
