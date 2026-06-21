"""Apply the 2026-05-14 dashboard audit additions (poindexter#504 follow-up).

Adds panels for: Lane C template split, topic_auto_resolve, pipeline throttle,
DB pool health, boot import audit Loki feed. Idempotent — re-running won't
duplicate panels (each new panel has a stable id ≥ 700, dropped + re-added).

Run from host:
    python scripts/_dashboard_additions.py

Then restart Grafana to pick up provisioned changes:
    docker restart poindexter-grafana
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DASHBOARDS_DIR = (
    Path(__file__).resolve().parent.parent
    / "infrastructure"
    / "grafana"
    / "dashboards"
)
POSTGRES_DATASOURCE = {
    "type": "grafana-postgresql-datasource",
    "uid": "local-brain-db",
}
PROMETHEUS_DATASOURCE = {
    "type": "prometheus",
    # Must match the provisioned datasource uid in
    # infrastructure/grafana/provisioning/datasources/local-prometheus.yml.
    # (It is NOT "prometheus" — that uid resolves to no datasource, and
    # local-prometheus is isDefault:false so there's no fallback.)
    "uid": "local-prometheus",
}
LOKI_DATASOURCE = {
    "type": "loki",
    "uid": "loki",
}

# Reserved panel-ID range for these additions. Each new panel gets a stable
# id so re-running this script idempotently replaces in place.
RESERVED_IDS = {
    # mission-control
    "mc_template_split": 700,
    "mc_heartbeat_age": 711,
    # pipeline-merged
    "pm_topic_auto_resolve_stat": 701,
    "pm_topic_auto_resolve_timeseries": 702,
    "pm_throttle_state": 703,
    "pm_throttle_seconds": 704,
    "pm_self_review_activity": 709,
    "pm_self_review_rate": 710,
    # system-health-merged
    "sh_db_pool": 705,
    "sh_unapplied_migrations": 706,
    "sh_ollama_health": 707,
    "sh_heartbeat_age_timeseries": 712,
    "sh_scheduler_freshness_table": 713,
    "sh_scheduler_freshness_topk": 714,
    "sh_scheduler_failed_jobs": 715,
    "sh_background_loops_overview": 716,
    # observability-merged
    "obs_boot_audit_logs": 708,
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, dashboard: dict) -> None:
    # Preserve the existing pretty-format (2-space indent, no trailing newline)
    # so diffs stay readable. Grafana doesn't care about format.
    path.write_text(
        json.dumps(dashboard, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _replace_or_append(panels: list[dict], new_panel: dict) -> str:
    """Replace a panel by id, or append. Returns 'replaced' or 'added'."""
    pid = new_panel["id"]
    for i, p in enumerate(panels):
        if p.get("id") == pid:
            panels[i] = new_panel
            return "replaced"
    panels.append(new_panel)
    return "added"


# ---------------------------------------------------------------------------
# Mission Control — Lane C template split
# ---------------------------------------------------------------------------


def mission_control_template_split() -> dict:
    """Pie chart showing canonical_blog vs dev_diary task split over 7 days.

    This is the panel that would have flagged the 49h dark period on
    2026-05-12 — dev_diary was 100% of tasks, canonical_blog 0%.
    """
    return {
        "id": RESERVED_IDS["mc_template_split"],
        "title": "Tasks by template (7d)",
        "type": "piechart",
        "description": (
            "Distribution of pipeline_tasks by template_slug over the last 7 "
            "days. Healthy mix is dev_diary (daily build-in-public) + "
            "canonical_blog (operator-curated). If canonical_blog hits 0% "
            "and stays there >24h, topic-resolve is dark — check the "
            "topic_auto_resolve job or operator-driven CLI."
        ),
        "datasource": POSTGRES_DATASOURCE,
        "gridPos": {"x": 0, "y": 25, "w": 8, "h": 7},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "mappings": [],
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "displayLabels": ["name", "percent"],
            "legend": {
                "displayMode": "list",
                "placement": "right",
                "showLegend": True,
                "values": ["value"],
            },
            "pieType": "donut",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [
            {
                "datasource": POSTGRES_DATASOURCE,
                "format": "table",
                "rawQuery": True,
                "rawSql": (
                    "SELECT COALESCE(NULLIF(template_slug, ''), 'none') AS metric, "
                    "COUNT(*)::int AS value "
                    "FROM pipeline_tasks "
                    "WHERE created_at > now() - interval '7 days' "
                    "GROUP BY 1 "
                    "ORDER BY 2 DESC"
                ),
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Mission Control + System Health — heartbeat age (silent-hang canary)
# ---------------------------------------------------------------------------


def mission_control_heartbeat_age() -> dict:
    """Worker heartbeat age stat — orange when stale, blue when healthy.

    Catches the silent-hang failure mode where poindexter_worker_up=1
    (the /metrics handler still responds) but background tasks are dead.
    Healthy: ~30s. Stale: ≥90s → heartbeat loop crashed.
    """
    return {
        "id": RESERVED_IDS["mc_heartbeat_age"],
        "title": "Worker heartbeat age",
        "type": "stat",
        "description": (
            "Seconds since the worker's capability_registry row was last "
            "updated. Healthy ≈ 30s (HEARTBEAT_INTERVAL). >90s means the "
            "heartbeat asyncio task died silently — poindexter_worker_up=1 "
            "is misleading because /metrics responds even when background "
            "tasks are dead. Exposed by the 2026-05-15 00:05 hang."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 8, "y": 25, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 90},
                    ],
                },
                "unit": "s",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "value",
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_worker_heartbeat_age_seconds",
                "instant": True,
                "refId": "A",
            }
        ],
    }


def system_health_heartbeat_age_timeseries() -> dict:
    """Heartbeat age time-series — sawtooth pattern when healthy."""
    return {
        "id": RESERVED_IDS["sh_heartbeat_age_timeseries"],
        "title": "Worker heartbeat age (silent-hang canary)",
        "type": "timeseries",
        "description": (
            "Heartbeat age over time. Sawtooth pattern (0→30s repeating) "
            "= healthy. Monotonic climb = heartbeat loop dead. Flat line "
            ">90s = silent-hang in progress; brain will auto-restart at "
            "the 5min monitor cycle."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 0, "y": 260, "w": 12, "h": 6},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 10,
                    "lineWidth": 2,
                    "showPoints": "never",
                    "spanNulls": False,
                    "thresholdsStyle": {"mode": "line"},
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 90},
                    ],
                },
                "unit": "s",
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": ["max", "lastNotNull"],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_worker_heartbeat_age_seconds",
                "legendFormat": "heartbeat age",
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Pipeline — topic_auto_resolve stat + time-series
# ---------------------------------------------------------------------------


def pipeline_topic_auto_resolve_stat() -> dict:
    return {
        "id": RESERVED_IDS["pm_topic_auto_resolve_stat"],
        "title": "Auto-resolved batches (24h)",
        "type": "stat",
        "description": (
            "topic_auto_resolve job (every 2h cron) auto-promotes the "
            "rank-1 topic_candidate in each open batch to a canonical_blog "
            "task. Replaces manual `poindexter topics rank-batch / "
            "resolve-batch` CLI calls. Master switch: "
            "topic_auto_resolve_enabled."
        ),
        "datasource": POSTGRES_DATASOURCE,
        "gridPos": {"x": 0, "y": 200, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "blue", "value": 1},
                    ],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "auto",
        },
        "targets": [
            {
                "datasource": POSTGRES_DATASOURCE,
                "format": "table",
                "rawQuery": True,
                "rawSql": (
                    "SELECT COUNT(*) AS \"Auto-resolved (24h)\" "
                    "FROM audit_log "
                    "WHERE event_type = 'topic_auto_resolved' "
                    "AND timestamp > now() - interval '24 hours'"
                ),
                "refId": "A",
            }
        ],
    }


def pipeline_topic_auto_resolve_timeseries() -> dict:
    return {
        "id": RESERVED_IDS["pm_topic_auto_resolve_timeseries"],
        "title": "Auto-resolve activity (7d, per niche)",
        "type": "timeseries",
        "description": (
            "topic_auto_resolved audit_log events bucketed daily, split "
            "by niche_slug from details->>'niche_slug'. Each bar = one "
            "canonical_blog task automatically promoted from an open "
            "topic_batch."
        ),
        "datasource": POSTGRES_DATASOURCE,
        "gridPos": {"x": 4, "y": 200, "w": 12, "h": 6},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "bars",
                    "fillOpacity": 70,
                    "lineWidth": 1,
                    "barAlignment": 0,
                    "showPoints": "never",
                    "spanNulls": False,
                    "stacking": {"mode": "normal", "group": "A"},
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "blue", "value": None}],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": ["sum"],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [
            {
                "datasource": POSTGRES_DATASOURCE,
                "format": "time_series",
                "rawQuery": True,
                "rawSql": (
                    "SELECT date_trunc('day', timestamp) AS time, "
                    "COALESCE(details->>'niche_slug', 'unknown') AS metric, "
                    "COUNT(*) AS value "
                    "FROM audit_log "
                    "WHERE event_type = 'topic_auto_resolved' "
                    "AND timestamp > now() - interval '7 days' "
                    "GROUP BY 1, 2 "
                    "ORDER BY 1"
                ),
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Pipeline — throttle state (orphan Prometheus metrics)
# ---------------------------------------------------------------------------


def pipeline_throttle_state() -> dict:
    return {
        "id": RESERVED_IDS["pm_throttle_state"],
        "title": "Pipeline throttle",
        "type": "stat",
        "description": (
            "Approval-queue throttle state. When 'active' = 1, new tasks "
            "are being suppressed because awaiting_approval count >= "
            "max_approval_queue. Operator must approve or reject pending "
            "posts to release the throttle."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 16, "y": 200, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [
                    {
                        "options": {
                            "0": {"text": "Open", "color": "blue", "index": 0},
                            "1": {"text": "THROTTLED", "color": "orange", "index": 1},
                        },
                        "type": "value",
                    }
                ],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 1},
                    ],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "value_and_name",
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_pipeline_throttle_active",
                "instant": True,
                "refId": "A",
            }
        ],
    }


def pipeline_throttle_seconds_total() -> dict:
    return {
        "id": RESERVED_IDS["pm_throttle_seconds"],
        "title": "Throttle time (last 24h)",
        "type": "timeseries",
        "description": (
            "Cumulative seconds the pipeline has spent in the throttled "
            "state. A flat line means no throttling fired in the window; "
            "a rising line means tasks were suppressed pending operator "
            "approval. Derivative gives the throttle-rate."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 20, "y": 200, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 10,
                    "lineWidth": 2,
                    "showPoints": "never",
                    "spanNulls": False,
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "blue", "value": None}],
                },
                "unit": "s",
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": [],
                "displayMode": "hidden",
                "placement": "bottom",
                "showLegend": False,
            },
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "increase(poindexter_pipeline_throttle_seconds_total[24h])",
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Pipeline — writer_self_review activity (enabled 2026-05-14)
# ---------------------------------------------------------------------------


def pipeline_self_review_rate() -> dict:
    return {
        "id": RESERVED_IDS["pm_self_review_rate"],
        "title": "Self-review pass rate (24h)",
        "type": "stat",
        "description": (
            "Percentage of self_review passes (no contradictions found) "
            "vs total reviews. The writer_self_review stage runs after "
            "the initial draft to catch internal contradictions before "
            "QA. High pass rate = drafts are coherent; low rate = "
            "writer model is producing self-contradictory content."
        ),
        "datasource": POSTGRES_DATASOURCE,
        "gridPos": {"x": 0, "y": 220, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "orange", "value": None},
                        {"color": "blue", "value": 60},
                    ],
                },
                "unit": "percent",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "value",
        },
        "targets": [
            {
                "datasource": POSTGRES_DATASOURCE,
                "format": "table",
                "rawQuery": True,
                "rawSql": (
                    "SELECT ROUND(100.0 * COUNT(*) FILTER ("
                    "WHERE event_type = 'writer_self_review_pass'"
                    ")::numeric / NULLIF(COUNT(*), 0), 1) AS value "
                    "FROM audit_log "
                    "WHERE event_type IN ("
                    "'writer_self_review', 'writer_self_review_pass'"
                    ") "
                    "AND timestamp > now() - interval '24 hours'"
                ),
                "refId": "A",
            }
        ],
    }


def pipeline_self_review_activity() -> dict:
    return {
        "id": RESERVED_IDS["pm_self_review_activity"],
        "title": "Self-review activity (7d, passed vs contradiction)",
        "type": "timeseries",
        "description": (
            "writer_self_review audit_log events bucketed daily. "
            "'pass' = no contradictions found; 'contradiction' = "
            "review fired and the revise loop attempted. Both are "
            "valuable signals: pass rate trending down means the writer "
            "model is getting noisier."
        ),
        "datasource": POSTGRES_DATASOURCE,
        "gridPos": {"x": 4, "y": 220, "w": 20, "h": 6},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "bars",
                    "fillOpacity": 70,
                    "lineWidth": 1,
                    "barAlignment": 0,
                    "showPoints": "never",
                    "spanNulls": False,
                    "stacking": {"mode": "normal", "group": "A"},
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "blue", "value": None}],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": ["sum"],
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [
            {
                "datasource": POSTGRES_DATASOURCE,
                "format": "time_series",
                "rawQuery": True,
                "rawSql": (
                    "SELECT date_trunc('day', timestamp) AS time, "
                    "CASE WHEN event_type = 'writer_self_review_pass' "
                    "THEN 'pass' ELSE 'contradiction_found' END AS metric, "
                    "COUNT(*) AS value "
                    "FROM audit_log "
                    "WHERE event_type IN ("
                    "'writer_self_review', 'writer_self_review_pass'"
                    ") "
                    "AND timestamp > now() - interval '7 days' "
                    "GROUP BY 1, 2 "
                    "ORDER BY 1"
                ),
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# System Health — DB pool, unapplied migrations, Ollama health
# ---------------------------------------------------------------------------


def system_health_db_pool() -> dict:
    return {
        "id": RESERVED_IDS["sh_db_pool"],
        "title": "DB pool — size / idle / max",
        "type": "timeseries",
        "description": (
            "asyncpg pool occupancy. ``idle`` should be > 0 most of the "
            "time; if it hits 0 and stays there, requests are queueing on "
            "pool exhaustion. ``size`` should track total demand; if it "
            "pegs at ``max``, raise db_pool_max_size in app_settings."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 0, "y": 250, "w": 12, "h": 6},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 5,
                    "lineWidth": 2,
                    "showPoints": "never",
                    "spanNulls": False,
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "blue", "value": None}],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": ["lastNotNull"],
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_db_pool_size",
                "legendFormat": "size",
                "refId": "A",
            },
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_db_pool_idle",
                "legendFormat": "idle",
                "refId": "B",
            },
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_db_pool_max_size",
                "legendFormat": "max",
                "refId": "C",
            },
        ],
    }


def system_health_unapplied_migrations() -> dict:
    return {
        "id": RESERVED_IDS["sh_unapplied_migrations"],
        "title": "Unapplied migrations",
        "type": "stat",
        "description": (
            "Count of services/migrations/*.py files not yet recorded in "
            "schema_migrations. Should be 0 outside a deploy window. "
            ">0 for >5 min triggers the migration-drift probe."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 12, "y": 250, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 1},
                    ],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "value",
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_unapplied_migrations_count",
                "instant": True,
                "refId": "A",
            }
        ],
    }


def system_health_ollama() -> dict:
    return {
        "id": RESERVED_IDS["sh_ollama_health"],
        "title": "Ollama health",
        "type": "stat",
        "description": (
            "Ollama backend reachability + loaded model count. The pipeline "
            "depends on Ollama for all local LLM inference; if reachable=0, "
            "every canonical_blog task will fail at the writer stage."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 16, "y": 250, "w": 8, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "orange", "value": None},
                        {"color": "blue", "value": 1},
                    ],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "horizontal",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "value_and_name",
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_ollama_reachable",
                "legendFormat": "Reachable",
                "refId": "A",
            },
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_ollama_model_count",
                "legendFormat": "Models loaded",
                "refId": "B",
            },
        ],
    }


# ---------------------------------------------------------------------------
# System Health — scheduler job freshness (all 37 jobs at once)
# ---------------------------------------------------------------------------


def system_health_scheduler_freshness_table() -> dict:
    """Full table of every scheduler job + last-run age + ok/err status.

    Powered by:
    - ``poindexter_scheduler_job_last_run_age_seconds{job_name=...}`` —
      seconds since last fire (from the job_run_state table, emitted by
      services.metrics_exporter.refresh_scheduler_job_state)
    - ``poindexter_scheduler_job_last_run_ok{job_name=...}`` —
      1=ok / 0=err on the most recent fire

    This is the "every background loop has a freshness panel" pattern
    from the 2026-05-15 heartbeat audit. 37+ jobs covered automatically
    — no per-job dashboard work needed when new jobs ship.
    """
    return {
        "id": RESERVED_IDS["sh_scheduler_freshness_table"],
        "title": "Scheduled jobs — freshness + last-run status",
        "type": "table",
        "description": (
            "Auto-populated for every PluginScheduler job. Healthy = "
            "age below the job's expected interval (every-2h jobs sit "
            "at 0-7200s, every-60s jobs at 0-90s). Orange highlight = "
            "age suggests the loop is dead. ok=0 = ran recently but "
            "failed; click the job_name to grep Loki for details."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 0, "y": 280, "w": 16, "h": 10},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "custom": {
                    "align": "left",
                    "displayMode": "auto",
                    "filterable": True,
                    "inspect": False,
                },
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "blue", "value": None}],
                },
                "unit": "short",
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Age (s)"},
                    "properties": [
                        {"id": "unit", "value": "s"},
                        {
                            "id": "custom.cellOptions",
                            "value": {
                                "type": "color-background",
                                "mode": "gradient",
                            },
                        },
                        {
                            "id": "thresholds",
                            "value": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "blue", "value": None},
                                    {"color": "orange", "value": 3600},
                                ],
                            },
                        },
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Last status"},
                    "properties": [
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "options": {
                                        "0": {"text": "ERR", "color": "orange", "index": 0},
                                        "1": {"text": "ok", "color": "blue", "index": 1},
                                    },
                                    "type": "value",
                                }
                            ],
                        },
                        {
                            "id": "custom.cellOptions",
                            "value": {
                                "type": "color-background",
                                "mode": "basic",
                            },
                        },
                    ],
                },
            ],
        },
        "options": {
            "showHeader": True,
            "footer": {
                "show": True,
                "reducer": ["count"],
                "fields": "",
            },
            "sortBy": [{"displayName": "Age (s)", "desc": True}],
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_scheduler_job_last_run_age_seconds",
                "format": "table",
                "instant": True,
                "refId": "A",
            },
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_scheduler_job_last_run_ok",
                "format": "table",
                "instant": True,
                "refId": "B",
            },
        ],
        "transformations": [
            # Drop the noisy series-internal columns Prometheus emits
            # (Time, __name__, instance, job) and keep job_name + Value.
            # Then join the two queries on job_name to get one row each.
            {
                "id": "merge",
                "options": {},
            },
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Time": True,
                        "__name__": True,
                        "instance": True,
                        "job": True,
                    },
                    "indexByName": {},
                    "renameByName": {
                        "job_name": "Job",
                        "Value #A": "Age (s)",
                        "Value #B": "Last status",
                    },
                },
            },
        ],
    }


def system_health_scheduler_topk_stale() -> dict:
    """Bar chart of the 10 stalest scheduler jobs.

    Companion to the freshness table — gives a quick visual signal of
    which jobs are climbing. A flat bar at job's expected interval =
    healthy. A bar towering over the others = that loop is dead.
    """
    return {
        "id": RESERVED_IDS["sh_scheduler_freshness_topk"],
        "title": "Stalest jobs (top 10)",
        "type": "bargauge",
        "description": (
            "Top 10 jobs by last-run age. Healthy state: all bars below "
            "their respective scheduled intervals. One bar far above "
            "the others = the loop has stopped firing. The threshold "
            "color flips to orange at the 1-hour mark — but a job that's "
            "scheduled to run every 6h naturally stays orange between "
            "fires. Cross-reference with the freshness table."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 16, "y": 280, "w": 8, "h": 10},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 3600},
                    ],
                },
                "unit": "s",
            },
            "overrides": [],
        },
        "options": {
            "displayMode": "gradient",
            "minVizHeight": 10,
            "minVizWidth": 0,
            "orientation": "horizontal",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "showUnfilled": True,
            "valueMode": "color",
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "topk(10, poindexter_scheduler_job_last_run_age_seconds)",
                "instant": True,
                "legendFormat": "{{job_name}}",
                "refId": "A",
            }
        ],
    }


def system_health_background_loops_overview() -> dict:
    """One-look summary of every non-scheduler background loop's freshness.

    Combines:
    - Worker heartbeat (every 30s)
    - Task executor poll (every 5s default)
    - Webhook delivery cycle (every POLL_INTERVAL s)

    Each loop should stay below its respective expected interval. A loop
    that climbs is the same failure mode as the 2026-05-15 heartbeat
    bug — silent stop with no log lines.
    """
    return {
        "id": RESERVED_IDS["sh_background_loops_overview"],
        "title": "Background loops — freshness overview",
        "type": "timeseries",
        "description": (
            "Wall-clock age of each named background loop's last "
            "successful iteration. Healthy: each line stays near 0 with "
            "a sawtooth at its expected interval. A flat climb = the "
            "loop has stopped firing (silent-hang). Heartbeat sits "
            "~30s; task_executor ~5s; webhook_delivery ~5s."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 0, "y": 295, "w": 16, "h": 6},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "fillOpacity": 5,
                    "lineWidth": 2,
                    "showPoints": "never",
                    "spanNulls": False,
                    "thresholdsStyle": {"mode": "line"},
                },
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 120},
                    ],
                },
                "unit": "s",
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": ["max", "lastNotNull"],
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "poindexter_worker_heartbeat_age_seconds",
                "legendFormat": "worker heartbeat",
                "refId": "A",
            },
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "time() - poindexter_background_loop_last_tick_timestamp_seconds",
                "legendFormat": "{{loop}}",
                "refId": "B",
            },
        ],
    }


def system_health_scheduler_failed_jobs() -> dict:
    """Count of jobs whose most recent fire FAILED.

    Healthy = 0. Distinct from staleness: a job can be fresh (just ran)
    but failed on its last fire. This panel surfaces that case.
    """
    return {
        "id": RESERVED_IDS["sh_scheduler_failed_jobs"],
        "title": "Jobs whose last fire failed",
        "type": "stat",
        "description": (
            "Number of scheduler jobs where the most recent fire status "
            "was 'err'. Healthy = 0. >0 = at least one job ran but "
            "raised — open the freshness table, sort by Last status, "
            "and check Loki for the underlying traceback."
        ),
        "datasource": PROMETHEUS_DATASOURCE,
        "gridPos": {"x": 0, "y": 290, "w": 4, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "blue", "value": None},
                        {"color": "orange", "value": 1},
                    ],
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "textMode": "value",
        },
        "targets": [
            {
                "datasource": PROMETHEUS_DATASOURCE,
                "expr": "count(poindexter_scheduler_job_last_run_ok == 0)",
                "instant": True,
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Observability — boot-audit Loki feed
# ---------------------------------------------------------------------------


def observability_boot_audit_logs() -> dict:
    return {
        "id": RESERVED_IDS["obs_boot_audit_logs"],
        "title": "Boot import audit + packaging regressions",
        "type": "logs",
        "description": (
            "Loki feed of [boot-audit] and PACKAGING REGRESSION log lines. "
            "Both worker (utils/import_audit.py) and brain "
            "(brain_daemon._audit_brain_module_imports) emit these on every "
            "restart. Healthy state: '[boot-audit] all N expected-present "
            "modules importable'. Unhealthy: 'PACKAGING REGRESSION' or "
            "'boot-audit] <module> missing'."
        ),
        "datasource": LOKI_DATASOURCE,
        "gridPos": {"x": 0, "y": 300, "w": 24, "h": 8},
        "options": {
            "showTime": True,
            "showLabels": False,
            "showCommonLabels": False,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "dedupStrategy": "none",
            "sortOrder": "Descending",
        },
        "targets": [
            {
                "datasource": LOKI_DATASOURCE,
                "expr": (
                    '{compose_project=~".+"} |~ '
                    '"\\\\[boot-audit\\\\]|PACKAGING REGRESSION|SHIM BROKEN|'
                    'shim wired"'
                ),
                "refId": "A",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Apply additions
# ---------------------------------------------------------------------------


def apply_to_dashboard(filename: str, panel_factories: list) -> None:
    path = DASHBOARDS_DIR / filename
    if not path.exists():
        print(f"  [SKIP] {filename} not found", file=sys.stderr)
        return
    dashboard = _load(path)
    panels = dashboard.setdefault("panels", [])
    for factory in panel_factories:
        new_panel = factory()
        action = _replace_or_append(panels, new_panel)
        print(f"  [{action}] id={new_panel['id']} '{new_panel['title']}'")
    _save(path, dashboard)
    print(f"  saved {filename} ({len(panels)} panels total)")


def main() -> int:
    print("=== mission-control.json ===")
    apply_to_dashboard(
        "mission-control.json",
        [mission_control_template_split, mission_control_heartbeat_age],
    )

    print("\n=== pipeline-merged.json ===")
    apply_to_dashboard(
        "pipeline-merged.json",
        [
            pipeline_topic_auto_resolve_stat,
            pipeline_topic_auto_resolve_timeseries,
            pipeline_throttle_state,
            pipeline_throttle_seconds_total,
            pipeline_self_review_rate,
            pipeline_self_review_activity,
        ],
    )

    print("\n=== system-health-merged.json ===")
    apply_to_dashboard(
        "system-health-merged.json",
        [
            system_health_db_pool,
            system_health_unapplied_migrations,
            system_health_ollama,
            system_health_heartbeat_age_timeseries,
            system_health_scheduler_freshness_table,
            system_health_scheduler_topk_stale,
            system_health_scheduler_failed_jobs,
            system_health_background_loops_overview,
        ],
    )

    print("\n=== observability-merged.json ===")
    apply_to_dashboard(
        "observability-merged.json",
        [observability_boot_audit_logs],
    )

    print("\nDone. Restart Grafana to load:")
    print("  docker restart poindexter-grafana")
    return 0


if __name__ == "__main__":
    sys.exit(main())
