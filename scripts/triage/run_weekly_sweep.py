"""Weekly issue-triage sweep I/O wrapper.

For each target repo: list open issues, apply the content-derived `type`
label where the title justifies it (and the issue's type axis is bare),
record each applied change to audit_log, and print a JSON report of the
remaining priority/milestone/area gaps + that repo's live milestone list for
the reasoning agent to propose over. NEVER applies priority or milestone.

Usage:
  python scripts/triage/run_weekly_sweep.py --dry-run
  python scripts/triage/run_weekly_sweep.py            # applies derivable type
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

# Make `services.*` (and brain.bootstrap for the audit_log write) importable
# when run as a bare script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src" / "cofounder_agent"))
sys.path.insert(0, str(_REPO_ROOT))

from services.triage.sweep import find_gaps  # noqa: E402

# Issues are content-routed to BOTH repos (OSS -> poindexter, business/internal
# -> glad-labs-stack), and a label is an issue-write, so the sweep applies the
# derivable type label in either repo. (CODE/PRs still go to glad-labs-stack
# only, but the sweep never touches code.)
REPOS = ["Glad-Labs/poindexter", "Glad-Labs/glad-labs-stack"]


def _gh_json(repo: str, *args: str) -> list[dict]:
    # Force UTF-8: gh emits UTF-8 (issue bodies have em-dashes/emoji), but
    # text=True would decode with the platform default (cp1252 on Windows) and
    # crash the reader threads. errors='replace' keeps one odd byte from
    # aborting the whole sweep.
    out = subprocess.run(
        ["gh", *args, "--repo", repo],
        capture_output=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    return json.loads(out or "[]")


def _milestones(repo: str) -> list[str]:
    data = subprocess.run(
        ["gh", "api", f"repos/{repo}/milestones", "--jq", "[.[].title]"],
        capture_output=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    return json.loads(data or "[]")


async def _record(applied: list[dict]) -> None:
    """Best-effort audit_log row per applied label (event_type='issue_triaged')."""
    if not applied:
        return
    try:
        import asyncpg  # local import: only needed when actually writing
        from brain.bootstrap import resolve_database_url
    except Exception as exc:  # pragma: no cover
        print(f"[sweep] audit_log skipped (deps unavailable): {exc}", file=sys.stderr)
        return
    dsn = resolve_database_url()
    conn = await asyncpg.connect(dsn)
    try:
        for a in applied:
            await conn.execute(
                """INSERT INTO audit_log (event_type, source, severity, details)
                   VALUES ('issue_triaged', 'weekly_sweep', 'info', $1::jsonb)""",
                json.dumps(a),
            )
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report: dict[str, dict] = {}
    applied: list[dict] = []

    for repo in REPOS:
        issues = _gh_json(repo, "issue", "list", "--state", "open", "--limit", "300",
                          "--json", "number,title,labels,milestone,body")
        gaps = find_gaps(issues)
        for g in gaps:
            if g["missing_type"] and g["derived_type"]:
                reason = f"type from title prefix -> {g['derived_type']}"
                if args.dry_run:
                    print(f"[dry-run] {repo}#{g['number']}: +{g['derived_type']} ({reason})")
                else:
                    subprocess.run(
                        ["gh", "issue", "edit", str(g["number"]), "--repo", repo,
                         "--add-label", g["derived_type"]], check=False,
                    )
                applied.append({"repo": repo, "number": g["number"],
                                "label": g["derived_type"], "axis": "type",
                                "reason": reason})
        report[repo] = {"milestones": _milestones(repo), "gaps": gaps}

    if not args.dry_run:
        asyncio.run(_record(applied))

    # The reasoning agent consumes this JSON to propose area/priority/milestone.
    print(json.dumps({"applied": applied, "report": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
