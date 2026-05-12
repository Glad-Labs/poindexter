"""Seed app_settings for the Discord bot reachability probe.

ISSUE: Glad-Labs/poindexter#435

Why: brain/discord_bot_probe.py needs DB-tunable cadence + dedup so an
operator can dial the polling frequency / alert volume without a redeploy.
Five rows, all idempotent.

* discord_bot_probe_enabled — master switch (default true; brain reads
  this every cycle).
* discord_bot_probe_interval_minutes — minutes between real HTTP
  round-trips. Probe is dispatched every brain cycle but skips between
  intervals (default 5).
* discord_bot_probe_timeout_seconds — httpx GET timeout (default 5).
* discord_bot_probe_dedup_hours — minimum hours between repeat 401/403
  pages while the token is broken (default 1 — once an hour).
* discord_bot_token — the actual bot token row, ``is_secret=true``.
  Already seeded by an earlier brain bring-up migration on most installs;
  the ``ON CONFLICT DO NOTHING`` here is just for fresh DBs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_ROWS = [
    (
        "discord_bot_probe_enabled",
        "true",
        "brain",
        "Master switch for brain/discord_bot_probe.py (poindexter#435). "
        "When false, the probe is skipped entirely.",
        False,
    ),
    (
        "discord_bot_probe_interval_minutes",
        "5",
        "brain",
        "Minutes between real Discord /users/@me round-trips. Probe is "
        "dispatched every brain cycle but skips inside the interval.",
        False,
    ),
    (
        "discord_bot_probe_timeout_seconds",
        "5",
        "brain",
        "httpx timeout for the Discord /users/@me round-trip.",
        False,
    ),
    (
        "discord_bot_probe_dedup_hours",
        "1",
        "brain",
        "Minimum hours between repeat alert_events writes while the Discord "
        "bot returns 401/403. Default 1h — one page per hour while broken.",
        False,
    ),
    (
        "discord_bot_token",
        "",
        "integrations",
        "Discord bot token (the same one Discord posts to channels with). "
        "Encrypted at rest via plugins.secrets. Used by the Discord bot "
        "process and brain/discord_bot_probe.py.",
        True,
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description, is_secret in _ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, $5, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description, is_secret,
            )
    logger.info(
        "Migration 20260512_215741: discord bot probe settings seeded "
        "(or already present).",
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
             WHERE key IN (
                 'discord_bot_probe_enabled',
                 'discord_bot_probe_interval_minutes',
                 'discord_bot_probe_timeout_seconds',
                 'discord_bot_probe_dedup_hours'
             )
            """
        )
    logger.info(
        "Migration 20260512_215741 down: discord bot probe tunables removed "
        "(discord_bot_token preserved — operator-owned secret).",
    )
