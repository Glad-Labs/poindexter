"""Refresh CLAUDE.md DB counts + surface migration drift. Deterministic, worktree.

Lands its own PR. CLAUDE.md's DB-derived counts are *generated* output, so when
``main`` moves under an open PR this session does not rebase — it throws the
edit away, regenerates on top of fresh ``main``, and force-pushes. See
``rebase_onto_fresh_main`` for why a textual rebase cannot work here.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/glad-labs-stack"

# Convergence budget for landing the PR. Env knobs rather than app_settings to
# match _common.py's OPS_OLLAMA_* idiom: these sessions are standalone by
# design (Python + asyncpg, no SiteConfig/DI) and have to stay runnable when
# the database is the thing that is broken.
#
# The wait defaults to an hour because the collision this exists to survive is
# GitHub Actions cron drift, not a race in seconds. The repo-derived sync
# (.github/workflows/sync-claude-md.yml) is scheduled 06:17 UTC — thirteen
# minutes *before* this session's 02:30 EDT timer, so on paper it always lands
# first. On 2026-08-08 it drifted to 07:16 and merged 47 minutes after this
# session had already pushed, leaving PR #3126 conflicted with green CI.
MAX_ATTEMPTS = int(os.environ.get("OPS_CLAUDE_MD_MAX_ATTEMPTS", "3"))
MERGE_WAIT_SECONDS = float(os.environ.get("OPS_CLAUDE_MD_MERGE_WAIT_SECONDS", "3600"))
MERGE_POLL_SECONDS = float(os.environ.get("OPS_CLAUDE_MD_POLL_SECONDS", "60"))

# This session's own PR branches, and *only* those. run-session.sh names them
# ``auto/<session>-<date>-<HHMM>``; the repo-derived CI sync pushes
# ``auto/claude-md-sync-<date>`` — same prefix, no ``-HHMM`` tail. That trailing
# time group is the only thing separating two different jobs sharing a branch
# namespace, so it is required here rather than left as a loose prefix match:
# without it the reaper below would close the CI job's PR. Pinned by
# tests/unit/scripts/test_ops_claude_md_sync.py.
OWN_BRANCH_RE = re.compile(r"^auto/claude-md-sync-\d{4}-\d{2}-\d{2}-\d{4}$")


def extract_migration_clause(source: str) -> str:
    try:
        doc = ast.get_docstring(ast.parse(source))
    except SyntaxError:
        return ""
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def newest_migration(migrations_dir: Path) -> Path | None:
    stamped = sorted(
        p for p in migrations_dir.glob("20*.py") if p.name[:8].isdigit()
    )
    return stamped[-1] if stamped else None


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "CLAUDE.md").exists())


def migration_drift_note(root: Path, log) -> str:
    """Surface a newest-migration CLAUDE.md never mentions. Never auto-rewrites prose."""
    migrations = root / "src" / "cofounder_agent" / "services" / "migrations"
    newest = newest_migration(migrations)
    if not newest:
        return ""
    if newest.name in (root / "CLAUDE.md").read_text(encoding="utf-8"):
        return ""
    clause = extract_migration_clause(newest.read_text(encoding="utf-8"))
    note = f"CLAUDE.md does not reference newest migration `{newest.name}` — {clause}"
    log.info(note)
    return note


def rebase_onto_fresh_main(root: str, branch: str, log) -> bool:
    """Re-point ``branch`` at the freshest ``origin/main``, discarding local edits.

    CLAUDE.md's DB-derived counts are generated, so the way to land them on a
    main that moved is to discard and regenerate — never a textual rebase. The
    repo-derived CI sync rewrites bullets *immediately adjacent* to these in the
    same "Key Numbers" list (``live posts`` sits directly above ``Python files``,
    ``app_settings keys`` directly below ``test files``), and adjacent-line edits
    on both sides of a 3-way merge conflict every time. Regenerating cannot
    conflict by construction: the generator is idempotent and prose-anchored, so
    it re-applies the same five claims to whatever main now says.

    ``checkout -B --force`` rather than ``reset --hard``: one command, moves the
    branch even from a dirty tree, and leaves no upstream surprise behind.
    """
    proc = c.git("fetch", "origin", "--quiet", cwd=root)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output captured").strip()[:1500]
        log.error("git fetch origin failed (rc=%s): %s", proc.returncode, detail)
        c.notify_fail(
            "claude-md-sync: git fetch failed",
            f"Could not fetch origin in {root}; CLAUDE.md's DB-derived counts "
            f"were NOT refreshed.\n{detail}",
            "claude_md_sync",
        )
        return False
    proc = c.git(
        "checkout", "-B", branch, "--no-track", "--force", "origin/main", cwd=root,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output captured").strip()[:1500]
        log.error("re-pointing %s at origin/main failed: %s", branch, detail)
        c.notify_fail(
            "claude-md-sync: could not re-point session branch",
            f"`git checkout -B {branch} origin/main` exited {proc.returncode} in "
            f"{root}. CLAUDE.md's DB-derived counts were NOT refreshed.\n{detail}",
            "claude_md_sync",
        )
        return False
    return True


def regenerate_db_counts(root: str, pkg: str, stats: Path, log) -> tuple[bool, bool]:
    """Run the DB-count generator. Returns ``(ok, changed_claude_md)``."""
    if not stats.exists():
        log.info("%s missing — skipping DB-count refresh", stats)
        return True, False
    # sys.executable = the main env python that launched us (has asyncpg);
    # the worktree has no provisioned venv.
    proc = c.run([sys.executable, str(stats)], cwd=pkg)
    # stack#2408: this result used to be discarded entirely — a DB-unreachable
    # crash (asyncpg.connect against a wedged port-forward) or any other
    # non-zero exit read identically to "already in sync", so CLAUDE.md's
    # DB-derived counts silently froze for a month behind a green session.
    if proc.stdout.strip():
        log.info("sync_claude_md_db_stats.py: %s", proc.stdout.strip())
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no output captured").strip()[:1500]
        log.error(
            "sync_claude_md_db_stats.py failed (rc=%s): %s", proc.returncode, detail,
        )
        c.notify_fail(
            "claude-md-sync: DB stats refresh failed",
            f"sync_claude_md_db_stats.py exited {proc.returncode} — CLAUDE.md's "
            f"DB-derived counts were NOT refreshed today.\n{detail}",
            "claude_md_sync",
        )
        return False, False
    status = c.git("status", "--porcelain", "CLAUDE.md", cwd=root)
    return True, bool(status.stdout.strip())


def reap_superseded_prs(keep_branch: str, *, cwd: str, log) -> list[int]:
    """Close this session's leftover PRs from earlier runs. Returns closed numbers.

    A run whose convergence budget expires leaves an open, conflicting PR
    behind. Today's regeneration restates the same five claims with fresher
    numbers, so yesterday's is dead weight — and an accumulating stack of
    conflicting docs PRs is precisely how this sync went unnoticed for six
    weeks, twice (stack#2408, stack#2809). Matches OWN_BRANCH_RE, so the CI
    sync's ``auto/claude-md-sync-<date>`` PRs are structurally out of reach.
    """
    # --limit because gh's default page is 30 and silently truncates.
    listing = c.gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "100",
        "--json", "number,headRefName", cwd=cwd,
    )
    if listing.returncode != 0:
        log.warning("could not list open PRs to reap superseded ones; skipping")
        return []
    try:
        rows = json.loads(listing.stdout or "[]")
    except json.JSONDecodeError:
        log.warning("unparseable `gh pr list` output; skipping reap")
        return []
    closed: list[int] = []
    for row in rows:
        head = str(row.get("headRefName") or "")
        if head == keep_branch or not OWN_BRANCH_RE.match(head):
            continue
        number = int(row["number"])
        proc = c.gh(
            "pr", "close", str(number), "--repo", REPO, "--delete-branch",
            "--comment",
            "Superseded by a fresher `claude-md-sync` run — the DB-derived "
            "counts in this PR have been regenerated on top of current `main`.",
            cwd=cwd,
        )
        if proc.returncode == 0:
            log.info("closed superseded PR #%s (%s)", number, head)
            closed.append(number)
        else:
            log.warning("could not close superseded PR #%s", number)
    return closed


def main() -> int:
    log = c.get_logger("claude-md-sync")
    root = _repo_root()
    roots = str(root)
    pkg = str(root / "src" / "cofounder_agent")
    stats = root / "scripts" / "sync_claude_md_db_stats.py"

    branch = c.current_branch(roots)
    if branch in ("", "main", "HEAD"):
        # commit_and_open_pr carries this guard too, but it has to be hoisted
        # here: this session now force-moves its own branch, so landing on the
        # wrong one must abort *before* the first checkout, not after it.
        log.error("refusing to run: HEAD in %s resolved to %r", roots, branch)
        c.notify_fail(
            "claude-md-sync: not on a session branch",
            f"HEAD in {roots} resolved to {branch or '<unresolvable>'}; expected "
            f"this session's `auto/*` worktree branch. Refusing to move any branch.",
            "claude_md_sync",
        )
        return 1

    reap_superseded_prs(branch, cwd=roots, log=log)

    pr_url: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not rebase_onto_fresh_main(roots, branch, log):
            return 1
        # Recomputed per attempt: the migrations dir is whatever fresh main says.
        drift_note = migration_drift_note(root, log)

        ok, changed = regenerate_db_counts(roots, pkg, stats, log)
        if not ok:
            return 1
        if not changed:
            if pr_url:
                # Only reachable when our own PR landed between polls — main now
                # already carries these counts, so there is nothing left to push.
                log.info("main already carries today's counts (%s landed)", pr_url)
            else:
                log.info("no CLAUDE.md changes")
                if drift_note:
                    c.notify_fail("CLAUDE.md migration drift", drift_note, "claude_md_sync")
            return 0

        pr_url = c.commit_and_open_pr(
            cwd=roots,
            repo=REPO,
            paths=["CLAUDE.md"],
            message="docs(CLAUDE.md): sync DB-derived counts (ops)",
            title="docs(CLAUDE.md): sync DB-derived counts (ops)",
            body=f"Automated DB-count refresh.\n\n{drift_note}".strip(),
            log=log,
            source="claude_md_sync",
            auto_merge=True,
            force_push=attempt > 1,
        )
        if pr_url is None:
            return 1

        outcome = c.wait_for_merge(
            REPO, branch, cwd=roots,
            budget_seconds=MERGE_WAIT_SECONDS,
            poll_seconds=MERGE_POLL_SECONDS,
            log=log,
        )
        if outcome == "MERGED":
            log.info("CLAUDE.md DB-count sync merged: %s", pr_url)
            return 0
        if outcome == "PENDING":
            # Clean and armed: GitHub merges it when CI finishes. Not a failure.
            log.info("%s left open with auto-merge armed", pr_url)
            return 0
        if outcome != "CONFLICTING":
            # CLOSED (a human declined it) or GONE (branch has no PR) — neither
            # is ours to retry past.
            log.error("PR for %s ended %s; not retrying", branch, outcome)
            c.notify_fail(
                f"claude-md-sync: PR {outcome.lower()} without merging",
                f"{pr_url} ended in state {outcome}. CLAUDE.md's DB-derived "
                f"counts did not land.",
                "claude_md_sync",
            )
            return 1
        log.info(
            "regenerating on top of the main that moved (attempt %s/%s)",
            attempt, MAX_ATTEMPTS,
        )

    log.error("gave up after %s attempts; %s is still conflicting", MAX_ATTEMPTS, pr_url)
    c.notify_fail(
        "claude-md-sync: could not converge on a mergeable PR",
        f"Regenerated CLAUDE.md's DB-derived counts on top of fresh main "
        f"{MAX_ATTEMPTS} times and {pr_url} was conflicting every time. Something "
        f"other than the repo-derived CI sync is rewriting the Key Numbers list.",
        "claude_md_sync",
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
