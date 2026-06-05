"""Migration: drop orphan plugin-job telemetry keys for deleted sync jobs.

The two-DB-era "pull cloud rows into the local brain DB" sync jobs are
obsolete under the local-only architecture (2026-04-05) — there is no
cloud DB to pull from:

- ``sync_newsletter_subscribers`` — deleted this change (poindexter#571).
  The local ``newsletter_subscribers`` table is now the system of record
  (``routes/newsletter_routes.py`` signup) + Resend webhook engagement
  events; the cloud→local pull is dead.
- ``sync_page_views`` — already deleted in the #955 batch; its
  ``plugin_job_last_*`` telemetry rows were left behind.

PluginScheduler auto-writes ``plugin_job_last_run_<job>`` /
``plugin_job_last_status_<job>`` rows for every registered job. Once the
job is unregistered nothing ever writes them again — they are pure
orphans. This drops them on existing DBs; the baseline still seeds them
on a fresh DB, so this migration cleans both (baseline seeds → this
DELETEs).

The ``idle_last_run_*`` rows are NOT touched — those belong to the
retained IdleWorker (poindexter#570), a separate surface.

Idempotent (DELETE no-ops when the keys are already absent).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ORPHAN_KEYS = (
    "plugin_job_last_run_sync_newsletter_subscribers",
    "plugin_job_last_status_sync_newsletter_subscribers",
    "plugin_job_last_run_sync_page_views",
    "plugin_job_last_status_sync_page_views",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_ORPHAN_KEYS),
        )
    logger.info(
        "Migration drop_orphan_plugin_job_telemetry: applied (%d keys)",
        len(_ORPHAN_KEYS),
    )


async def down(pool) -> None:
    # Re-seed the telemetry rows in their baseline shape. Harmless on
    # rollback; PluginScheduler would recreate them anyway for any
    # re-registered job.
    rows = [
        ("plugin_job_last_run_sync_newsletter_subscribers", "0",
         "Unix epoch of last fire for plugin job 'sync_newsletter_subscribers' (auto-written by PluginScheduler)"),
        ("plugin_job_last_status_sync_newsletter_subscribers", "ok",
         "Outcome of last fire for plugin job 'sync_newsletter_subscribers': 'ok' or 'err'"),
        ("plugin_job_last_run_sync_page_views", "0",
         "Unix epoch of last fire for plugin job 'sync_page_views' (auto-written by PluginScheduler)"),
        ("plugin_job_last_status_sync_page_views", "ok",
         "Outcome of last fire for plugin job 'sync_page_views': 'ok' or 'err'"),
    ]
    async with pool.acquire() as conn:
        for key, value, description in rows:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'plugin_telemetry', $3, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
    logger.info("Migration drop_orphan_plugin_job_telemetry down: re-seeded")
