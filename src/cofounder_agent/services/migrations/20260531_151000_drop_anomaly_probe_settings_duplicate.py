"""Migration 20260531_151000: drop orphaned anomaly_* settings (duplicate probe)

Reverts the settings half of the #440 brain ``probe_anomaly``. That probe was a
duplicate of the EXISTING ``services/jobs/detect_anomalies.py``
(``DetectAnomaliesJob``) — already "Z-score outlier detection across failure
rate, quality, cost, error-rate" feeding the findings -> findings_alert_router
-> alert_dispatcher pipeline. The brain probe was removed; its
``anomaly_probe_enabled`` / ``anomaly_sigma_threshold`` / ``anomaly_baseline_days``
/ ``anomaly_min_samples`` settings are now orphaned (detect_anomalies uses its
own ``brain_anomaly_current_window_hours`` / ``brain_anomaly_baseline_window_days``
/ ``z_score_threshold`` keys).

Removes only rows still at the seeded default so an operator-tuned value isn't
clobbered. Idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# key -> seeded default (from the reverted 20260531_120000 seed migration).
_ORPHANED = {
    "anomaly_probe_enabled": "true",
    "anomaly_sigma_threshold": "3.0",
    "anomaly_baseline_days": "7",
    "anomaly_min_samples": "5",
}


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, default in _ORPHANED.items():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key, default,
            )
    logger.info(
        "Migration drop_anomaly_probe_settings: removed %d orphaned anomaly_* "
        "keys (detect_anomalies is the canonical detector)", len(_ORPHANED),
    )


async def down(pool) -> None:
    """Re-seed the defaults (reversibility)."""
    async with pool.acquire() as conn:
        for key, default in _ORPHANED.items():
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO NOTHING",
                key, default,
            )
