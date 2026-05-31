"""Migration 20260531_170000: seed glitchtip-triage + alertmanager_url settings

ISSUE: Glad-Labs/poindexter#304 (alert/infra audit follow-up)

Three tunables were read with hardcoded code-defaults but never seeded into
``app_settings``, so they were invisible to operators (couldn't be tuned via
``poindexter set`` / the settings UI) and absent from the config plane:

* ``glitchtip_triage_alert_freshness_hours`` (default 24) — the
  triage probe's "don't re-page stale issues" window.
* ``glitchtip_triage_default_resolve_max_count`` (default 50) — the
  ceiling injected into ``resolve`` rules that omit ``max_count``, so an
  unbounded resolve rule can't silently auto-close a runaway outage (#304).
* ``alertmanager_url`` (default ``http://alertmanager:9093``) — where the
  brain health-probe loop checks Alertmanager liveness before deciding
  whether PROMETHEUS_COVERED alert suppression is safe (#304).

Seeds at the code-default via ``ON CONFLICT DO NOTHING`` so any
operator-tuned value already present is preserved. Idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "glitchtip_triage_alert_freshness_hours": "24",
    "glitchtip_triage_default_resolve_max_count": "50",
    "alertmanager_url": "http://alertmanager:9093",
}


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO NOTHING",
                key, value,
            )
    logger.info(
        "Migration seed_glitchtip_triage_and_alertmanager_settings: seeded %d "
        "keys (ON CONFLICT DO NOTHING)", len(_DEFAULTS),
    )


async def down(pool) -> None:
    """Remove only rows still at the seeded default (don't clobber tuning)."""
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key, value,
            )
