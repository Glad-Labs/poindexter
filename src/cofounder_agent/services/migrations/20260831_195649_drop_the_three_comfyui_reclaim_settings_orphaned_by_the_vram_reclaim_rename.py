"""Drop the three comfyui_reclaim_* settings orphaned by the vram_reclaim rename.

``comfyui_reclaim_settle_seconds``, ``comfyui_reclaim_min_freed_gb`` and
``comfyui_restart_cooldown_minutes`` were introduced by the ComfyUI hard-reclaim
rung (Glad-Labs/poindexter#1019) and renamed to ``vram_reclaim_*`` one PR later,
when the same verification was extended to wan / image-gen / stable-audio and
the keys stopped being ComfyUI-specific.

The rename removed them from ``settings_defaults.DEFAULTS``, so nothing seeds
them and nothing reads them — but the rows persist on any install that booted
between the two PRs (a few hours on the Glad Labs operator box). They are in
none of the other seed sources: never in ``0000_baseline.seeds.sql``, never in
``brain/seed_app_settings.json``. This migration is the whole fix.

Why bother with three dead rows: they read as live config and are wrong. An
operator tuning ``comfyui_restart_cooldown_minutes`` would believe they had
changed the restart cadence; they would have changed nothing, because the rung
now reads ``vram_reclaim_restart_cooldown_minutes``. That is the same trap
``20260828_005606`` documented for ``embedding_collapse_summary_model``, and
the reason not to leave a renamed key behind.

``ProbeZeroReaderSettingsJob`` would eventually flag them, but only after a
30-day grace and only if they survive its report cap — and they are this
session's own mess, so cleaning them up now beats leaving them to be
rediscovered.

ISSUE: Glad-Labs/poindexter#1019
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ORPHANED = (
    "comfyui_reclaim_settle_seconds",
    "comfyui_reclaim_min_freed_gb",
    "comfyui_restart_cooldown_minutes",
)


async def up(pool) -> None:
    """Delete the orphaned rows. Idempotent — a no-op where they never existed."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_ORPHANED),
        )
    logger.info("Migration drop comfyui_reclaim_* orphans: %s", result)


async def down(pool) -> None:
    """One-way: these keys have no reader to restore.

    Re-inserting them would recreate exactly the misleading dead config this
    migration removes. The live equivalents are the ``vram_reclaim_*`` keys,
    which ``settings_defaults`` seeds on every boot.
    """
    return
