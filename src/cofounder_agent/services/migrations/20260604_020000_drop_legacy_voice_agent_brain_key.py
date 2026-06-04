"""Migration 20260604_020000: drop the retired legacy voice_agent_brain key.

ISSUE: Glad-Labs/glad-labs-stack#1006 (voice settings cleanup)

``voice_agent_brain`` was the original brain-mode key (seeded 2026-05-05). It
was superseded by ``voice_agent_brain_mode`` and kept only as a soft-transition
fallback in ``_resolve_brain_mode``. That fallback is now removed (the resolver
reads only the canonical ``voice_agent_brain_mode``), so the legacy row is dead
config — its value was never read while the canonical key is set. Drop it.

Idempotent: ``DELETE`` no-ops if the row is already gone (fresh installs that
post-date the legacy seed). ``down`` re-creates it (value ``ollama``, the
documented base default) for reversibility.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Delete the dead legacy ``voice_agent_brain`` key."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'voice_agent_brain'",
        )
    logger.info("Migration drop_legacy_voice_agent_brain_key: applied")


async def down(pool) -> None:
    """Re-seed the legacy key (reversibility) — no consumer reads it anymore."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_active)
            VALUES (
                'voice_agent_brain',
                'ollama',
                'general',
                'Retired legacy brain key — superseded by voice_agent_brain_mode '
                'and no longer read (#1006).',
                true
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info("Migration drop_legacy_voice_agent_brain_key down: reverted")
