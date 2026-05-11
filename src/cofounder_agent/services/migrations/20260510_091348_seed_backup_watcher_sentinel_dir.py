"""Seed app_settings.backup_watcher_sentinel_dir for #444.

Glad-Labs/poindexter#444 extends ``brain/backup_watcher.py`` with a
sentinel-scanning path: each cycle it walks the configured directory
for ``dr-backup-*-failed.sentinel`` files (dropped by the host-side
dr-backup scripts when their primary Telegram alert path fails) and
surfaces them through ``alert_events``.

The default ``/host-backup-logs`` matches the read-only bind mount
this PR adds to the ``brain-daemon`` block in
``docker-compose.local.yml``. Operators with a non-default backup
log location override this row to point at their actual mount.

Idempotent — ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SETTINGS = [
    (
        "backup_watcher_sentinel_dir",
        "/host-backup-logs",
        "backup",
        "Container path the brain bind-mounts ~/.poindexter/logs into "
        "(read-only). brain/backup_watcher.py scans this directory each "
        "cycle for dr-backup-*-failed.sentinel files dropped by the "
        "host-side dr-backup scripts and surfaces any it finds via "
        "alert_events. Set to a path that does NOT exist (e.g. "
        "'/disabled') to short-circuit the sentinel scan without "
        "disabling the rest of the probe (Glad-Labs/poindexter#444).",
    ),
]


async def run_migration(conn) -> None:
    for key, value, category, description in _SETTINGS:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            key,
            value,
            category,
            description,
        )
    logger.info(
        "Migration 20260510_091348: backup_watcher_sentinel_dir seeded "
        "(default '/host-backup-logs' — matches docker-compose bind mount)."
    )
