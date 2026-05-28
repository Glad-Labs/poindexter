"""Scrub private-repo URLs from dev_diary posts (post-2026-05-20 recurrence).

ISSUE: 7 published dev_diary posts (most recently
``what-we-shipped-on-2026-05-28-efabd25c`` with 16 references) link to
``github.com/Glad-Labs/poindexter``, the private operator repo.
The 2026-05-20 strip migrations were a one-shot backfill; the writer
side kept regenerating links because the bundle fed the LLM raw PR
URLs and the prompt actively instructed inline-link citation. This
migration finishes the backfill on the 7 posts that re-leaked between
2026-05-20 and 2026-05-28; the matching code fix in
``services/atoms/narrate_bundle.py`` removes URLs from the bundle,
rewrites the prompt to direct plain-text ``(PR #N)`` citations, and
adds a post-LLM scrub as defense-in-depth.

The regex set mirrors ``_scrub_private_repo_refs`` in narrate_bundle:
inline markdown links → ``text (PR #N)`` / ``text (`sha7`)``,
autolinks + bare URLs → ``(PR #N)`` / ``(`sha7`)``, and remaining
``Glad-Labs/poindexter`` text mentions → ``Glad-Labs/poindexter``
(the public mirror — safe and accurate, the PR/commit numbers don't
match across repos but the text mention is fine).

Idempotent: the WHERE clause filters to rows still containing the
private repo path, and every regex_replace is no-op on already-clean
content.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the scrub to all posts still containing private-repo refs."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            r"""
            UPDATE posts
               SET content = regexp_replace(
                       regexp_replace(
                         regexp_replace(
                           regexp_replace(
                             regexp_replace(
                               regexp_replace(
                                 content,
                                 -- inline markdown link: [text](pull/N)
                                 '\[([^]]+)\]\(https?://github\.com/Glad-Labs/poindexter/pull/(\d+)\)',
                                 '\1 (PR #\2)', 'g'
                               ),
                               -- inline markdown link: [text](commit/SHA)
                               '\[([^]]+)\]\(https?://github\.com/Glad-Labs/poindexter/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*\)',
                               '\1 (`\2`)', 'g'
                             ),
                             -- autolink: <url-pull>
                             '<https?://github\.com/Glad-Labs/poindexter/pull/(\d+)>',
                             '(PR #\1)', 'g'
                           ),
                           -- autolink: <url-commit>
                           '<https?://github\.com/Glad-Labs/poindexter/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*>',
                           '(`\1`)', 'g'
                         ),
                         -- bare URL: pull
                         'https?://github\.com/Glad-Labs/poindexter/pull/(\d+)',
                         '(PR #\1)', 'g'
                       ),
                       -- bare URL: commit
                       'https?://github\.com/Glad-Labs/poindexter/commit/([0-9a-fA-F]{7})[0-9a-fA-F]*',
                       '(`\1`)', 'g'
                     ),
                   updated_at = NOW()
             WHERE content ~ 'Glad-Labs/poindexter'
            """,
        )
        # Second pass: rewrite any remaining bare text mentions of the
        # private repo path to the public mirror. Separate UPDATE so it
        # also fires on rows the first pass already touched.
        result2 = await conn.execute(
            r"""
            UPDATE posts
               SET content = regexp_replace(
                       content,
                       'Glad-Labs/poindexter',
                       'Glad-Labs/poindexter', 'g'
                     ),
                   updated_at = NOW()
             WHERE content ~ 'Glad-Labs/poindexter'
            """,
        )
        logger.info(
            "Migration scrub_private_repo_urls_dev_diary: pass1=%s pass2=%s",
            result, result2,
        )


async def down(pool) -> None:
    """No-op revert — restoring private URLs would re-leak the operator repo."""
    logger.info(
        "Migration scrub_private_repo_urls_dev_diary down: no-op "
        "(restoring private URLs would re-leak the operator repo)"
    )
