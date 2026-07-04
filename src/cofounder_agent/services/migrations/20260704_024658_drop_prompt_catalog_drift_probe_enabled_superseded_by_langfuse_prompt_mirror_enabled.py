"""Migration 20260704_024658: drop prompt_catalog_drift_probe_enabled.

ISSUE: Glad-Labs/poindexter#825

The prompt-catalog drift probe (ProbePromptCatalogDriftJob, poindexter#824)
was superseded one day after shipping by SyncPromptCatalogToLangfuseJob —
Langfuse is now a read-only mirror of the SKILL.md catalog, gated by the new
``langfuse_prompt_mirror_enabled`` key. This drops the probe's orphaned
enable flag from installs that booted while it was seeded (the seeder ran on
prod 2026-07-03); the key is gone from settings_defaults.py so it will not
re-seed. No-op on fresh installs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'prompt_catalog_drift_probe_enabled'"
        )
    logger.info(
        "Migration drop_prompt_catalog_drift_probe_enabled: applied "
        "(superseded by langfuse_prompt_mirror_enabled)"
    )


async def down(pool) -> None:
    # One-way: the key's owner job no longer exists, so restoring the row
    # would only recreate a zero-reader orphan. Explicit no-op.
    del pool
    return
