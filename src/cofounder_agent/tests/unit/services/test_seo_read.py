"""Roundtrip tests for ``services.seo_read.read_seo``.

Exercises the real SQL against the Postgres test DB via the ``db_pool``
fixture: seeds ``seo_opportunities`` rows across the lifecycle
(open / queued / refreshed / dismissed) then asserts the structured
summary — the actionable queue (open+queued by gap_score), the recent
refreshes (with a baseline→outcome improvement delta), and the
by-status / by-tier rollups.

``seo_opportunities`` holds live production data, so every row is seeded
with a unique slug prefix and cleaned up by that prefix only — never a
blanket DELETE (db_pool can resolve to prod on this machine). Count
assertions use ``>=`` for the same reason. The HTTP contract is covered
separately by ``tests/unit/routes/test_seo_routes.py`` (mocked).
"""

from __future__ import annotations

import pytest

from services.seo_read import read_seo

# The db_pool fixture is loop_scope="session"; tests must share that loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")

_PREFIX = "test-seo-p11-"


async def _seed(
    conn,
    *,
    slug,
    tier,
    status,
    gap,
    position=None,
    baseline=None,
    outcome=None,
    measured=False,
    refreshed=False,
):
    await conn.execute(
        "INSERT INTO seo_opportunities "
        "(slug, target_query, tier, status, gap_score, current_position, "
        " baseline_position, outcome_position, outcome_measured_at, refreshed_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8, "
        "        CASE WHEN $9 THEN NOW() ELSE NULL END, "
        "        CASE WHEN $10 THEN NOW() ELSE NULL END)",
        _PREFIX + slug,
        "query " + slug,
        tier,
        status,
        gap,
        position,
        baseline,
        outcome,
        measured,
        refreshed,
    )


async def _reset(conn):
    await conn.execute("DELETE FROM seo_opportunities WHERE slug LIKE $1", _PREFIX + "%")


async def test_queue_refreshes_and_counts(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        await _seed(conn, slug="a", tier="quick_win", status="open", gap=120.5, position=8.2)
        await _seed(conn, slug="b", tier="quick_win", status="queued", gap=80.0, position=11.0)
        await _seed(
            conn,
            slug="c",
            tier="high_value",
            status="refreshed",
            gap=50.0,
            position=6.0,
            baseline=12.0,
            outcome=6.0,
            measured=True,
            refreshed=True,
        )
        await _seed(conn, slug="d", tier="long_tail", status="dismissed", gap=10.0)

    try:
        out = await read_seo(db_pool, limit=50)

        # queue = open + queued only, ordered by gap_score DESC
        q = [r for r in out["queue"] if r["slug"].startswith(_PREFIX)]
        qslugs = {r["slug"] for r in q}
        assert _PREFIX + "a" in qslugs
        assert _PREFIX + "b" in qslugs
        assert _PREFIX + "c" not in qslugs  # refreshed is not actionable queue
        assert _PREFIX + "d" not in qslugs  # dismissed is excluded
        order = [r["slug"] for r in q]
        assert order.index(_PREFIX + "a") < order.index(_PREFIX + "b")  # gap DESC
        # NUMERIC columns come back as float, not Decimal
        a_row = next(r for r in q if r["slug"] == _PREFIX + "a")
        assert isinstance(a_row["gap_score"], float)
        assert a_row["gap_score"] == pytest.approx(120.5)

        # refreshes = refreshed / outcome-measured rows, with an improvement delta
        refreshes = {r["slug"]: r for r in out["refreshes"] if r["slug"].startswith(_PREFIX)}
        assert _PREFIX + "c" in refreshes
        # delta = baseline_position - outcome_position = 12 - 6 = +6 (moved up)
        assert refreshes[_PREFIX + "c"]["delta"] == pytest.approx(6.0)

        # rollups include our seeds (>= because prod rows may coexist)
        by_status = {r["status"]: r["count"] for r in out["by_status"]}
        assert by_status.get("open", 0) >= 1
        assert by_status.get("queued", 0) >= 1
        assert by_status.get("refreshed", 0) >= 1
        by_tier = {r["tier"]: r["count"] for r in out["by_tier"]}
        assert by_tier.get("quick_win", 0) >= 2
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
