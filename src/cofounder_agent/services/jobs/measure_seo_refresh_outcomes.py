"""Scheduled job: measure the GSC outcome of an seo_refresh, N days later.

SEO Harvest Loop Phase 2c (#763). Read-only / safe-on (no master switch — it only
ever touches status='refreshed' rows; no refreshes -> no-op). For each refreshed
opportunity older than seo.refresh.outcome_measure_after_days with no outcome
yet, re-reads the latest post_performance snapshot (the LOCAL GSC mirror — no
Google API call) and records outcome_position / outcome_ctr /
outcome_measured_at, then emits a finding with the delta vs the pre-refresh
baseline. This is the empirical proof the harvest loop works.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SELECT_DUE_SQL = """
SELECT o.id AS opportunity_id, o.post_id, o.slug,
       o.baseline_position, o.baseline_ctr, o.refreshed_at
FROM seo_opportunities o
WHERE o.status = 'refreshed'
  AND o.outcome_measured_at IS NULL
  AND o.refreshed_at IS NOT NULL
  AND o.refreshed_at < NOW() - ($1::int * INTERVAL '1 day')
"""

# Latest GSC snapshot for one post (mirrors striking_distance._LATEST_SNAPSHOT_SQL).
_LATEST_PERF_SQL = """
SELECT pp.google_impressions  AS impressions,
       pp.google_clicks       AS clicks,
       pp.google_avg_position AS position
FROM post_performance pp
WHERE pp.post_id = $1
ORDER BY pp.measured_at DESC
LIMIT 1
"""

_WRITE_OUTCOME_SQL = """
UPDATE seo_opportunities
   SET outcome_position    = $2,
       outcome_ctr         = $3,
       outcome_measured_at = NOW()
 WHERE id = $1::uuid
"""


def _fmt_delta(baseline: Any, outcome: float, lower_is_better: bool) -> str:
    """Human-readable 'baseline->outcome (improved/flat/regressed)' for findings."""
    if baseline is None:
        return "n/a"
    delta = float(baseline) - outcome if lower_is_better else outcome - float(baseline)
    arrow = "improved" if delta > 0 else ("flat" if delta == 0 else "regressed")
    return f"{float(baseline):.2f}->{outcome:.2f} ({arrow})"


class MeasureSeoRefreshOutcomesJob:
    name = "measure_seo_refresh_outcomes"
    description = (
        "Measure GSC position/CTR delta N days after an seo_refresh "
        "(read-only; reads the local post_performance mirror)"
    )
    schedule = "every 24 hours"
    idempotent = True  # outcome_measured_at guard makes it write-once

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        after_days = (
            int(sc.get_float("seo.refresh.outcome_measure_after_days", 14))
            if sc
            else 14
        )

        measured: list[dict[str, Any]] = []
        try:
            async with pool.acquire() as conn:
                due = await conn.fetch(_SELECT_DUE_SQL, after_days)
                for r in due:
                    try:
                        perf = await conn.fetchrow(_LATEST_PERF_SQL, r["post_id"])
                        if perf is None:
                            continue
                        impressions = int(perf["impressions"] or 0)
                        clicks = int(perf["clicks"] or 0)
                        position = (
                            float(perf["position"])
                            if perf["position"] is not None
                            else None
                        )
                        ctr = round(clicks / impressions, 5) if impressions else 0.0
                        await conn.execute(
                            _WRITE_OUTCOME_SQL,
                            str(r["opportunity_id"]),
                            position,
                            ctr,
                        )
                        measured.append(
                            {
                                "slug": r["slug"],
                                "position": _fmt_delta(
                                    r["baseline_position"], position or 0.0, True
                                ),
                                "ctr": _fmt_delta(r["baseline_ctr"], ctr, False),
                            }
                        )
                    except Exception as e:  # noqa: BLE001 — one bad row never aborts
                        logger.warning(
                            "[measure_seo_refresh_outcomes] failed for %s: %s",
                            r["slug"],
                            e,
                        )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[measure_seo_refresh_outcomes] due query failed: %s", e, exc_info=True
            )
            return JobResult(
                ok=False, detail=f"due query failed: {type(e).__name__}: {e}"
            )

        if measured:
            body = "## SEO refresh — outcomes measured\n\n" + "\n".join(
                f"- **{m['slug']}** — pos {m['position']}, ctr {m['ctr']}"
                for m in measured
            )
            emit_finding(
                source="measure_seo_refresh_outcomes",
                kind="seo_refresh_outcome",
                title=f"SEO: {len(measured)} refresh outcome(s) measured",
                body=body,
                # 'warn' so findings_alert_router fetches it (it filters out
                # 'info'); findings.seo_refresh_outcome.delivery='discord' then
                # pins the ops channel. Routine notification, not a page.
                severity="warn",
                extra={"count": len(measured)},
            )

        logger.info(
            "[measure_seo_refresh_outcomes] measured %d outcome(s)", len(measured)
        )
        return JobResult(
            ok=True,
            detail=f"measured {len(measured)} outcome(s)",
            changes_made=len(measured),
            metrics={"measured": len(measured)},
        )
