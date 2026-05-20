"""Corrective: strip autolink-style ``<url>`` glad-labs-stack refs from dev_diary posts.

ISSUE: The prior migration 20260520_172353 only matched markdown-link
style ``[text](url)`` — but the LLM had emitted Markdown autolinks
``<https://github.com/Glad-Labs/glad-labs-stack/...>``. Survey post-merge:
56 autolinks + 1 markdown link + 0 bare URLs across the 12 affected
posts, so the prior backfill caught 1/57 (~2%). The "See the work"
footer was appended successfully (the WHERE clause hit on the autolinks
too), but the URL substitution was a no-op for the autolink shape.

This corrective pass:

- Replaces ``<https://github.com/Glad-Labs/glad-labs-stack/pull/N>`` with
  the plain-text ``PR #N`` representation matching what the original
  migration intended.
- Replaces ``<https://github.com/Glad-Labs/glad-labs-stack/commit/SHA>``
  with the plain-text `` `SHA[:7]` `` form (truncating to the standard
  short-SHA length so the result reads naturally).
- Also reruns the markdown-link patterns from the prior migration so this
  one is self-contained for fresh installs that somehow ended up in a
  partial state. Idempotent: the WHERE clause skips already-cleaned rows.

No new schema, no niche-prompt changes — the niche prompt was already
correctly updated by 20260520_172353, this is pure data cleanup.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the corrective backfill.

    Idempotent: WHERE clause filters to rows that still contain the
    private-repo URL; subsequent runs match zero rows once cleaned.
    """
    async with pool.acquire() as conn:
        # Four passes — autolink-PR, autolink-commit, markdown-PR,
        # markdown-commit. Separate calls are easier to read than a
        # quadruple-nested regexp_replace.
        result = await conn.execute(
            r"""
            UPDATE posts
               SET content = regexp_replace(
                                 regexp_replace(
                                     regexp_replace(
                                         regexp_replace(
                                             content,
                                             '<https?://github\.com/Glad-Labs/glad-labs-stack/pull/(\d+)>',
                                             'PR #\1', 'g'
                                         ),
                                         '<https?://github\.com/Glad-Labs/glad-labs-stack/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*>',
                                         '`\1`', 'g'
                                     ),
                                     '\[PR #(\d+)\]\(https?://github\.com/Glad-Labs/glad-labs-stack/pull/\d+\)',
                                     'PR #\1', 'g'
                                 ),
                                 '\[`([0-9a-fA-F]{7})[0-9a-fA-F]*`\]\(https?://github\.com/Glad-Labs/glad-labs-stack/commit/[0-9a-fA-F]+\)',
                                 '`\1`', 'g'
                             ),
                   updated_at = NOW()
             WHERE status = 'published'
               AND content ~ 'github\.com/Glad-Labs/glad-labs-stack/(pull|commit)/'
            """,
        )
        logger.info(
            "Migration strip_remaining_autolink_glad_labs_stack: %s",
            result,
        )


async def down(pool) -> None:
    """No-op revert — private URLs in published content were never the
    desired state; reintroducing them would re-break 404s for readers.
    """
    logger.info(
        "Migration strip_remaining_autolink_glad_labs_stack down: no-op"
    )
