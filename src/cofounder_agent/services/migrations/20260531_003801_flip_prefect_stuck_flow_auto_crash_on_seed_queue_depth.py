"""Migration 20260531_003801: flip prefect_stuck_flow_auto_crash on + seed queue depth

ISSUE: Glad-Labs/poindexter#526

Two changes for the brain ``prefect_stuck_flow_probe`` (#526):

1. Flip ``prefect_stuck_flow_auto_crash`` from its still-default ``'false'``
   to ``'true'``. The probe's auto-CRASHED remediation is now the default:
   the stuck-duration threshold has been tuned across two captured
   incidents (romantic-harrier 35h RUNNING, smoky-chowchow 50h PENDING),
   so hands-off recovery of a held concurrency slot is safe. The UPDATE is
   guarded with ``AND value='false'`` so it only flips the row that still
   holds the seeded default — an operator who deliberately set something
   else (or already turned it on) is left untouched.

2. Seed a new ``prefect_stuck_flow_queue_depth_threshold`` (default ``'3'``)
   that drives the probe's new queue-backlog detection: when more than this
   many SCHEDULED runs are overdue (scheduled start in the past), the probe
   emits a distinct ``probe.prefect_queue_backlog_detected`` page — the
   backlog symptom of a held slot surfaces even before the held run crosses
   the stuck-duration threshold.

The baseline (``0000_baseline.seeds.sql``) is intentionally NOT edited; the
auto-crash row is seeded ``'false'`` there and live prod also holds
``'false'``, so this forward migration is the seam that flips it.

Idempotent: the UPDATE no-ops once the value is ``'true'`` (the
``value='false'`` guard fails to match); the INSERT uses
``ON CONFLICT (key) DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_QUEUE_DEPTH_KEY = "prefect_stuck_flow_queue_depth_threshold"
_QUEUE_DEPTH_DEFAULT = "3"
_QUEUE_DEPTH_DESCRIPTION = (
    "Brain prefect_stuck_flow_probe: page with a distinct "
    "probe.prefect_queue_backlog_detected signal when MORE than this many "
    "SCHEDULED runs are overdue (scheduled start in the past). With "
    "concurrency=1 an overdue pile-up almost always means the single slot is "
    "held by a stuck/PENDING run. Default 3 — a couple of missed cron ticks "
    "is noise, a real backlog is several."
)


async def up(pool) -> None:
    """Flip auto-crash on (only if still default) + seed queue-depth threshold."""
    async with pool.acquire() as conn:
        flip_result = await conn.execute(
            """
            UPDATE app_settings
            SET value = 'true'
            WHERE key = 'prefect_stuck_flow_auto_crash'
              AND value = 'false'
            """
        )
        logger.info(
            "Migration flip_prefect_stuck_flow_auto_crash_on_seed_queue_depth: "
            "auto-crash flip (%s)", flip_result,
        )

        seed_result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, 'brain-probes', $3, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _QUEUE_DEPTH_KEY,
            _QUEUE_DEPTH_DEFAULT,
            _QUEUE_DEPTH_DESCRIPTION,
        )
        logger.info(
            "Migration flip_prefect_stuck_flow_auto_crash_on_seed_queue_depth: "
            "queue-depth seed %s (%s)", _QUEUE_DEPTH_KEY, seed_result,
        )


async def down(pool) -> None:
    """Revert: restore auto-crash to the seeded default + drop the queue key.

    Only flips auto-crash back to 'false' when it still holds the 'true'
    this migration set — an operator who changed it afterwards keeps their
    value. The queue-depth row is removed only while it holds the seeded
    default so a tuned value survives.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
            SET value = 'false'
            WHERE key = 'prefect_stuck_flow_auto_crash'
              AND value = 'true'
            """
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1 AND value = $2",
            _QUEUE_DEPTH_KEY,
            _QUEUE_DEPTH_DEFAULT,
        )
        logger.info(
            "Migration flip_prefect_stuck_flow_auto_crash_on_seed_queue_depth down: "
            "auto-crash reverted to default, queue-depth default row removed "
            "(operator-tuned values preserved)"
        )
