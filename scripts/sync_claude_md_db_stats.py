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
ops session (``scripts/ops_sessions/claude_md_sync.py``, fired by a systemd
timer) invokes it, then opens the CLAUDE.md PR. (It used to name
``scripts/claude-sessions.ps1`` — dead since the Pop!_OS migration moved
scheduling to systemd.)

Design mirrors the sibling script on purpose:

* **Idempotent** — re-running on already-fresh state produces zero changes.
* **``--check``** — exits non-zero if drift would happen (no write); useful
  for a dry-run gate.
* **``--json``** — prints the collected counts and exits (no DB write to
  CLAUDE.md), so a caller can emit ``claude-md-db-stats.json`` for audit.
* Each replacement is **prose-anchored** so a bare ``\d+`` never rewrites an
  unrelated number, and each rewrite is logged for the PR description.

Whenever a count actually moves, the freshness marker in the ``### Key
Numbers`` header is restamped to the run's own UTC date. Without that, the
header keeps asserting whatever date a human last typed there while the
numbers under it refresh nightly — a staleness marker that reads as
authoritative and is wrong by construction.

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
from datetime import UTC, datetime
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

# The "### Key Numbers" header carries a freshness marker for the DB-derived
# half of the section. Anchored on the literal heading plus the "DB-derived
# counts" phrase; everything from there to the closing paren is replaced
# wholesale, so a hand-edited variant ("hand-refreshed 2026-07-26", "last
# refreshed 2026-06-10, pending a prod-DB probe", …) converges back on one
# canonical clause rather than needing a pattern per historical wording.
# ``[^)\n]`` keeps the match on a single line and inside one paren group.
#
# Keep the clause terse. #2820 cut CLAUDE.md by 35% to claw back ~9k tokens a
# session; a stamp that re-inflates the heading every night would quietly undo
# that. The date is the only part that has to be here.
HEADER_RE = re.compile(
    r"^(### Key Numbers \([^)\n]*?DB-derived counts)[^)\n]*(\)[ \t]*)$",
    re.MULTILINE,
)
HEADER_CLAUSE = " last refreshed {date}"


def _utc_today() -> str:
    """Today's UTC date as ``YYYY-MM-DD``.

    Stdlib-only on purpose. This script runs standalone under the ops-session
    harness (``scripts/ops_sessions/claude_md_sync.py`` shells it out with only
    asyncpg importable), so ``services/clock.py`` is out of reach — its
    operator-timezone helpers need a DB pool or a ``SiteConfig``. UTC is also
    the honest unit here: the marker describes when a machine read the
    database, not an operator-facing local time.
    """
    return datetime.now(UTC).date().isoformat()


def stamp_header(text: str, today: str) -> tuple[str, bool]:
    """Restamp the Key Numbers freshness marker to ``today``.

    Returns ``(new_text, matched)``. ``matched`` is False only when the
    anchor is gone — reported by the caller rather than swallowed, because a
    silent miss here is exactly the failure this stamping exists to prevent.
    """
    new, n = HEADER_RE.subn(rf"\1{HEADER_CLAUSE.format(date=today)}\2", text, count=1)
    return new, bool(n)


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
    today: str | None = None,
) -> tuple[str, list[str]]:
    r"""Return ``(new_text, changes)``.

    ``changes`` lists which claims were rewritten so the PR description can
    summarise. Each pattern is anchored on surrounding prose; numbers are
    formatted with thousands separators to match CLAUDE.md's existing style
    (``1,626`` / ``40,497``). Pass ``text`` to operate on a string in-memory
    (used by tests); otherwise the on-disk CLAUDE.md is read. Pass ``today``
    (``YYYY-MM-DD``) to pin the header stamp; defaults to the current UTC date.

    If any count moved, the Key Numbers freshness marker is restamped to
    ``today``. The stamp is deliberately gated on a real count change: an
    unconditional restamp would make ``--check`` report drift every single day
    on nothing but the calendar, and the nightly session would open an empty PR
    each morning.
    """
    # Bind a definitely-``str`` local so the nested closure narrows cleanly.
    current: str = CLAUDE_MD.read_text(encoding="utf-8") if text is None else text
    changes: list[str] = []

    def _sub(pattern: str, repl: str, label: str) -> None:
        nonlocal current
        new, n = re.subn(pattern, repl, current, count=1)
        if not n:
            # Zero matches means the prose this anchor rides on was reworded and
            # the claim silently stopped syncing — CLAUDE.md keeps presenting a
            # frozen number as current. Exactly how `app_settings` went stale
            # when #2820 rewrote its bullet. A miss is reported, never assumed
            # to mean "already correct".
            changes.append(
                f"WARNING: anchor not found for {label.split(' ->')[0]} — its "
                f"CLAUDE.md wording changed, so that count is no longer synced. "
                f"Update the pattern in scripts/sync_claude_md_db_stats.py.",
            )
            return
        if new != current:
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

    # "801 app_settings keys (60 secret) plus ..." — and, post-#2820,
    # "1,279 app_settings keys live on prod (68 secret; drifts as keys are
    # added)". The qualifier between "keys" and the paren is captured rather
    # than spelled out, and everything from "secret" onward is left alone, so
    # the anchor survives prose edits on either side of the two numbers.
    _sub(
        r"[\d,]+ (app_settings keys[^(\n]*)\(\d+ secret",
        rf"{fmt('app_settings')} \1({fmt('app_settings_secret')} secret",
        f"app_settings ->{fmt('app_settings')} ({fmt('app_settings_secret')} secret)",
    )

    # "40,497 embeddings across posts / issues / audit / ..."
    _sub(
        r"[\d,]+ embeddings across",
        f"{fmt('embeddings')} embeddings across",
        f"embeddings ->{fmt('embeddings')}",
    )

    # Only restamp once a count has actually moved — see the docstring.
    if changes:
        stamp_date = _utc_today() if today is None else today
        stamped, matched = stamp_header(current, stamp_date)
        if not matched:
            # Loud, not fatal: the counts below are correct and worth landing,
            # but the header now asserts a freshness date nothing maintains.
            changes.append(
                "WARNING: Key Numbers header not restamped — the "
                "'### Key Numbers (… DB-derived counts …)' anchor is gone, so "
                "its freshness date is now stale prose. Fix HEADER_RE in "
                "scripts/sync_claude_md_db_stats.py or restore the header.",
            )
        elif stamped != current:
            changes.append(f"header last-refreshed ->{stamp_date}")
            current = stamped

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
