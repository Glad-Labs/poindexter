"""Migration 20260604_040000: seed LiveKit creds into app_settings (#1000).

ISSUE: Glad-Labs/glad-labs-stack#1000

The LiveKit HS256 secret currently lives in several env/file copies that must
stay in lockstep (`.env`, `bootstrap.toml`, `~/.claude.json`). On 2026-06-02 a
rotation missed the `~/.claude.json` copy → the host voice-bridge minted tokens
with the stale secret → desync. This moves the *minters* (our code: the
always-on bot, `/voice/join`, the bridge) to read the key/secret from
`app_settings` first, env as fallback — so rotation becomes DB-first and the
scattered copies collapse.

The LiveKit **server** binary still reads its key from env/yaml (third-party,
can't read our DB), so the env copy is irreducible — this is the fallback the
minters use until the DB rows are populated.

Seeds both keys `is_secret=true` with an **empty** value: an empty value means
the minters fall back to env (current behaviour, zero change), so applying this
migration is a no-op until an operator populates the rows (via
`plugins.secrets.set_secret`, which encrypts) from the current env/bootstrap
value. `ON CONFLICT DO NOTHING` so a live value is never clobbered.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed empty LiveKit cred rows (env fallback stays active until populated)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES
                (
                    'livekit_api_key',
                    '',
                    'voice',
                    'LiveKit API key the token minters use (#1000). Empty = fall '
                    'back to the LIVEKIT_API_KEY env var. DB-first lets rotation '
                    'update one place instead of every minter copy.',
                    true,
                    true
                ),
                (
                    'livekit_api_secret',
                    '',
                    'voice',
                    'LiveKit HS256 API secret the token minters sign JWTs with '
                    '(#1000). Empty = fall back to the LIVEKIT_API_SECRET env '
                    'var. Populate via plugins.secrets.set_secret (encrypts).',
                    true,
                    true
                )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info("Migration seed_livekit_creds_app_settings_1000: applied")


async def down(pool) -> None:
    """Drop the two LiveKit cred keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN ('livekit_api_key', 'livekit_api_secret')
            """
        )
        logger.info("Migration seed_livekit_creds_app_settings_1000 down: reverted")
