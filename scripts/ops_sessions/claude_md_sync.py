"""Refresh CLAUDE.md DB counts + surface migration drift. Deterministic, worktree."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import _common as c

REPO = "Glad-Labs/poindexter"


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


def main() -> int:
    log = c.get_logger("claude-md-sync")
    root = _repo_root()
    roots = str(root)
    pkg = str(root / "src" / "cofounder_agent")
    # Step 1: DB counts via the existing script (self-edits CLAUDE.md in place).
    stats = root / "scripts" / "sync_claude_md_db_stats.py"
    db_stats_ok = True
    if stats.exists():
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
            db_stats_ok = False
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
    # Step 2: migration-drift CHECK (surface, do not auto-rewrite prose).
    drift_note = ""
    migrations = root / "src" / "cofounder_agent" / "services" / "migrations"
    newest = newest_migration(migrations)
    if newest:
        clause = extract_migration_clause(newest.read_text(encoding="utf-8"))
        referenced = newest.name in (root / "CLAUDE.md").read_text(encoding="utf-8")
        if not referenced:
            drift_note = f"CLAUDE.md does not reference newest migration `{newest.name}` — {clause}"
            log.info(drift_note)
    # Step 3: PR only if the DB-count script actually changed CLAUDE.md.
    status = c.git("status", "--porcelain", cwd=roots)
    pr_ok = True
    if status.stdout.strip():
        # stack#2809: this used to log "opened CLAUDE.md sync PR" unconditionally
        # while `gh pr create` had been failing every night since 2026-06-10 —
        # it ran without a cwd, so it read the *shared* checkout (on main) instead
        # of this worktree. Six weeks of correct DB counts were committed, pushed
        # to an `auto/*` branch, and then orphaned behind a green log line.
        pr_ok = c.commit_and_open_pr(
            cwd=roots,
            repo=REPO,
            paths=["CLAUDE.md"],
            message="docs(CLAUDE.md): sync DB-derived counts (ops)",
            title="docs(CLAUDE.md): sync DB-derived counts (ops)",
            body=f"Automated DB-count refresh.\n\n{drift_note}".strip(),
            log=log,
            source="claude_md_sync",
        ) is not None
    elif drift_note:
        c.notify_fail("CLAUDE.md migration drift", drift_note, "claude_md_sync")
    else:
        log.info("no CLAUDE.md changes")
    return 0 if (db_stats_ok and pr_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
