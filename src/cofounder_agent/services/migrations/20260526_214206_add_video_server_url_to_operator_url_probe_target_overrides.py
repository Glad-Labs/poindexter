"""Migration 20260526_214206: add video_server_url to operator_url_probe overrides

Why: the 2026-05-26 repoint of ``video_server_url`` from ``:9837`` to
``:9840`` (the wan-server's actual port) closed the connection-refused
alert but exposed the next layer — the wan-server is API-only and
returns HTTP 404 for ``GET /``. ``brain.operator_url_probe`` flagged
that as ``surface unreachable`` even though the service is healthy.

The probe already has a per-URL override mechanism
(``operator_url_probe_target_overrides``) for exactly this case —
``storage_public_url`` and the IndexNow / Google sitemap ping URLs
all use it because they're also API-only with no root index. This
migration adds ``video_server_url`` to the same map.

Idempotent via ``jsonb_set`` — re-running adds the entry if missing
and otherwise no-ops without clobbering other entries.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_VIDEO_SERVER_URL_OVERRIDE = {
    "alive_codes": "200-499",
    "method": "HEAD",
    "reason": (
        "poindexter-wan-server (video generation, port 9840) has no "
        "root index — bare HEAD/GET / returns 404. Real callers POST "
        "to /generate; service-alive means the host answers at all. "
        "5xx + network errors stay real."
    ),
}


async def up(pool) -> None:
    """Add ``video_server_url`` to the probe-overrides JSON map.

    ``jsonb_set`` with ``create_missing=true`` (default) inserts the
    new key when absent and overwrites it when present — idempotent
    either way. The row itself was seeded by the 20260510_152609
    migration; if it doesn't exist (shouldn't happen on any DB that
    ran that migration first) the WHERE matches nothing and we no-op.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
            SET value = jsonb_set(
                value::jsonb,
                '{video_server_url}',
                $1::jsonb,
                true
            )::text
            WHERE key = 'operator_url_probe_target_overrides'
            """,
            json.dumps(_VIDEO_SERVER_URL_OVERRIDE),
        )
        logger.info(
            "Migration 20260526_214206: video_server_url override "
            "added to operator_url_probe_target_overrides"
        )


async def down(pool) -> None:
    """Remove the ``video_server_url`` key from the override map.

    The probe will fall back to the global ``200-399`` alive default
    for that URL, which means the alert resumes if the wan-server's
    root path still returns 404.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
            SET value = (value::jsonb - 'video_server_url')::text
            WHERE key = 'operator_url_probe_target_overrides'
            """
        )
        logger.info(
            "Migration 20260526_214206 down: video_server_url override "
            "removed from operator_url_probe_target_overrides"
        )
