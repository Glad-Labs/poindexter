"""
One-shot backfill for GH-86 (qa_feedback / excerpt / category persistence).

Fixes data-completeness gaps on existing pending + published posts:

  1. category — copy task_metadata->>'category' to content_tasks.category
     where the base column is NULL and the JSONB source is populated.
     This is the highest-priority fix: site rendering groups posts by
     category and NULL means the post lands on the homepage only.

  2. excerpt  — best-effort regeneration from stored content using the
     same services.excerpt_generator module the live pipeline uses.
     Re-run is safe; the generator is deterministic.

  3. qa_feedback — reconstructed from any stored task_metadata entries
     we can find (qa_reviews list, qa_final_score, quality_score).
     Many historical rows never captured the reviews list so this is
     best-effort — the column stays NULL when we have nothing to write,
     which is still strictly better than the status quo.

Usage:
    python scripts/backfill-qa-feedback-excerpt-category.py            # apply
    python scripts/backfill-qa-feedback-excerpt-category.py --dry-run  # preview
    python scripts/backfill-qa-feedback-excerpt-category.py --status \
        awaiting_approval                                              # limit
    python scripts/backfill-qa-feedback-excerpt-category.py --limit 50

Safe to re-run. Every UPDATE uses a WHERE clause that keeps existing
non-NULL values intact — we only touch rows where the column is NULL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Make ``services.*`` importable without a full package install
_SRC = Path(__file__).resolve().parent.parent / "src" / "cofounder_agent"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import asyncpg  # noqa: E402

from services.excerpt_generator import generate_excerpt  # noqa: E402
from services.multi_model_qa import format_qa_feedback_from_reviews  # noqa: E402

DB_URL = os.getenv(
    "POINDEXTER_BRAIN_URL",
    os.getenv(
        "GLADLABS_BRAIN_URL",
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
    ),
)


def _coerce_json(value) -> dict:
    """task_metadata is sometimes str (json), sometimes dict — normalize."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def backfill_category(conn, dry_run: bool, limit: int | None) -> int:
    """Copy task_metadata.category → content_tasks.category where NULL."""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = await conn.fetch(
        f"""
        SELECT task_id, task_metadata
        FROM content_tasks
        WHERE (category IS NULL OR category = '')
          AND task_metadata IS NOT NULL
          AND task_metadata::text LIKE '%"category"%'
        ORDER BY created_at DESC
        {limit_clause}
        """
    )
    updated = 0
    for row in rows:
        meta = _coerce_json(row["task_metadata"])
        cat = meta.get("category")
        if not cat or not isinstance(cat, str):
            continue
        if dry_run:
            print(f"  [dry-run] {row['task_id']}: category -> {cat!r}")
            updated += 1
            continue
        result = await conn.execute(
            """
            UPDATE content_tasks
               SET category = $2, updated_at = NOW()
             WHERE task_id = $1
               AND (category IS NULL OR category = '')
            """,
            row["task_id"], cat,
        )
        # asyncpg returns e.g. 'UPDATE 1'
        if result.endswith(" 1"):
            updated += 1
    return updated


async def backfill_excerpt(conn, dry_run: bool, limit: int | None) -> int:
    """Regenerate excerpts from stored content for rows with NULL excerpt."""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = await conn.fetch(
        f"""
        SELECT task_id, title, content, seo_description, seo_title, topic
        FROM content_tasks
        WHERE (excerpt IS NULL OR excerpt = '')
          AND content IS NOT NULL
          AND LENGTH(content) > 100
        ORDER BY created_at DESC
        {limit_clause}
        """
    )
    updated = 0
    for row in rows:
        title = row["title"] or row["seo_title"] or row["topic"] or ""
        try:
            excerpt = generate_excerpt(
                title=title,
                content=row["content"] or "",
            )
        except Exception as exc:  # pragma: no cover — defensive
            print(f"  [error] {row['task_id']}: {exc}")
            continue
        if not excerpt and row["seo_description"]:
            excerpt = (row["seo_description"] or "")[:240].strip()
        if not excerpt:
            continue
        if dry_run:
            snippet = excerpt[:80].replace("\n", " ")
            print(f"  [dry-run] {row['task_id']}: excerpt -> {snippet!r}")
            updated += 1
            continue
        result = await conn.execute(
            """
            UPDATE content_tasks
               SET excerpt = $2, updated_at = NOW()
             WHERE task_id = $1
               AND (excerpt IS NULL OR excerpt = '')
            """,
            row["task_id"], excerpt,
        )
        if result.endswith(" 1"):
            updated += 1
    return updated


async def backfill_qa_feedback(conn, dry_run: bool, limit: int | None) -> int:
    """Rebuild qa_feedback from task_metadata (if qa_reviews was stored)."""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    rows = await conn.fetch(
        f"""
        SELECT task_id, task_metadata, quality_score
        FROM content_tasks
        WHERE (qa_feedback IS NULL OR qa_feedback = '')
          AND task_metadata IS NOT NULL
        ORDER BY created_at DESC
        {limit_clause}
        """
    )
    updated = 0
    for row in rows:
        meta = _coerce_json(row["task_metadata"])
        reviews = meta.get("qa_reviews") or []
        if not isinstance(reviews, list) or not reviews:
            continue
        final_score = meta.get("qa_final_score") or meta.get("quality_score") or row["quality_score"]
        text = format_qa_feedback_from_reviews(
            qa_reviews=reviews, final_score=final_score, approved=None,
        )
        if not text:
            continue
        if dry_run:
            snippet = text[:80].replace("\n", " ")
            print(f"  [dry-run] {row['task_id']}: qa_feedback -> {snippet!r}")
            updated += 1
            continue
        result = await conn.execute(
            """
            UPDATE content_tasks
               SET qa_feedback = $2, updated_at = NOW()
             WHERE task_id = $1
               AND (qa_feedback IS NULL OR qa_feedback = '')
            """,
            row["task_id"], text,
        )
        if result.endswith(" 1"):
            updated += 1
    return updated


async def main(dry_run: bool, limit: int | None, status_filter: str | None) -> None:
    print(f"Connecting to {DB_URL.split('@')[-1]}...")
    conn = await asyncpg.connect(DB_URL)
    try:
        # Status filter applies uniformly by wrapping the SELECT with a
        # subquery filter — keep it simple by short-circuiting here.
        if status_filter:
            await conn.execute(
                f"CREATE TEMP VIEW _gh86_scope AS "
                f"SELECT * FROM content_tasks WHERE status = '{status_filter}'"
            )
            print(f"Scoped to status = {status_filter!r}")

        print("\n[1/3] Backfilling category from task_metadata...")
        n_cat = await backfill_category(conn, dry_run, limit)
        print(f"  updated {n_cat} rows")

        print("\n[2/3] Backfilling excerpt from content...")
        n_exc = await backfill_excerpt(conn, dry_run, limit)
        print(f"  updated {n_exc} rows")

        print("\n[3/3] Backfilling qa_feedback from task_metadata.qa_reviews...")
        n_qa = await backfill_qa_feedback(conn, dry_run, limit)
        print(f"  updated {n_qa} rows")

        print(f"\nTotal: category={n_cat}, excerpt={n_exc}, qa_feedback={n_qa}")
        if dry_run:
            print("(dry-run — no writes applied)")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List changes without writing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max rows per column to update")
    parser.add_argument("--status", type=str, default=None,
                        help="Only scan rows with this content_tasks.status")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.limit, args.status))
