"""Striking-distance SEO analyzer (Harvest Loop Phase 1).

A pure classifier + a thin DB reader. The classifier decides a published post's
SEO opportunity tier and a gap_score from its latest Search Console metrics; the
analyzer pulls the latest ``post_performance`` snapshot per published post and
upserts results into ``seo_opportunities``. Read-only with respect to content.

Tier priority (each post is assigned its single highest-priority tier):

1. ``page1_push``       — pos in [push_min, push_max] AND impressions >= push
                          floor. One optimization from page 1; the biggest,
                          fastest wins.
2. ``striking_distance`` — pos in [striking_min, striking_max]. Ranks, but on
                          page 2.
3. ``low_ctr``          — impressions >= floor AND ctr <= ceiling. Ranks
                          somewhere but the title/meta isn't earning the click.

gap_score is deterministic (no LLM): estimated clicks left on the table if the
post reached ``target_ctr``. It orders the "fix this first" list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

OPPORTUNITY_TIERS = ("page1_push", "striking_distance", "low_ctr")

DEFAULT_THRESHOLDS: dict[str, float] = {
    "striking_position_min": 5.0,
    "striking_position_max": 20.0,
    "push_position_min": 3.0,
    "push_position_max": 10.0,
    "push_min_impressions": 100.0,
    "low_ctr_min_impressions": 100.0,
    "low_ctr_max_ctr": 0.01,
    "target_ctr": 0.05,
}


@dataclass(frozen=True)
class Opportunity:
    """A classified SEO opportunity for one post."""

    tier: str
    gap_score: float


def compute_gap_score(metrics: dict[str, Any], thresholds: dict[str, float]) -> float:
    """Estimated clicks left on the table if this post reached ``target_ctr``.

    Deterministic. ``gap_score = impressions * (target_ctr - current_ctr)``,
    floored at 0. Larger impression bases with weak CTR score highest — exactly
    the "fix this first" ordering.
    """
    impressions = float(metrics.get("impressions") or 0)
    ctr = float(metrics.get("ctr") or 0.0)
    target = float(thresholds.get("target_ctr", DEFAULT_THRESHOLDS["target_ctr"]))
    return max(0.0, impressions * (target - ctr))


def classify_opportunity(
    metrics: dict[str, Any], thresholds: dict[str, float]
) -> Opportunity | None:
    """Return the single highest-priority Opportunity for a post, or None."""
    position = metrics.get("position")
    if position is None:
        return None
    position = float(position)
    impressions = float(metrics.get("impressions") or 0)
    ctr = float(metrics.get("ctr") or 0.0)

    gap = compute_gap_score(metrics, thresholds)

    if (
        thresholds["push_position_min"] <= position <= thresholds["push_position_max"]
        and impressions >= thresholds["push_min_impressions"]
    ):
        return Opportunity(tier="page1_push", gap_score=gap)

    if thresholds["striking_position_min"] <= position <= thresholds["striking_position_max"]:
        return Opportunity(tier="striking_distance", gap_score=gap)

    if (
        impressions >= thresholds["low_ctr_min_impressions"]
        and ctr <= thresholds["low_ctr_max_ctr"]
    ):
        return Opportunity(tier="low_ctr", gap_score=gap)

    return None


# --------------------------------------------------------------------------
# DB analyzer (thin reader + upsert)
# --------------------------------------------------------------------------

_LATEST_SNAPSHOT_SQL = """
SELECT DISTINCT ON (pp.post_id)
       pp.post_id,
       pp.slug,
       pp.google_impressions  AS impressions,
       pp.google_clicks       AS clicks,
       pp.google_avg_position AS position
FROM post_performance pp
JOIN posts p ON p.id = pp.post_id AND p.status = 'published'
WHERE pp.google_impressions > 0
ORDER BY pp.post_id, pp.measured_at DESC
"""

_UPSERT_SQL = """
INSERT INTO seo_opportunities
    (post_id, slug, target_query, tier, current_position,
     impressions, ctr, gap_score, status, detected_at)
VALUES ($1, $2, '', $3, $4, $5, $6, $7, 'open', NOW())
ON CONFLICT (post_id, target_query) DO UPDATE
    SET tier             = EXCLUDED.tier,
        slug             = EXCLUDED.slug,
        current_position = EXCLUDED.current_position,
        impressions      = EXCLUDED.impressions,
        ctr              = EXCLUDED.ctr,
        gap_score        = EXCLUDED.gap_score,
        status           = 'open',
        detected_at      = NOW()
"""


async def analyze(pool: Any, thresholds: dict[str, float]) -> list[dict[str, Any]]:
    """Read the latest GSC snapshot per published post and classify each.

    Returns a list of upsert-ready dicts. Pure read — no writes.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LATEST_SNAPSHOT_SQL)

    out: list[dict[str, Any]] = []
    for r in rows:
        impressions = int(r["impressions"] or 0)
        clicks = int(r["clicks"] or 0)
        position = float(r["position"]) if r["position"] is not None else None
        ctr = (clicks / impressions) if impressions else 0.0
        metrics = {
            "impressions": impressions,
            "clicks": clicks,
            "ctr": ctr,
            "position": position,
        }
        opp = classify_opportunity(metrics, thresholds)
        if opp is None:
            continue
        out.append(
            {
                "post_id": r["post_id"],
                "slug": r["slug"],
                "tier": opp.tier,
                "position": position,
                "impressions": impressions,
                "ctr": round(ctr, 5),
                "gap_score": round(opp.gap_score, 2),
            }
        )
    return out


async def upsert_opportunities(pool: Any, opportunities: list[dict[str, Any]]) -> int:
    """Best-effort upsert into seo_opportunities. Returns rows written."""
    written = 0
    async with pool.acquire() as conn:
        for o in opportunities:
            try:
                await conn.execute(
                    _UPSERT_SQL,
                    o["post_id"],
                    o["slug"],
                    o["tier"],
                    o["position"],
                    o["impressions"],
                    o["ctr"],
                    o["gap_score"],
                )
                written += 1
            except Exception as e:  # noqa: BLE001 — one bad row never aborts the run
                logger.warning(
                    "seo_opportunities upsert failed for %s: %s", o.get("slug"), e
                )
    return written


async def analyze_and_upsert(pool: Any, thresholds: dict[str, float]) -> int:
    """Convenience: analyze then upsert. Returns rows written."""
    opportunities = await analyze(pool, thresholds)
    return await upsert_opportunities(pool, opportunities)
