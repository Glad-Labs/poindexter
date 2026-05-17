"""Fix pie charts that show only 1 slice in Grafana 13.0.1.

Grafana 13 changed how `reduceOptions` interprets tabular SQL data.
With `values: false` (the old default), the whole table is treated as
one series and reduced to a single value — showing 1 slice. For
tabular data with `metric` + `value` columns (the Poindexter
convention), the right config is:

    "reduceOptions": {
        "calcs": ["lastNotNull"],
        "fields": "/^value$/",   # explicitly select the value field
        "values": true            # one slice per row
    }

This script walks every dashboard JSON in the provisioning dir, finds
every `piechart` panel, and updates its `reduceOptions` in place.
Idempotent — re-running is a no-op once panels are fixed.

Run from host:
    python scripts/_dashboard_fix_piecharts.py
Then:
    docker restart poindexter-grafana
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DASHBOARDS_DIR = Path(
    r"C:\Users\mattm\glad-labs-website\infrastructure\grafana\dashboards"
)

# What every pie chart panel's reduceOptions should look like.
# - calcs: lastNotNull is the standard for the value-per-series
# - fields: regex selects the 'value' column from the SQL output
# - values: true means "render every row as its own series" — without
#   this Grafana 13 collapses the table to one row.
DESIRED_REDUCE_OPTIONS = {
    "calcs": ["lastNotNull"],
    "fields": "/^value$/",
    "values": True,
}


def fix_panel(panel: dict) -> bool:
    """Update one panel's reduceOptions. Returns True if changed."""
    if panel.get("type") != "piechart":
        return False
    options = panel.setdefault("options", {})
    current = options.get("reduceOptions") or {}
    if (
        current.get("calcs") == DESIRED_REDUCE_OPTIONS["calcs"]
        and current.get("fields") == DESIRED_REDUCE_OPTIONS["fields"]
        and current.get("values") == DESIRED_REDUCE_OPTIONS["values"]
    ):
        return False  # already correct
    options["reduceOptions"] = dict(DESIRED_REDUCE_OPTIONS)
    return True


def main() -> int:
    changed_count = 0
    inspected_count = 0
    for path in sorted(DASHBOARDS_DIR.glob("*.json")):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        panels = dashboard.get("panels", [])
        changed_in_this_dashboard = []
        for p in panels:
            inspected_count += 1
            if fix_panel(p):
                changed_in_this_dashboard.append(
                    (p.get("id", "?"), p.get("title", "<no title>"))
                )
        if changed_in_this_dashboard:
            path.write_text(
                json.dumps(dashboard, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            changed_count += len(changed_in_this_dashboard)
            print(f"{path.name}: fixed {len(changed_in_this_dashboard)} pie chart(s)")
            for pid, title in changed_in_this_dashboard:
                print(f"  [{pid}] {title}")
        else:
            piecharts = sum(1 for p in panels if p.get("type") == "piechart")
            print(f"{path.name}: no fix needed ({piecharts} pie chart(s) already correct)")
    print(f"\nTotal: inspected {inspected_count} panels, fixed {changed_count} pie chart(s)")
    if changed_count:
        print("\nRestart Grafana to load:")
        print("  docker restart poindexter-grafana")
    return 0


if __name__ == "__main__":
    sys.exit(main())
