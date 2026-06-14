"""Read + summarize the SEO-refresh opportunity pipeline for the operator console.

``seo_opportunities`` rows are the striking-distance opportunities the SEO
harvest analyzer detects (estimated clicks left on the table), which the
``seo.refresh`` loop then processes through a lifecycle:

    open → queued → refreshed → (outcome measured)

This wraps that table into a console-shaped summary — the actionable QUEUE
(open + queued, highest ``gap_score`` first), the RECENT REFRESHES (rows the
loop has acted on, with a baseline→outcome SERP-position improvement), and
by-status / by-tier rollups — for ``GET /api/seo``. Read-only; the refresh
loop runs autonomously, so the console only observes.
"""

from __future__ import annotations

from typing import Any


def _f(v: Any) -> float | None:
    """asyncpg returns Decimal for NUMERIC — coerce to float for JSON."""
    return float(v) if v is not None else None


def _iso(ts: Any) -> str | None:
    return ts.isoformat() if ts is not None else None


async def read_seo(pool: Any, *, limit: int = 30) -> dict[str, Any]:
    """Return a structured SEO-refresh summary for the operator console.

    ``limit`` (clamped 1..100) bounds the queue + refreshes detail lists; the
    by-status / by-tier rollups always cover the whole table.
    """
    limit = max(1, min(int(limit), 100))

    # Actionable queue: opportunities not yet refreshed, highest gap first.
    queue_rows = await pool.fetch(
        "SELECT id::text AS id, slug, target_query, tier, status, "
        "       current_position, impressions, gap_score, detected_at "
        "  FROM seo_opportunities "
        " WHERE status IN ('open', 'queued') "
        " ORDER BY gap_score DESC, detected_at DESC "
        " LIMIT $1",
        limit,
    )
    # Recent refreshes: rows the loop has acted on (baseline captured), newest
    # first. delta = baseline_position - outcome_position (positive = moved up,
    # since a lower SERP position number is better).
    refresh_rows = await pool.fetch(
        "SELECT id::text AS id, slug, target_query, tier, status, "
        "       baseline_position, outcome_position, current_position, "
        "       outcome_measured_at, refreshed_at "
        "  FROM seo_opportunities "
        " WHERE status = 'refreshed' OR outcome_measured_at IS NOT NULL "
        " ORDER BY COALESCE(outcome_measured_at, refreshed_at) DESC NULLS LAST "
        " LIMIT $1",
        limit,
    )
    by_status_rows = await pool.fetch(
        "SELECT status, COUNT(*) AS c FROM seo_opportunities GROUP BY 1 ORDER BY c DESC"
    )
    by_tier_rows = await pool.fetch(
        "SELECT tier, COUNT(*) AS c FROM seo_opportunities GROUP BY 1 ORDER BY c DESC"
    )

    queue = [
        {
            "id": r["id"],
            "slug": r["slug"],
            "target_query": r["target_query"] or "",
            "tier": r["tier"],
            "status": r["status"],
            "position": _f(r["current_position"]),
            "impressions": int(r["impressions"] or 0),
            "gap_score": _f(r["gap_score"]) or 0.0,
            "detected_at": _iso(r["detected_at"]),
        }
        for r in queue_rows
    ]

    refreshes: list[dict[str, Any]] = []
    for r in refresh_rows:
        baseline = _f(r["baseline_position"])
        outcome = _f(r["outcome_position"])
        latest = outcome if outcome is not None else _f(r["current_position"])
        delta = baseline - outcome if (baseline is not None and outcome is not None) else None
        refreshes.append(
            {
                "id": r["id"],
                "slug": r["slug"],
                "target_query": r["target_query"] or "",
                "tier": r["tier"],
                "status": r["status"],
                "baseline_position": baseline,
                "outcome_position": latest,
                "delta": delta,
                "measured_at": _iso(r["outcome_measured_at"] or r["refreshed_at"]),
            }
        )

    return {
        "queue": queue,
        "refreshes": refreshes,
        "by_status": [{"status": r["status"], "count": int(r["c"])} for r in by_status_rows],
        "by_tier": [{"tier": r["tier"], "count": int(r["c"])} for r in by_tier_rows],
    }
