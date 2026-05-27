"""Migration 20260527_034229: seed app_settings.sdxl_enabled = true

Why: PR #603 introduced the ``sdxl_enabled`` master toggle in
``services/stages/source_featured_image.py`` (default ``true`` when
the row is missing). The 2026-05-27 architecture audit flagged that
operators can't toggle SDXL via the standard settings UI / API
because the row was never seeded — code-default behaviour is
correct, but the knob is invisible.

Adds the row to baseline + ensures Matt's live DB carries it after
this migration runs. Idempotent via ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Insert ``sdxl_enabled`` app_settings row if absent."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES (
                'sdxl_enabled', 'true', 'general',
                'Master toggle for the SDXL featured/inline image pipeline. '
                'When false, source_featured_image skips the SDXL HTTP server '
                'and uses Pexels-only. Operators flip to false during SDXL '
                'maintenance windows. Read by '
                'services/stages/source_featured_image.py (PR #603).',
                'f', 't'
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
        if result.endswith(" 1"):
            logger.info(
                "Migration 20260527_034229: seeded sdxl_enabled=true",
            )


async def down(pool) -> None:
    """Remove the seeded row (Matt's operator-set value would be lost
    on re-up; intended only for full rollback)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'sdxl_enabled'"
        )
