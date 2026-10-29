"""Migration 20261029_122458: move topic notes out of the post "In brief" summary.

Publishing copied the task's metadata onto the post, including the topic
source's own note under ``summary`` ("Google autocomplete suggests this
query..."). On a post, ``metadata->>'summary'`` is the "In brief" text, and
``GeneratePostSummariesJob`` fills only posts where it is empty, so every
topic-sourced post showed the note as its summary: 77 on 2026-10-29, one of
them quoting the claim its own article disputes. ``publish_service`` now
stores the note as ``topic_summary``; this moves it the same way on posts
already published, so the job writes real summaries for them.

A topic-sourced post is one whose metadata carries ``source`` (the topic
source). If the job had written such a post's summary, it moves too and the job
writes it again; that costs one model call, never a wrong summary.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UP_SQL = """
    UPDATE posts
       SET metadata = (metadata - 'summary') || jsonb_build_object('topic_summary', metadata->'summary')
     WHERE metadata ? 'source'
       AND COALESCE(metadata->>'summary', '') <> ''
       AND NOT metadata ? 'topic_summary'
"""


async def up(pool) -> None:
    """Move topic notes to topic_summary. Re-runnable: moved rows no longer match."""
    async with pool.acquire() as conn:
        tag = await conn.execute(_UP_SQL)
    logger.info("move_topic_notes_out_of_the_post_in_brief_summary: %s", tag)


async def down(pool) -> None:
    """No-op: the summaries the job has since written are better than the notes."""
    logger.info("move_topic_notes_out_of_the_post_in_brief_summary: down is a no-op")
