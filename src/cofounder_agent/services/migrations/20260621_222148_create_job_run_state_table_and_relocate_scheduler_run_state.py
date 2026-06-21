"""Migration 20260621_222148: create job_run_state table and relocate scheduler run state

ISSUE: Glad-Labs/poindexter#TODO   (replace with the real issue)

The PluginScheduler wrote two app_settings rows per job fire —
plugin_job_last_run_<job> (epoch) and plugin_job_last_status_<job> (ok/err).
That is mutable runtime STATE, not config, and polluted the config table +
the generated settings reference. This migration creates a dedicated
job_run_state table, backfills it from the existing app_settings rows
(mapping the '0'/empty "never ran" sentinel to NULL so the scheduler's
restart-survival anchoring keeps correct never-run semantics), then deletes
the relocated rows. The baseline seeds for these keys are removed in the same
change, so fresh installs never seed them and this delete stays a one-way
relocation. See docs/superpowers/specs/2026-06-21-job-run-state-table-design.md.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Create job_run_state, backfill from app_settings, delete the old rows.

    Idempotent: CREATE TABLE IF NOT EXISTS + backfill ON CONFLICT DO NOTHING +
    a delete that no-ops once the rows are gone. Safe to re-run.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_run_state (
                job_name    text PRIMARY KEY,
                last_run_at timestamptz,
                last_status text,
                updated_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO job_run_state (job_name, last_run_at, last_status, updated_at)
            SELECT
                substring(r.key FROM (length('plugin_job_last_run_') + 1)) AS job_name,
                CASE WHEN r.value ~ '^[0-9]+$' AND r.value <> '0'
                     THEN to_timestamp(r.value::double precision)
                     ELSE NULL END                                          AS last_run_at,
                s.value                                                     AS last_status,
                now()
            FROM app_settings r
            LEFT JOIN app_settings s
                ON s.key = 'plugin_job_last_status_'
                        || substring(r.key FROM (length('plugin_job_last_run_') + 1))
            WHERE r.key LIKE 'plugin_job_last_run_%'
            ON CONFLICT (job_name) DO NOTHING
            """
        )
        result = await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key LIKE 'plugin_job_last_run_%'
               OR key LIKE 'plugin_job_last_status_%'
            """
        )
    logger.info(
        "job_run_state: created + backfilled; relocated app_settings rows (%s)", result
    )


async def down(pool) -> None:
    """One-way relocation — intentionally not reverted.

    job_run_state is now the scheduler's source of truth for last-run/status.
    Reverting would have to re-seed the plugin_job_last_* app_settings keys and
    reintroduce exactly the config-table pollution this migration removed, so
    down() is an explicit no-op.
    """
    return
