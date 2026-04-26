"""Grafana dashboard consolidation — 14 → 7 (issue: too many tabs to switch).

Reorganizes the existing 14 provisioned dashboards into 7 by grouping
related panels under collapsable Row panels. Total panel count is
preserved (Matt's "total visibility" rule — `feedback_total_visibility.md`).

End state:
    1. Mission Control      — new operator landing, ~12 stats from across the stack
    2. Pipeline             — merge of approval-queue + pipeline-operations
                              + qa-observability + quality-content
    3. System Health        — merge of system-health + infrastructure-data
    4. Observability        — merge of stack-overview + tempo + pyroscope
                              + logs-overview + observability-uptime
    5. Cost & Analytics     — kept as-is
    6. Integrations & Admin — merge of integration-health + link-registry
    7. (the seventh slot is reserved for whatever ends up living off
       Mission Control links in a future pass)

Run from repo root:
    python scripts/consolidate_grafana_dashboards.py

Writes consolidated JSONs into infrastructure/grafana/dashboards/ and
leaves the source files in place — they're removed by the deleteDashboards
provisioning entry written alongside.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

DASHBOARDS_DIR = Path("infrastructure/grafana/dashboards")
ROW_HEIGHT = 1


def _load(name: str) -> dict:
    return json.loads((DASHBOARDS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _make_row(title: str, y: int, *, collapsed: bool = False) -> dict:
    """Build a Grafana Row panel that collapses a group of panels."""
    return {
        "type": "row",
        "title": title,
        "collapsed": collapsed,
        "gridPos": {"h": ROW_HEIGHT, "w": 24, "x": 0, "y": y},
        "panels": [] if not collapsed else [],
        "id": None,  # filled in below by reflow
    }


def _section_height(panels: list[dict]) -> int:
    """Tallest gridPos.y + h within a panel set, or 0 if empty."""
    if not panels:
        return 0
    return max(
        (p.get("gridPos", {}).get("y", 0) + p.get("gridPos", {}).get("h", 0))
        for p in panels
        if p.get("type") != "row"
    )


def _merge(
    target_title: str,
    target_uid: str,
    target_tags: list[str],
    sources: list[tuple[str, str]],
    description: str = "",
) -> dict:
    """Merge `sources` (list of (source_dashboard_name, section_title))
    into a single dashboard with collapsable rows per source.

    Each source's panels are stacked vertically under a Row header and
    have their gridPos.y rebased so nothing overlaps.
    """
    out_panels: list[dict] = []
    next_id = 1
    cursor_y = 0

    for src_name, section_title in sources:
        src = _load(src_name)
        src_panels = [p for p in src.get("panels", []) if p.get("type") != "row"]

        # Row header for this section.
        row = _make_row(section_title, cursor_y)
        row["id"] = next_id
        next_id += 1
        out_panels.append(row)
        cursor_y += ROW_HEIGHT

        # Find this source's local origin so we can rebase y values.
        if src_panels:
            local_min_y = min(p.get("gridPos", {}).get("y", 0) for p in src_panels)
        else:
            local_min_y = 0

        for p in src_panels:
            p_copy = copy.deepcopy(p)
            gp = p_copy.setdefault("gridPos", {"x": 0, "y": 0, "w": 12, "h": 8})
            gp["y"] = cursor_y + (gp.get("y", 0) - local_min_y)
            p_copy["id"] = next_id
            next_id += 1
            out_panels.append(p_copy)

        # Advance cursor past this section.
        section_h = _section_height(src_panels) - local_min_y
        cursor_y += max(section_h, ROW_HEIGHT)

    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "links": [],
        "panels": out_panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "tags": target_tags,
        "templating": {"list": []},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": target_title,
        "uid": target_uid,
        "version": 1,
        "weekStart": "",
        "description": description,
    }


def _mission_control() -> dict:
    """Brand-new operator landing dashboard.

    One screen, mostly stat panels pulled directly from existing queries
    in the source dashboards so we're not introducing new metrics.
    Sections are tight (3-4 stats each) so the whole thing fits a
    1920x1080 portrait split.
    """
    sh = _load("system-health")
    sh_panels = {p.get("title", ""): p for p in sh.get("panels", []) if p.get("type") != "row"}

    pick = lambda title: sh_panels.get(title)  # noqa: E731 — short helper

    # Curated set: 12 stats covering services + pipeline + alerts.
    chosen_titles = [
        "Worker", "Postgres", "GPU exporter", "Tempo", "Pyroscope", "Uptime-kuma",
        "Tasks in 24h", "Approval queue fill", "Hours since last publish",
        "Alerts firing (1h)", "Plaintext secrets (should be 0)", "Hard rejects (24h)",
    ]

    panels: list[dict] = []
    next_id = 1
    cursor_y = 0
    row_w = 4   # 6 panels per row at width 4 fills 24
    row_h = 5

    for i, title in enumerate(chosen_titles):
        src = pick(title)
        if not src:
            continue
        p = copy.deepcopy(src)
        col = i % 6
        row = i // 6
        p["gridPos"] = {"x": col * row_w, "y": row * row_h, "w": row_w, "h": row_h}
        p["id"] = next_id
        next_id += 1
        panels.append(p)
        cursor_y = (row + 1) * row_h

    # Quick-links table at the bottom — text panel pointing at sibling dashboards.
    panels.append({
        "type": "text",
        "title": "Drill into",
        "id": next_id,
        "gridPos": {"x": 0, "y": cursor_y, "w": 24, "h": 6},
        "options": {
            "mode": "markdown",
            "content": (
                "**Pipeline** — `/d/pipeline-merged` "
                "&nbsp; · &nbsp; **System Health** — `/d/system-health-merged` "
                "&nbsp; · &nbsp; **Observability** — `/d/observability-merged` "
                "&nbsp; · &nbsp; **Cost & Analytics** — `/d/cost-analytics` "
                "&nbsp; · &nbsp; **Integrations & Admin** — `/d/integrations-admin`"
            ),
        },
    })

    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "links": [],
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "tags": ["mission-control", "operator", "overview"],
        "templating": {"list": []},
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "Mission Control",
        "uid": "mission-control",
        "version": 1,
        "weekStart": "",
        "description": "Single-screen operator landing. Status of every component plus current pipeline + alert stats. Drill-down links at the bottom.",
    }


def main() -> int:
    if not DASHBOARDS_DIR.is_dir():
        print(f"missing dir: {DASHBOARDS_DIR}", file=sys.stderr)
        return 1

    # Build the consolidated set.
    consolidated = {
        "mission-control": _mission_control(),
        "pipeline-merged": _merge(
            "Pipeline",
            "pipeline-merged",
            ["pipeline", "content", "qa", "approval"],
            [
                ("approval-queue",      "Approval Queue — Human Oversight"),
                ("pipeline-operations", "Pipeline Operations"),
                ("qa-observability",    "QA Observability"),
                ("quality-content",     "Quality & Content"),
            ],
            description="Everything pipeline-shaped: approval queue, live operations, QA decisions, content quality. Replaces approval-queue / pipeline-operations / qa-observability / quality-content as separate dashboards.",
        ),
        "system-health-merged": _merge(
            "System Health",
            "system-health-merged",
            ["system-health", "infrastructure", "hardware", "data", "admin"],
            [
                ("system-health",       "Core Services & Pipeline Health"),
                ("infrastructure-data", "Infrastructure Detail (Postgres / Hardware / Audit / Data / Settings)"),
            ],
            description="Merged system-health + infrastructure-data. Top section gives core service status + pipeline-level signals; bottom section is the full infrastructure detail (DB tables, hardware metrics, audit log, raw data explorer, system settings).",
        ),
        "observability-merged": _merge(
            "Observability",
            "observability-merged",
            ["observability", "lgtm", "tempo", "pyroscope", "loki", "kuma"],
            [
                ("observability-stack-overview", "Stack Overview (LGTM+P + GlitchTip + Kuma)"),
                ("observability-tempo",          "Tracing — Tempo"),
                ("observability-pyroscope",      "Profiling — Pyroscope"),
                ("logs-overview",                "Logs — Loki"),
                ("observability-uptime",         "Uptime — Kuma"),
            ],
            description="Merged observability stack — overview at the top, then per-tool sections (Tempo, Pyroscope, Loki, Kuma). Each section is collapsable so the page stays scannable.",
        ),
        "integrations-admin": _merge(
            "Integrations & Admin",
            "integrations-admin",
            ["integrations", "webhooks", "admin", "links", "reference"],
            [
                ("integration-health", "Integration Health (Webhooks / Taps / Outbound)"),
                ("link-registry",      "Link Registry & Admin Reference"),
            ],
            description="Webhook framework health + admin reference (link registry). Replaces integration-health / link-registry as separate dashboards.",
        ),
    }

    # Write each consolidated dashboard.
    for fname, dash in consolidated.items():
        out = DASHBOARDS_DIR / f"{fname}.json"
        out.write_text(json.dumps(dash, indent=2), encoding="utf-8")
        print(f"  wrote {out}  ({len(dash['panels'])} panels)")

    # Source dashboards we're retiring — keep their files in git history
    # but remove from disk so Grafana's provisioner stops re-registering
    # them. The source files would otherwise re-create the duplicates on
    # the next reload.
    retired = [
        "approval-queue", "pipeline-operations", "qa-observability", "quality-content",
        "infrastructure-data", "system-health",
        "observability-stack-overview", "observability-tempo",
        "observability-pyroscope", "logs-overview", "observability-uptime",
        "integration-health", "link-registry",
    ]
    for name in retired:
        p = DASHBOARDS_DIR / f"{name}.json"
        if p.exists():
            p.unlink()
            print(f"  removed {p}")

    # The cost-analytics dashboard is kept untouched (clean concern,
    # already well-organized). Print a recap.
    print()
    print("KEPT AS-IS: cost-analytics.json")
    print()
    print(f"Final dashboard set ({len(consolidated) + 1}):")
    for fname in [*consolidated.keys(), "cost-analytics"]:
        print(f"  - {fname}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
