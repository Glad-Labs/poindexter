"""Backfill ``title`` + ``seo_title`` from the body H1 (issue GH-85).

Every existing post in the pipeline was written while the pipeline had three
independent title sources (``title`` column, ``seo_title`` column, body H1).
After GH-85, body H1 is the canonical source. This script re-derives the
``title`` and ``seo_title`` columns from the body H1 for every existing row.

Tables touched:
    * ``posts``             — status = 'published'/'scheduled'/'draft', all
                              rows that have a ``content`` body with an H1.
    * ``content_tasks``     — rows in ``awaiting_approval`` or ``completed``
                              with body in ``task_metadata->>'content'``.
    * ``pipeline_versions`` — latest version per task (table is versioned).

Rule applied (matching ``utils.title_utils.propagate_canonical_title``):
    canonical       = body H1 (first ``# `` heading), emoji preserved in body.
    title column    = canonical with emoji stripped.
    seo_title       = canonical with emoji stripped + word-boundary-truncate
                      at ≤ 60 chars. Never mid-word.

Usage
-----

    python scripts/backfill-canonical-titles.py --dry-run   # print diffs only
    python scripts/backfill-canonical-titles.py             # write changes
    python scripts/backfill-canonical-titles.py --limit 5   # sanity run
    python scripts/backfill-canonical-titles.py --table posts   # only one table

Idempotent: running twice is a no-op once everything lines up.

Edge cases (logged, handled where safe):
    * No body / empty content         — nothing to do, skipped.
    * No H1 in body                   — fall back to the existing ``title``
                                        column as canonical. In practice the
                                        Next.js publish path strips the
                                        body-H1 because the template renders
                                        ``title`` as the page heading, so
                                        this case dominates the ``posts``
                                        table and is safe to handle.
    * Neither H1 nor title            — skipped and logged.
    * Derived values already match    — idempotent; skipped silently.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Make the project ``utils`` package importable when run from repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_SRC = os.path.join(_HERE, "..", "src", "cofounder_agent")
if os.path.isdir(_AGENT_SRC):
    sys.path.insert(0, os.path.abspath(_AGENT_SRC))

import asyncpg  # noqa: E402

from utils.title_utils import (  # noqa: E402
    DEFAULT_SEO_TITLE_MAX_LEN,
    derive_seo_title,
    extract_body_h1,
    strip_emoji,
)


DEFAULT_DB_URL = (
    os.getenv("POINDEXTER_BRAIN_URL")
    or os.getenv("GLADLABS_BRAIN_URL")
    or "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
)


@dataclass
class BackfillDiff:
    table: str
    row_id: str
    old_title: Optional[str]
    new_title: str
    old_seo_title: Optional[str]
    new_seo_title: str
    body_h1: str

    def changed(self) -> bool:
        return self.old_title != self.new_title or self.old_seo_title != self.new_seo_title


def _derive_from_h1(body_h1: str) -> Tuple[str, str]:
    """Return ``(new_title, new_seo_title)`` for a given body H1."""
    new_title = strip_emoji(body_h1)
    new_seo_title = derive_seo_title(body_h1, max_len=DEFAULT_SEO_TITLE_MAX_LEN)
    return new_title, new_seo_title


# ---------------------------------------------------------------------------
# Table handlers
# ---------------------------------------------------------------------------


def _pick_canonical(body: Optional[str], existing_title: Optional[str]) -> Tuple[Optional[str], str]:
    """Pick canonical title: body H1 preferred, else existing title column.

    Returns ``(canonical, source_label)`` where source_label is 'h1' or
    'title_col'. Returns ``(None, 'none')`` if neither is usable.
    """
    if body:
        h1 = extract_body_h1(body)
        if h1:
            return h1, "h1"
    if existing_title and existing_title.strip():
        return existing_title.strip(), "title_col"
    return None, "none"


async def _scan_posts(conn, limit: Optional[int]) -> Tuple[List[BackfillDiff], int, int]:
    """Scan the ``posts`` table. Returns (diffs, skipped_no_canonical, skipped_no_body)."""
    diffs: List[BackfillDiff] = []
    skipped_no_canonical = 0
    skipped_no_body = 0

    query = "SELECT id::text AS id, title, seo_title, content FROM posts"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = await conn.fetch(query)

    for row in rows:
        body = row["content"]
        canonical, source = _pick_canonical(body, row["title"])
        if canonical is None:
            if not body:
                skipped_no_body += 1
            else:
                skipped_no_canonical += 1
            continue
        new_title, new_seo_title = _derive_from_h1(canonical)
        diffs.append(BackfillDiff(
            table="posts",
            row_id=row["id"],
            old_title=row["title"],
            new_title=new_title,
            old_seo_title=row["seo_title"],
            new_seo_title=new_seo_title,
            body_h1=f"{canonical} [source={source}]",
        ))

    return diffs, skipped_no_canonical, skipped_no_body


async def _scan_content_tasks(
    conn, limit: Optional[int]
) -> Tuple[List[BackfillDiff], int, int]:
    diffs: List[BackfillDiff] = []
    skipped_no_canonical = 0
    skipped_no_body = 0

    # content_tasks stores the body under ``task_metadata->>'content'`` for
    # awaiting_approval / completed rows. ``content`` column exists too but
    # is sometimes empty.
    query = (
        "SELECT task_id, title, seo_title, "
        "COALESCE(content, task_metadata->>'content') AS content "
        "FROM content_tasks "
        "WHERE status IN ('awaiting_approval', 'completed', 'published')"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = await conn.fetch(query)

    for row in rows:
        body = row["content"]
        canonical, source = _pick_canonical(body, row["title"])
        if canonical is None:
            if not body:
                skipped_no_body += 1
            else:
                skipped_no_canonical += 1
            continue
        new_title, new_seo_title = _derive_from_h1(canonical)
        diffs.append(BackfillDiff(
            table="content_tasks",
            row_id=row["task_id"],
            old_title=row["title"],
            new_title=new_title,
            old_seo_title=row["seo_title"],
            new_seo_title=new_seo_title,
            body_h1=f"{canonical} [source={source}]",
        ))

    return diffs, skipped_no_canonical, skipped_no_body


async def _scan_pipeline_versions(
    conn, limit: Optional[int]
) -> Tuple[List[BackfillDiff], int, int]:
    diffs: List[BackfillDiff] = []
    skipped_no_canonical = 0
    skipped_no_body = 0

    # Latest version per task only — older versions are history we don't need
    # to backfill.
    query = """
        SELECT DISTINCT ON (task_id)
            id, task_id, title, seo_title, content
        FROM pipeline_versions
        ORDER BY task_id, version DESC
    """
    if limit:
        query = f"SELECT * FROM ({query}) _sub LIMIT {int(limit)}"
    rows = await conn.fetch(query)

    for row in rows:
        body = row["content"]
        canonical, source = _pick_canonical(body, row["title"])
        if canonical is None:
            if not body:
                skipped_no_body += 1
            else:
                skipped_no_canonical += 1
            continue
        new_title, new_seo_title = _derive_from_h1(canonical)
        diffs.append(BackfillDiff(
            table="pipeline_versions",
            row_id=str(row["id"]),
            old_title=row["title"],
            new_title=new_title,
            old_seo_title=row["seo_title"],
            new_seo_title=new_seo_title,
            body_h1=f"{canonical} [source={source}]",
        ))

    return diffs, skipped_no_canonical, skipped_no_body


async def _apply_diff(conn, diff: BackfillDiff) -> None:
    if diff.table == "posts":
        await conn.execute(
            "UPDATE posts SET title = $1, seo_title = $2, updated_at = NOW() WHERE id::text = $3",
            diff.new_title, diff.new_seo_title, diff.row_id,
        )
    elif diff.table == "content_tasks":
        await conn.execute(
            "UPDATE content_tasks SET title = $1, seo_title = $2, updated_at = NOW() WHERE task_id = $3",
            diff.new_title, diff.new_seo_title, diff.row_id,
        )
    elif diff.table == "pipeline_versions":
        await conn.execute(
            "UPDATE pipeline_versions SET title = $1, seo_title = $2 WHERE id = $3",
            diff.new_title, diff.new_seo_title, int(diff.row_id),
        )
    else:
        raise ValueError(f"unknown table: {diff.table}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_diff(d: BackfillDiff) -> str:
    # ASCII-only arrows — some consoles (Windows cp1252) can't encode '→'.
    lines = [f"[{d.table}] {d.row_id}"]
    lines.append(f"  body_h1  : {d.body_h1!r}")
    if d.old_title != d.new_title:
        lines.append(f"  title    : {d.old_title!r}")
        lines.append(f"          -> {d.new_title!r}")
    if d.old_seo_title != d.new_seo_title:
        lines.append(f"  seo_title: {d.old_seo_title!r}")
        lines.append(f"          -> {d.new_seo_title!r}")
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> int:
    db_url = args.db_url or DEFAULT_DB_URL
    print(f"[backfill-canonical-titles] Connecting to {db_url.split('@')[-1]!r}")
    conn = await asyncpg.connect(db_url)
    try:
        all_diffs: List[BackfillDiff] = []
        total_skipped_no_canonical = 0
        total_skipped_no_body = 0

        tables_to_scan = [args.table] if args.table else ["posts", "content_tasks", "pipeline_versions"]

        for table in tables_to_scan:
            if table == "posts":
                d, no_canon, no_body = await _scan_posts(conn, args.limit)
            elif table == "content_tasks":
                d, no_canon, no_body = await _scan_content_tasks(conn, args.limit)
            elif table == "pipeline_versions":
                d, no_canon, no_body = await _scan_pipeline_versions(conn, args.limit)
            else:
                print(f"[backfill] Unknown table: {table}", file=sys.stderr)
                return 2
            print(
                f"[{table}] scanned {len(d) + no_canon + no_body} rows - "
                f"{len(d)} candidates, {no_canon} with no canonical, {no_body} with no body"
            )
            all_diffs.extend(d)
            total_skipped_no_canonical += no_canon
            total_skipped_no_body += no_body

        changed = [d for d in all_diffs if d.changed()]
        unchanged = [d for d in all_diffs if not d.changed()]

        print()
        print(f"Would change: {len(changed)} rows")
        print(f"Already correct: {len(unchanged)} rows (idempotent)")
        print(f"Skipped (no canonical): {total_skipped_no_canonical} rows")
        print(f"Skipped (no body): {total_skipped_no_body} rows")
        print()

        for d in changed:
            print(_format_diff(d))
            print()

        if args.dry_run:
            print("[backfill] DRY RUN — no database writes performed")
            return 0

        if not changed:
            print("[backfill] Nothing to apply.")
            return 0

        if not args.yes:
            resp = input(f"Apply {len(changed)} updates? [y/N] ").strip().lower()
            if resp != "y":
                print("[backfill] aborted.")
                return 1

        async with conn.transaction():
            for d in changed:
                await _apply_diff(conn, d)

        print(f"[backfill] Applied {len(changed)} updates.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="print diffs without writing")
    p.add_argument("--limit", type=int, default=None, help="cap rows per table for sanity runs")
    p.add_argument(
        "--table",
        choices=["posts", "content_tasks", "pipeline_versions"],
        help="restrict to one table",
    )
    p.add_argument("--db-url", help="override DATABASE_URL (default POINDEXTER_BRAIN_URL)")
    p.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompt")
    args = p.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
