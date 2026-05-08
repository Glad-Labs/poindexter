"""DetectAnomaliesJob — z-score outlier detection across system metrics.

Replaces ``IdleWorker._detect_anomalies``. Runs every 4 hours by default.
Flags any metric that sits more than 2 stddev from its 30-day daily mean,
logs a row in ``audit_log``, and files a Gitea issue if 2+ metrics are
anomalous in the same cycle.

Metrics monitored:
- task_failure_rate (content_tasks.status='failed' / total, daily)
- avg_quality_score (content_tasks.quality_score, daily)
- cost_per_day (cost_logs.cost_usd sum, daily)
- error_log_rate (audit_log where severity='error', daily)

Config (``plugin.job.detect_anomalies``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 14400)
- ``config.current_window_hours`` (default 24)
- ``config.baseline_window_days`` (default 30)
- ``config.z_score_threshold`` (default 2.0)
- ``config.issue_threshold`` (default 2) — how many anomalies in one
  cycle before filing a Gitea issue
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


def _metric_queries(current_h: int, baseline_d: int) -> list[tuple[str, str, str]]:
    """Build the (name, recent_sql, historical_sql) tuples for each metric.

    SQL strings interpolate ``current_h`` and ``baseline_d`` (both ints,
    sourced from app_settings via ``site_config.get_int`` and re-cast to
    int by the caller). All ``# nosec B608`` suppressions below cover
    the same false positive: integer-only interpolation, no user input.
    """
    # Defensive int cast — already int by typing, but belt-and-suspenders
    # for the SQL interpolation below.
    current_h = int(current_h)
    baseline_d = int(baseline_d)
    return [
        (
            "task_failure_rate",
            f"""SELECT CASE WHEN COUNT(*) = 0 THEN 0
                ELSE SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::float / COUNT(*)
                END as val
                FROM content_tasks WHERE created_at > NOW() - INTERVAL '{current_h} hours'""",  # nosec B608  # current_h is int
            f"""SELECT AVG(daily_rate) as mean, STDDEV(daily_rate) as stddev FROM (
                SELECT date_trunc('day', created_at) as day,
                    CASE WHEN COUNT(*) = 0 THEN 0
                    ELSE SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::float / COUNT(*)
                    END as daily_rate
                FROM content_tasks
                WHERE created_at > NOW() - INTERVAL '{baseline_d} days'
                GROUP BY day
            ) t""",  # nosec B608  # baseline_d is int
        ),
        (
            "avg_quality_score",
            f"""SELECT AVG(quality_score) as val FROM content_tasks
                WHERE created_at > NOW() - INTERVAL '{current_h} hours'
                AND quality_score IS NOT NULL""",  # nosec B608  # current_h is int
            f"""SELECT AVG(daily_avg) as mean, STDDEV(daily_avg) as stddev FROM (
                SELECT date_trunc('day', created_at) as day, AVG(quality_score) as daily_avg
                FROM content_tasks
                WHERE created_at > NOW() - INTERVAL '{baseline_d} days' AND quality_score IS NOT NULL
                GROUP BY day
            ) t""",  # nosec B608  # baseline_d is int
        ),
        (
            "cost_per_day",
            f"""SELECT COALESCE(SUM(cost_usd), 0) as val FROM cost_logs
                WHERE created_at > NOW() - INTERVAL '{current_h} hours'""",  # nosec B608  # current_h is int
            f"""SELECT AVG(daily_cost) as mean, STDDEV(daily_cost) as stddev FROM (
                SELECT date_trunc('day', created_at) as day, COALESCE(SUM(cost_usd), 0) as daily_cost
                FROM cost_logs
                WHERE created_at > NOW() - INTERVAL '{baseline_d} days'
                GROUP BY day
            ) t""",  # nosec B608  # baseline_d is int
        ),
        (
            "error_log_rate",
            f"""SELECT COUNT(*) as val FROM audit_log
                WHERE severity = 'error' AND timestamp > NOW() - INTERVAL '{current_h} hours'""",  # nosec B608  # current_h is int
            f"""SELECT AVG(daily_errors) as mean, STDDEV(daily_errors) as stddev FROM (
                SELECT date_trunc('day', timestamp) as day, COUNT(*) as daily_errors
                FROM audit_log WHERE severity = 'error'
                AND timestamp > NOW() - INTERVAL '{baseline_d} days'
                GROUP BY day
            ) t""",  # nosec B608  # baseline_d is int
        ),
    ]


class DetectAnomaliesJob:
    name = "detect_anomalies"
    description = "Z-score outlier detection across failure rate, quality, cost, error-rate"
    schedule = "every 4 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.site_config import site_config

        current_h = int(
            config.get("current_window_hours")
            or site_config.get_int("brain_anomaly_current_window_hours", 24)
        )
        baseline_d = int(
            config.get("baseline_window_days")
            or site_config.get_int("brain_anomaly_baseline_window_days", 30)
        )
        z_threshold = float(config.get("z_score_threshold", 2.0))
        issue_threshold = int(config.get("issue_threshold", 2))

        anomalies: list[dict[str, Any]] = []

        for name, recent_q, hist_q in _metric_queries(current_h, baseline_d):
            try:
                recent = await pool.fetchval(recent_q)
                hist = await pool.fetchrow(hist_q)

                if recent is None or hist is None:
                    continue
                mean = float(hist["mean"] or 0)
                stddev = float(hist["stddev"] or 0)
                value = float(recent)

                if stddev == 0 or math.isnan(stddev):
                    continue  # not enough variance to detect anomalies

                z_score = (value - mean) / stddev
                if abs(z_score) > z_threshold:
                    anomalies.append({
                        "metric": name,
                        "value": round(value, 4),
                        "mean": round(mean, 4),
                        "stddev": round(stddev, 4),
                        "z_score": round(z_score, 2),
                        "direction": "spike" if z_score > 0 else "drop",
                    })
            except Exception as e:
                logger.debug("[ANOMALY] check failed for %s: %s", name, e)

        if not anomalies:
            return JobResult(ok=True, detail="all metrics within normal range", changes_made=0)

        # Persist to audit_log.
        try:
            await pool.execute(
                "INSERT INTO audit_log (event_type, source, details, severity) VALUES ($1, $2, $3, $4)",
                "anomaly_detected", "detect_anomalies_job",
                json.dumps(anomalies), "warning",
            )
        except Exception as e:
            logger.warning("[ANOMALY] audit_log insert failed: %s", e)

        # Emit a finding when enough anomalies are present (avoid noise).
        if len(anomalies) >= issue_threshold:
            from utils.findings import emit_finding
            body = "## Anomalies Detected\n\n" + "\n".join(
                f"- **{a['metric']}**: {a['value']} ({a['direction']}, "
                f"z={a['z_score']}, mean={a['mean']}±{a['stddev']})"
                for a in anomalies
            )
            emit_finding(
                source="detect_anomalies",
                kind="anomaly",
                severity="critical",
                title=f"anomaly: {len(anomalies)} metrics outside normal range",
                body=body,
                dedup_key="anomaly",
                extra={"anomaly_count": len(anomalies)},
            )

        logger.warning(
            "[ANOMALY] detected: %s",
            ", ".join(f"{a['metric']}={a['value']} (z={a['z_score']})" for a in anomalies),
        )
        return JobResult(
            ok=True,
            detail=f"{len(anomalies)} anomalous metric(s)",
            changes_made=len(anomalies),
            metrics={"anomalies": anomalies},
        )
