"""Migration 20260710_193000: drop the retired ``video_server_url`` setting.

Convergence step for the ``:9837`` host-slideshow retirement. The legacy
``video_service.generate_video_for_post`` lane + the ``scripts/video-server.py``
host process it POSTed to were deleted; the live media pipeline renders through
``video_renderers/shot_list_renderer`` (image-gen / Wan i2v clips assembled by
the FFmpeg compositor), which never reads ``video_server_url``. The default was
dropped from ``settings_defaults`` and ``0000_baseline.seeds.sql`` so fresh
installs never seed it — this migration removes the orphaned row (and its
now-dangling ``operator_url_probe_target_overrides`` entry) from existing
installs.

  * Fresh installs — the key was never seeded, so both statements no-op.
  * Existing installs (prod, seeded from the pre-retirement baseline) — the
    ``video_server_url`` row is deleted and the probe-override key is stripped
    from the JSON, converging prod to the new schema.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'video_server_url'"
        )
        # Strip the orphaned probe override for the deleted key. ``jsonb - 'key'``
        # returns the value unchanged when the key is absent, so this is a
        # write-back no-op on installs already converged (incl. fresh ones).
        await conn.execute(
            """
            UPDATE app_settings
            SET value = ((value::jsonb) - 'video_server_url')::text
            WHERE key = 'operator_url_probe_target_overrides'
            """
        )
    logger.info(
        "drop_retired_video_server_url_setting up: removed the video_server_url "
        "app_setting + its operator_url_probe override (no-op where already absent)"
    )


async def down(pool) -> None:
    # No-op: video_server_url is retired (the :9837 slideshow lane it addressed
    # was deleted). Re-adding it would only restore an orphaned row with no reader.
    logger.info(
        "drop_retired_video_server_url_setting down: no-op (refusing to re-add a retired setting)"
    )
