"""Migration 20260528_040918: repoint ``video_server_url`` from :9840 to :9837.

The slideshow video server (the one that
``services/video_service.py::generate_video_for_post`` POSTs
``image_paths`` / ``audio_path`` / ``ken_burns`` to) listens on
port 9837. The Wan 2.1 1.3B text-to-video model server listens on
port 9840 and only accepts a ``prompt`` field — image_paths +
audio_path get rejected as 422 Validation Error.

A previous migration (``20260526_214206``) repointed
``video_server_url`` to ``:9840`` thinking the wan-server was the
right backend for the slideshow path. It wasn't. The result:
every ``generate_video_for_post`` call has been 422'ing for ~a
week (caught by the ``media_reconciliation:media_drift`` brain
alert listing 4 specific posts missing videos — Glad-Labs/glad-labs-stack#649).

This migration repoints ``video_server_url`` back to ``:9837`` so
the slideshow path renders again. The Wan 2.1 server stays
available at ``:9840`` for the ``Wan21Provider`` plugin — which
already addresses it via ``wan_server_url`` /
``plugin.video_provider.wan2.1-1.3b.server_url`` and sends the
correct ``prompt`` body shape.

Idempotent: only updates rows where the value is the
known-bad ``:9840`` URL. Custom operator URLs are left alone.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_BAD_URL = "http://host.docker.internal:9840"
_FIXED_URL = "http://host.docker.internal:9837"


async def up(pool) -> None:
    """Repoint ``video_server_url`` from the wan-server port to the
    slideshow port. Only acts on the known-bad value so operator
    customisations don't get clobbered.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE app_settings
            SET value = $1, updated_at = NOW()
            WHERE key = 'video_server_url' AND value = $2
            """,
            _FIXED_URL,
            _BAD_URL,
        )
        logger.info(
            "Migration 20260528_040918_repoint_video_server_url_to_slideshow_port: %s",
            result,
        )


async def down(pool) -> None:
    """Restore the (broken) :9840 URL.

    Only useful if an operator intentionally swapped their slideshow
    server onto port 9840 — extremely unlikely. Provided for
    completeness; ``up`` is the action that matters.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
            SET value = $1, updated_at = NOW()
            WHERE key = 'video_server_url' AND value = $2
            """,
            _BAD_URL,
            _FIXED_URL,
        )
