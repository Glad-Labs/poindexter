"""Seed app_settings for bounded scene-visuals concurrency.

ISSUE: Glad-Labs/poindexter#164

Why: ``services/stages/scene_visuals.py`` previously resolved scenes
strictly sequentially to avoid SDXL VRAM contention. Bounded concurrency
(asyncio.Semaphore) lets operators with VRAM headroom resolve multiple
scenes in parallel — 2-3× faster video pipeline on a 6-scene script.

Default 1 preserves the prior one-at-a-time behavior. Raising the cap is
an explicit operator opt-in once they've watched the per-scene timing
audit_log rows (``video.scene_visual_resolved``) and confirmed VRAM
headroom on their box.

Idempotent — ``ON CONFLICT DO NOTHING`` so the migration is safe to
replay on a database where the row was already created by hand.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES (
                'video_scene_visuals_max_concurrent',
                '1',
                'pipeline',
                'Max concurrent scene resolutions in video.scene_visuals '
                'stage (poindexter#164). Default 1 = sequential (prior '
                'behavior). Raise carefully — each parallel slot can issue '
                'an SDXL render, so the safe ceiling is ~VRAM / per-render '
                'cost. Watch the video.scene_visual_resolved audit rows '
                'for per-scene elapsed_s before bumping past 2.',
                false,
                true
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration 20260516_091017: video_scene_visuals_max_concurrent "
        "seeded (or already present).",
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
             WHERE key = 'video_scene_visuals_max_concurrent'
            """
        )
    logger.info(
        "Migration 20260516_091017 down: video_scene_visuals_max_concurrent "
        "row removed.",
    )
