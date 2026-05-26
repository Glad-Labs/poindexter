"""Seed app_settings defaults for the Prefect stuck-flow probe.

Captured 2026-05-26: a single ``content_generation`` flow run sat in
state=RUNNING for 35 hours, holding the deployment's concurrency slot
and idling the entire content pipeline with no direct alert. Matt's
brain auto-triage misdiagnosed it as Ollama unresponsiveness because
the only signal it saw was a downstream ``cost_freshness`` staleness.

The new ``brain/prefect_stuck_flow_probe.py`` watches Prefect for
flow runs stuck beyond a threshold. The defaults are baked into the
probe so it works out of the box; this migration just seeds the
``app_settings`` rows so they appear in ``poindexter set --help``,
``docs/reference/app-settings.md``, and the Settings dashboard
without an operator having to discover them by reading source code.

All ``ON CONFLICT DO NOTHING`` — re-runnable, never overwrites
operator-tuned values.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, category, is_secret)
            VALUES
              (
                'prefect_stuck_flow_probe_enabled', 'true',
                'Master kill switch for brain/prefect_stuck_flow_probe. '
                'Set to false to disable detection of stuck Prefect '
                'flow runs (use only if Prefect itself is being '
                'rebuilt and the probe would 500 every cycle).',
                'brain-probes', false
              ),
              (
                'prefect_api_base_url', 'http://prefect-server:4200/api',
                'Where the brain probes hit the Prefect REST API. '
                'Defaults to the in-stack compose hostname; '
                'docker_utils.localize_url() rewrites localhost to '
                'host.docker.internal only when the brain runs '
                'inside the container.',
                'brain-probes', false
              ),
              (
                'prefect_stuck_flow_flow_names', 'content_generation',
                'Comma-separated list of Prefect flow names the '
                'stuck-flow probe should watch. Add additional flow '
                'names if you spawn separate deployments (e.g. for '
                'dev_diary or experiment runs).',
                'brain-probes', false
              ),
              (
                'prefect_stuck_flow_threshold_minutes', '30',
                'A content_generation flow run RUNNING longer than '
                'this is considered stuck. Default 30m is ~5-6x the '
                'typical 5-min duration; tune downward as you build '
                'confidence in the probe, tune upward if you ever '
                'have legitimately long runs that should not be '
                'flagged.',
                'brain-probes', false
              ),
              (
                'prefect_stuck_flow_auto_crash', 'false',
                'When true, the probe force-CRASHED stuck flow runs '
                'via Prefect''s /set_state API so subsequent scheduled '
                'dispatches resume immediately. Defaults to false '
                'so the operator sees the page before destructive '
                'action; flip to true once the threshold is tuned '
                'for hands-off recovery.',
                'brain-probes', false
              )
            ON CONFLICT (key) DO NOTHING;
            """
        )
        logger.info(
            "Migration seed_prefect_stuck_flow_probe_app_settings: applied",
        )


async def down(pool) -> None:
    """Remove the seeded rows. Safe because operators who tuned the
    values away from defaults would still get the defaults from the
    probe code itself on the next cycle."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
              'prefect_stuck_flow_probe_enabled',
              'prefect_api_base_url',
              'prefect_stuck_flow_flow_names',
              'prefect_stuck_flow_threshold_minutes',
              'prefect_stuck_flow_auto_crash'
            );
            """
        )
        logger.info(
            "Migration seed_prefect_stuck_flow_probe_app_settings down: reverted",
        )
