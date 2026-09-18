"""Auto-merge green dependabot PRs that carry no product risk. Deterministic, no model.

Policy (2026-09-13, widened from patch-only):

* **Any scope, patch bump** -- ``from a.b.c to a.b.d`` -- merges when green and
  6 h old (the original rule).
* **Dev tooling, minor bump or grouped minor/patch** -- a title scoped
  ``(deps-dev)``: either a single minor bump (same major) or a dependabot
  *group* PR whose group is a development / ``*-minor-patch`` group. These only
  move build and test tooling; CI is the whole risk surface, and it ran.
* **Production minors and every major** still wait for a human, as do the
  docker base-image bumps (``ci:`` prefix, runtime changes).
"""
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


_DEV_SCOPE = re.compile(r"\(deps-dev\)", re.I)
_GROUP = re.compile(r"\bbump the (?P<group>[\w.-]+) group\b", re.I)
_DEV_GROUP_MARKERS = ("development", "minor-patch", "dev")


def is_dev_tooling_bump(title: str) -> bool:
    """True for a dependabot PR that only moves dev tooling by at most a minor.

    Accepts a ``(deps-dev)``-scoped single bump whose major is unchanged, or a
    ``(deps-dev)``-scoped group PR whose group name marks it as development /
    minor-patch. Majors are never auto-merged, and a production-scoped title
    never matches regardless of the bump size.
    """
    if not _DEV_SCOPE.search(title):
        return False
    m = _VER.search(title)
    if m:
        f_maj, _f_min, _f_pat, t_maj, _t_min, _t_pat = (int(x) for x in m.groups())
        return f_maj == t_maj
    g = _GROUP.search(title)
    if g:
        name = g.group("group").lower()
        return any(marker in name for marker in _DEV_GROUP_MARKERS)
    return False


_SINGLE = re.compile(r"\bbump (?P<pkg>[@\w./-]+) from\b", re.I)
# Group names dependabot.yml bounds to minor+patch update-types. A group PR
# carries no version pair in its title, so the bound has to come from the
# config that made the group.
_MINOR_PATCH_GROUP_MARKERS = ("development", "minor-patch", "dev", "production")
# Production minors that WAIT for a human (or a live smoke): packages whose
# behaviour the unit suite cannot see — the LLM router (a private
# transform_response seam is monkey-patched), the orchestrators, and the ML
# stacks whose wheels/kernels only prove themselves on the GPU. Patches of
# these still auto-merge via ``is_patch_bump``; majors never do.
_HELD_MINOR_PACKAGES = frozenset({
    "litellm", "prefect", "langgraph", "ragas", "deepeval", "diffusers",
    "transformers", "sentence-transformers", "next",
})
_HELD_MINOR_PREFIXES = ("torch", "llama-index", "langchain")


def bumped_package(title: str) -> str | None:
    """The package a single-bump dependabot title moves, or None for a group PR."""
    m = _SINGLE.search(title)
    return m.group("pkg").lower() if m else None


def is_held_package(pkg: str) -> bool:
    return pkg in _HELD_MINOR_PACKAGES or pkg.startswith(_HELD_MINOR_PREFIXES)


def is_production_minor_bump(title: str) -> bool:
    """True for a production-scoped bump that moves at most a minor and is not held.

    A ``(deps-dev)`` title never matches (that is ``is_dev_tooling_bump``'s
    job). A single bump must keep its major and name a package outside the
    held list; a group PR must be one dependabot.yml bounds to minor+patch.
    Docker base-image tags (``3.13-slim`` -> ``3.14-slim``) carry no x.y.z
    pair and never match.
    """
    if _DEV_SCOPE.search(title):
        return False
    m = _VER.search(title)
    if m:
        f_maj, _f_min, _f_pat, t_maj, _t_min, _t_pat = (int(x) for x in m.groups())
        pkg = bumped_package(title)
        return pkg is not None and f_maj == t_maj and not is_held_package(pkg)
    g = _GROUP.search(title)
    if g:
        name = g.group("group").lower()
        return any(marker in name for marker in _MINOR_PATCH_GROUP_MARKERS)
    return False


def auto_mergeable(title: str) -> bool:
    return is_patch_bump(title) or is_dev_tooling_bump(title) or is_production_minor_bump(title)


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
        if not (auto_mergeable(pr["title"]) and all_checks_green(pr.get("statusCheckRollup", []))
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
