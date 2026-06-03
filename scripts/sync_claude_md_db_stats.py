r"""Sync the DB-derived counts in CLAUDE.md to current production state.

Companion to ``sync-claude-md-stats.py``. That script owns the stats
derivable from the checked-in repo (file counts, dashboard count, latest
migration name) and runs in CI. **This** script owns the stats that only
the live production database can answer:

* live (published) post count
* total post count
* lifetime ``pipeline_tasks`` rows
* ``app_settings`` key count (and how many are secret)
* total ``embeddings`` vectors

Because those need a DB connection, this script runs **locally** (on the
brain/worker box, where ``bootstrap.toml`` resolves a DSN) — never in CI,
which has no path to the operator's Postgres. The daily ``claude-md-sync``
scheduled Claude session (``scripts/claude-sessions.ps1``) invokes it,
then opens the CLAUDE.md PR.

Design mirrors the sibling script on purpose:

* **Idempotent** — re-running on already-fresh state produces zero changes.
* **``--check``** — exits non-zero if drift would happen (no write); useful
  for a dry-run gate.
* **``--json``** — prints the collected counts and exits (no DB write to
  CLAUDE.md), so a caller can emit ``claude-md-db-stats.json`` for audit.
* Each replacement is **prose-anchored** so a bare ``\d+`` never rewrites an
  unrelated number, and each rewrite is logged for the PR description.

The count queries are copied verbatim from the canonical callers so the
numbers match what operators already see elsewhere:
``mcp-server/server.py::get_post_count`` (published posts) and
``::memory_stats`` (embeddings).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"

# Make ``brain`` importable regardless of the caller's CWD (the script is
# launched from the repo root, from src/cofounder_agent via poetry, or from
# a scheduled-session worktree). ``brain.bootstrap`` owns DSN resolution.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Canonical count queries. Keyed to match ``apply_to_claude_md`` anchors.
COUNT_QUERIES: OrderedDict[str, str] = OrderedDict([
    # published posts — verbatim from mcp-server/server.py::get_post_count
    ("live_posts", "SELECT COUNT(*) FROM posts WHERE status = 'published'"),
    ("total_posts", "SELECT COUNT(*) FROM posts"),
    ("pipeline_tasks", "SELECT COUNT(*) FROM pipeline_tasks"),
    ("app_settings", "SELECT COUNT(*) FROM app_settings"),
    ("app_settings_secret", "SELECT COUNT(*) FROM app_settings WHERE is_secret = true"),
    # embeddings — same table memory_stats sums; we only need the grand total
    ("embeddings", "SELECT COUNT(*) FROM embeddings"),
])


async def collect_stats_from_db(dsn: str) -> OrderedDict[str, int]:
    """Run every count query against ``dsn`` and return an ordered dict.

    asyncpg is imported lazily so the pure-logic helpers (and their unit
    tests) don't need the driver installed.
    """
    import asyncpg  # local import: keeps module import DB-driver-free

    conn = await asyncpg.connect(dsn)
    try:
        out: OrderedDict[str, int] = OrderedDict()
        for key, query in COUNT_QUERIES.items():
            out[key] = int(await conn.fetchval(query))
        return out
    finally:
        await conn.close()


def apply_to_claude_md(
    stats: OrderedDict[str, int],
    text: str | None = None,
) -> tuple[str, list[str]]:
    r"""Return ``(new_text, changes)``.

    ``changes`` lists which claims were rewritten so the PR description can
    summarise. Each pattern is anchored on surrounding prose; numbers are
    formatted with thousands separators to match CLAUDE.md's existing style
    (``1,626`` / ``40,497``). Pass ``text`` to operate on a string in-memory
    (used by tests); otherwise the on-disk CLAUDE.md is read.
    """
    # Bind a definitely-``str`` local so the nested closure narrows cleanly.
    current: str = CLAUDE_MD.read_text(encoding="utf-8") if text is None else text
    changes: list[str] = []

    def _sub(pattern: str, repl: str, label: str) -> None:
        nonlocal current
        new, n = re.subn(pattern, repl, current, count=1)
        if n and new != current:
            changes.append(label)
            current = new

    def fmt(key: str) -> str:
        return f"{stats[key]:,}"

    # "78 live posts on gladlabs.io (222 posts total; 1,626 pipeline_tasks ..."
    _sub(
        r"[\d,]+ live posts on gladlabs\.io",
        f"{fmt('live_posts')} live posts on gladlabs.io",
        f"live_posts ->{fmt('live_posts')}",
    )
    _sub(
        r"\([\d,]+ posts total;",
        f"({fmt('total_posts')} posts total;",
        f"total_posts ->{fmt('total_posts')}",
    )
    _sub(
        r"[\d,]+ pipeline_tasks across",
        f"{fmt('pipeline_tasks')} pipeline_tasks across",
        f"pipeline_tasks ->{fmt('pipeline_tasks')}",
    )

    # "801 app_settings keys (60 secret) plus ..."
    _sub(
        r"[\d,]+ app_settings keys \(\d+ secret\)",
        f"{fmt('app_settings')} app_settings keys ({fmt('app_settings_secret')} secret)",
        f"app_settings ->{fmt('app_settings')} ({fmt('app_settings_secret')} secret)",
    )

    # "40,497 embeddings across posts / issues / audit / ..."
    _sub(
        r"[\d,]+ embeddings across",
        f"{fmt('embeddings')} embeddings across",
        f"embeddings ->{fmt('embeddings')}",
    )

    return current, changes


def _resolve_dsn(explicit: str | None) -> str:
    """Resolve the prod DSN via the brain's canonical resolver; exit 2 if none."""
    from brain.bootstrap import (
        resolve_database_url,  # type: ignore[import-not-found]  # lazy: brain pkg on sys.path at runtime
    )

    dsn = resolve_database_url(explicit=explicit)
    if not dsn:
        print(
            "No database URL resolved (bootstrap.toml / DATABASE_URL / "
            "LOCAL_DATABASE_URL / POINDEXTER_MEMORY_DSN all empty). This "
            "script must run on a box with prod DB access.",
            file=sys.stderr,
        )
        sys.exit(2)
    return dsn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url", default=None,
        help="Explicit DSN (overrides bootstrap.toml / env resolution).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if CLAUDE.md would change; don't write.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the collected counts as JSON and exit (no CLAUDE.md write).",
    )
    args = parser.parse_args(argv)

    dsn = _resolve_dsn(args.db_url)
    stats = asyncio.run(collect_stats_from_db(dsn))

    if args.json:
        print(json.dumps(stats, indent=2))
        return 0

    new_text, changes = apply_to_claude_md(stats)
    original = CLAUDE_MD.read_text(encoding="utf-8")
    drift = new_text != original

    if args.check:
        if drift:
            print("CLAUDE.md DB-stat drift detected:")
            for c in changes:
                print(f"  - {c}")
            return 1
        print("CLAUDE.md DB stats are in sync.")
        return 0

    if drift:
        CLAUDE_MD.write_text(new_text, encoding="utf-8")
        print("CLAUDE.md updated:")
        for c in changes:
            print(f"  - {c}")
    else:
        print("CLAUDE.md DB stats are in sync (no changes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
