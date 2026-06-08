"""Migration 20260608_020831_seed_migration_drift_auto_sync_settings.

ISSUE: Glad-Labs/poindexter#228 (genuine self-heal for migration drift)

Seeds the knobs for the migration-drift probe's new resolution step. Before
this, auto-recover could only `docker restart` the worker — which can't fix
drift caused by a stale/polluted checkout (the 2026-06-07 storm). The probe now
optionally resyncs a DEDICATED deploy checkout (`git reset --hard origin/main`
+ `git clean -fd`) before restarting, with exponential backoff, and only
suppresses + pages as a last resort after attempts are exhausted.

Ships DARK: `migration_drift_auto_sync_enabled` defaults to 'false', so this
migration is a behavior no-op until the operator (a) creates the deploy clone
via scripts/setup-deploy-checkout.sh, (b) repoints the worker bind-mount at it,
and (c) flips the flag. See docs/operations/migration-drift-self-heal.md.

All seeds are INSERT ... ON CONFLICT DO NOTHING — re-runnable, never clobbers an
operator-tuned value. app_settings.value is NOT NULL; defaults use real values.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (key, value, is_secret, description)
_SETTINGS: tuple[tuple[str, str, bool, str], ...] = (
    (
        "migration_drift_auto_sync_enabled",
        "false",
        False,
        "When true, the migration-drift probe resyncs the deploy checkout "
        "(git reset --hard origin/main + clean -fd) before restarting the "
        "worker. Default off until the dedicated deploy checkout is wired.",
    ),
    (
        "migration_drift_deploy_checkout_path",
        "/host-deploy",
        False,
        "In-brain-container path where the dedicated deploy checkout is "
        "mounted RW. The probe runs git here. Nothing else touches this "
        "checkout, so reset --hard is always safe.",
    ),
    (
        "migration_drift_recover_max_attempts",
        "3",
        False,
        "Max consecutive recovery attempts (sync+restart) for one drift "
        "episode before the probe gives up, pages once, and suppresses until "
        "the pending count changes/clears. Backoff between attempts is "
        "exponential (2^n brain cycles).",
    ),
)


async def up(pool) -> None:
    """Seed the auto-sync settings (idempotent)."""
    async with pool.acquire() as conn:
        for key, value, is_secret, description in _SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, is_secret, description)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, is_secret, description,
            )
        logger.info(
            "Migration seed_migration_drift_auto_sync_settings: seeded %d "
            "app_settings (ON CONFLICT DO NOTHING)",
            len(_SETTINGS),
        )


async def down(pool) -> None:
    """Remove the seeded keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [s[0] for s in _SETTINGS],
        )
        logger.info(
            "Migration seed_migration_drift_auto_sync_settings down: removed "
            "%d app_settings", len(_SETTINGS),
        )
