"""Migration 20260928_141858: clear the last_read_at stamps left by settings-admin lookups

ISSUE: Glad-Labs/poindexter#756 (settings read telemetry)

Until 2026-09-28 the settings-admin surfaces counted as reads.
``GET/POST/PUT /api/settings/{key}`` fetched the row through
``AdminDatabase.get_setting``, which recorded the key, so
``poindexter settings get <key>`` (the usual check after a ``settings set``)
stamped ``app_settings.last_read_at`` within a minute, through the worker's
flush job. ``ProbeZeroReaderSettingsJob`` lists only keys that were never
stamped, so a single lookup hid a key from it for good. The change that stops
those surfaces from recording also clears the stamps they left.

Which stamps: the ones that landed within five minutes after the row's last
value edit (``updated_at``) and have not moved in the day since. That is the
signature of "set it, then look at it". A key the running system reads at
least daily is restamped by the flush after each read (at most hourly, per
``settings_read_telemetry_min_restamp_seconds``), so its stamp never sits a
day old. On prod, 2026-09-28,
this matches 11 of 742 stamped keys. Every one was stamped 6-68 s after a
value edit and never again. Loki still holds three of those edits, and shows a
``GET /api/settings/<key>`` from the host just before each stamp:
``ragas_enabled``, ``compose_drift_on_demand_services`` and
``persona.presenter.portrait_url``.

Clearing a stamp only puts a key back to "no recorded reader". A key the
system does read is stamped again by the next flush after its next read. The
worst case is a live key read less than daily whose only recent read came
right after an edit: it appears in the advisory zero-reader finding until it
is read again.

A no-op on a fresh install. ``down()`` restores nothing: the stamps recorded
lookups, not reads, and the next real read writes a truthful one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Minutes after a value edit within which a stamp reads as the operator
# checking the edit (the worker flushes a recorded read within ~1 minute).
EDIT_WINDOW_MINUTES = 5
# How long the stamp must have sat unmoved: a key the system reads at least
# daily is restamped within that time.
UNMOVED_FOR_HOURS = 24

CLEAR_SQL = """
    UPDATE app_settings
       SET last_read_at = NULL
     WHERE last_read_at IS NOT NULL
       AND updated_at IS NOT NULL
       AND last_read_at >= updated_at
       AND last_read_at <= updated_at + ($1 * INTERVAL '1 minute')
       AND last_read_at < NOW() - ($2 * INTERVAL '1 hour')
 RETURNING key
"""


async def up(pool) -> None:
    """Clear the stamps whose only evidence is a settings-admin lookup."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(CLEAR_SQL, EDIT_WINDOW_MINUTES, UNMOVED_FOR_HOURS)
    keys = sorted(r["key"] for r in rows)
    logger.info(
        "Migration clear_last_read_at_stamps_left_by_settings_admin_lookups: "
        "cleared %d stamp(s)%s",
        len(keys),
        f": {', '.join(keys)}" if keys else "",
    )


async def down(pool) -> None:
    """One-way: the cleared stamps recorded lookups, not reads.

    There is nothing truthful to restore. The next real read of each key
    stamps it again.
    """
    del pool
    logger.info(
        "Migration clear_last_read_at_stamps_left_by_settings_admin_lookups: "
        "down() is a no-op (the next real read restamps each key)"
    )
