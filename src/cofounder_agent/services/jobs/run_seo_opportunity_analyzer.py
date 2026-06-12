"""Scheduled job: classify published posts into SEO opportunity tiers.

Read-only over post_performance — produces the "fix these N posts" list in
seo_opportunities plus a findings summary. Gated by
``seo.harvest.analyzer_enabled`` (default true; the analyzer mutates no
content). The content-mutating refresh path (Phase 2) gates separately on
``seo.refresh.enabled``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.seo.striking_distance import (
    DEFAULT_THRESHOLDS,
    analyze,
    upsert_opportunities,
)
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# app_settings key -> classifier threshold key
_SETTING_MAP = {
    "seo.striking_distance.position_min": "striking_position_min",
    "seo.striking_distance.position_max": "striking_position_max",
    "seo.push_candidate.position_min": "push_position_min",
    "seo.push_candidate.position_max": "push_position_max",
    "seo.push_candidate.min_impressions": "push_min_impressions",
    "seo.low_ctr.min_impressions": "low_ctr_min_impressions",
    "seo.low_ctr.max_ctr": "low_ctr_max_ctr",
    "seo.opportunity.target_ctr": "target_ctr",
}


def _thresholds(sc: Any) -> dict[str, float]:
    """Resolve classifier thresholds from site_config, defaulting per key."""
    th = dict(DEFAULT_THRESHOLDS)
    if sc is None:
        return th
    for setting_key, th_key in _SETTING_MAP.items():
        th[th_key] = float(sc.get_float(setting_key, DEFAULT_THRESHOLDS[th_key]))
    return th


class RunSeoOpportunityAnalyzerJob:
    name = "run_seo_opportunity_analyzer"
    description = (
        "Classify published posts into SEO opportunity tiers "
        "(page1_push / striking_distance / low_ctr) from the latest GSC snapshot"
    )
    schedule = "every 24 hours"
    idempotent = True  # read + upsert is safe to re-run

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is not None and not sc.get_bool("seo.harvest.analyzer_enabled", True):
            return JobResult(
                ok=True, detail="seo.harvest.analyzer_enabled is off; skipped"
            )

        thresholds = _thresholds(sc)
        try:
            opportunities = await analyze(pool, thresholds)
            written = await upsert_opportunities(pool, opportunities)

            by_tier: dict[str, int] = {}
            for o in opportunities:
                by_tier[o["tier"]] = by_tier.get(o["tier"], 0) + 1
            push = by_tier.get("page1_push", 0)

            if push:
                top = sorted(
                    opportunities, key=lambda o: o["gap_score"], reverse=True
                )[:10]
                body = "## SEO harvest — page-1-push candidates\n\n" + "\n".join(
                    f"- **{o['slug']}** — pos {o['position']:.1f}, "
                    f"{o['impressions']} impr, gap≈{o['gap_score']:.0f} clicks/mo"
                    for o in top
                )
                emit_finding(
                    source="run_seo_opportunity_analyzer",
                    kind="seo_opportunity",
                    title=(
                        f"SEO: {push} page-1-push, "
                        f"{by_tier.get('striking_distance', 0)} striking, "
                        f"{by_tier.get('low_ctr', 0)} low-CTR posts"
                    ),
                    body=body,
                    dedup_key="seo_opportunities",
                    extra=by_tier,
                )

            logger.info(
                "[run_seo_opportunity_analyzer] wrote %d opportunities %s",
                written,
                by_tier,
            )
            return JobResult(
                ok=True,
                detail=f"{written} opportunities ({by_tier})",
                changes_made=written,
                metrics=by_tier,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[run_seo_opportunity_analyzer] failed (non-fatal): %s",
                e,
                exc_info=True,
            )
            return JobResult(
                ok=False, detail=f"analyzer failed: {type(e).__name__}: {e}"
            )
