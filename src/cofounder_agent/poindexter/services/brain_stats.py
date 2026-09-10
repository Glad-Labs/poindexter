"""Brain daemon stats service.

Reads brain_decisions + brain_knowledge aggregates for the operator console
and Grafana. Called by routes/brain_routes.py — SQL lives here so the route
stays a thin adapter (transport-adapter contract, ADR 2026-06-10, #1340).

brain_queue was dropped in migration 0080 (2026-04-21); it is not referenced.
"""

from datetime import datetime, timezone


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


async def get_brain_stats(pool) -> dict:
    """Aggregate brain daemon activity.

    Returns:
        decisions_24h: decisions in the last 24 hours
        decisions_7d: decisions in the last 7 days
        avg_confidence_7d: mean confidence over last 7 days
        last_cycle_at: ISO timestamp of most recent decision
        knowledge_total: total rows in brain_knowledge
        recent_decisions: last 10 decisions (decision truncated to 120 chars)
    """
    counts = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 day')  AS decisions_24h,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') AS decisions_7d,
            ROUND(AVG(confidence) FILTER (
                WHERE created_at > NOW() - INTERVAL '7 days'
            )::numeric, 3)                                                  AS avg_confidence_7d,
            MAX(created_at)                                                 AS last_cycle_at
        FROM brain_decisions
        """
    )
    knowledge_total = await pool.fetchval("SELECT COUNT(*) FROM brain_knowledge")

    recent_rows = await pool.fetch(
        """
        SELECT
            id,
            LEFT(decision, 120) AS decision,
            outcome,
            confidence,
            created_at
        FROM brain_decisions
        ORDER BY created_at DESC
        LIMIT 10
        """
    )

    recent = [
        {
            "id": str(r["id"]),
            "decision": r["decision"],
            "outcome": r["outcome"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "created_at": _iso(r["created_at"]),
        }
        for r in recent_rows
    ]

    return {
        "decisions_24h": int(counts["decisions_24h"] or 0),
        "decisions_7d": int(counts["decisions_7d"] or 0),
        "avg_confidence_7d": (
            float(counts["avg_confidence_7d"])
            if counts["avg_confidence_7d"] is not None
            else None
        ),
        "last_cycle_at": _iso(counts["last_cycle_at"]),
        "knowledge_total": int(knowledge_total or 0),
        "recent_decisions": recent,
    }
