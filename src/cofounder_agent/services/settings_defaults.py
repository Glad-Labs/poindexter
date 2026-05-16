"""Consolidated default values for app_settings keys (#379).

Centralises every default that previously lived inline in
``site_config.get(key, default)`` calls across ~120 service files.

Why this exists
---------------
On a fresh DB, only the ~149 keys explicitly seeded by
``services/migrations/`` exist in ``app_settings``. The remaining
~300 are inserted lazily by SettingsService the first time the worker
queries them. That violates ``feedback_no_silent_defaults`` (defaults
appear at query-time, not loud at install-time) and makes
``poindexter setup --check`` report ``SKIP api_base_url unset`` on a
fresh install even though the worker would happily use a default.

``seed_all_defaults(pool)`` walks ``DEFAULTS`` and inserts each row
with ``ON CONFLICT (key) DO NOTHING`` so:

* Operator-tuned values are NEVER clobbered (the ON CONFLICT branch
  keeps the existing row untouched).
* Re-running on an up-to-date DB is a fast no-op (no rows inserted).
* Migrations and this seeder can both write the same key — first one
  wins, the other is a no-op.

Wired into:

* ``StartupManager._run_migrations`` — every worker boot.
* ``cli/setup.py`` — runs after ``poindexter setup`` finishes
  migrations, so a fresh install ends with a complete app_settings
  table.

What this module is NOT
-----------------------
* **Not for secrets.** Keys matching ``*_api_key``, ``*_password``,
  ``*_secret``, ``database_url``, ``operator_id`` etc. are
  deliberately excluded. They must remain unset on fresh install —
  the operator sets them via ``poindexter setup`` prompts or the
  ``set_secret`` API. Putting placeholder values here would trigger
  the ``app_settings`` auto-encrypt trigger (migration 0130) and
  bury a bogus ciphertext in the DB.
* **Not the source of truth at runtime.** ``site_config.get(key,
  default)`` callers still pass their own default — this registry
  just makes sure the DB row exists so the call returns the DB value
  instead of the inline default. Removing the inline default in code
  is a separate cleanup pass (#198 follow-up).
* **Not auto-generated reflectively.** The list is committed source
  to keep it grep-able and review-able. ``scripts/extract_settings_defaults.py``
  + ``scripts/generate_settings_defaults_module.py`` regenerate it
  from the codebase if a sweep adds new keys; the diff is the audit.

Auto-generated from:

* ``scripts/extract_settings_defaults.py``  (AST sweep of
  ``site_config.get*(key, default)`` call sites)
* ``scripts/extract_secret_keys.py``         (secret-key blocklist)
* ``scripts/generate_settings_defaults_module.py`` (this writer)
"""
from __future__ import annotations

from typing import Any


# Every value is stored as `str` because `app_settings.value` is a TEXT
# column. Numeric / bool consumers parse via `site_config.get_int()`,
# `get_float()`, `get_bool()` etc.
DEFAULTS: dict[str, str] = {
    # ----- Identity / branding -----
    'app_version': '3.0.1',
    'company_name': '',
    'development_mode': '',
    'disable_auth_for_dev': 'false',
    'environment': 'development',
    'owner_name': '',
    'site_domain': '',
    'site_name': '',
    'site_url': '',

    # ----- Cost / billing -----
    'monthly_spend_limit': '100.0',

    # ----- LLM model selection -----
    'default_ollama_model': 'gemma3:27b',
    'embed_model': 'nomic-embed-text',
    'embedding_model': '',
    'inline_image_prompt_model': 'llama3:latest',
    'local_llm_api_url': 'http://localhost:11434',
    'local_llm_model_name': 'auto',
    'model_role_image_decision': 'qwen3:8b',
    'pipeline_architect_timeout_seconds': '120.0',
    'pipeline_fallback_model': 'gemma3:27b',
    'pipeline_writer_model': 'gemma3:27b',
    'qa_fallback_writer_model': 'gemma3:27b',
    'use_ollama': 'false',

    # ----- LLM providers / endpoints -----
    'flux_schnell_server_url': '',
    'ollama_base_url': 'http://host.docker.internal:11434',
    'plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.output_format': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.server_url': '',
    'plugin.image_provider.flux_schnell.server_url': '',
    'plugin.llm_provider.gemini.enabled': 'false',
    'plugin.video_provider.wan2.1-1.3b.server_url': '',
    'sdxl_server_url': 'http://host.docker.internal:9836',
    'stable_audio_open_server_url': '',
    'video_server_url': 'http://host.docker.internal:9837',
    'wan_server_url': '',

    # ----- RAG / retrieval -----
    'niche_internal_rag_per_kind_limit': '5',
    'niche_internal_rag_snippet_max_chars': '600',
    'rag_default_top_k': '5',
    'rag_hybrid_enabled': 'false',
    'rag_min_similarity': '0.3',
    'rag_rerank_enabled': 'false',
    'rag_rerank_model': 'cross-encoder/ms-marco-MiniLM-L-6-v2',
    'rag_rrf_k': '60',
    'rag_source_filter': '',
    'writer_rag_citation_budget_snippet_limit': '12',
    'writer_rag_context_snippet_max_chars': '500',
    'writer_rag_research_topic_max_sources': '2',
    'writer_rag_story_spine_snippet_limit': '15',
    'writer_rag_story_spine_snippet_max_chars': '600',
    'writer_rag_topic_only_snippet_limit': '8',
    'writer_rag_two_pass_research_max_sources': '2',
    'writer_rag_two_pass_snippet_limit': '20',

    # ----- Quality assurance pipeline -----
    'qa_accuracy_bad_link_max_penalty': '2.0',
    'qa_accuracy_bad_link_penalty': '0.5',
    'qa_accuracy_baseline': '7.0',
    'qa_accuracy_citation_bonus': '0.3',
    'qa_accuracy_first_person_max_penalty': '3.0',
    'qa_accuracy_first_person_penalty': '1.0',
    'qa_accuracy_good_link_bonus': '0.3',
    'qa_accuracy_good_link_max_bonus': '1.0',
    'qa_accuracy_meta_commentary_max_penalty': '2.0',
    'qa_accuracy_meta_commentary_penalty': '0.5',
    'qa_allow_first_person_niches': '',
    'qa_artifact_penalty_max': '20.0',
    'qa_artifact_penalty_per': '5.0',
    'qa_clarity_good_max_wps': '25',
    'qa_clarity_good_min_wps': '10',
    'qa_clarity_ideal_max_wps': '20',
    'qa_clarity_ideal_min_wps': '15',
    'qa_clarity_ok_max_wps': '30',
    'qa_clarity_ok_min_wps': '8',
    'qa_completeness_heading_bonus': '0.3',
    'qa_completeness_heading_max_bonus': '1.5',
    'qa_completeness_truncation_penalty': '3.0',
    'qa_completeness_word_1000_score': '5.0',
    'qa_completeness_word_1500_score': '6.0',
    'qa_completeness_word_2000_score': '6.5',
    'qa_completeness_word_500_score': '3.5',
    'qa_completeness_word_min_score': '2.0',
    'qa_critical_floor': '50.0',
    'qa_engagement_baseline': '6.0',
    'qa_fk_target_max': '12.0',
    'qa_fk_target_min': '8.0',
    'qa_llm_buzzword_fail_threshold': '5',
    'qa_llm_buzzword_max_penalty': '5.0',
    'qa_llm_buzzword_penalty_per': '0.5',
    'qa_llm_buzzword_warn_max_penalty': '2.0',
    'qa_llm_buzzword_warn_penalty_per': '0.3',
    'qa_llm_buzzword_warn_threshold': '3',
    'qa_llm_exclamation_max_penalty': '2.0',
    'qa_llm_exclamation_penalty_per': '0.3',
    'qa_llm_exclamation_threshold': '5',
    'qa_llm_filler_fail_threshold': '4',
    'qa_llm_filler_max_penalty': '4.0',
    'qa_llm_filler_penalty_per': '0.5',
    'qa_llm_filler_warn_penalty_per': '0.3',
    'qa_llm_filler_warn_threshold': '2',
    'qa_llm_formulaic_min_avg_words': '50',
    'qa_llm_formulaic_structure_penalty': '2.0',
    'qa_llm_formulaic_variance': '0.2',
    'qa_llm_hedge_penalty': '2.0',
    'qa_llm_hedge_ratio_threshold': '0.02',
    'qa_llm_listicle_title_penalty': '2.0',
    'qa_llm_opener_penalty': '5.0',
    'qa_llm_patterns_enabled': 'true',
    'qa_llm_repetitive_min_count': '3',
    'qa_llm_repetitive_starter_max_penalty': '4.0',
    'qa_llm_repetitive_starter_penalty_per': '1.0',
    'qa_llm_transition_min_count': '2',
    'qa_llm_transition_penalty_per': '1.0',
    'qa_pass_threshold': '70.0',
    'qa_relevance_high_coverage_score': '8.5',
    'qa_relevance_low_coverage_score': '5.5',
    'qa_relevance_med_coverage_score': '7.0',
    'qa_relevance_no_topic_default': '6.0',
    'qa_relevance_none_coverage_score': '3.0',
    'qa_relevance_stuffing_hard_density': '5.0',
    'qa_relevance_stuffing_soft_density': '3.0',
    'qa_seo_baseline': '6.0',
    'qa_title_originality_enabled': 'true',
    'qa_title_similarity_threshold': '0.6',
    'qa_topic_dedup_hours': '48',

    # ----- Topic discovery / dedup / ranking -----
    'niche_batch_expires_days': '7',
    'niche_carry_forward_decay_factor': '0.7',
    'niche_embedding_model': 'nomic-embed-text',
    'niche_goal_descriptions': '',
    'niche_ollama_chat_timeout_seconds': '60.0',
    'niche_top_n_per_pool': '5',
    'topic_dedup_engine': 'word_overlap',
    'topic_dedup_existing_threshold': '0.7',
    'topic_dedup_intra_batch_threshold': '0.65',
    'topic_discovery_category_searches': '',
    'topic_discovery_ideation_lookback_days': '30',
    'topic_discovery_length_distribution': '',
    'topic_discovery_news_patterns': '',
    'topic_discovery_streak_window_hours': '6',
    'topic_discovery_style_distribution': '',

    # ----- Content router / writer / self-review -----
    'content_router_contradiction_review_max_tokens': '1500',
    'content_router_contradiction_revise_max_tokens': '8000',
    'content_router_contradiction_timeout_seconds': '120',
    'content_router_qa_rewrite_max_tokens': '8000',
    'content_router_qa_rewrite_timeout_seconds': '240',
    'content_router_seo_title_max_tokens': '4000',
    # why: advisory rail, cheap to run, data flows to audit_log for tuning per 2026-05-10 Lane D close-out
    'deepeval_enabled': 'true',
    'enable_training_capture': 'true',
    'enable_writer_self_review': 'false',
    # why: advisory rail, cheap to run, data flows to audit_log for tuning per 2026-05-10 Lane D close-out
    'guardrails_enabled': 'true',
    # why: advisory rail, cheap to run, data flows to audit_log for tuning per 2026-05-10 Lane D close-out
    'ragas_enabled': 'true',
    'ragas_judge_model': '',
    'self_consistency_enabled': 'false',

    # ----- Image generation -----
    'enable_sdxl_warmup': '',
    'image_model': 'sdxl_lightning',
    'image_negative_prompt': '',
    'image_styles': '',

    # ----- Video / podcast / TTS -----
    'audio_gen_engine': '',
    'podcast_description': '',
    'podcast_name': '',
    'podcast_tts_engine': '',
    'scheduled_publisher_poll_seconds': '60',
    'tts_acronym_replacements': '',
    'tts_pronunciations': '',
    'video_compositor': '',
    'video_feed_name': '',
    'video_negative_prompt': '',
    'video_tts_engine': '',

    # ----- Voice agent -----
    'voice_agent_brain': 'ollama',
    'voice_agent_brain_mode': 'ollama',
    'voice_agent_identity': 'poindexter-bot',
    'voice_agent_livekit_enabled': 'true',
    'voice_agent_livekit_url': '',
    'voice_agent_llm_model': 'glm-4.7-5090:latest',
    'voice_agent_ollama_url': 'http://localhost:11434/v1',
    'voice_agent_public_join_url': '',
    'voice_agent_recall_k': '3',
    'voice_agent_recall_min_similarity': '0.5',
    'voice_agent_room_name': 'poindexter',
    'voice_agent_system_prompt': '',
    'voice_agent_tts_speed': '1.0',
    'voice_agent_tts_voice': 'bf_emma',
    'voice_agent_vad_stop_secs': '0.2',
    # voice_agent_webrtc_* defaults retired 2026-05-08 — livekit is the
    # canonical voice surface. Existing app_settings rows from migrations
    # 0108 + 20260505 stay (orphan but harmless); no new installs seed them.
    'voice_agent_whisper_model': 'base',

    # ----- Devto / external publishing -----
    'devto_api_base': 'https://dev.to/api',
    'mastodon_instance_url': '',

    # ----- Newsletter / email -----
    'newsletter_batch_delay_seconds': '2',
    'newsletter_batch_size': '50',
    'newsletter_enabled': 'false',
    'newsletter_from_name': '',
    'newsletter_provider': 'resend',
    'smtp_host': '',
    'smtp_port': '587',
    'smtp_use_tls': 'true',

    # ----- Storage / R2 -----
    'r2_public_url': '',

    # ----- Observability / monitoring -----
    'enable_pyroscope': 'false',
    'enable_tracing': 'false',
    'langfuse_host': '',
    'langfuse_tracing_enabled': 'true',
    'pyroscope_server_url': 'http://pyroscope:4040',
    'sentry_enabled': 'true',
    'template_runner_progress_streaming': 'true',
    'template_runner_use_postgres_checkpointer': 'false',

    # ----- Security / auth -----
    'max_approval_queue': '3',
    'oauth_issuer_url': '',

    # ----- Logging -----
    'max_log_backup_count': '3',
    'max_log_size_mb': '5',

    # ----- Brain daemon -----
    'brain_anomaly_baseline_window_days': '30',
    'brain_anomaly_current_window_hours': '24',

    # ----- Title originality / SEO -----
    'google_sitemap_ping_url': 'https://www.google.com/ping',
    'indexnow_key': '',
    'indexnow_ping_url': 'https://api.indexnow.org/indexnow',
    'title_originality_cache_ttl_hours': '24',
    'title_originality_external_check_enabled': 'true',
    'title_originality_external_penalty': '-50',

    # ----- Auto-publish gate (dev_diary) -----
    'dev_diary_auto_publish_dry_run': 'true',
    'dev_diary_auto_publish_max_edit_distance': '50',
    'dev_diary_auto_publish_min_clean_runs': '3',
    'dev_diary_auto_publish_threshold': '-1',

    # ----- Gitea / external integrations -----
    'publish_quiet_hours': '',

    # ----- Trusted source domains -----
    'trusted_source_domains': '',

    # ----- Misc -----
    'pexels_api_base': 'https://api.pexels.com/v1',

    # ----- Shared httpx.AsyncClient (lifespan-bound, services/http_client.py) -----
    # The whole worker / coordinator process shares ONE httpx.AsyncClient
    # so the connection pool stays warm across 100+ per-task HTTP calls
    # (Ollama / SDXL / Pexels / Discord / Vercel). Per-call timeouts at
    # the request site override these defaults when a specific caller
    # needs aggressive cutoffs (health checks) or generous ones (LLM gen).
    'shared_http_client_timeout_seconds': '30.0',
    'shared_http_client_max_connections': '100',
    'shared_http_client_max_keepalive': '20',

}


async def seed_all_defaults(pool: Any) -> int:
    """Insert every DEFAULTS entry into app_settings, skipping existing rows.

    Returns the count of rows actually inserted (i.e. fresh-install gap
    closed). On an up-to-date DB this is 0.

    Operator-tuned values survive — the ``ON CONFLICT (key) DO NOTHING``
    clause means an existing row is never overwritten by this seeder.

    Args:
        pool: An asyncpg pool. ``DatabaseService`` instances expose this
            as ``database_service.pool``; the migrate CLI uses a bare
            pool directly.

    Returns:
        Number of rows inserted (0 ≤ n ≤ len(DEFAULTS)).
    """
    if pool is None:
        return 0

    inserted = 0
    async with pool.acquire() as conn:
        for key, value in DEFAULTS.items():
            # asyncpg returns "INSERT 0 1" / "INSERT 0 0" status string.
            # We parse the count off the end to know whether ON CONFLICT
            # fired or the row was actually new.
            status = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active, updated_at)
                VALUES
                    ($1, $2, 'general',
                     'Auto-seeded by services.settings_defaults (#379)',
                     FALSE, TRUE, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
            )
            try:
                # Status looks like "INSERT 0 N"
                if status.endswith(" 1"):
                    inserted += 1
            except Exception:
                pass
    return inserted


def keys() -> list[str]:
    """Return the sorted list of keys this module knows about.

    Useful for diagnostics (``poindexter setup --check`` could compare
    DEFAULTS.keys() against the live DB to flag drift).
    """
    return sorted(DEFAULTS.keys())
