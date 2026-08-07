"""Migration 20260807_183202: drop the HelloTap demo embedding row.

ISSUE: Glad-Labs/poindexter#989 follow-up.

``HelloTap`` (``plugins/samples/hello_tap.py``) is a reference
implementation for third-party Tap authors — the smallest possible Tap. It
yields one static Document, ``"hello from the HelloTap sample plugin"``,
which lands in ``embeddings`` as ``source_table='samples'`` and then never
changes again.

On Matt's install that single row sat untouched from 2026-06-05, which made
it permanently the stalest source on the corpus-freshness panel added in
poindexter#989 — a fake red that trains an operator to ignore the panel.
Fabricated content also has no business in a real corpus
(``feedback_no_dummy_data``).

Deleting alone would not stick: the tap runs on every hourly sidecar cycle,
finds no stored hash, and re-embeds the same document. The paired change is
``settings_defaults.py``'s new ``plugin.tap.hello`` default of
``{"enabled": false}``, which the boot-time seeder applies right after this
migration runs. The tap stays registered so authors can still flip it on.

Scoped to the sample's own ``source_id`` rather than
``source_table='samples'`` wholesale, so an operator who deliberately files
their own rows under that table keeps them.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SOURCE_ID = "samples/hello/greeting"


async def up(pool) -> None:
    """Apply the migration. Idempotent: re-running deletes nothing."""
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            """
            WITH removed AS (
                DELETE FROM embeddings
                 WHERE source_table = 'samples'
                   AND source_id = $1
             RETURNING 1
            )
            SELECT count(*) FROM removed
            """,
            _SOURCE_ID,
        )
        logger.info(
            "Migration %s: dropped %s HelloTap demo embedding row(s); the tap "
            "is disabled by default so it will not be re-ingested.",
            __name__,
            deleted,
        )


async def down(pool) -> None:
    """One-way — nothing of value to restore.

    The row is a static sample string. Re-enabling ``plugin.tap.hello``
    regenerates it byte-for-byte on the next tap run.
    """
    return
