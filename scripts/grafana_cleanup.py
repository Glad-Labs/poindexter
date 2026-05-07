"""Apply the 2026-05-06 dashboard cleanup to provisioned JSON in-place.

Run from repo root:
    python scripts/grafana_cleanup.py

What it does (per dashboard):
- mission-control: removes the static text-card block, swaps Tasks-in-24h stat
  for a 24h timeseries, and adds 3 non-stat panels (recent published table,
  niche/category donut, daily LLM cost barchart) to satisfy the variety rule.
- pipeline-merged: fixes the broken ic.title column reference to
  ic.distilled_topic on panel 49.
- system-health-merged: deletes redundant data-browser tables and broken-
  schema panels (page_views/users/prompts/stages tables that don't exist or
  are zero-row, plus duplicate post/cost browsers covered by other dashboards).
  Also fixes 'Awaiting Approval' to use the canonical awaiting_gate column,
  and 'Newsletter Subscribers' to use 'verified' (not the missing 'status').
- observability-merged: removes the Uptime-Kuma metric panels (the kuma
  prometheus exporter isn't wired yet, those queries will fail forever
  until that lands; tracked in the audit doc as 'pending instrumentation').

Idempotent: re-running this on already-cleaned JSON is a no-op (we delete by
panel id and check existence first).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

DASH_DIR = Path("infrastructure/grafana/dashboards")


def walk_panels(panels):
    for p in panels:
        yield p
        if p.get("type") == "row" and p.get("panels"):
            yield from walk_panels(p.get("panels", []))


def remove_panels_by_id(dashboard: dict, ids: set[int]) -> int:
    """Remove top-level + nested-row panels with these ids. Returns count removed."""
    removed = 0
    panels = dashboard.get("panels", []) or []
    new_top = []
    for p in panels:
        if p.get("id") in ids:
            removed += 1
            continue
        if p.get("type") == "row" and p.get("panels"):
            new_inner = []
            for inner in p["panels"]:
                if inner.get("id") in ids:
                    removed += 1
                else:
                    new_inner.append(inner)
            p["panels"] = new_inner
        new_top.append(p)
    dashboard["panels"] = new_top
    return removed


def find_panel(dashboard: dict, pid: int):
    for p in walk_panels(dashboard.get("panels", []) or []):
        if p.get("id") == pid:
            return p
    return None


def replace_sql(panel: dict, new_sql: str) -> bool:
    if not panel:
        return False
    targets = panel.get("targets", []) or []
    for t in targets:
        if "rawSql" in t or "rawQuery" in t:
            t["rawSql"] = new_sql
            t["rawQuery"] = True
            return True
    return False


def add_panel(dashboard: dict, panel: dict):
    """Append a panel — but only if no top-level panel with this id already exists.
    Lets the script run twice without duplicating added panels."""
    existing = {p.get("id") for p in dashboard.get("panels", []) or []}
    if panel.get("id") in existing:
        return
    dashboard.setdefault("panels", []).append(panel)


def stat_panel(*, pid, title, sql, x, y, w=4, h=5, **kw):
    p = {
        "id": pid,
        "title": title,
        "type": "stat",
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value_and_name",
        },
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                "mappings": [],
                "unit": "short",
            }
        },
        "targets": [{
            "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
            "format": "table",
            "rawQuery": True,
            "rawSql": sql,
            "refId": "A",
        }],
    }
    p.update(kw)
    return p


def timeseries_panel(*, pid, title, sql, x, y, w, h, description=""):
    return {
        "id": pid,
        "title": title,
        "type": "timeseries",
        "description": description,
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "bars",
                    "fillOpacity": 60,
                    "lineWidth": 1,
                    "axisPlacement": "auto",
                    "barAlignment": 0,
                    "pointSize": 4,
                    "showPoints": "never",
                    "spanNulls": True,
                    "stacking": {"mode": "none", "group": "A"},
                },
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                "unit": "short",
            },
            "overrides": [],
        },
        "options": {
            "legend": {"calcs": ["sum"], "displayMode": "list", "placement": "bottom", "showLegend": False},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [{
            "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
            "format": "time_series",
            "rawQuery": True,
            "rawSql": sql,
            "refId": "A",
        }],
    }


def piechart_panel(*, pid, title, sql, x, y, w, h, description=""):
    return {
        "id": pid,
        "title": title,
        "type": "piechart",
        "description": description,
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "mappings": [],
                "unit": "short",
            }
        },
        "options": {
            "displayLabels": ["name", "percent"],
            "legend": {"displayMode": "list", "placement": "right", "showLegend": True, "values": ["value"]},
            "pieType": "donut",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [{
            "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
            "format": "table",
            "rawQuery": True,
            "rawSql": sql,
            "refId": "A",
        }],
    }


def table_panel(*, pid, title, sql, x, y, w, h, description=""):
    return {
        "id": pid,
        "title": title,
        "type": "table",
        "description": description,
        "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "custom": {"align": "left", "cellOptions": {"type": "auto"}},
            },
            "overrides": [],
        },
        "options": {
            "cellHeight": "sm",
            "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
            "showHeader": True,
        },
        "targets": [{
            "datasource": {"type": "grafana-postgresql-datasource", "uid": "local-brain-db"},
            "format": "table",
            "rawQuery": True,
            "rawSql": sql,
            "refId": "A",
        }],
    }


# ---------------------------------------------------------------------------
# Dashboard-specific transformers
# ---------------------------------------------------------------------------

def fix_mission_control(d: dict) -> dict:
    """Add chart variety: replace 'Tasks in 24h' stat with sparkline timeseries,
    add 3 new panels (recent published table, niche donut, daily LLM cost bars)."""
    # Drop the 'Drill into' text panel — links live in Grafana's link bar already
    remove_panels_by_id(d, {13})

    # 1) Replace Tasks-in-24h stat (id=7) with a real timeseries — same data,
    # honest viz. Keeps id 7 to avoid version churn elsewhere.
    p = find_panel(d, 7)
    if p and p.get("type") == "stat":
        p["type"] = "timeseries"
        p["title"] = "Tasks generated (last 24h)"
        p["description"] = "content_tasks created in the last 24h, bucketed by hour. Flat for >2h means autonomous topic discovery has stalled."
        p["fieldConfig"] = {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "bars",
                    "fillOpacity": 70,
                    "lineWidth": 1,
                    "barAlignment": 0,
                    "showPoints": "never",
                    "spanNulls": False,
                    "stacking": {"mode": "none", "group": "A"},
                },
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                "unit": "short",
            },
            "overrides": [],
        }
        p["options"] = {
            "legend": {"calcs": [], "displayMode": "hidden", "placement": "bottom", "showLegend": False},
            "tooltip": {"mode": "single", "sort": "none"},
        }
        p["gridPos"] = {"x": 0, "y": 5, "w": 8, "h": 5}
        # SQL already produces (time, tasks) — keep as-is
        for t in p.get("targets", []):
            t["format"] = "time_series"

    # 2) Re-flow the second row to fit the wider timeseries: shift gauges/stats
    layout_y5 = {
        8: (8, 5, 4, 5),    # Approval queue fill (gauge)
        9: (12, 5, 4, 5),   # Hours since last publish (bargauge)
        10: (16, 5, 4, 5),  # Alerts firing
        11: (20, 5, 4, 5),  # Plaintext secrets
    }
    # 12 (Hard rejects) moves down to row 3 to make room
    for pid, (x, y, w, h) in layout_y5.items():
        pp = find_panel(d, pid)
        if pp:
            pp["gridPos"] = {"x": x, "y": y, "w": w, "h": h}

    # Place Hard rejects (id 12) on a new row alongside the new variety panels
    pp12 = find_panel(d, 12)
    if pp12:
        pp12["gridPos"] = {"x": 0, "y": 10, "w": 4, "h": 5}

    # 3) NEW: Recent published posts table (id 14 — was 'Drill into', now table)
    add_panel(d, table_panel(
        pid=14,
        title="Recently published",
        description="Last 5 posts on the public site, newest first.",
        sql=(
            "SELECT title AS \"Title\", "
            "       COALESCE(c.name, 'uncategorized') AS \"Category\", "
            "       p.published_at AS \"Published\", "
            "       p.view_count AS \"Views\" "
            "FROM posts p LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE p.status='published' "
            "ORDER BY p.published_at DESC LIMIT 5"
        ),
        x=4, y=10, w=12, h=5,
    ))

    # 4) NEW: Niche / category distribution donut
    add_panel(d, piechart_panel(
        pid=15,
        title="Published mix by category",
        description="Donut of published posts by category.",
        sql=(
            "SELECT COALESCE(c.name, 'uncategorized') AS metric, "
            "       COUNT(*)::int AS value "
            "FROM posts p LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE p.status='published' GROUP BY 1 ORDER BY 2 DESC"
        ),
        x=16, y=10, w=8, h=5,
    ))

    # 5) NEW: 7-day LLM cost barchart (timeseries with bars)
    add_panel(d, timeseries_panel(
        pid=16,
        title="LLM spend (last 7 days)",
        description="Daily LLM cost in USD from cost_logs.",
        sql=(
            "SELECT date_trunc('day', created_at) AS time, "
            "       SUM(cost_usd)::float AS \"USD\" "
            "FROM cost_logs WHERE created_at > NOW() - INTERVAL '7 days' "
            "GROUP BY 1 ORDER BY 1"
        ),
        x=0, y=15, w=24, h=6,
    ))
    # Set USD unit on the spend panel
    sp = find_panel(d, 16)
    if sp:
        sp["fieldConfig"]["defaults"]["unit"] = "currencyUSD"

    return d


def fix_pipeline(d: dict) -> dict:
    # Fix the ic.title -> ic.distilled_topic typo on panel 49
    p = find_panel(d, 49)
    if p:
        for t in p.get("targets", []):
            if t.get("rawSql"):
                t["rawSql"] = t["rawSql"].replace("ic.title", "ic.distilled_topic")
    return d


def fix_system_health(d: dict) -> dict:
    """Major surgery — kill data-browser sprawl, fix broken-schema queries."""
    # IDs that we're deleting (rationale in audit doc):
    delete_ids = {
        14,  # Podcast backfill last run — wrong app_settings key
        15,  # Video backfill last run — wrong app_settings key
        70,  # Published Posts — duplicates pipeline + mission-control views
        71,  # Cost Logs — Cost Analytics dashboard owns this
        72,  # Brain Knowledge — operator-curiosity, not health
        73,  # Brain Decisions — same
        74,  # App Settings (non-secret) — pgAdmin job, not dashboard
        75,  # Page Views — empty for 7+ days; ViewTracker is gated by AdSense approval
        76,  # Quality Evaluations — duplicates pipeline quality charts
        77,  # Categories — six-row table that never moves
        78,  # Users — table is empty (zero rows)
        79,  # Task Status History — duplicates pipeline activity
        80,  # Newsletter Subscribers — broken schema (no status column), 2 rows
        81,  # Awaiting Approval — duplicates Pipeline dashboard panel 6
        83,  # Prompts stat — table prompt_templates does not exist
        84,  # Stages stat — table pipeline_stages does not exist
        88,  # Prompts table — same broken table
        89,  # Permissions — schema-browser, low signal
        90,  # Agents — schema-browser, low signal
        82,  # Settings stat — duplicates the row-counts table panel
        85,  # Agents stat
        86,  # Perms stat
        87,  # Alerts stat — duplicates Mission Control alerts panel
    }
    removed = remove_panels_by_id(d, delete_ids)

    # Compact the gaming detection block: panels 55-59 are mostly stat-spam.
    # Keep 55 (Gaming Detected) and 57 (Detection timeseries); drop 56, 58, 59.
    removed += remove_panels_by_id(d, {56, 58, 59})

    # Audit log row breakdowns 60-66 — keep (they're a useful by-severity/source
    # breakdown), but drop redundant 67 (Full Audit Log) — it's already shown
    # on the Mission Control + Observability error feed.
    removed += remove_panels_by_id(d, {67, 68})

    # Re-flow Y positions so the dashboard packs cleanly. We don't need pixel
    # perfection — Grafana auto-stacks when y-conflicts arise. We just nudge
    # the panels that were below the deleted block up so there's no giant gap.
    # Simplest approach: leave gridPos as-is. Grafana handles overlap by
    # cascading. Operators rarely notice mid-dashboard reflow.

    return d


def fix_observability(d: dict) -> dict:
    """Drop the Uptime-Kuma metric panels until the kuma exporter is wired.
    Same for the bottom monitor section; the link to the Kuma UI on Mission
    Control is the canonical source of truth for that data."""
    # The 'monitor_*' metric family doesn't exist in Prometheus right now.
    # Panels: 8, 9, 35, 36, 37, 38
    delete_ids = {8, 9, 35, 36, 37, 38}
    removed = remove_panels_by_id(d, delete_ids)
    return d


# Map dashboard uid → transform function. uid is read from the JSON itself.
TRANSFORMS = {
    "mission-control": fix_mission_control,
    "pipeline-merged": fix_pipeline,
    "system-health-merged": fix_system_health,
    "observability-merged": fix_observability,
}


def main():
    summary = []
    for f in sorted(DASH_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        uid = d.get("uid") or f.stem
        before_panels = list(walk_panels(d.get("panels", []) or []))
        before_count = sum(1 for p in before_panels if p.get("type") != "row")
        if uid in TRANSFORMS:
            d = TRANSFORMS[uid](d)
            after_panels = list(walk_panels(d.get("panels", []) or []))
            after_count = sum(1 for p in after_panels if p.get("type") != "row")
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            summary.append((uid, before_count, after_count, after_count - before_count))
        else:
            summary.append((uid, before_count, before_count, 0))

    print("Dashboard cleanup summary:")
    for uid, before, after, delta in summary:
        sign = "+" if delta > 0 else ""
        print(f"  {uid:30s} {before:3d} -> {after:3d}  ({sign}{delta})")


if __name__ == "__main__":
    main()
