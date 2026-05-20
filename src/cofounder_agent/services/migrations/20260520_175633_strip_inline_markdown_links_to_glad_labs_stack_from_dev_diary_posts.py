"""Final corrective: strip inline-markdown-link refs to glad-labs-stack.

ISSUE: After 20260520_172353 (PR-format only) and 20260520_174023
(autolink ``<url>`` form), one post still contained an inline markdown
link of the form ``[noun](https://github.com/Glad-Labs/glad-labs-stack/
pull/N)`` where the bracket text is an arbitrary code noun, NOT
``PR #N``. The prior regex required ``PR #N`` as the bracket text,
so this one slipped through.

Example (post ``what-we-shipped-on-2026-05-19-4a8eabdb``):

    The [alert_events](https://github.com/Glad-Labs/glad-labs-stack/pull/478)
    showed ten identical warnings...

This migration catches the generic shape: any markdown link
``[<text>](glad-labs-stack/pull/N)`` becomes ``<text> (PR #N)`` —
preserving both the inline noun and the PR reference, dropping the
404 URL. Same for commit links.

Idempotent (WHERE clause filters to rows still containing the URL).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the final corrective.

    The previous two migrations handled standalone ``[PR #N](url)`` and
    autolink ``<url>`` shapes. This one handles the inline-text shape
    where the bracket label is arbitrary.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            r"""
            UPDATE posts
               SET content = regexp_replace(
                                 regexp_replace(
                                     content,
                                     '\[([^]]+)\]\(https?://github\.com/Glad-Labs/glad-labs-stack/pull/(\d+)\)',
                                     '\1 (PR #\2)', 'g'
                                 ),
                                 '\[([^]]+)\]\(https?://github\.com/Glad-Labs/glad-labs-stack/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*\)',
                                 '\1 (`\2`)', 'g'
                             ),
                   updated_at = NOW()
             WHERE status = 'published'
               AND content ~ 'github\.com/Glad-Labs/glad-labs-stack/(pull|commit)/'
            """,
        )
        logger.info(
            "Migration strip_inline_markdown_glad_labs_stack: %s", result,
        )


async def down(pool) -> None:
    """No-op revert — restoring private URLs would re-introduce 404s."""
    logger.info(
        "Migration strip_inline_markdown_glad_labs_stack down: no-op"
    )
