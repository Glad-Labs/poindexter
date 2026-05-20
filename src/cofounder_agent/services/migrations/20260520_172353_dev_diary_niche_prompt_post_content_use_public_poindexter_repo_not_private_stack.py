"""Strip private glad-labs-stack URLs from dev_diary niche prompt + post content.

ISSUE: 13 published dev_diary posts (``what-we-shipped-on-...``) contained
104 outbound links to ``github.com/Glad-Labs/glad-labs-stack/pull/N`` and
``.../commit/SHA``. The repo is private — every one of those links is a
404 for the public site's readers.

Source: ``niches.dev_diary.writer_prompt_override`` (the dev_diary
narrate prompt) had two example format lines that explicitly told the
LLM to emit those URLs:

    [PR #N](https://github.com/Glad-Labs/glad-labs-stack/pull/N) by author
    - [`SHA[:7]`](https://github.com/Glad-Labs/glad-labs-stack/commit/SHA) <subject>

The LLM dutifully followed the format. Matt's direction (2026-05-20):
"point to public repo commits or just a 'see the work here: poindexter
repo' type of thing instead of links to the private repo."

Schema change: none. Data change:

1. ``niches.dev_diary.writer_prompt_override`` — drop the private-URL
   anchors from the format examples (plain-text ``PR #N`` /
   `` `SHA[:7]` ``) and replace the closing footer with one that points
   at the public mirror ``github.com/Glad-Labs/poindexter``.

2. ``posts.content`` for the 13 affected dev_diary posts — regex-replace
   the same two URL shapes to their plain-text equivalents. Then append
   a single "See the work" footer line to each affected post so readers
   still have one outbound link to the public repo. Idempotent: the
   WHERE clauses skip already-rewritten rows.

Why not rewrite to public-mirror commit SHAs: the public mirror is a
``git filter`` + force-push rebuild, so commit SHAs differ from the
private repo. PR numbers don't map at all (different PR set on each
side). Either path would need a manual mapping table; plain-text +
single repo pointer is the cleanest fix per Matt's "OR".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_PRIVATE_PR_PATTERN = (
    r'\[PR #(\d+)\]\(https://github\.com/Glad-Labs/glad-labs-stack/pull/\d+\)'
)
_PRIVATE_COMMIT_PATTERN = (
    r'\[`([0-9a-fA-F]{7,40})`\]\(https://github\.com/Glad-Labs/glad-labs-stack/commit/[0-9a-fA-F]+\)'
)


_NEW_PROMPT_FOOTER = (
    "_Auto-compiled by Poindexter from today's commits and PRs. "
    "[See the work: github.com/Glad-Labs/poindexter](https://github.com/Glad-Labs/poindexter)._"
)
_OLD_PROMPT_FOOTER = (
    "_Auto-compiled by Poindexter from today's commits and PRs._"
)


async def up(pool) -> None:
    """Apply the migration.

    Idempotent: prompt UPDATE only matches rows still containing the old
    private-URL examples; post content UPDATEs only match rows that
    still have a glad-labs-stack URL anywhere in body. Re-running once
    applied is a no-op.
    """
    async with pool.acquire() as conn:
        # ----- Niche prompt template -----
        # Drop the URL anchor from the PR-format example, drop the URL
        # anchor from the commit-format example, and swap the closing
        # footer to include the public-repo link.
        result_prompt = await conn.execute(
            """
            UPDATE niches
               SET writer_prompt_override = REPLACE(
                       REPLACE(
                           REPLACE(
                               writer_prompt_override,
                               '[PR #N](https://github.com/Glad-Labs/glad-labs-stack/pull/N) by author',
                               'PR #N by author'
                           ),
                           '- [`SHA[:7]`](https://github.com/Glad-Labs/glad-labs-stack/commit/SHA) <subject>',
                           '- `SHA[:7]` <subject>'
                       ),
                       $1,
                       $2
                   ),
                   updated_at = NOW()
             WHERE slug = 'dev_diary'
               AND writer_prompt_override IS NOT NULL
               AND writer_prompt_override LIKE '%glad-labs-stack%'
            """,
            _OLD_PROMPT_FOOTER,
            _NEW_PROMPT_FOOTER,
        )

        # ----- Post content backfill -----
        # Regex-replace private PR + commit URLs in published post bodies,
        # then append a one-line "See the work" footer to each affected
        # post if it doesn't already have one (idempotent guard).
        result_posts = await conn.execute(
            """
            UPDATE posts
               SET content = regexp_replace(
                                 regexp_replace(content, $1, 'PR #\\1', 'g'),
                                 $2, '`\\1`', 'g'
                             )
                          || CASE
                                 WHEN content LIKE '%See the work: github.com/Glad-Labs/poindexter%'
                                     THEN ''
                                 ELSE E'\\n\\n_[See the work: github.com/Glad-Labs/poindexter](https://github.com/Glad-Labs/poindexter)_'
                             END,
                   updated_at = NOW()
             WHERE status = 'published'
               AND content ~ 'github\\.com/Glad-Labs/glad-labs-stack/(pull|commit)/'
            """,
            _PRIVATE_PR_PATTERN,
            _PRIVATE_COMMIT_PATTERN,
        )

        logger.info(
            "Migration dev_diary_public_repo_links: niche_prompt=%s posts=%s",
            result_prompt, result_posts,
        )


async def down(pool) -> None:
    """Revert is a no-op.

    Restoring private URLs would re-introduce 404s for every new
    dev_diary post; the public-friendly form is strictly better.
    Reconstructable from git history (``baseline.seeds.sql`` carries
    the original format strings) if needed.
    """
    logger.info(
        "Migration dev_diary_public_repo_links down: no-op (forward is strictly safer)"
    )
