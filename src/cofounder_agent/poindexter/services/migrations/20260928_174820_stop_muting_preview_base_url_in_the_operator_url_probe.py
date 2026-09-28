"""Migration 20260928_174820: stop muting preview_base_url in the operator URL probe

ISSUE: Glad-Labs/poindexter#214 (operator URL probe) follow-up, 2026-09-28.

``preview_base_url`` is the link in every awaiting-approval message, the most
operator-facing URL there is. It has sat in ``operator_url_probe_skip_keys``
since the probe's false-positive cleanup in early May 2026 (#143 era; the
reason was not recorded), and a mute is blind. On the Pop!_OS host it hid the
value that actually broke: the retired Windows node's tailnet IP, dead since
the migration. Every approval link went nowhere for about ten weeks and
nothing alerted.

The probe can check the link now, including the MagicDNS name it should hold.
A MagicDNS name used to be unprobeable from the container (public DNS answers
it with the Tailscale Funnel ingress). Now a host ending in
``operator_url_probe_tailnet_suffixes`` (``.ts.net``) is resolved through
``operator_url_probe_tailnet_resolver`` (Tailscale's 100.100.100.100), the way
the operator's phone resolves it. The worker answers its root with a liveness
JSON, so the bare base URL is a real check. So the mute comes off. Removes the
one CSV entry and keeps the operator's other mutes in their order. The
baseline seed drops it too, so a fresh install never gets it.

``down()`` puts the entry back (appended) if it is missing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "operator_url_probe_skip_keys"
_ENTRY = "preview_base_url"


def _split(value: str | None) -> list[str]:
    """The skip list as the probe reads it: comma-separated, whitespace-tolerant."""
    return [s.strip() for s in (value or "").split(",") if s.strip()]


async def up(pool) -> None:
    """Remove preview_base_url from the operator URL probe's mute list."""
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", _KEY,
        )
        if value is None:
            logger.info(
                "Migration stop_muting_preview_base_url: no %s row; nothing to do", _KEY,
            )
            return
        entries = _split(value)
        if _ENTRY not in entries:
            logger.info(
                "Migration stop_muting_preview_base_url: %s not muted; nothing to do", _ENTRY,
            )
            return
        kept = [e for e in entries if e != _ENTRY]
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = NOW() WHERE key = $1",
            _KEY, ",".join(kept),
        )
    logger.info(
        "Migration stop_muting_preview_base_url: removed %s from %s (%d entries remain)",
        _ENTRY, _KEY, len(kept),
    )


async def down(pool) -> None:
    """Mute preview_base_url again (appended) if it is not already muted."""
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", _KEY,
        )
        if value is None:
            return
        entries = _split(value)
        if _ENTRY in entries:
            return
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = NOW() WHERE key = $1",
            _KEY, ",".join([*entries, _ENTRY]),
        )
    logger.info("Migration stop_muting_preview_base_url: down() re-muted %s", _ENTRY)
