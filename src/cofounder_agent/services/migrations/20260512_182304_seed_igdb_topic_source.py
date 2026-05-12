"""Seed the IGDB topic source — opt-in, requires Twitch credentials.

Adds:

1. ``plugin.topic_source.igdb`` — PluginConfig row (enabled=false by
   default so the source stays dormant on fresh installs until
   credentials are configured).
2. ``igdb_twitch_client_id`` — Twitch app client id (plain).
3. ``igdb_twitch_client_secret`` — Twitch app client secret
   (``is_secret=true``, encrypted at rest via plugins.secrets).

After this migration runs, the operator:

1. Goes to https://dev.twitch.tv/console/apps, registers a new app.
2. Copies the client ID + client secret into the rows above:
   ``poindexter set-setting igdb_twitch_client_id '<your-id>'``
   ``poindexter set-setting igdb_twitch_client_secret '<your-secret>'``
3. Flips ``plugin.topic_source.igdb.enabled`` to ``true``.

The IGDBSource (services/topic_sources/igdb.py) reads both credentials
via plugins.secrets, exchanges them for a Twitch OAuth bearer token,
and queries IGDB for recently-released indie games (theme id = 32).

Idempotent — ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_PLUGIN_CONFIG = {
    "enabled": False,
    "interval_seconds": 3600 * 6,  # poll every 6 hours
    "config": {
        "lookback_days": 14,
        "limit": 20,
        "theme_id": 32,  # IGDB Indie
    },
}


_DESCRIPTION_PLUGIN = (
    "IGDB indie-games topic source. Disabled by default — set Twitch "
    "credentials (igdb_twitch_client_id + igdb_twitch_client_secret), "
    "then flip enabled=true. Source code: "
    "services/topic_sources/igdb.py."
)

_DESCRIPTION_CLIENT_ID = (
    "Twitch app client ID for IGDB access. Get it from "
    "https://dev.twitch.tv/console/apps. Public — not a secret. "
    "Used by services.topic_sources.igdb.IGDBSource."
)

_DESCRIPTION_CLIENT_SECRET = (
    "Twitch app client SECRET for IGDB access. is_secret=true → "
    "encrypted at rest via plugins.secrets. Get it from "
    "https://dev.twitch.tv/console/apps. Used by "
    "services.topic_sources.igdb.IGDBSource."
)


async def run_migration(conn) -> None:
    # 1. Plugin config row.
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES (
            'plugin.topic_source.igdb',
            $1,
            'plugins',
            $2,
            false,
            true
        )
        ON CONFLICT (key) DO NOTHING
        """,
        json.dumps(_PLUGIN_CONFIG),
        _DESCRIPTION_PLUGIN,
    )

    # 2. Public client ID — operator fills in.
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES (
            'igdb_twitch_client_id',
            '',
            'integrations',
            $1,
            false,
            true
        )
        ON CONFLICT (key) DO NOTHING
        """,
        _DESCRIPTION_CLIENT_ID,
    )

    # 3. Client SECRET — is_secret=true; plugins.secrets will encrypt
    # the row on first non-empty set. Empty string is left plaintext.
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES (
            'igdb_twitch_client_secret',
            '',
            'integrations',
            $1,
            true,
            true
        )
        ON CONFLICT (key) DO NOTHING
        """,
        _DESCRIPTION_CLIENT_SECRET,
    )

    logger.info(
        "20260512_182304: IGDB topic source seeded (disabled). Set "
        "igdb_twitch_client_id + igdb_twitch_client_secret, then flip "
        "plugin.topic_source.igdb.enabled to 'true' to activate.",
    )
