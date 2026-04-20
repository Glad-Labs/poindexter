"""TunePublishThresholdJob — auto-tune ``auto_publish_threshold`` app_setting.

Replaces ``IdleWorker._tune_thresholds``. Runs every 6 hours by default.
Reads the last N days of content_tasks, computes pass/fail rates + avg
quality score, and nudges the ``auto_publish_threshold`` value in
app_settings by at most ±step per cycle to drift toward an operating
point where pass_rate and avg_score are balanced.

## Control logic

Current state → adjustment:

- fail_rate > 50%          → -step (too strict, let more through)
- pass_rate > 90% and
  avg_score < threshold-5  → +step (gating nothing; content quality is
                                   slipping past the threshold)
- fail_rate > 30%          → -1  (gentle ease)
- pass_rate > 95% and
  avg_score > 85           → +1  (room to be pickier)
- else                     → 0

Adjustments are clamped to ``[min_threshold, max_threshold]``. Each
change is logged to ``audit_log`` as a ``threshold_auto_tuned`` event.

## Config (``plugin.job.tune_publish_threshold``)

- ``config.window_days`` (default 7) — lookback for stats
- ``config.min_samples`` (default 10) — skip the cycle if fewer
- ``config.step`` (default 3) — max adjustment magnitude per cycle
- ``config.min_threshold`` (default 50)
- ``config.max_threshold`` (default 90)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


def _compute_adjustment(
    *,
    fail_rate: float,
    pass_rate: float,
    avg_score: float,
    current_threshold: int,
    step: int,
) -> tuple[int, str]:
    """Decide (adjustment, reason) from the input stats.

    Pure function — the hard-to-test decision logic pulled out so tests
    can drive the matrix in isolation without pool mocking.
    """
    if fail_rate > 50:
        return -step, f"high failure rate ({fail_rate:.0f}%) — lowering threshold"
    if pass_rate > 90 and avg_score < current_threshold - 5:
        return step, (
            f"high pass rate ({pass_rate:.0f}%) with low avg score "
            f"({avg_score:.0f}) — raising threshold"
        )
    if fail_rate > 30:
        return -1, f"moderate failure rate ({fail_rate:.0f}%) — small decrease"
    if pass_rate > 95 and avg_score > 85:
        return 1, f"excellent quality (avg {avg_score:.0f}) — raising slightly"
    return 0, "no change needed"


class TunePublishThresholdJob:
    name = "tune_publish_threshold"
    description = "Auto-adjust auto_publish_threshold based on 7-day pass/fail rates"
    schedule = "every 6 hours"
    idempotent = True  # Changes clamp at bounds; repeat runs are safe

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        window_days = int(config.get("window_days", 7))
        min_samples = int(config.get("min_samples", 10))
        step = int(config.get("step", 3))
        min_threshold = int(config.get("min_threshold", 50))
        max_threshold = int(config.get("max_threshold", 90))

        try:
            async with pool.acquire() as conn:
                stats = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) as published,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                        AVG(quality_score) as avg_score
                    FROM content_tasks
                    WHERE created_at > NOW() - INTERVAL '1 day' * $1
                      AND quality_score IS NOT NULL
                    """,
                    window_days,
                )
        except Exception as e:
            logger.exception("TunePublishThresholdJob: stats fetch failed: %s", e)
            return JobResult(ok=False, detail=f"stats fetch failed: {e}", changes_made=0)

        total = int(stats["total"]) if stats and stats["total"] is not None else 0
        if total < min_samples:
            return JobResult(
                ok=True,
                detail=f"insufficient data ({total} tasks, need {min_samples})",
                changes_made=0,
                metrics={"total_tasks": total, "min_samples": min_samples},
            )

        published = int(stats["published"] or 0)
        failed = int(stats["failed"] or 0)
        avg_score = float(stats["avg_score"] or 0)
        pass_rate = published / total * 100 if total else 0.0
        fail_rate = failed / total * 100 if total else 0.0

        try:
            async with pool.acquire() as conn:
                current_raw = await conn.fetchval(
                    "SELECT value FROM app_settings WHERE key = 'auto_publish_threshold'"
                )
        except Exception as e:
            logger.exception("TunePublishThresholdJob: current-threshold fetch failed: %s", e)
            return JobResult(
                ok=False,
                detail=f"current-threshold fetch failed: {e}",
                changes_made=0,
            )
        current_threshold = int(current_raw) if current_raw else 75

        adjustment, reason = _compute_adjustment(
            fail_rate=fail_rate,
            pass_rate=pass_rate,
            avg_score=avg_score,
            current_threshold=current_threshold,
            step=step,
        )

        new_threshold = current_threshold
        if adjustment != 0:
            proposed = current_threshold + adjustment
            new_threshold = max(min_threshold, min(max_threshold, proposed))
            if new_threshold == current_threshold:
                adjustment = 0
                reason = f"at boundary ({new_threshold}), no change"

        if adjustment != 0:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE app_settings SET value = $1, updated_at = NOW() "
                        "WHERE key = 'auto_publish_threshold'",
                        str(new_threshold),
                    )
            except Exception as e:
                logger.exception("TunePublishThresholdJob: threshold UPDATE failed: %s", e)
                return JobResult(
                    ok=False,
                    detail=f"threshold UPDATE failed: {e}",
                    changes_made=0,
                )

            # audit_log insert — best-effort (a missing audit_log table
            # shouldn't abort the successful threshold change).
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO audit_log (event_type, source, details, severity) "
                        "VALUES ($1, $2, $3, $4)",
                        "threshold_auto_tuned",
                        "tune_publish_threshold_job",
                        json.dumps({
                            "old": current_threshold,
                            "new": new_threshold,
                            "adjustment": adjustment,
                            "reason": reason,
                            "stats": {
                                "total": total,
                                "pass_rate": round(pass_rate, 1),
                                "fail_rate": round(fail_rate, 1),
                                "avg_score": round(avg_score, 1),
                            },
                        }),
                        "info",
                    )
            except Exception as e:
                logger.warning(
                    "TunePublishThresholdJob: audit_log insert failed: %s", e,
                )

            logger.info(
                "TunePublishThresholdJob: %d → %d (%s)",
                current_threshold, new_threshold, reason,
            )

        detail = (
            f"{total} tasks, pass {pass_rate:.0f}%, fail {fail_rate:.0f}%, "
            f"avg {avg_score:.0f} → threshold {current_threshold} → {new_threshold}"
        )
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=1 if adjustment else 0,
            metrics={
                "total_tasks": total,
                "pass_rate": round(pass_rate, 1),
                "fail_rate": round(fail_rate, 1),
                "avg_score": round(avg_score, 1),
                "current_threshold": current_threshold,
                "new_threshold": new_threshold,
                "adjustment": adjustment,
                "reason": reason,
            },
        )
