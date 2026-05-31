"""Migration 20260531_120000_seed_anomaly_probe_settings: seed anomaly probe settings

Seeds the four app_settings keys that drive the brain's ``probe_anomaly``
rolling-baseline health probe (Glad-Labs/poindexter#440).

Why
---
Existing probes are point checks against hand-picked thresholds
(``probe_traffic_anomaly`` = ">60% drop", ``probe_quality_trend`` =
">10pt decline"). ``probe_anomaly`` is the statistical complement: for each
tracked metric it builds a rolling ``baseline_days``-day envelope (mean +
sample std over complete days) and flags the most recent complete day when
it sits beyond ``sigma`` standard deviations *in the direction that is bad*
for that metric (cost/error/failure spikes; throughput drops). This catches
"this metric left its own normal range" without a human guessing a constant.

Keys seeded (DB-first config per project rule):

* ``anomaly_probe_enabled`` (``true``)   — master switch; false = skip.
* ``anomaly_sigma_threshold`` (``3.0``)  — std devs from baseline = anomaly.
* ``anomaly_baseline_days`` (``7``)      — rolling envelope length.
* ``anomaly_min_samples`` (``5``)        — minimum NON-ZERO baseline days
  before a metric is evaluated (guards a young system from false-alarming
  on first real activity).

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
    "anomaly_probe_enabled": "true",
    "anomaly_sigma_threshold": "3.0",
    "anomaly_baseline_days": "7",
    "anomaly_min_samples": "5",
}


async def up(pool) -> None:
    """Insert each anomaly-probe setting row if absent."""
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
                "Migration seed_anomaly_probe_settings: %s (%s)", key, result,
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
            "Migration seed_anomaly_probe_settings down: removed default rows "
            "(operator-tuned values preserved)"
        )
