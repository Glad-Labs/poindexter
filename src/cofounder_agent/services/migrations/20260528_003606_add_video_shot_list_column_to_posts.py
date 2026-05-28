"""Migration 20260528_003606: add ``posts.video_shot_list jsonb`` column.

Foundation for the director-driven video composition system (Glad-Labs
issue #649, design in ``docs/architecture/video-composition.md``).

The new ``generate_video_shot_list`` pipeline stage produces a JSON
shot list — an ordered sequence of shots with target durations, intents,
content sources (SDXL / Pexels / Wan2.1 / Ken Burns), and prompts. The
shot list is auditable + regeneratable independently of the renderer,
which is the design's key seam.

This PR only LANDS the column + populates it from the new stage. The
renderer keeps using its (currently broken) hardcoded path; PR 2 in the
sequenced plan wires the shot list into actual video assembly. The
intentional gap lets the operator review director output for a few real
posts before committing the renderer to it.

Why JSONB
=========
The shot list schema is shaped loosely — shot count varies, fields
vary per source type (``prompt`` for SDXL/Wan21, ``query`` for Pexels,
``kenburns_zoom`` for the Ken Burns wrapper). JSONB lets us evolve the
schema without ALTER TABLE per field. The Pydantic model in
``schemas/video_shot_list.py`` is the SOURCE OF TRUTH; the DB column
stores whatever the model serializes.

Idempotent
==========
``ADD COLUMN IF NOT EXISTS`` so replay on the prod DB no-ops. Fresh
CI DB picks up the column on first apply. NULL is the not-yet-generated
state — existing posts default to NULL and the renderer treats that as
"director hasn't run, fall back to legacy path".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE posts
                ADD COLUMN IF NOT EXISTS video_shot_list jsonb
            """,
        )
        logger.info(
            "Migration 20260528_003606_add_video_shot_list_column_to_posts: applied",
        )


async def down(pool) -> None:
    """Revert: drop the column. Director output is regeneratable from
    the source post + podcast script, so this is reversible at the cost
    of one director-LLM call per existing post."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE posts DROP COLUMN IF EXISTS video_shot_list",
        )
