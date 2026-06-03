"""Migration: reset page_views.id sequence past max(id) (poindexter#555).

The ``page_views`` table's ``id`` SERIAL sequence drifted **behind** the
real ``max(id)``: the ~150 historical rows were bulk-loaded with explicit
ids (the legacy cloud->local ``sync_page_views`` copy) without advancing
the sequence. So when first-party analytics was revived, the
``sync_cloudflare_analytics`` INSERT collided on the primary key::

    duplicate key value violates unique constraint "page_views_pkey"
    DETAIL:  Key (id)=(6) already exists.

``setval`` bumps the sequence to ``GREATEST(max(id), 1)`` so the next
INSERT receives a fresh id above every existing row. Idempotent (re-running
just re-sets it to the current max) and safe on a fresh/empty DB
(``max(id)`` -> NULL -> coalesced to 1).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        newval = await conn.fetchval(
            """
            SELECT setval(
                pg_get_serial_sequence('page_views', 'id'),
                GREATEST((SELECT COALESCE(MAX(id), 1) FROM page_views), 1),
                true
            )
            """
        )
    logger.info(
        "Migration reset_page_views_id_sequence: page_views_id_seq -> %s "
        "(next INSERT id = %s)",
        newval,
        (newval + 1) if newval is not None else "?",
    )


async def down(pool) -> None:
    # No-op: lowering a live sequence is unsafe (would re-introduce the
    # drift and risk new collisions). The corrected high-water value stays.
    logger.info(
        "Migration reset_page_views_id_sequence down: no-op "
        "(sequence intentionally left at its corrected value)"
    )
