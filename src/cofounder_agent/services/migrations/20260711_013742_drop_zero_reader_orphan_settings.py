"""Migration 20260711_013742: delete 26 zero-reader orphan app_settings keys.

Retires 26 ``app_settings`` keys that ``ProbeZeroReaderSettingsJob`` flagged
(NULL ``last_read_at`` past the 30-day grace) AND that a full-repo grep
(``src/`` + ``brain/`` + ``scripts/`` + ``mcp-server*/`` + ``console/`` +
``web/`` + ``skills/`` + ``infrastructure/``) confirms **nothing reads** — not
via ``SiteConfig.get``/``SettingsService.get``, not via raw SQL, not via the
brain daemon's asyncpg process, not via a prompt template. Each is either
superseded by a newer mechanism or a flag/feature that was never wired.

Grouped by why they're dead:

  * **Global token limits, superseded by per-model/per-call sizing:**
    ``max_tokens_per_request``, ``max_tokens_per_task``, ``max_tokens_default``.
  * **Legacy QA-workflow reviewer-chain config** (the pre-atom-cutover
    ``{"reviewers":[...]}`` scheme, replaced by the ``qa.*`` atoms + ``qa_gates``
    in #355): ``qa_workflow_blog_content``, ``qa_workflow_premium_content``,
    ``qa_workflow_quick_check``.
  * **Dead duplicates of the live QA knobs** (the graph_def path reads
    ``qa_final_score_threshold`` + ``qa_validator_weight`` + ``qa_critic_weight``,
    which stay): ``qa_overall_score_threshold``, ``qa_critical_dimension_floor``.
  * **Superseded cost/energy + sweep + retry + refinement mechanisms:**
    ``ollama_electricity_cost_per_1k_tokens`` (per-model
    ``plugin.llm_provider.*.model.*.energy_per_1k_wh`` now),
    ``task_sweep_interval_seconds`` (Prefect-native sweep),
    ``max_task_retries`` (Prefect retries + the ``pipeline_tasks.retry_count``
    stale-sweep), ``content_max_refinement_attempts`` (the bounded
    ``qa_rewrite_max_attempts`` rescue cycle).
  * **Content knobs no producer reads** (word counts aren't pinned in prompts
    per policy; temperature is per-model; quality gating is the ``qa.aggregate``
    atom): ``content_temperature``, ``content_target_word_count``,
    ``content_min_word_count``, ``content_quality_minimum``, ``content_weekly_cap``.
  * **Image knobs superseded by the Image Decision Agent + LRU style rotation /
    per-niche styles:** ``image_primary_source``, ``image_style_default``.
  * **Unwired feature flags** (the feature runs unconditionally; the flag gates
    nothing): ``enable_featured_image``, ``enable_mcp_server``,
    ``enable_memory_system``, ``enable_training_capture``.
  * **Cloud-API gating superseded by cost_guard + LiteLLM routing + per-step
    model pins:** ``cloud_api_mode``, ``cloud_api_daily_limit``.
  * **Depreciation input with no consumer** (no depreciation feature reads it):
    ``hardware_cost_total``.

**Explicitly NOT dropped** (flagged by the same probe but verified live —
they are blind-spots the read-telemetry can't see, or intentional operator
config): the 17 keys read via raw SQL / brain / job-config dicts
(``auto_publish_threshold``, ``stale_task_timeout_minutes``, ``api_base_url``,
``pipeline_writer_model``/``critic``/``fallback``, the six live ``qa_*`` knobs,
``cost_alert_threshold_pct``, ``grafana_url``, ``cloudinary_cloud_name``,
``newsletter_from_email``, ``openclaw_webhook_url``); the five unread-but-
intentional operator-identity keys (``discord_ops_channel_id`` — pinned by the
operator-leak guard, ``privacy_email``, ``gpu_model``, ``social_x_url``,
``social_linkedin_url``); and ``telegram_alerts_enabled`` (an unenforced
control — wire-or-drop decision, left in place). One operator-only finance
snapshot key (present in no seed source, and an operator-overlay identifier
stripped from the public mirror) is retired out-of-band on the operator DB,
not here.

**Why this migration survives the squash.** A baseline only ever
``INSERT ... ON CONFLICT DO NOTHING``, a no-op on installs that already carry
the rows — so a baseline that simply omits these keys leaves prod carrying them
while fresh installs lack them. This is the convergence step:

  * Fresh installs — the seed sources (``0000_baseline.seeds.sql``,
    ``settings_defaults.DEFAULTS``, ``brain/seed_app_settings.json``) no longer
    emit these rows, so the DELETE matches nothing (no-op).
  * Existing installs (prod, seeded from the pre-removal baseline) — this
    performs the real delete.

Both converge to a settings table free of the 26 dead keys. Same one-way
posture as 20260623_202131_drop_dead_model_settings and
20260624_030607_drop_orphaned_bench_harness_app_settings_keys. The next squash
can fold this away once every install has run it.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = (
    # global token limits (superseded by per-model / per-call sizing)
    "max_tokens_per_request",
    "max_tokens_per_task",
    "max_tokens_default",
    # legacy QA-workflow reviewer-chain config (replaced by qa.* atoms + qa_gates)
    "qa_workflow_blog_content",
    "qa_workflow_premium_content",
    "qa_workflow_quick_check",
    # dead duplicates of the live qa_final_score_threshold / weight knobs
    "qa_overall_score_threshold",
    "qa_critical_dimension_floor",
    # superseded cost/energy + sweep + retry + refinement mechanisms
    "ollama_electricity_cost_per_1k_tokens",
    "task_sweep_interval_seconds",
    "max_task_retries",
    "content_max_refinement_attempts",
    # content knobs no producer reads
    "content_temperature",
    "content_target_word_count",
    "content_min_word_count",
    "content_quality_minimum",
    "content_weekly_cap",
    # image knobs superseded by the Image Decision Agent + LRU / per-niche styles
    "image_primary_source",
    "image_style_default",
    # unwired feature flags (feature runs unconditionally)
    "enable_featured_image",
    "enable_mcp_server",
    "enable_memory_system",
    "enable_training_capture",
    # cloud-API gating superseded by cost_guard + LiteLLM + per-step model pins
    "cloud_api_mode",
    "cloud_api_daily_limit",
    # depreciation input with no consumer
    "hardware_cost_total",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_DEAD_KEYS),
        )
    logger.info(
        "drop_zero_reader_orphan_settings up: removed %d zero-reader keys (%s)",
        len(_DEAD_KEYS),
        result,
    )


async def down(_pool) -> None:
    # No-op: these keys are retired zero-reader orphans with no reader to
    # restore. Re-seeding them would only reintroduce set-but-never-read config.
    # Same one-way posture as 20260623_202131_drop_dead_model_settings.
    logger.info(
        "drop_zero_reader_orphan_settings down: no-op "
        "(refusing to re-seed retired zero-reader keys)"
    )
