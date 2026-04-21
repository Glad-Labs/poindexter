"""One-shot backfill for GitHub issue Glad-Labs/poindexter#84.

Scrubs pipeline stage tokens (``||sdxl:*||``, ``||pexels:*||``) out of
the ``alt`` attribute on every ``<img>`` tag in:

* ``posts.content``            — already-published posts on the live site
* ``pipeline_versions.content``— pending / awaiting-approval tasks

Uses the same regex as the live pipeline (:mod:`services.alt_text`) so
there's exactly one source of truth.

Idempotent: rerunning does no damage — already-clean rows stay clean,
and we only UPDATE rows whose content actually changes.

Usage (from repo root)::

    python scripts/backfill-alt-text.py --dry-run   # show what would change
    python scripts/backfill-alt-text.py             # apply to live DB
    python scripts/backfill-alt-text.py --post-id <uuid>  # limit to one post

DB connection is resolved from (in order):

1. ``--database-url`` CLI flag,
2. ``POINDEXTER_BRAIN_URL`` env var,
3. ``GLADLABS_BRAIN_URL`` env var,
4. ``DATABASE_URL`` env var,
5. local default (``postgresql://.../poindexter_brain``) — matches the
   convention used by ``scripts/regen-featured-images.py``.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable

import asyncpg

# Make the sibling ``src/cofounder_agent/services`` importable so we
# reuse the live regex without copy/paste drift.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_PARENT = _REPO_ROOT / "src" / "cofounder_agent"
if str(_SERVICES_PARENT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_PARENT))

from services.alt_text import (  # noqa: E402  (sys.path munge above)
    iter_img_alts,
    strip_tokens_from_img_tags,
)


DEFAULT_DB_URL = (
    "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
)


def _resolve_db_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    for env_key in ("POINDEXTER_BRAIN_URL", "GLADLABS_BRAIN_URL", "DATABASE_URL"):
        val = os.getenv(env_key)
        if val:
            return val
    return DEFAULT_DB_URL


async def _table_has_column(conn, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM information_schema.columns
                WHERE table_name = $1 AND column_name = $2
            )
            """,
            table,
            column,
        )
    )


async def _scrub_table(
    conn: asyncpg.Connection,
    table: str,
    id_column: str,
    content_column: str,
    where: str,
    dry_run: bool,
) -> tuple[int, int, list[str]]:
    """Scrub token leaks from a single (table, content_column).

    Returns (rows_scanned, rows_updated, sample_ids_updated).
    """
    if not await _table_has_column(conn, table, content_column):
        print(f"[skip] table {table}.{content_column} does not exist")
        return 0, 0, []

    rows = await conn.fetch(
        f"""
        SELECT {id_column} AS row_id, {content_column} AS content
        FROM {table}
        WHERE {where}
        """
    )

    scanned = len(rows)
    updated = 0
    sample_ids: list[str] = []
    for row in rows:
        orig = row["content"] or ""
        if not orig:
            continue
        cleaned = strip_tokens_from_img_tags(orig)
        if cleaned == orig:
            continue
        updated += 1
        if len(sample_ids) < 5:
            sample_ids.append(str(row["row_id"]))
        if dry_run:
            continue
        await conn.execute(
            f"UPDATE {table} SET {content_column} = $1 WHERE {id_column} = $2",
            cleaned,
            row["row_id"],
        )

    return scanned, updated, sample_ids


async def run(dry_run: bool, post_id: str | None, db_url: str) -> int:
    conn = await asyncpg.connect(db_url)
    try:
        total_updated = 0
        print(f"[backfill-alt-text] connected to DB ({'DRY RUN' if dry_run else 'LIVE'})")

        # 1) posts.content — published / drafts
        where = "content LIKE '%||%:%||%'"
        if post_id:
            where = f"id = '{post_id}'::uuid AND {where}"
        scanned, updated, sample = await _scrub_table(
            conn, "posts", "id", "content", where, dry_run
        )
        total_updated += updated
        print(
            f"[posts]              scanned={scanned:>5}  updated={updated:>5}  "
            f"sample_ids={sample}"
        )

        # 2) pipeline_versions.content — pending/awaiting_approval
        where = "content LIKE '%||%:%||%'"
        if post_id:
            # pipeline_versions.task_id is the pipeline task id, not the
            # post id — the ``--post-id`` flag only targets `posts`.
            where = "FALSE"
        scanned, updated, sample = await _scrub_table(
            conn, "pipeline_versions", "task_id", "content", where, dry_run
        )
        total_updated += updated
        print(
            f"[pipeline_versions]  scanned={scanned:>5}  updated={updated:>5}  "
            f"sample_ids={sample}"
        )

        # 3) Legacy: content_tasks table still exists (migration 0066)
        #    until gradual cutover finishes. Scrub it too for safety.
        if await _table_has_column(conn, "content_tasks", "content"):
            where = "content LIKE '%||%:%||%'"
            if post_id:
                where = "FALSE"
            scanned, updated, sample = await _scrub_table(
                conn, "content_tasks", "id", "content", where, dry_run
            )
            total_updated += updated
            print(
                f"[content_tasks]      scanned={scanned:>5}  updated={updated:>5}  "
                f"sample_ids={sample}"
            )

        verb = "would update" if dry_run else "updated"
        print(f"[backfill-alt-text] {verb} {total_updated} row(s) total")
        return 0
    finally:
        await conn.close()


def _verify_alts_clean(samples: Iterable[str]) -> None:
    """Development aid — print any alt text that still looks broken.

    Not used in the main flow; kept here so operators can extend the
    script if they want to audit the DB after running.
    """
    from services.alt_text import assert_alt_text_clean

    for alt in samples:
        try:
            assert_alt_text_clean(alt, budget=120)
        except ValueError as exc:
            print(f"[warn] still broken: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan rows and print what would change, don't write.",
    )
    parser.add_argument(
        "--post-id",
        help="Only touch posts.content for this single UUID "
        "(pipeline_versions ignored).",
    )
    parser.add_argument(
        "--database-url",
        help="Override DB URL (otherwise read from POINDEXTER_BRAIN_URL / "
        "GLADLABS_BRAIN_URL / DATABASE_URL / local default).",
    )
    args = parser.parse_args()

    db_url = _resolve_db_url(args.database_url)
    return asyncio.run(run(args.dry_run, args.post_id, db_url))


if __name__ == "__main__":
    sys.exit(main())
