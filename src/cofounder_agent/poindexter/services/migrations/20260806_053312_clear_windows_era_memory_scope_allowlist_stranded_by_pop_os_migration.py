"""Migration 20260806_053312: clear Windows-era memory scope allowlists.

ISSUE: Glad-Labs/poindexter — memory + session taps silently ingested
nothing for 17 days after the Pop!_OS migration.

``plugin.tap.memory.config.memory_scope_allowlist`` existed to dedup the
Windows Junction ``C--Users-alice`` ⇄ ``C--Users-alice-myproject``: the
latter is a reparse point to the former, so without an allowlist the host
embedded the same files under two scopes.

The Pop!_OS migration re-keyed Claude project scopes from the Windows
naming (``C--Users-<you>-...``) to the Linux checkout path
(``-home-<you>-<project>``). The allowlist value was left behind
pointing at a scope that no longer exists, so it filtered out *every*
scope on disk and the memory tap ingested zero files — while still
reporting a clean run. Junctions are a Windows-only construct, so a
``C--``-prefixed allowlist can only ever be dead weight on a Linux host.

This clears only allowlist entries that are Windows-era scope names
(``C--`` prefix, case-insensitive). A deliberately-set allowlist naming
real scopes on the current host is left untouched, and the whole
migration is a no-op on any install that never set one — which is every
fresh install, since ``plugin.tap.memory`` is not seeded by
``settings_defaults.py``, the baseline seeds, or the brain seed.

The code-side guard against a recurrence lives in
``services/taps/memory.py``: discovery is now platform-neutral (a scope is
any directory holding a ``memory/`` subdir, not a name matching ``C--*``),
and an allowlist that matches no scope on disk logs a WARNING naming the
scopes that *do* exist instead of silently returning an empty list.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_KEY = "plugin.tap.memory"


def _strip_windows_scopes(allowlist: str) -> str:
    """Drop ``C--``-prefixed (Windows-era) scopes, preserving the rest."""
    kept = [
        scope
        for scope in (s.strip() for s in allowlist.split(","))
        if scope and not scope.lower().startswith("c--")
    ]
    return ",".join(kept)


async def up(pool) -> None:
    """Apply the migration. Idempotent: re-running is a no-op."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", _KEY)
        if raw is None:
            logger.info("Migration %s: no %s row — nothing to repair.", __name__, _KEY)
            return

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            # Fail loud rather than guess. A malformed row is an operator
            # problem; silently rewriting it could destroy real config.
            logger.warning(
                "Migration %s: %s is not valid JSON — leaving untouched: %r",
                __name__,
                _KEY,
                raw,
            )
            return

        config = payload.get("config") if isinstance(payload, dict) else None
        if not isinstance(config, dict):
            return

        allowlist = config.get("memory_scope_allowlist")
        if not isinstance(allowlist, str) or not allowlist.strip():
            return

        cleaned = _strip_windows_scopes(allowlist)
        if cleaned == allowlist:
            return  # nothing Windows-era in there

        config["memory_scope_allowlist"] = cleaned
        await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() WHERE key = $2",
            json.dumps(payload),
            _KEY,
        )
        logger.info(
            "Migration %s: cleared Windows-era scope allowlist %r -> %r; the "
            "memory tap will now discover every scope on this host.",
            __name__,
            allowlist,
            cleaned,
        )


async def down(pool) -> None:
    """One-way repair — no rollback.

    The prior value named a Claude project scope that does not exist on
    this host, so restoring it would only re-break memory ingestion. An
    operator who genuinely wants a scope allowlist should set one naming
    scopes that exist, via ``poindexter taps set-config``.
    """
    return
