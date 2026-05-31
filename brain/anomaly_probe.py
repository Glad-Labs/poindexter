"""Rolling-baseline anomaly probe (Glad-Labs/poindexter#440).

The brain's other probes are *point* checks against hand-picked thresholds
("daily traffic dropped >60%", "quality fell >10pts"). This probe is the
statistical complement: for each tracked metric it builds a rolling
``baseline_days``-day envelope (mean + sample std over complete days) and
flags the most recent complete day when it sits beyond ``sigma`` standard
deviations *in the direction that is actually bad* for that metric.

Why complete days only: the current (partial) day always looks like a drop
against full-day baselines, so the observation point is the last *complete*
day and the baseline is the ``baseline_days`` complete days before it.

Why direction-aware: a cost or error *spike* is bad but a *dip* is fine; a
throughput *drop* (the #524 pipeline-flatline scenario) is bad but a surge
is fine. A symmetric ``|z|`` test would page on good news.

Why zero-filled series: a stalled pipeline produces *no* rows, not rows of
zero — ``generate_series`` + ``LEFT JOIN`` turns missing days into real 0s
so a flatline-to-nothing is visible instead of silently absent.

Metrics intentionally scoped to those with a daily DB time-series. Queue
depth and GPU temperature are point-in-time / Prometheus-sourced and already
have dedicated probes (``stuck_tasks``, ``gpu_temperature``); modelling a
7-day envelope for them would require fabricated history.

Known v1 limitation: a z-score over a daily-count series fits *high-volume*
metrics (throughput, cost) well, but a *rare-event* metric whose normal is
~0/day (pipeline_failures) stays ``insufficient_history`` under the
non-zero ``min_samples`` guard until that event becomes common enough to
baseline. This is deliberate — it keeps the probe silent rather than
false-alarming on a young/sparse system. Proper rare-event detection
(Poisson / "N today vs baseline total") is a follow-up, not a v1 promise.

The result dict follows the ``probe_X(pool) -> dict`` contract, so it is
registered in ``health_probes.PROBES`` and inherits brain_knowledge
persistence, the doctor check-graph node, and the failure/notify path for
free. ``severity="warning"`` keeps a tripped anomaly out of critical-only
Telegram until it persists past the standard ``ALERT_AFTER_FAILURES``.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# --- Tunables (DB-first; defaults mirrored in services/settings_defaults.py
#     and a seed migration, per the cadence_slo convention). ---
_SETTING_KEYS = [
    "anomaly_probe_enabled",
    "anomaly_sigma_threshold",
    "anomaly_baseline_days",
    "anomaly_min_samples",
]
_DEFAULT_SIGMA = 3.0
_DEFAULT_BASELINE_DAYS = 7
_DEFAULT_MIN_SAMPLES = 5


@dataclass(frozen=True)
class MetricSpec:
    """A single anomaly-tracked metric.

    ``direction`` decides which tail is an alarm:
    ``"high"`` (spikes are bad), ``"low"`` (drops are bad), ``"both"``.
    """

    name: str
    direction: str
    table: str
    ts_col: str
    agg: str
    where_extra: str = ""
    unit: str = ""


# The ts column for audit_log is the quoted reserved word "timestamp".
METRICS: list[MetricSpec] = [
    MetricSpec(
        name="post_throughput",
        direction="low",
        table="posts",
        ts_col="published_at",
        agg="COUNT(*)",
        where_extra="AND status = 'published'",
        unit="posts/day",
    ),
    MetricSpec(
        name="llm_cost",
        direction="high",
        table="cost_logs",
        ts_col="created_at",
        agg="COALESCE(SUM(cost_usd), 0)",
        unit="USD/day",
    ),
    MetricSpec(
        name="pipeline_failures",
        direction="high",
        table="pipeline_tasks",
        ts_col="updated_at",
        agg="COUNT(*)",
        where_extra="AND status = 'failed'",
        unit="failed tasks/day",
    ),
    MetricSpec(
        name="audit_errors",
        direction="high",
        table="audit_log",
        ts_col='"timestamp"',
        agg="COUNT(*)",
        where_extra="AND severity IN ('error', 'critical')",
        unit="error events/day",
    ),
]


def _bool(val: str | None, default: bool) -> bool:
    if val is None or val == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _float(val: str | None, default: float) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int(val: str | None, default: int) -> int:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _series_sql(spec: MetricSpec) -> str:
    """Zero-filled daily series for the last ``$1`` complete days.

    ``$1`` is bound to ``baseline_days + 1``; the final row is the most
    recent complete day (the observation), the rest form the baseline.
    """
    return f"""
        WITH days AS (
            SELECT generate_series(
                date_trunc('day', NOW()) - make_interval(days => $1::int),
                date_trunc('day', NOW()) - INTERVAL '1 day',
                INTERVAL '1 day'
            ) AS d
        )
        SELECT days.d AS d, COALESCE(agg.v, 0)::float8 AS v
        FROM days
        LEFT JOIN (
            SELECT date_trunc('day', {spec.ts_col}) AS d, {spec.agg} AS v
            FROM {spec.table}
            WHERE {spec.ts_col} >= date_trunc('day', NOW()) - make_interval(days => $1::int)
              AND {spec.ts_col} < date_trunc('day', NOW())
              {spec.where_extra}
            GROUP BY 1
        ) agg ON agg.d = days.d
        ORDER BY days.d
    """


def _zscore(baseline: list[float], current: float) -> tuple[float, float, float]:
    """Return (z, mean, std). std==0 collapses to a signed inf when current
    differs from a flatline baseline (e.g. cost appears where there was none).
    """
    mean = statistics.fmean(baseline)
    std = statistics.stdev(baseline) if len(baseline) >= 2 else 0.0
    if std == 0.0:
        if current == mean:
            return 0.0, mean, std
        return (math.inf if current > mean else -math.inf), mean, std
    return (current - mean) / std, mean, std


def _is_anomaly(z: float, direction: str, sigma: float) -> bool:
    if direction == "high":
        return z >= sigma
    if direction == "low":
        return z <= -sigma
    return abs(z) >= sigma


def _evaluate(series: list[float], spec: MetricSpec, sigma: float, min_samples: int) -> dict:
    """Evaluate one metric's series. ``min_samples`` is the minimum number of
    *non-zero* baseline days required — guards against a young system whose
    mostly-empty history would otherwise false-alarm on first real activity.
    """
    if len(series) < 2:
        return {"status": "insufficient_data", "samples": len(series), "anomaly": False}

    *baseline, current = series
    nonzero = sum(1 for v in baseline if v != 0)
    if nonzero < min_samples:
        return {
            "status": "insufficient_history",
            "samples": nonzero,
            "current": round(current, 4),
            "anomaly": False,
        }

    z, mean, std = _zscore(baseline, current)
    anomaly = _is_anomaly(z, spec.direction, sigma)
    return {
        "status": "evaluated",
        "current": round(current, 4),
        "baseline_mean": round(mean, 4),
        "baseline_std": round(std, 4),
        "z": None if math.isinf(z) else round(z, 2),
        "flatline_break": math.isinf(z),
        "direction": spec.direction,
        "samples": len(baseline),
        "unit": spec.unit,
        "anomaly": anomaly,
    }


async def probe_anomaly(pool) -> dict:
    """Rolling-baseline anomaly check across throughput / cost / failures /
    errors. ``ok=False`` (severity warning) when any metric is anomalous or a
    metric query errors.
    """
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
            _SETTING_KEYS,
        )
        settings = {r["key"]: r["value"] for r in rows}

        if not _bool(settings.get("anomaly_probe_enabled"), True):
            return {
                "ok": True,
                "status": "disabled",
                "detail": "anomaly probe disabled (anomaly_probe_enabled=false)",
            }

        sigma = _float(settings.get("anomaly_sigma_threshold"), _DEFAULT_SIGMA)
        baseline_days = _int(settings.get("anomaly_baseline_days"), _DEFAULT_BASELINE_DAYS)
        min_samples = _int(settings.get("anomaly_min_samples"), _DEFAULT_MIN_SAMPLES)
        total_days = baseline_days + 1

        metrics: dict[str, dict] = {}
        anomalies: list[str] = []
        errored: list[str] = []

        for spec in METRICS:
            try:
                series_rows = await pool.fetch(_series_sql(spec), total_days)
                series = [float(r["v"]) for r in series_rows]
                result = _evaluate(series, spec, sigma, min_samples)
            except Exception as e:  # one bad metric must not blind the others
                result = {"status": "error", "detail": str(e)[:200], "anomaly": False}
                errored.append(spec.name)
                logger.warning("[ANOMALY] metric %s query failed: %s", spec.name, e)
            metrics[spec.name] = result
            if result.get("anomaly"):
                anomalies.append(spec.name)

        ok = not anomalies and not errored
        if anomalies:
            parts = []
            for name in anomalies:
                m = metrics[name]
                cur = m.get("current")
                mean = m.get("baseline_mean")
                parts.append(f"{name}={cur} (baseline~{mean}, {m.get('unit', '')})")
            detail = "ANOMALY: " + "; ".join(parts)
        elif errored:
            detail = f"probe degraded — metric queries failed: {', '.join(errored)}"
        else:
            evaluated = sum(1 for m in metrics.values() if m.get("status") == "evaluated")
            detail = f"{evaluated}/{len(METRICS)} metrics within {sigma:g}σ envelope"

        return {
            "ok": ok,
            "severity": "warning",
            "detail": detail,
            "sigma": sigma,
            "baseline_days": baseline_days,
            "anomalies": anomalies,
            "metrics": metrics,
        }
    except Exception as e:
        return {"ok": False, "detail": f"anomaly probe crashed: {str(e)[:200]}"}
