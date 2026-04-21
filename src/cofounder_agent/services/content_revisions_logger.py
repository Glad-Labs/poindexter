"""
Content revisions logger — write-through helper for the content_revisions
table (gitea#271 Phase 3.A2 / GH#27).

Every pipeline stage that mutates ``context["content"]`` should call
``log_revision`` so the feedback loop sees the full QA → rewrite trajectory,
not just the final published draft. The helper is safe to call from any
async context, never raises, and auto-numbers revisions per task.

Consumers read from content_revisions to answer:
- Which QA iterations actually changed the content? (diff revision N-1 → N)
- Which models do the biggest lifts on bad drafts?
- Is the final_score ever *lower* than an earlier revision? (QA over-correcting)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def log_revision(
    pool: Any,
    *,
    task_id: str,
    content: str,
    title: str | None = None,
    change_type: str,
    change_summary: str | None = None,
    model_used: str | None = None,
    quality_score: float | None = None,
    post_id: str | None = None,
) -> int | None:
    """Append a row to content_revisions for ``task_id``.

    Auto-selects the next revision_number. Never raises — the feedback
    pipeline is additive observability, not load-bearing for content.

    Returns:
        The revision_number that was written, or None on failure.
    """
    if not task_id or not content:
        return None
    try:
        async with pool.acquire() as conn:
            next_rev = await conn.fetchval(
                "SELECT COALESCE(MAX(revision_number), 0) + 1 "
                "FROM content_revisions WHERE task_id = $1",
                str(task_id),
            )
            word_count = len(content.split()) if content else 0
            await conn.execute(
                """
                INSERT INTO content_revisions (
                    task_id, post_id, revision_number, content, title,
                    word_count, quality_score, change_summary, change_type,
                    model_used
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                str(task_id),
                post_id,
                next_rev,
                content,
                title,
                word_count,
                quality_score,
                change_summary,
                change_type,
                model_used,
            )
            return int(next_rev)
    except Exception as e:
        logger.debug("[content_revisions] log_revision failed: %s", e)
        return None
