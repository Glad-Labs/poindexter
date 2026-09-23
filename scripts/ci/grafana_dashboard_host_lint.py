#!/usr/bin/env python3
"""Fail when a Grafana dashboard hardcodes a loopback host in a link.

A dashboard link to a SIBLING service (Prefect :4200, Langfuse :3010,
GlitchTip :8080, pgAdmin :18443, Prometheus :9091, worker API :8002, ...)
has two wrong answers and one right one:

``http://localhost:<port>``
    Resolves only for a browser running ON the Docker host. Every operator who
    reaches Grafana from anywhere else -- a phone over the tailnet is the
    normal case here -- gets a dead link. This is what shipped for months.

``http://<operator-host>:<port>``
    Works for one operator and puts a private hostname in a publicly-mirrored
    repo. Not this lint's job: ``check_public_mirror_safety.py`` already owns
    the real leak patterns and is the single source of truth for them. Two
    gates, one job each.

``http://__POINDEXTER_SERVICE_HOST__:<port>``
    The placeholder. The Grafana container entrypoint substitutes it at start
    (and re-syncs every 30s) from ``app_settings.operator_service_host``,
    defaulting to ``localhost``. Ships clean, works everywhere, configurable.

Grafana's OWN origin is a separate case and must stay RELATIVE (``/d/<uid>``,
``/explore?...``): a relative URL inherits whatever origin the reader already
used to reach Grafana, so it needs no host and no render at all.

Deliberately narrow -- it flags loopback only. It does NOT flag arbitrary
external hosts: github.com, grafana.com and vercel.com are all legitimate
links in these boards, and a lint that reddens on them is the
bandit-91-false-positives mistake (a gate with bad signal-to-noise gets
ignored, then disabled).

Why a ratchet rather than an issue: this defect has been fixed twice and
regressed once. PR #3114 repointed Mission Control's links after the tailnet
re-addressed and missed the same four links on Pipeline and QA Rails, which
stayed broken until 2026-09-23. A partial fix reads exactly like a complete
one -- but does not grep like one.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() or (p / ".git").exists()
)
DASHBOARD_ROOT = _REPO_ROOT / "infrastructure" / "grafana" / "dashboards"

PLACEHOLDER = "__POINDEXTER_SERVICE_HOST__"

_LOOPBACK_URL = re.compile(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(?::(\d+))?")

# Port 3000 is Grafana itself; that case wants a RELATIVE url, not the
# placeholder, so it gets its own remedy line.
_GRAFANA_PORT = "3000"


def _iter_strings(node, path=""):
    """Yield every string in the dashboard, with a readable JSON path."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def main() -> int:
    require_dir(DASHBOARD_ROOT, lint="grafana_dashboard_host_lint")

    failures: list[str] = []
    scanned = 0

    for f in sorted(DASHBOARD_ROOT.glob("*.json")):
        scanned += 1
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{f.name}: invalid JSON -- {exc}")
            continue

        for jpath, text in _iter_strings(doc):
            for host, port in _LOOPBACK_URL.findall(text):
                if port == _GRAFANA_PORT:
                    remedy = (
                        "fix: this is Grafana's OWN origin -- use a relative URL "
                        "(/d/<uid>, /explore?...), which inherits whatever origin "
                        "the reader reached Grafana on and needs no host at all"
                    )
                else:
                    remedy = (
                        f"fix: this is a SIBLING service -- use "
                        f"http://{PLACEHOLDER}:{port or '<port>'}, rendered by the "
                        f"Grafana entrypoint from app_settings.operator_service_host"
                    )
                failures.append(
                    f"{f.name}{jpath}: hardcoded {host!r}\n"
                    f"      {text.strip()[:110]}\n"
                    f"      {remedy}"
                )

    require_scanned(
        scanned,
        lint="grafana_dashboard_host_lint",
        what="dashboard JSON files",
        roots=(DASHBOARD_ROOT,),
    )

    if failures:
        print("[grafana-dashboard-host] hardcoded loopback link host(s):\n")
        for msg in failures:
            print(f"  {msg}\n")
        print(f"{len(failures)} finding(s) across {scanned} dashboard(s).")
        return 1

    print(
        f"[grafana-dashboard-host] OK -- {scanned} dashboard(s), "
        "no hardcoded loopback link hosts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
