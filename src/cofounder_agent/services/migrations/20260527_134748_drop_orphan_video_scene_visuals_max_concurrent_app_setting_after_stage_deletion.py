"""Drop orphan ``video_scene_visuals_max_concurrent`` app_setting.

ISSUE: Glad-Labs/glad-labs-stack#238 (cycle-3 orphan-stage deletion).

Background
----------
Migration ``20260516_091017_seed_scene_visuals_max_concurrent.py`` seeded an
``app_settings`` row whose only consumer was
``services/stages/scene_visuals.py``. That stage was deleted in cycle-3
cleanup (#238 — never referenced by any registered pipeline template
nor any production caller; the live video pipeline runs through
``services/video_service.py`` + the video-provider plugins).

This migration removes the now-orphan row so ``poindexter setup --check``
stops surfacing it as "configured" when nothing reads it. Idempotent —
``DELETE`` on a non-existent row is a no-op.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Delete the orphan row. No-op when the row is already gone."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM app_settings
             WHERE key = 'video_scene_visuals_max_concurrent'
            """
        )
        logger.info(
            "Migration 20260527_134748: drop video_scene_visuals_max_concurrent — %s",
            result,
        )


async def down(pool) -> None:
    """Restore the orphan row (matches the original seed exactly).

    Down is provided for symmetry — operators who hand-write a
    `scene_visuals` substitute stage in their own deployment can revert
    this and resurrect the setting. The default value (``1``) matches
    the original seed's sequential-by-default behaviour.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES (
                'video_scene_visuals_max_concurrent',
                '1',
                'pipeline',
                'Max concurrent scene resolutions (legacy scene_visuals stage). '
                'Restored by migration 20260527_134748 down().',
                false,
                true
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260527_134748 down: video_scene_visuals_max_concurrent restored.",
        )
