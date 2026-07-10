"""Auto-merge green patch-bump dependabot PRs. Deterministic, no model."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys

import _common as c

REPO = "Glad-Labs/poindexter"
_VER = re.compile(r"from\s+v?(\d+)\.(\d+)\.(\d+)\S*\s+to\s+v?(\d+)\.(\d+)\.(\d+)\S*", re.I)


def is_patch_bump(title: str) -> bool:
    m = _VER.search(title)
    if not m:
        return False
    f_maj, f_min, f_pat, t_maj, t_min, t_pat = (int(x) for x in m.groups())
    return f_maj == t_maj and f_min == t_min and t_pat != f_pat


def all_checks_green(rollup: list[dict]) -> bool:
    if not rollup:
        return False
    for ctx in rollup:
        outcome = ctx.get("conclusion") or ctx.get("state") or ""
        if outcome.upper() not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            return False
    return True


def older_than_hours(created_at_iso: str, hours: int, *, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(dt.UTC)
    created = dt.datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    return (now - created) >= dt.timedelta(hours=hours)


def main() -> int:
    log = c.get_logger("dependency-review")
    proc = c.gh(
        "pr", "list", "--repo", REPO,
        "--search", "is:pr is:open author:app/dependabot",
        "--json", "number,title,createdAt,statusCheckRollup", "--limit", "30",
    )
    if proc.returncode != 0:
        c.notify_fail("dependency-review failed", proc.stderr[:500], "dependency_review")
        return 1
    prs = json.loads(proc.stdout or "[]")
    merged, skipped = [], []
    for pr in prs:
        num = pr["number"]
        if not (is_patch_bump(pr["title"]) and all_checks_green(pr.get("statusCheckRollup", []))
                and older_than_hours(pr["createdAt"], 6)):
            skipped.append(num)
            continue
        c.gh("pr", "review", "--repo", REPO, str(num), "--approve")
        c.gh("pr", "merge", "--repo", REPO, str(num), "--squash", "--delete-branch", "--auto")
        merged.append(num)
    log.info("merged=%s skipped=%s", merged, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
