"""Migration 20260529_054100_prune_deprecated_app_settings_keys: prune deprecated app_settings keys

A 2026-05-29 audit cross-referenced every seeded ``app_settings`` key
against live code usage and found 54 keys with zero non-migration /
non-test references. They are no longer read by any production code
path and have been removed from ``0000_baseline.seeds.sql`` so fresh
DBs never seed them again. This migration deletes the same 54 rows
from existing / live databases so prod converges on the pruned set.

Grouped removal set (audit groups A-D):

* Group A — firm-dead feature flags / cost / investment / image-style /
  approval-gate-*-enabled / task-* / dedup / misc keys (46).
* Group B — Gitea decommissioned: ``gitea_url`` +
  ``memory_stale_threshold_seconds_gitea`` +
  ``memory_stale_threshold_seconds_gitea-issues-legacy`` (3).
* Group C — legacy keys tied to the deleted ``task_executor.py``
  (test-only refs remained): ``log_to_file``,
  ``task_executor_first_retry_writer_model``,
  ``task_retry_max_attempts`` (3).
* Group D — legacy R2 keys made dead by the ``storage_*`` cutover
  (#731 / #733): ``r2_public_url``, ``media_r2_upload_delay_seconds`` (2).

The replacement ``storage_public_url`` / ``media_upload_delay_seconds``
rows were already backfilled from these two legacy R2 keys by migration
``20260529_050524`` before this prune runs, so no operator value is lost.

Idempotent: a fresh DB never seeds these keys, so the DELETE matches
zero rows (no-op). A live DB deletes exactly the stale rows on first
apply and matches zero rows on any re-run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Verified-dead app_settings keys (54). Each was confirmed to have zero
# non-migration, non-test references via fixed-string grep across
# src/cofounder_agent, brain, mcp-server, mcp-server-gladlabs, scripts,
# and infrastructure on 2026-05-29.
DEPRECATED_KEYS = [
    # Group A — firm dead (audit found 0 refs)
    "alert_dedup_state_retention_hours",
    "approval_gate_draft_enabled",
    "approval_gate_final_enabled",
    "approval_gate_media_generation_failed_enabled",
    "approval_gate_podcast_enabled",
    "approval_gate_short_enabled",
    "approval_gate_topic_enabled",
    "approval_gate_video_enabled",
    "approval_queue_alert_threshold",
    # NOTE: backup_daily_enabled / backup_hourly_enabled are NOT dead —
    # scripts/backup/run.sh reads them dynamically (backup_${TIER}_enabled)
    # via psql, which the Python-only usage grep missed. Kept seeded + live.
    "cloud_api_notify_on_use",
    "enable_semantic_dedup",
    "google_gemini_credit",
    "gpu_idle_watts",
    "hardware_useful_life_months",
    "image_style_business",
    "image_style_engineering",
    "image_style_insights",
    "image_style_security",
    "image_style_startup",
    "image_style_technology",
    "integrations_framework_version",
    "investment_business_setup",
    "investment_cloud_mistakes",
    "investment_hardware",
    "investment_time_estimate",
    "investment_total_estimate",
    "location_state",
    "media_generation_retry_limit",
    "model_role_code_review",
    "model_role_creative",
    "monthly_insurance",
    "ops_triage_audit_logged",
    "patreon_account",
    "podcast_rss_url",
    "rag_enabled_for_research",
    "rate_limit_per_minute",
    "semantic_dedup_threshold",
    "system_idle_watts",
    "task_executor_idle_alert_threshold_seconds",
    "task_retry_backoff_initial_seconds",
    "task_timeout_seconds",
    "telegram_alert_types",
    "topic_discovery_cooldown_minutes",
    "writer_rag_citation_budget_min_citations",
    # Group B — Gitea decommissioned (Gitea retired 2026-04-30). The
    # gitea_user/gitea_repo/gitea_password operator-cred rows lingered in
    # the live DB; their last code references were removed alongside the
    # gitea_url config-sync block in brain/health_probes.py.
    "gitea_url",
    "gitea_user",
    "gitea_repo",
    "gitea_password",
    "memory_stale_threshold_seconds_gitea",
    "memory_stale_threshold_seconds_gitea-issues-legacy",
    # Group C — legacy task_executor keys (test-only refs remained)
    "log_to_file",
    "task_executor_first_retry_writer_model",
    "task_retry_max_attempts",
    # Group D — legacy R2 keys retired by the storage_* cutover (#731)
    "r2_public_url",
    "media_r2_upload_delay_seconds",
]


async def up(pool) -> None:
    """Apply the migration.

    Deletes the 54 verified-dead rows. Idempotent: matches zero rows on
    a fresh DB (the keys are no longer seeded) and on any re-run after
    the first apply.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            DEPRECATED_KEYS,
        )
        logger.info(
            "Migration prune_deprecated_app_settings_keys: applied (%s)",
            result,
        )


async def down(pool) -> None:
    """Revert the migration.

    One-way prune — the deleted rows carried no operator-tuned value
    worth restoring (the two R2 keys were backfilled into the
    ``storage_*`` replacements by migration ``20260529_050524`` before
    this prune ran). Re-seeding the dead defaults would only re-introduce
    keys no code reads, so ``down()`` is an intentional no-op.
    """
    async with pool.acquire() as conn:
        await conn.execute("SELECT 1")
        logger.info(
            "Migration prune_deprecated_app_settings_keys down: no-op "
            "(one-way prune of unreferenced keys)"
        )
