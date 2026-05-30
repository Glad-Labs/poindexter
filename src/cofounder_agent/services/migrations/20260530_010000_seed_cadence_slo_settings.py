"""Migration 20260530_010000_seed_cadence_slo_settings: seed cadence SLO probe settings

Seeds the four app_settings keys that drive the brain's ``probe_cadence_slo``
health probe (Glad-Labs/poindexter#525).

Why
---
On 2026-05-28 a cadence change quietly slowed the content pipeline and NO
probe caught it. The existing probes are too coarse:

* ``probe_publish_rate`` only fires on 0 posts in 3 days.
* ``probe_pipeline_throughput`` only fires on a >50% drop vs the prior 7 days.

``probe_cadence_slo`` compares ACTUAL publish output (count of
``posts`` rows with ``status='published'`` and ``published_at`` inside the
trailing window) against a CONFIGURED cadence target, so a shortfall is
caught within hours. The target is deliberately NOT derived from
``prefect_content_flow_cron`` — that cron is the flow's tick/drain rate
(~every 2 min), not the content production target.

Keys seeded (DB-first config per project rule):

* ``cadence_slo_enabled`` (``true``)            — master switch; false = skip.
* ``cadence_slo_expected_posts_per_day`` (``1``) — target cadence.
* ``cadence_slo_window_hours`` (``24``)          — trailing window.
* ``cadence_slo_shortfall_ratio`` (``0.5``)      — page when
  ``actual < ratio * expected_for_window``.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — never clobbers an
operator-tuned value, and a re-run on an up-to-date DB is a no-op. A fresh
DB also gets these keys from ``settings_defaults.seed_all_defaults``; first
writer wins.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Key -> seeded default. Mirrors services/settings_defaults.py.
_DEFAULTS = {
    "cadence_slo_enabled": "true",
    "cadence_slo_expected_posts_per_day": "1",
    "cadence_slo_window_hours": "24",
    "cadence_slo_shortfall_ratio": "0.5",
}


async def up(pool) -> None:
    """Insert each cadence-SLO setting row if absent."""
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
            )
            logger.info(
                "Migration seed_cadence_slo_settings: %s (%s)", key, result,
            )


async def down(pool) -> None:
    """Remove the seeded rows.

    Only deletes a row when it still holds the seeded default — an operator
    who tuned a key keeps their value (the down-migration shouldn't destroy
    operator config).
    """
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key,
                value,
            )
        logger.info(
            "Migration seed_cadence_slo_settings down: removed default rows "
            "(operator-tuned values preserved)"
        )
