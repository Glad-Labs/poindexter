"""Migration 20261003_031940: take GlitchTip off the port-forward watch, now
that its host port is published on loopback only

The commit that adds this migration publishes ``glitchtip-web`` on
``127.0.0.1:8080`` instead of every interface. GlitchTip's login page was
reachable from anything on the LAN, and its accounts are superusers. Nothing in
the stack uses the host port: every sender and the triage job reach GlitchTip
over the compose network (``sentry_dsn`` / ``glitchtip_base_url`` →
``glitchtip-web:8000``). Only a browser on this machine does.

The brain's port-forward probe (``poindexter/brain/docker_port_forward_probe.py``)
checks each ``docker_port_forward_watch_list`` entry at
``host.docker.internal:<host_port>``. That address is the Docker bridge
gateway, not loopback, so a loopback-only port is unreachable from the brain by
design and the entry would page as a wedged forward on every cycle. This
removes the GlitchTip entry and leaves every other entry as it was.

The baseline seed (``0000_baseline.seeds.sql``) drops the same entry in this
commit, so a fresh install never gets it.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

KEY = "docker_port_forward_watch_list"
CONTAINER = "poindexter-glitchtip-web"

# The entry as seeded, restored by ``down``.
_ENTRY = {"container": CONTAINER, "port": 8000, "host_port": 8080, "path": "/api/0/"}


def _load(raw: str | None) -> list | None:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


async def up(pool) -> None:
    """Drop the GlitchTip entry. No-op when the row or the entry is absent."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", KEY)
        entries = _load(raw)
        if entries is None:
            logger.info(
                "Migration take_glitchtip_off_the_port_forward_watch: skipped (%s is %s)",
                KEY, "absent" if raw is None else "not a JSON list",
            )
            return
        kept = [e for e in entries if not (isinstance(e, dict) and e.get("container") == CONTAINER)]
        if len(kept) == len(entries):
            logger.info("Migration take_glitchtip_off_the_port_forward_watch: no GlitchTip entry, nothing to do")
            return
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = NOW() WHERE key = $1",
            KEY, json.dumps(kept),
        )
    logger.info(
        "Migration take_glitchtip_off_the_port_forward_watch: applied (%d -> %d entries)",
        len(entries), len(kept),
    )


async def down(pool) -> None:
    """Put the entry back, if the list is present and lacks it."""
    async with pool.acquire() as conn:
        raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", KEY)
        entries = _load(raw)
        if entries is None or any(
            isinstance(e, dict) and e.get("container") == CONTAINER for e in entries
        ):
            return
        await conn.execute(
            "UPDATE app_settings SET value = $2, updated_at = NOW() WHERE key = $1",
            KEY, json.dumps([*entries, _ENTRY]),
        )
    logger.info("Migration take_glitchtip_off_the_port_forward_watch: reverted (entry restored)")
