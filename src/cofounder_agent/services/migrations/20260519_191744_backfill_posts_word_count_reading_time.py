"""Backfill ``posts.word_count`` + ``posts.reading_time`` for legacy rows.

ISSUE: Glad-Labs/glad-labs-stack#480 jank-audit finding #2.

Why: ``posts.word_count`` and ``posts.reading_time`` columns have existed
on the table since the baseline (both ``integer`` NULL-allowed) but the
INSERT in ``content_db.create_post`` never listed them, so every post in
production has both columns NULL despite roughly 20 different places in
production code computing ``len(content.split())`` on the same content.

Same dead-seam class as ``posts.media_to_generate`` before #482 — the
column existed, looked editable from the operator's perspective, but
nothing populated it. The companion code change in this PR adds both
columns to ``content_db.create_post``'s INSERT so new posts carry the
right values from the start. This migration fixes the legacy rows.

Formula (matches ``seo_content_generator.calculate_reading_time`` —
the canonical formula already in production):

  word_count    = number of whitespace-separated tokens in posts.content
  reading_time  = max(1, round(word_count / 200))   # 200 wpm

Note on the 200 wpm choice: ``seo_content_generator.py:244`` has been
using 200 wpm since the SEO pipeline shipped; matching it here so the
column and the dataclass produced by the SEO stage agree post-#492.
225 wpm is the more common industry default but switching it now would
make legacy ``reading_time`` values inconsistent with whatever the SEO
stage was computing for them at publish time. Pinning at 200.

Idempotent — UPDATE is gated on ``word_count IS NULL`` so an operator
who has hand-tuned a row (e.g., for a paywalled excerpt with a
manually-curated read time) doesn't get their value stomped on replay.
``content IS NOT NULL`` guards the edge case of in-flight draft rows
that don't yet have content.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Compute both columns in a single pass. PostgreSQL's
            # ``array_length(string_to_array(content, NULL), 1)`` counts
            # whitespace-separated tokens the same way Python's
            # ``str.split()`` would, including collapsing runs of
            # whitespace. ``regexp_split_to_array(content, '\s+')`` is
            # the equivalent that handles the leading-whitespace edge
            # case cleanly.
            result = await conn.execute(
                """
                UPDATE posts
                   SET word_count = sub.wc,
                       reading_time = GREATEST(1, ROUND(sub.wc::numeric / 200)::int)
                  FROM (
                      SELECT id,
                             COALESCE(
                                 array_length(
                                     regexp_split_to_array(
                                         trim(content), '\\s+'
                                     ), 1
                                 ),
                                 0
                             ) AS wc
                        FROM posts
                       WHERE word_count IS NULL
                         AND content IS NOT NULL
                  ) sub
                 WHERE posts.id = sub.id
                """
            )

    # asyncpg's ``execute`` returns "UPDATE <n>" for an UPDATE
    # command-tag; log the count for the operator-visibility trail.
    logger.info(
        "[migration] posts.word_count + posts.reading_time backfilled: %s",
        result,
    )
