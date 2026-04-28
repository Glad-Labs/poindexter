"""One-off helper to add a Dry-Run Mode section to the Pipeline-Merged dashboard.

Run from the host:
    python scripts/add_dryrun_panels.py
"""
from __future__ import annotations

import json
from pathlib import Path

DASHBOARD = (
    Path(__file__).resolve().parent.parent
    / "infrastructure" / "grafana" / "dashboards" / "pipeline-merged.json"
)


def main() -> None:
    with open(DASHBOARD, encoding="utf-8") as f:
        d = json.load(f)

    panels = d.get("panels") or []
    if any("Dry-Run" in (p.get("title") or "") for p in panels):
        print("[skip] dry-run section already present in dashboard")
        return

    existing_ids = [p.get("id") for p in panels if p.get("id") is not None]
    next_id = max(existing_ids, default=100) + 1
    max_y = max(
        (
            p.get("gridPos", {}).get("y", 0)
            + p.get("gridPos", {}).get("h", 0)
            for p in panels
        ),
        default=0,
    )

    pg = {"type": "postgres", "uid": "poindexter-postgres"}

    new_panels = [
        {
            "type": "row",
            "id": next_id,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": max_y},
            "title": "Dry-Run Mode (gh#215)",
            "collapsed": False,
            "panels": [],
        },
        {
            "type": "stat",
            "id": next_id + 1,
            "gridPos": {"h": 5, "w": 6, "x": 0, "y": max_y + 1},
            "title": "Dry-Run Tasks (1h)",
            "datasource": pg,
            "targets": [
                {
                    "rawSql": (
                        "SELECT COUNT(*)::int AS value FROM pipeline_tasks "
                        "WHERE status = 'dry_run' "
                        "AND updated_at > NOW() - INTERVAL '1 hour'"
                    ),
                    "format": "table",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "blue", "value": None},
                            {"color": "green", "value": 1},
                        ],
                    },
                    "unit": "short",
                },
            },
            "options": {
                "colorMode": "background",
                "graphMode": "area",
                "textMode": "auto",
                "justifyMode": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            },
        },
        {
            "type": "stat",
            "id": next_id + 2,
            "gridPos": {"h": 5, "w": 6, "x": 6, "y": max_y + 1},
            "title": "Dry-Run Total (all-time)",
            "datasource": pg,
            "targets": [
                {
                    "rawSql": (
                        "SELECT COUNT(*)::int AS value FROM pipeline_tasks "
                        "WHERE status = 'dry_run'"
                    ),
                    "format": "table",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": "purple", "value": None}],
                    },
                    "unit": "short",
                },
            },
            "options": {
                "colorMode": "value",
                "graphMode": "none",
                "textMode": "auto",
                "justifyMode": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            },
        },
        {
            "type": "stat",
            "id": next_id + 3,
            "gridPos": {"h": 5, "w": 6, "x": 12, "y": max_y + 1},
            "title": "Dry-Run Avg Quality",
            "datasource": pg,
            "targets": [
                {
                    "rawSql": (
                        "SELECT ROUND(AVG(quality_score), 1)::float AS value "
                        "FROM pipeline_tasks WHERE status = 'dry_run' "
                        "AND quality_score IS NOT NULL"
                    ),
                    "format": "table",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "red", "value": None},
                            {"color": "yellow", "value": 60},
                            {"color": "green", "value": 80},
                        ],
                    },
                    "unit": "short",
                    "max": 100,
                    "min": 0,
                },
            },
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "textMode": "auto",
                "justifyMode": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            },
        },
        {
            "type": "stat",
            "id": next_id + 4,
            "gridPos": {"h": 5, "w": 6, "x": 18, "y": max_y + 1},
            "title": "pipeline_dry_run_mode",
            "datasource": pg,
            "targets": [
                {
                    "rawSql": (
                        "SELECT CASE WHEN value IN ('true','1','yes','on') "
                        "THEN 'ON' ELSE 'off' END AS value "
                        "FROM app_settings WHERE key = 'pipeline_dry_run_mode'"
                    ),
                    "format": "table",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "mappings": [
                        {"type": "value", "options": {"ON": {"color": "purple", "index": 0}}},
                        {"type": "value", "options": {"off": {"color": "blue", "index": 1}}},
                    ],
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [{"color": "blue", "value": None}],
                    },
                },
            },
            "options": {
                "colorMode": "background",
                "graphMode": "none",
                "textMode": "auto",
                "justifyMode": "auto",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            },
        },
        {
            "type": "timeseries",
            "id": next_id + 5,
            "gridPos": {"h": 8, "w": 24, "x": 0, "y": max_y + 6},
            "title": "Dry-Run Generation Rate",
            "datasource": pg,
            "targets": [
                {
                    "rawSql": (
                        "SELECT date_trunc('minute', updated_at) AS time, "
                        "COUNT(*)::int AS dry_run "
                        "FROM pipeline_tasks "
                        "WHERE status = 'dry_run' "
                        "AND updated_at > $__timeFrom() AND updated_at < $__timeTo() "
                        "GROUP BY 1 ORDER BY 1"
                    ),
                    "format": "time_series",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "drawStyle": "bars",
                        "fillOpacity": 70,
                        "lineWidth": 1,
                    },
                    "unit": "short",
                },
            },
            "options": {
                "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"},
            },
        },
    ]

    d["panels"] = panels + new_panels

    with open(DASHBOARD, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    print(f"[add ] 6 dry-run panels (1 row + 4 stat + 1 timeseries) at y={max_y}")


if __name__ == "__main__":
    main()
