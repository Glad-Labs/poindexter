"""Render Grafana SQL alert rules from ``app_settings`` (DB-first).

Analogous to :mod:`prometheus_rule_builder`: reads ``grafana.threshold.*``
from ``app_settings``, substitutes them into the ``alert-rules.yml.tmpl``
template, returns the rendered YAML string.

Key scheme
----------
``grafana.threshold.<name>``
    Scalar threshold (stored as ``app_settings.value``, parsed at render
    time).  Referenced inside the template as ``{threshold.<name>}``.
    Examples::

        grafana.threshold.error_rate_hourly_max = "5"
        grafana.threshold.db_size_warning_gb    = "5"

The template lives at
``infrastructure/grafana/provisioning/alerting/alert-rules.yml.tmpl``
and is bind-mounted into the worker container at
``/etc/grafana-alerting/alert-rules.yml.tmpl`` (see docker-compose).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_GRAFANA_THRESHOLDS: dict[str, str] = {
    # alert #1 — brain-high-error-rate
    "error_rate_hourly_max": "5",          # condition: error count > N in window
    "error_rate_window_hours": "1",        # SQL INTERVAL hours
    # alert #2 — brain-stale-tasks
    "stale_task_hours": "2",               # SQL INTERVAL: in_progress staleness
    # alert #3 — brain-embedding-sync-lag
    "embedding_lag_hours": "6",            # condition: hours since newest embedding
    # alert #4 — brain-db-size-warning
    "db_size_warning_gb": "5",             # condition: DB size in GB
    # alert #5 — brain-daily-cost-spike
    "cost_spike_usd": "5",                 # condition: rolling spend exceeds $N
    "cost_spike_window_hours": "24",       # SQL INTERVAL hours
    # alert #6 — brain-content-quality-drop
    "quality_score_min": "70",             # condition: avg score falls below N
    "quality_window_days": "7",            # SQL INTERVAL days
    # alert #7 — brain-traffic-anomaly
    "traffic_drop_ratio": "0.5",           # condition: ratio < N (0.5 = 50% drop)
    # alert #7b — brain-page-views-capture-dead
    "page_view_gsc_min_clicks": "20",      # SQL: GSC clicks min for dead-beacon check
    # alert #10 — brain-ollama-down
    "ollama_down_hours": "6",              # SQL INTERVAL: absence of local inference
    # alert #11 — brain-heartbeat-stale
    "brain_heartbeat_stale_minutes": "15", # condition: minutes since last brain_decision
    # alerts #13 (GPU Metrics Stale) and #14 (GPU Temperature High) were removed
    # 2026-06-18 (poindexter#653). GPU temp alerting moved to Prometheus
    # GpuTemperatureHigh in prometheus_rule_builder.py DEFAULT_RULES.
}

THRESHOLD_PREFIX = "grafana.threshold."


async def load_thresholds(pool_or_conn: Any) -> dict[str, str]:
    """Return ``threshold_name -> value`` with defaults filled in."""
    out = dict(DEFAULT_GRAFANA_THRESHOLDS)
    escaped = THRESHOLD_PREFIX.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await pool_or_conn.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE $1 ESCAPE '\\'",
        f"{escaped}%",
    )
    for row in rows:
        key, raw = row["key"], row["value"]
        name = key[len(THRESHOLD_PREFIX):]
        if raw is not None:
            out[name] = str(raw).strip()
    return out


def _substitute_thresholds(template: str, thresholds: dict[str, str]) -> str:
    """Replace ``{threshold.X}`` tokens with the configured value."""
    out = template
    for name, value in thresholds.items():
        out = out.replace("{threshold." + name + "}", value)
    return out


async def build_current(pool_or_conn: Any, template_path: Path) -> str:
    """Read the template, load thresholds from DB, return rendered YAML."""
    template = template_path.read_text(encoding="utf-8")
    thresholds = await load_thresholds(pool_or_conn)
    return _substitute_thresholds(template, thresholds)
