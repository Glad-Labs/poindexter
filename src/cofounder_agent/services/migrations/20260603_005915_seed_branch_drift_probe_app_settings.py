"""Migration: seed branch-drift canary app_settings (glad-labs-stack#942).

Five DB tunables for brain/branch_drift_probe.py. Idempotent
(ON CONFLICT DO NOTHING) so it no-ops on Matt's already-running prod.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ROWS = [
    ("branch_drift_probe_enabled", "true", "monitoring",
     "Master switch for the brain branch-drift deploy canary (#942). When true, the brain pages the operator if the bind-mounted prod checkout falls behind origin/main."),
    ("branch_drift_poll_interval_minutes", "15", "monitoring",
     "Internal cadence gate (minutes) for the branch-drift canary's GitHub round-trip. The probe is dispatched every brain cycle (~5 min) but does real work only this often."),
    ("branch_drift_repo", "Glad-Labs/glad-labs-stack", "monitoring",
     "owner/name of the source-of-truth repo the branch-drift canary compares against. Paired with the gh_token secret for private-repo access."),
    ("branch_drift_dedup_hours", "6", "monitoring",
     "Re-page interval (hours) for an unchanged branch-drift state. Dedup is keyed on (repo, local HEAD, origin/main SHA); a new commit on either side re-pages immediately."),
    ("branch_drift_git_dir", "/host-git", "monitoring",
     "git --git-dir path inside the brain container for reading the running checkout's HEAD. Matches the read-only ./.git:/host-git:ro mount in docker-compose.local.yml."),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
    logger.info("Migration seed_branch_drift_probe_app_settings: applied (%d keys)", len(_ROWS))


async def down(pool) -> None:
    keys = [r[0] for r in _ROWS]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app_settings WHERE key = ANY($1::text[])", keys)
    logger.info("Migration seed_branch_drift_probe_app_settings down: removed %d keys", len(keys))
