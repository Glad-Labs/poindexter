"""Migration 20260921_190000: rename stale compose service names in settings.

Two CSV settings name compose **services**, and each carried a name that
no longer matches any service, so the entry silently did nothing:

- ``game_mode_parked_services`` listed ``stable-audio``; the compose
  service is ``stable-audio-server`` (container ``poindexter-stable-audio``,
  which is where the short name came from). Game mode has therefore never
  parked stable-audio, and it kept its VRAM through every game.
- ``compose_drift_on_demand_services`` listed ``sdxl-server``, the name
  before the image-gen rename. The code default was updated to
  ``image-gen-server`` but existing rows were not, so an idle image-gen
  server that shuts itself down reads as a crash.

This rewrites only those two tokens and leaves every other entry, order
included, untouched. A row that doesn't contain the stale name is a no-op,
as is an install without the row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# setting key -> {stale service name: current service name}
_RENAMES: dict[str, dict[str, str]] = {
    "game_mode_parked_services": {"stable-audio": "stable-audio-server"},
    "compose_drift_on_demand_services": {"sdxl-server": "image-gen-server"},
}


def _rename_entries(csv: str, renames: dict[str, str]) -> str:
    """Rename whole CSV entries (never substrings), dropping resulting duplicates."""
    out: list[str] = []
    for entry in (s.strip() for s in csv.split(",")):
        if not entry:
            continue
        entry = renames.get(entry, entry)
        if entry not in out:
            out.append(entry)
    return ",".join(out)


async def up(pool) -> None:
    """Apply the migration. Idempotent: re-running is a no-op."""
    async with pool.acquire() as conn:
        for key, renames in _RENAMES.items():
            value = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
            if not isinstance(value, str) or not value.strip():
                continue
            fixed = _rename_entries(value, renames)
            if fixed == value:
                continue
            await conn.execute(
                "UPDATE app_settings SET value = $1, updated_at = now() WHERE key = $2",
                fixed,
                key,
            )
            logger.info("Migration %s: %s %r -> %r", __name__, key, value, fixed)


async def down(pool) -> None:
    """One-way repair — no rollback.

    The prior names match no compose service, so restoring them would only
    switch the entries back off.
    """
    return
