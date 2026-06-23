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

    # ----- Off-machine backup (Tier 2, #386) -----
    # Non-secret tunables for the backup-offsite restic loop + the brain
    # offsite_backup_watch probe. Seeded every boot (reaches existing
    # deployments, not just fresh installs). The 3 secrets
    # (offsite_backup_restic_password / _s3_access_key_id /
    # _s3_secret_access_key) are NOT here — the wizard writes them encrypted.
    'offsite_backup_enabled': 'true',
    'offsite_backup_interval': '24h',
    'offsite_backup_keep_daily': '7',
    'offsite_backup_keep_monthly': '6',
    'offsite_backup_keep_weekly': '4',
    'offsite_backup_max_age_hours': '26',
    'offsite_backup_prune_enabled': 'false',
    'offsite_backup_repository': '',
    'offsite_backup_restic_image': 'restic/restic:0.16.4',
    'offsite_backup_s3_region': '',
    'offsite_backup_source_tier': 'daily',
    'offsite_backup_verify_enabled': 'true',
    'offsite_backup_verify_interval_hours': '168',
    'offsite_backup_verify_read_data_subset_percent': '5',
    'offsite_backup_watch_enabled': 'true',
    'offsite_backup_watch_max_retries': '2',
    'offsite_backup_watch_retry_delay_seconds': '120',

    # Non-secret tunables for the brain auto_embed_watch probe — the
    # self-heal-before-paging liveness watch for the auto-embed sidecar
    # (sibling of offsite_backup_watch). Reads the auto_embed_succeeded
    # audit_log heartbeat that scripts/auto-embed.py stamps each run; stale =>
    # `docker restart poindexter-auto-embed`, then a warning-level
    # auto_embed_stale alert on escalate. Seeded every boot. auto-embed runs
    # hourly, so 6h ~= 6 missed cycles before paging.
    'auto_embed_max_age_hours': '6',
    'auto_embed_watch_enabled': 'true',
    'auto_embed_watch_max_retries': '2',
    'auto_embed_watch_retry_delay_seconds': '120',

    # ----- Cost / billing -----
    'daily_spend_limit_usd': '2.0',
    'monthly_spend_limit_usd': '100.0',
    # Electricity ledger (cost_ledger.get_spend): prefer the brain's measured PSU
    # rows; fall back to per-call kWh estimates for windows the measured feed
    # didn't cover (HX1500i sampling has been flaky). A sample "covers" up to
    # *_gap_minutes after it; below *_min_coverage_pct of the window => estimated.
    'electricity_measured_min_coverage_pct': '80',
    'electricity_source_gap_minutes': '15',

    # ----- LLM model selection -----
    'default_ollama_model': 'auto',
    'embed_model': 'nomic-embed-text',
    'inline_image_prompt_model': 'llama3:latest',
    'local_llm_api_url': 'http://localhost:11434',
    'model_role_image_decision': 'qwen3:8b',
    'pipeline_architect_timeout_seconds': '120.0',
    # why: VRAM guard against the writer (~20GB) + SDXL (~12GB) overlap
    # at the stage-5→stage-7 boundary. Default-on fixes the 24GB-card OOM;
    # operators on 80+GB hardware can flip to 'false' to skip the
    # ~3-5s reload tax (see services/llm_providers/ollama_unload.py).
    'pipeline_explicit_writer_unload_before_sdxl': 'true',
    'pipeline_fallback_model': 'ollama/gemma-4-31B-it-qat:latest',
    # Daily-driver content writer. gemma-4-31B won the 2026-06-18 writer bakeoff
    # (98/100): it names grounded specifics without the glm writer's [placeholder]
    # hedging or qwen2.5's stat fabrication, and rarely needs the rescue. The
    # cross-model rescue reviser is glm (qa_rewrite_model). Operators tune live.
    'pipeline_writer_model': 'ollama/gemma-4-31B-it-qat:latest',
    # Ops alert-triage (firefighter /api/triage) is a one-paragraph diagnosis,
    # NOT content — it defaults to the small free-tier model, never the 19 GB
    # writer. Unset before, it fell through to pipeline_writer_model, so a triage
    # reloaded the writer into VRAM mid-media-render and CUDA-OOM'd the SDXL
    # server (2026-06-21). A 2 GB model coexists with wan + SDXL on a 32 GB card,
    # so triage can't oversubscribe the GPU even when un-gated.
    'ops_triage_writer_model': 'ollama/llama3.2:3b',
    # Video director + self-critique run on the writer model — scene judgment is
    # the top video-quality lever (video-quality spec §3.1). One shared key feeds
    # both the generate_video_shot_list draft pass and the review_video_shot_list
    # critique; kept equal to pipeline_writer_model (asserted in tests). Was unset
    # before, falling through to default_ollama_model=auto → weak standard tier.
    'video_director_model': 'ollama/gemma-4-31B-it-qat:latest',
    # Per-call ceiling (seconds) for the director LLM dispatch — long + short
    # shot lists. Writer-grade director models (gemma-4-31B) emit a full
    # structured shot list (max_tokens 6144) and need well over the old
    # hardcoded 120s, which was timing out and leaving an empty shot list so
    # Stage-2 video never rendered. Read via cfg.get_int in
    # modules/content/stages/generate_video_shot_list.py.
    'video_director_timeout_seconds': '300',
    # Per-shot vision-QA render-check loop (video-quality spec §3.2). The render
    # loop in shot_list_renderer scores each rendered frame with qa_vision_model
    # and regenerates (stochastic sources) or falls back to holdover on a miss.
    # site_config=None (legacy/test path) disables it regardless of this default.
    'video_shot_qa_enabled': 'true',
    'video_shot_qa_threshold': '60',
    'video_shot_qa_max_retries': '2',
    # Caption ASR engine for media.transcribe_narration. Default 'speaches'
    # reuses the already-running Speaches faster-whisper sidecar (narration TTS /
    # voice STT) instead of a second whisper.cpp install. The prior default,
    # 'whisper_local', shelled a whisper-cli binary that was never baked into the
    # worker image — so transcribe returned success=False and BOTH video lanes
    # rendered with NO burned-in captions (2026-06-21 validation). Set
    # 'whisper_local' to use a locally-installed whisper.cpp instead. Read via
    # services/caption_providers/get_caption_provider.
    'video_caption_engine': 'speaches',
    # Speaches caption provider config (plugin.caption_provider.speaches.*). The
    # base_url is the same OpenAI-compatible host the TTS path already uses; the
    # model is the faster-whisper weight Speaches serves. enabled is a kill switch
    # that fails loud (success=False) rather than silently producing no captions.
    'plugin.caption_provider.speaches.enabled': 'true',
    'plugin.caption_provider.speaches.base_url': 'http://speaches:8000/v1',
    'plugin.caption_provider.speaches.model': 'Systran/faster-whisper-medium',
    'plugin.caption_provider.speaches.timeout_seconds': '180',
    # initial_prompt biases faster-whisper toward the supplied vocabulary (the
    # OpenAI 'prompt' field → initial_prompt). A short comma-separated list of
    # proper nouns nudges the decoder's spelling toward them, so a brand or
    # product name isn't transcribed as an acoustically similar word. Soft bias,
    # not find-replace. Default '' (the NOT-NULL unset sentinel) = no bias =
    # unchanged behaviour; the empty OSS default keeps operator-specific brand
    # vocabulary out of the public mirror — operators set their terms in the DB.
    'plugin.caption_provider.speaches.initial_prompt': '',
    # GPU scheduler — external (non-stack) workload detection. The stack is
    # normally the only thing running models, so cross-process GPU contention is
    # already serialized by the pg_advisory_lock + asyncio.Lock; treating a
    # sibling stack process's legitimate GPU use as "gaming" only causes phantom
    # pipeline pauses (validation finding 4a). Default OFF — set true only when
    # sharing this GPU with a non-stack app (e.g. a game on the same box). Read
    # via _cfg_bool in services/gpu_scheduler.py::_wait_for_gaming_clear.
    'gpu_external_workload_wait_enabled': 'false',
    # GPU power/util are read from Prometheus (which scrapes + caches the
    # nvidia-smi exporter), NOT from the exporter directly. Prometheus serves
    # the last scrape instantly and never blocks on a slow nvidia-smi under
    # render load, and container-internal DNS (prometheus:9090) sidesteps the
    # Windows Docker host-port-forward wedge that made the direct
    # host.docker.internal:9835 read flap with RemoteDisconnected (2026-06-21).
    # Read via services/gpu_scheduler.py::_prometheus_query_url.
    'gpu_metrics_prometheus_url': 'http://prometheus:9090',
    # GPU-serialize fix: hold gpu.lock("ollama") around every LOCAL LLM dispatch
    # (services/llm_providers/dispatcher.py::dispatch_complete) so scheduled
    # worker jobs (topic research, SEO, newsletter) can't load the ~19GB writer
    # concurrently with a media render and blow past 32GB VRAM. Reentrant, so
    # it's a no-op inside content stages that already hold the lock. Default ON;
    # operators with abundant VRAM can flip to 'false' to skip the serialization.
    'gpu_serialize_llm_dispatch': 'true',
    # why: when true, after issuing keep_alive=0 the unload helper re-polls
    # Ollama /api/ps until the model is actually gone BEFORE the next
    # (SDXL/video) model loads — instead of blind-sleeping and hoping. On a
    # single 32GB GPU shared with the Windows desktop, returning while the
    # 18GB writer is still resident overlaps it with the incoming diffusion
    # model, exhausts VRAM, and freezes WDDM. See
    # services/llm_providers/ollama_unload.py. Set false to fall back to the
    # legacy blind pipeline_writer_unload_grace_seconds sleep.
    'pipeline_writer_unload_confirm_enabled': 'true',
    # Upper bound (seconds) on the confirm poll. If the model is still
    # resident after this window the helper logs a WARNING and proceeds —
    # it never hangs the pipeline. Generous default: an 18GB evict is
    # typically 1-5s; 15s leaves headroom under render load.
    'pipeline_writer_unload_confirm_timeout_seconds': '15',
    # why: asyncio.sleep() after issuing keep_alive=0 so Ollama actually
    # releases VRAM before the inline-image /generate lands. Only used when
    # pipeline_writer_unload_confirm_enabled=false (legacy fallback). 2s is
    # the sweet spot — long enough for the kernel to free, short enough to
    # stay invisible in pipeline latency.
    'pipeline_writer_unload_grace_seconds': '2',
    # Interval (seconds) between confirm-poll /api/ps checks. Smaller =
    # tighter handoff (less wasted wait once the model frees), more polls.
    'pipeline_writer_unload_poll_interval_seconds': '0.5',
    'qa_fallback_writer_model': 'ollama/gemma-4-31B-it-qat:latest',
    # Cross-model rescue reviser. The qa.rewrite step routes here instead of the
    # writer (pipeline_writer_model). Default glm-4.7: cautious and never
    # fabricates — the ideal final-pass reviser. Its [placeholder] weakness is a
    # from-scratch-WRITER failure mode; when revising already-concrete text it
    # just applies the targeted fix. Cross-model vs the gemma writer so their
    # biases cancel (#1692 bakeoff). Empty = use the writer model.
    'qa_rewrite_model': 'ollama/glm-4.7-5090:latest',
    # poindexter#716: vision QA model keys — seeded here so the DB always has
    # a value and code never falls back to a hardcoded literal.  Empty string =
    # operator deliberately cleared the key — the vision check is skipped.
    'qa_preview_vision_model': 'ollama/qwen3-vl:30b',
    'qa_vision_model': 'ollama/qwen3-vl:30b',
    'qa_vision_num_predict': '1024',  # #563: room for qwen3-vl <think> + JSON verdict
    # why: structured-JSON extraction calls (topic discovery distill +
    # candidate ranking) need a JSON-reliable INSTRUCT model. The writer
    # model (pipeline_writer_model) may be a reasoning model that returns
    # empty content under response_format=json_object — which crashed the
    # whole topic-discovery sweep (2026-05-28 content-gen stall). Kept
    # separate + DB-configurable so operators can pin a writing model
    # without breaking structured extraction.
    'structured_extraction_model': 'ollama/gemma-4-31B-it-qat:latest',
    # poindexter#716: vision alt-text + media-qa human-detect model key.
    # The baseline seeds this as 'qwen3-vl:30b'; seeded here too so fresh
    # installs without the baseline seeds can still get a sensible default.
    'vision_alt_model': 'qwen3-vl:30b',
    'use_ollama': 'false',
    # Boot-time validation of *_model / cost_tier.*.model keys against
    # installed Ollama models (glad-labs-stack#1284). Flip to 'false' on
    # non-Ollama deployments or when Ollama is deliberately unreachable at
    # startup (e.g. remote-only LiteLLM routing).
    'ollama_model_validation_enabled': 'true',

    # ----- LLM providers / endpoints -----
    'flux_schnell_server_url': '',
    'ollama_base_url': 'http://host.docker.internal:11434',
    'plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.output_format': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate': '',
    'plugin.audio_gen_provider.stable-audio-open-1.0.server_url': '',
    'plugin.image_provider.flux_schnell.server_url': '',
    'plugin.llm_provider.gemini.enabled': 'false',
    # Refuse paid base_url targets unless explicitly opted in. The default
    # base_url is host.docker.internal:11434/v1 (Ollama) so most installs
    # never trip this gate; setting it true is required to dispatch to
    # Groq / OpenRouter / Together / Fireworks / Anthropic-OAI-compat.
    # Enforced by services/llm_providers/openai_compat.py per
    # feedback_no_paid_apis.
    'plugin.llm_provider.openai_compat.allow_paid_base_url': 'false',
    # Same gate, one layer up: LiteLLM is the default router for every
    # cost tier (free/budget/standard/premium/flagship) and auto-discovers
    # OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY from env. A bare
    # model string like 'openai/gpt-4o' with a stray env var fires a paid
    # call. Default-deny refuses both non-local api_base AND non-local
    # model prefixes; flip to 'true' to authorise any paid LiteLLM path.
    'plugin.llm_provider.litellm.allow_paid_base_url': 'false',
    'plugin.video_provider.wan2.1-1.3b.server_url': '',
    'sdxl_server_url': 'http://host.docker.internal:9836',
    'stable_audio_open_server_url': '',
    'video_server_url': 'http://host.docker.internal:9837',
    'wan_server_url': '',

    # ----- RAG / retrieval -----
    'niche_internal_rag_per_kind_limit': '5',
    'niche_internal_rag_snippet_max_chars': '600',
    # Cap internal_rag's discovery-batch share (finding #5); 1.0 disables.
    'niche_internal_rag_batch_share_cap': '0.5',
    'rag_default_top_k': '5',
    'rag_embed_retry_attempts': '3',
    'rag_embed_retry_base_delay_seconds': '0.25',
    'rag_hybrid_enabled': 'false',
    'rag_min_similarity': '0.3',
    'rag_rerank_enabled': 'false',
    'rag_rerank_model': 'cross-encoder/ms-marco-MiniLM-L-6-v2',
    'rag_rerank_device': 'cpu',
    'rag_rrf_k': '60',
    # CSV of embeddings.source_table values the writer's research RAG may draw
    # from. MUST default to content-only ('posts'): an empty value means "all
    # tables", which pulls claude_sessions / brain / audit ops-logs into the
    # writer's grounding context and leaks meta-commentary + agent instructions
    # into drafts (2026-06 contamination incident). Operators add more content-
    # bearing tables (e.g. 'posts,samples') as their corpus grows.
    'rag_source_filter': 'posts',
    # Minimum acceptable writer-draft length; below this the draft is treated
    # as a generation failure (empty/too-short → status='failed' + finding,
    # not a misleading reviewer_count:0 QA reject). A real canonical_blog post
    # is never this short — a sub-threshold draft means the reasoning writer
    # model returned (near-)empty content. poindexter#691.
    'writer_min_draft_chars': '200',
    'writer_rag_context_snippet_max_chars': '500',
    'writer_rag_research_topic_max_sources': '2',
    'writer_rag_two_pass_research_max_sources': '2',
    'writer_rag_two_pass_snippet_limit': '20',
    # Web-research grounding. When true (default) the writer's research step
    # FETCHES and extracts real page text from each web source (up to
    # web_research_max_content_chars) via WebResearcher.search — instead of
    # just a title + a 100-char DuckDuckGo snippet — so drafts cite sourced
    # facts/numbers rather than inventing them. Flip false for the cheaper
    # snippet-only path (search_simple) when fetch latency/bandwidth matters.
    # research_web_content_chars_per_source caps how much of each source's
    # extracted text is injected into the generation prompt (token budget;
    # 600 ≈ one substantial paragraph × up-to-5 sources).
    'research_extract_web_content': 'true',
    'research_web_content_chars_per_source': '600',

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
    'qa_allow_first_person_niches': 'dev_diary,glad-labs',
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
    # Points the programmatic validator shaves per (non-critical) warning when
    # scoring an otherwise-clean draft. Default 5 (was a hard-coded 10): soft
    # nits should nudge the score, not sink a clean post under the QA gate.
    'qa_validator_warning_penalty': '5.0',
    'title_max_length': '90',
    'qa_topic_dedup_hours': '48',
    # Web fact-check rail (qa.web_factcheck) claim-verification heuristics.
    # A claim is treated as VERIFIED when at least `match_ratio` of its key
    # terms (tokens longer than `min_term_len` chars) appear in the first
    # `snippet_chars` characters of the top `num_results` DuckDuckGo hits.
    # `max_claims` caps how many extracted claims are searched per post.
    # Previously hardcoded inline in modules/content/multi_model_qa.py; moved
    # here so the rail's strictness is tunable without a redeploy.
    'qa_web_factcheck_match_ratio': '0.6',
    'qa_web_factcheck_num_results': '3',
    'qa_web_factcheck_snippet_chars': '500',
    'qa_web_factcheck_min_term_len': '2',
    'qa_web_factcheck_max_claims': '3',
    # QA rescue cycle: max bounded rewrite passes before a salvageable reject is
    # hard-rejected. Default 2 = write -> qa -> revise -> qa -> revise (the
    # one-shot default was 0-for-4 at saving a post). 0 disables; clamped [0,3].
    # Fabrication/gate/missing_required vetoes are never rescued — only soft
    # critic vetoes + below-threshold scores. The cycle keeps the best-scoring
    # draft across passes (qa.aggregate keep-best guard), so a worse revision
    # never replaces a better earlier draft.
    'qa_rewrite_max_attempts': '2',

    # Self-heal before paging (#qa-self-heal): when true, qa.aggregate stops
    # discarding a non-approvable draft — after the bounded regen cycle it FLAGS
    # the draft (qa_flagged) and rides the forward edge to awaiting_approval with
    # the per-rail findings attached, never writing rejected/rejected_final
    # (operator-only). Default 'true' since 2026-06-22: shipped OFF behind this
    # switch, e2e-verified live (task ce1f7499 — a 94-score no-veto draft flagged
    # to awaiting_approval instead of discarded), then flipped on prod + here so
    # fresh installs default to never-discard. 'false' restores legacy discard.
    'qa_flag_instead_of_reject': 'true',

    # ----- Topic discovery / dedup / ranking -----
    'niche_batch_expires_days': '7',
    'niche_carry_forward_decay_factor': '0.7',
    'niche_embedding_model': 'nomic-embed-text',
    'niche_goal_descriptions': '',
    'niche_ollama_chat_timeout_seconds': '300.0',
    'niche_top_n_per_pool': '5',
    # Manual-injection dedup (create_post MCP tool / POST /api/tasks): cosine
    # similarity at/above which a caller-supplied topic is refused (409) as a
    # near-duplicate of an already-published post. Auto-discovered topics use
    # the topic_dedup_* engine above; this guards the manual path. force=true
    # overrides. See services/topic_dedup_guard.py.
    'create_post_dedup_threshold': '0.75',
    'topic_dedup_engine': 'word_overlap',
    'topic_dedup_existing_threshold': '0.7',
    'topic_dedup_intra_batch_threshold': '0.65',
    'topic_discovery_category_searches': '',
    'topic_discovery_ideation_lookback_days': '30',
    'topic_discovery_length_distribution': '',
    'topic_discovery_news_patterns': '',
    'topic_discovery_streak_window_hours': '6',
    'topic_discovery_style_distribution': '',
    # Stale-batch reaper (services/jobs/reap_stale_topic_batches.py). A
    # topic_batch stuck status='open' wedges its niche content-dark via the
    # one-open-batch-per-niche index. The reaper alerts on any batch open
    # past _stuck_hours and — when _reaper_enabled — auto-expires the dead
    # (already past expires_at) ones to self-heal the niche. Default OFF:
    # flipping it on auto-clears expired batches, which reactivates sweeps on
    # any active niche that has since moved to its own content path (e.g.
    # dev_diary's daily cron) — an explicit operator decision. Set such a
    # niche active=false first; the reaper is scoped to active niches.
    'topic_batch_reaper_enabled': 'false',
    'topic_batch_stuck_hours': '24',

    # ----- Content router / writer / self-review -----
    'content_router_contradiction_review_max_tokens': '1500',
    'content_router_contradiction_revise_max_tokens': '8000',
    'content_router_contradiction_timeout_seconds': '120',
    'content_router_qa_rewrite_max_tokens': '8000',
    # The qa.rewrite rescue pass is a writer-class full-revision LLM call, so it
    # gets the same budget as the writer (niche_ollama_chat_timeout_seconds=600).
    # The inherited 240s default (orphaned from the deleted cross_model_qa) was
    # too low: under glm<->gemma single-GPU thrash the revise call always timed
    # out and the rescue (#1674) was silently skipped on salvageable drafts.
    'content_router_qa_rewrite_timeout_seconds': '600',
    'content_router_seo_title_max_tokens': '4000',
    # Content-validator per-category promotion thresholds. 0 = never promote
    # this warning category to a hard critical (Glad-Labs/poindexter#692):
    # both rules are pattern heuristics that can't tell a fabricated external
    # ref from a rhetorical phrase / real post-cutoff product / internal file,
    # so the hard veto lives with the LLM critic + qa.web_factcheck (#661).
    # Raise > 0 to re-arm count-based promotion for that category.
    'content_validator_hallucinated_reference_warning_threshold': '0',
    'content_validator_unlinked_citation_warning_threshold': '0',
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
    'self_consistency_sample_count': '3',
    'self_consistency_threshold': '0.7',
    # Citation reconciliation + advisory unlinked-attribution rail (#765).
    # why: deterministic repair that re-links named sources the writer dropped
    # the URL for, matched against the research corpus by domain handle — free,
    # high-precision, on by default.
    'citation_reconcile_enabled': 'true',
    # why: re-point pass in the same atom — swaps a writer-fabricated path on a
    # single-brand corpus domain for that source's real URL (a 404 the trusted-
    # host scrub keeps) before qa.citations flags it dead. On by default.
    'citation_repoint_enabled': 'true',
    # Multi-tenant hosts where "same domain, different path" = DIFFERENT content
    # (re-pointing would mis-cite), so the re-point pass skips them. Empty =
    # use the built-in DEFAULT_MULTITENANT_HOSTS denylist; a non-empty CSV
    # REPLACES it wholesale (mirrors trusted_source_domains' override semantics).
    'citation_repoint_multitenant_hosts': '',
    # why: advisory rail scoring named-source attributions left unlinked +
    # unmatched against the corpus; on by default, feeds qa_feedback + Grafana.
    'unlinked_attribution_enabled': 'true',
    # Gentle score: each unmatched attribution shaves N points down to a floor,
    # so a single missing link nudges the weighted QA mean without sinking a post.
    'unlinked_attribution_penalty_per': '8',
    'unlinked_attribution_score_floor': '60',

    # ----- Image generation -----
    'enable_sdxl_warmup': '',
    # Worker in-process diffusers registry default (services/image_providers).
    # The live render path is the SDXL HTTP server, which reads the separate
    # 'image_generation_model' key (seeded in 0000_baseline.seeds.sql); both
    # point at z_image_turbo as of the 2026-06-19 bake-off. #image-zimage-and-variety.
    'image_model': 'z_image_turbo',
    # Operator-supplied negative prompt overrides the built-in default.
    # Leave empty to keep "text, words, letters, watermark, face, person, ..."
    # (Ignored by guidance-distilled models like z_image_turbo, which run at
    # CFG 0 and take no negative prompt.)
    'image_negative_prompt': '',
    # Style suffix appended to every SDXL prompt — niche brand voice.
    # Examples: "cyberpunk, neon accents" (tech), "natural light, botanical" (gardening)
    'image_base_style_prompt': '',
    # Pexels orientation default for featured + inline images.
    # Options: landscape (default) | portrait | square
    'image_aspect_ratio': 'landscape',
    # Comma-separated fallback keywords for Pexels when the semantic query
    # returns zero results.  Leave empty to use the built-in generic list.
    # Example for gardening: "plants, garden, outdoor, nature, floral"
    'image_pexels_fallback_keywords': '',
    'image_styles': '',
    # Inline-illustration style pool (JSON array of style strings). Empty =>
    # the stylized code fallback (modules/content/stages/replace_inline_images.py
    # INLINE_STYLES). Parallels 'image_styles' for the featured image. Photoreal
    # styles were dropped from the fallback — low-step SDXL butchers photoreal
    # detail and the brand is stylized. #image-zimage-and-variety.
    'inline_image_styles': '',
    # Cross-post style-dedup window: how many recently-published posts' image
    # styles to filter out when picking the next featured style
    # (source_featured_image._load_recent_published_styles). 0 disables the
    # cross-post filter (in-process per-worker dedup still applies).
    'image_style_dedup_window': '5',
    # In-process featured-image style-rotation dedup window
    # (services/image_style_rotation.py). `size` caps how many recent picks
    # are remembered; `ttl_seconds` caps how long a pick blocks its own reuse.
    # Previously module-level constants; externalised so the rotation window
    # is tunable (a small style pool wants a shorter window to avoid starving).
    'image_style_history_size': '10',
    'image_style_history_ttl_seconds': '3600',
    # Per-call HTTP timeout (seconds) for a local image inference server
    # (SDXL / FLUX / Z-Image `/generate`) to render one image. Must cover a
    # COLD model load: the SDXL server unloads after 60s idle (so Ollama can
    # use the GPU) and is re-evicted on every Ollama call, so most renders pay
    # the reload. Measured cold-load for Z-Image-Turbo (6B) is ~133s + render;
    # 90s was too tight and silently fell back to Pexels / failed the shot.
    # 240 gives headroom while still bounding a genuinely hung GPU server.
    # Wired into the featured + inline + video render calls. #image-zimage-and-variety.
    'image_render_timeout_seconds': '240',
    # LLM params for the image-PROMPT generation step — the small model that
    # writes the SDXL prompt from the topic + chosen style (NOT the image
    # render itself). Externalised so prompt creativity / length / patience are
    # tunable without a code edit. #image-zimage-and-variety.
    'image_prompt_temperature': '0.8',
    'image_prompt_max_tokens': '150',
    'image_prompt_timeout_seconds': '90',

    # ----- Video / podcast / TTS -----
    'audio_gen_engine': '',
    # Per-call HTTP timeout (seconds) for a local audio inference server
    # (Stable Audio Open `/generate`) to render one clip. Was a hardcoded
    # literal in services/audio_gen_providers/stable_audio_open.py.
    'audio_render_timeout_seconds': '180',
    # Stage-2 media trigger (#689 Plan 7) — the dispatch_media_pipeline job is
    # scheduled but DORMANT until media_pipeline_trigger_enabled flips on; this
    # is what takes media_pipeline from dormant to LIVE in prod.
    # media_pipeline_max_per_cycle caps GPU-bound renders kicked off per cycle.
    'media_pipeline_max_per_cycle': '1',
    'media_pipeline_trigger_enabled': 'false',
    # media_distribute (#689 Plan 8 / 8b-2) links rendered media_assets to their
    # published post + seeds Gate-2 approvals; caps assets linked per cycle.
    # Gated on the same media_pipeline_trigger_enabled master switch.
    'media_distribute_max_per_cycle': '20',
    # Stage-3 podcast lane (#689 deviation — separate isolated graph). Its own
    # master switch so podcast goes live independently of the video media_pipeline
    # (default off — safe for OSS forks; the operator flips prod to 'true').
    # The dispatch job caps renders/cycle; distribute caps link+seed+deliver/cycle.
    'podcast_pipeline_trigger_enabled': 'false',
    'podcast_pipeline_max_per_cycle': '2',
    'podcast_distribute_max_per_cycle': '20',
    # Per-medium call-to-action outros (DB-tunable; ML-optimizable later).
    # ``media.cta.podcast`` is LIVE — ``podcast.render`` appends it to the script
    # before TTS so the episode asks for ratings/reviews. The video CTAs are
    # seeded ahead of their reader: the video render appends them as an end beat,
    # which lands with the deferred video-side half of #689 (the video render
    # shares the base narration, so a spoken video CTA needs its own render path —
    # see docs/architecture/podcast-pipeline-stage3.md §11).
    'media.cta.podcast': (
        'If this was useful, follow the show and leave a quick rating or review '
        'on Spotify or Apple Podcasts — it genuinely helps us reach more people.'
    ),
    'media.cta.video': 'If this helped, like the video and subscribe for more.',
    'media.cta.video_short': 'Follow for more — like and subscribe.',
    # Gate-2 earned-autonomy (#531) — automatic Tier-2 approval when the last N
    # dispatches for a (niche, medium) combo all succeeded. Disabled by default;
    # operator flips media.gate2.earned_autonomy_enabled to 'true' once they are
    # satisfied with the track record. min_dispatches is the consecutive-success
    # window; per-niche overrides (niche.<slug>.media.<medium>.earned_autonomy_
    # min_dispatches) take precedence over the global value when set.
    'media.gate2.earned_autonomy_enabled': 'false',
    'media.gate2.earned_autonomy_min_dispatches': '5',
    'podcast_description': '',
    'podcast_name': '',
    'podcast_tts_engine': '',
    'podcast_tts_enabled': 'false',
    'podcast_tts_base_url': 'http://speaches:8000/v1',
    'podcast_tts_voice': 'bf_emma',
    'podcast_tts_model': 'speaches-ai/Kokoro-82M-v1.0-ONNX',
    'podcast_tts_format': 'mp3',
    # Normalize the audio after Speaches byte-concatenates its internal segments
    # (else players cut off mid-episode AND transcoders can mishandle the
    # multi-header structure at the tail). Fail-soft; needs ffmpeg.
    'podcast_tts_remux_enabled': 'true',
    # 'reencode' (default) collapses the per-segment Xing/LAME headers into one
    # clean stream; 'copy' is the legacy lossless `-c copy` header-only repair.
    'podcast_tts_remux_mode': 'reencode',
    # Output bitrate for re-encode mode (mono spoken-word; 96k is ample).
    'podcast_tts_remux_bitrate': '96k',
    'scheduled_publisher_poll_seconds': '60',
    # TTS pronunciation defaults — JSON objects operators can tune via
    # `poindexter settings set`. The code merges DB values on top of the
    # hardcoded constants in podcast_service, so DB entries add-to / override
    # defaults. See skills/content/tts/SKILL.md for format and examples.
    # NOTE: settings seeding uses ON CONFLICT DO NOTHING, so changing these
    # values here only affects new installs. Existing installs keep whatever
    # was seeded first; update via `poindexter settings set` to override.
    'tts_acronym_replacements': (
        '{"SOC": "security operations", "CRM": "customer relationship management",'
        ' "SLA": "service level agreement", "KPI": "key performance indicator",'
        ' "ROI": "return on investment", "MVP": "minimum viable product",'
        ' "POC": "proof of concept", "EOL": "end of life"}'
    ),
    'tts_pronunciations': (
        '{"VRAM": "Vee RAM", "SRAM": "Ess RAM", "DRAM": "Dee RAM",'
        ' "PB": "petabyte", "TB": "terabyte", "GB": "gigabyte",'
        ' "MB": "megabyte", "KB": "kilobyte",'
        ' "GHz": "gigahertz", "MHz": "megahertz", "kHz": "kilohertz",'
        ' "Gbps": "gigabits per second", "Mbps": "megabits per second",'
        ' "Kbps": "kilobits per second", "fps": "frames per second",'
        ' "GitFlow": "git flow", "GitHub": "git hub", "GitLab": "git lab",'
        ' "DevSecOps": "dev sec ops", "DevOps": "dev ops", "DevEx": "dev ex",'
        ' "FastAPI": "fast A P I", "PostgreSQL": "postgres", "MongoDB": "mongo D B",'
        ' "GraphQL": "graph Q L", "WebSocket": "web socket",'
        ' "TypeScript": "type script", "JavaScript": "java script",'
        ' "Next.js": "next J S", "Node.js": "node J S", "Vue.js": "view J S",'
        ' "CI/CD": "CI CD", "I/O": "I O", "TCP/IP": "TCP IP", "OS/2": "OS 2",'
        ' "e.g.": "for example", "i.e.": "that is", "etc.": "and so on",'
        ' "vs": "versus", "vs.": "versus",'
        ' "approx.": "approximately", "incl.": "including",'
        ' "w/": "with", "w/o": "without"}'
    ),
    # Voice-rotation pool (#689 Plan 7) — DB-configurable override of the
    # podcast_service VOICE_POOL constant. Default-off / empty falls back to
    # the constant (zero behavior change); an operator supplies engine-
    # appropriate, comma-separated voice names to customise rotation.
    'tts_voice_pool': '',
    'tts_voice_rotation_enabled': 'false',
    'video_compositor': '',
    'video_feed_name': '',
    'video_negative_prompt': '',
    'video_tts_engine': '',

    # ----- Voice agent -----
    'voice_agent_brain': 'ollama',
    'voice_agent_brain_mode': 'ollama',
    'voice_agent_default_identity': 'operator',
    'voice_agent_identity': 'poindexter-bot',
    'voice_agent_livekit_enabled': 'true',
    'voice_agent_livekit_url': '',
    'voice_agent_llm_model': 'glm-4.7-5090:latest',
    'voice_agent_ollama_url': 'http://localhost:11434/v1',
    'voice_agent_public_join_url': '',
    'voice_agent_public_livekit_url': '',
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

    # ----- Storage (provider-agnostic S3-compatible: R2 / S3 / B2 / MinIO) -----
    # Public base URL for the object store; consumers append the object
    # key. Replaces the deprecated ``r2_public_url`` (storage_* cutover,
    # Glad-Labs/poindexter#731).
    'storage_public_url': '',
    # Custom vanity domain for image objects (e.g. ``https://images.gladlabs.io``).
    # When set, image URLs use this base instead of the rate-limited r2.dev
    # public bucket URL. Empty = fall back to storage_public_url (poindexter#732).
    # Configure via: poindexter settings set storage_image_custom_domain https://images.gladlabs.io
    'storage_image_custom_domain': '',
    # Wait this many seconds after a post publishes before uploading
    # podcast/video/short to the object-store CDN — gives generation
    # time to finish. Storage-agnostic rename of the deprecated
    # ``media_r2_upload_delay_seconds`` (#731).
    'media_upload_delay_seconds': '240',
    # Minimum ASR-vs-script similarity ratio for the Stage-2 caption
    # fidelity check (media.transcribe_narration, Plan 5 #676). When the
    # one-ASR-pass transcript diverges below this normalized
    # SequenceMatcher ratio from the source narration script, an advisory
    # ``caption_fidelity`` finding fires (likely a TTS dropout / truncation).
    # Advisory only — never fails the render.
    'media.caption.fidelity_min_ratio': '0.80',
    # Gate for the Stage-2 media QA frame human-detection check
    # (media.qa, Plan 6 #1193). When 'true', a midpoint frame of each
    # rendered video is vision-checked for a photorealistic human (policy
    # #675). Fail-soft: a missing ffmpeg / vision error is a no-op (no
    # finding). Set 'false' to skip the vision call entirely.
    'media_qa_frame_detection_enabled': 'true',
    # Max allowed drift (seconds) between the probed render duration and the
    # director shot-list's planned total_duration_s before media.qa emits an
    # advisory ``av_desync`` finding (Plan 6 #1193). Advisory only.
    'media.qa.av_sync_tolerance_s': '2.0',

    # ----- Observability / monitoring -----
    # DataFabric store URLs (#429). DataFabric clients run inside the
    # worker/brain containers, so the defaults use compose-service DNS — a
    # 'localhost' default would resolve to the container itself (the
    # in-container footgun PR #1827 fixed for the GPU-metrics URL). Internal
    # DNS also avoids the host wslrelay port-forward that can wedge on Windows.
    # prometheus listens on 9090 internally (host-published 9091).
    'data_fabric_prometheus_url': 'http://prometheus:9090',
    'data_fabric_loki_url': 'http://loki:3100',
    'data_fabric_tempo_url': 'http://tempo:3200',
    'data_fabric_pyroscope_url': 'http://pyroscope:4040',
    'enable_pyroscope': 'false',
    # Defaulted true 2026-05-17 (Glad-Labs/poindexter#409) — the
    # OTLP gRPC exporter + Tempo container + per-probe instrumentation
    # have been live on prod since 2026-05-13. Baseline seed already
    # ships true; the in-code default was the last spot where a fresh
    # ``SiteConfig`` or missing DB row silently produced a NoopTracer
    # (spans dropped, Tempo panels empty).
    'enable_tracing': 'true',
    'langfuse_host': '',
    'langfuse_tracing_enabled': 'true',
    # Tempo's OTLP HTTP receiver on /v1/traces. Matches the exporter
    # we actually import (``opentelemetry.exporter.otlp.proto.http``).
    # The gRPC port 4317 is wrong for this exporter — using it produces
    # no spans in Tempo but no errors loud enough to notice, which is
    # exactly the silent-failure mode #505 describes.
    'otel_exporter_otlp_endpoint': 'http://tempo:4318/v1/traces',
    'pyroscope_server_url': 'http://pyroscope:4040',
    'sentry_enabled': 'true',
    'template_runner_progress_streaming': 'true',
    # Defaulted true 2026-05-17 (Glad-Labs/poindexter#412) — the
    # AsyncPostgresSaver wiring has been live on prod since 2026-05-13
    # without incident, smoke test at
    # ``scripts/smoke_371_postgres_checkpointer.py`` stays green, and the
    # baseline seeds row matches. The pre-flip default of 'false' meant
    # any fresh install or test SiteConfig without a DB row silently
    # used MemorySaver (no durability across runs) — exactly the kind of
    # silent fallback ``feedback_no_silent_defaults`` calls out.
    'template_runner_use_postgres_checkpointer': 'true',
    # Pipeline progress streaming (#361): where per-node on_event progress lands.
    # 'discord' keeps the existing Discord progress feed (on_event no-op, no double-post);
    # 'telegram' edit-streams a single message in place; 'off' silences on_event.
    'pipeline_streaming_channel': 'discord',
    'pipeline_streaming_min_edit_interval_s': '5',

    # ----- Security / auth -----
    'max_approval_queue': '3',
    'oauth_issuer_url': '',

    # ----- Logging -----
    'max_log_backup_count': '3',
    'max_log_size_mb': '5',

    # ----- Brain daemon -----
    'brain_anomaly_baseline_window_days': '30',
    'brain_anomaly_current_window_hours': '24',
    'brain_digest_window_hours': '6',

    # ----- Migration-drift in-flight guard (brain/migration_drift_probe.py, #228) -----
    # When true, the migration-drift auto-recover path defers the worker
    # restart while a content task is mid-generation (pipeline_tasks.status
    # = 'in_progress'). A restart mid-run orphans a multi-minute
    # canonical_blog task in 'in_progress' (the claim path never re-picks
    # it) until the 180-min stale sweep. Deferring lets the in-flight job
    # finish before applying pending migrations.
    'migration_drift_defer_while_inflight': 'true',
    # Safety cap on consecutive defers (≈ one per 5-min brain cycle, so 6
    # ≈ 30 min). Once reached the probe STOPS deferring and falls through
    # to the normal restart — pending migrations matter too, and a wedged
    # 'in_progress' row shouldn't block recovery forever.
    'migration_drift_max_inflight_defers': '6',
    # Auto-sync knobs (#228). Ships dark (auto_sync_enabled=false) until the
    # operator wires up the dedicated deploy checkout + bind-mount. When on, the
    # probe resyncs the checkout (git reset --hard origin/main + clean -fd)
    # before restarting, with exponential backoff across recover_max_attempts.
    'migration_drift_auto_sync_enabled': 'false',
    'migration_drift_deploy_checkout_path': '/host-deploy',
    'migration_drift_recover_max_attempts': '3',

    # ----- Cadence SLO probe (brain probe_cadence_slo, issue #525) -----
    # Compares ACTUAL publish output against this CONFIGURED target so a
    # cadence slowdown is caught within hours (existing publish_rate /
    # pipeline_throughput probes are too coarse — 3-day / 7-day windows).
    # NOT derived from prefect_content_flow_cron (that's the flow tick rate,
    # not the production target).
    'cadence_slo_enabled': 'true',
    'cadence_slo_expected_posts_per_day': '1',
    'cadence_slo_window_hours': '24',
    'cadence_slo_shortfall_ratio': '0.5',

    # ----- Scheduler job-failure escalation (#302 / alert audit) -----
    # When a scheduled job returns ok=False or raises, the scheduler emits a
    # finding (most jobs) or directly notifies the operator (alert-delivery
    # jobs). Master switch; default on so failures are never silently swallowed.
    'scheduler_alert_on_job_failure': 'true',

    # ----- Findings daily digest (job findings_daily_digest, #549) -----
    # Once-a-day Discord rollup of audit_log findings (by kind + delivery
    # policy + pending-delivery backlog). Routine, so Discord, never Telegram.
    # The schedule is tuned like every other job via the
    # plugin.job.findings_daily_digest config.schedule override (default
    # 0 9 * * * = 09:00 local).
    'findings_daily_digest_enabled': 'true',
    'findings_daily_digest_lookback_hours': '24',
    'findings_daily_digest_top_n': '5',

    # ----- Findings per-kind delivery policy (#461) -----
    # Per-kind policy for findings.<kind>.{delivery,fallback,cooldown_minutes,
    # min_severity}. Intended to drive per-kind suppression on the EXISTING
    # findings_alert_router (services/jobs/findings_alert_router.py) — e.g.
    # media_drift -> log_only so it doesn't page. The parallel brain
    # findings_dispatcher that originally read these was reverted as a
    # duplicate of findings_alert_router; these settings are kept for the
    # router enhancement. delivery in {auto_fix, discord, telegram,
    # github_issue, log_only}; findings.default is the unknown-kind catch-all.
    'findings.default.delivery': 'log_only',
    'findings.default.fallback': 'log_only',
    'findings.default.cooldown_minutes': '1440',
    'findings.default.min_severity': 'warn',
    'findings.anomaly.delivery': 'telegram',
    'findings.anomaly.fallback': 'discord',
    'findings.anomaly.cooldown_minutes': '60',
    'findings.anomaly.min_severity': 'critical',
    'findings.quality_regression.delivery': 'github_issue',
    'findings.quality_regression.fallback': 'discord',
    'findings.quality_regression.cooldown_minutes': '1440',
    'findings.quality_regression.min_severity': 'warn',
    'findings.broken_link.delivery': 'discord',
    'findings.broken_link.fallback': 'log_only',
    'findings.broken_link.cooldown_minutes': '360',
    'findings.broken_link.min_severity': 'warn',
    'findings.broken_external_link.delivery': 'auto_fix',
    'findings.broken_external_link.fallback': 'discord',
    'findings.broken_external_link.cooldown_minutes': '60',
    'findings.broken_external_link.min_severity': 'warn',
    'findings.broken_internal_link.delivery': 'auto_fix',
    'findings.broken_internal_link.fallback': 'discord',
    'findings.broken_internal_link.cooldown_minutes': '60',
    'findings.broken_internal_link.min_severity': 'warn',
    'findings.missing_seo.delivery': 'auto_fix',
    'findings.missing_seo.fallback': 'github_issue',
    'findings.missing_seo.cooldown_minutes': '1440',
    'findings.missing_seo.min_severity': 'warn',
    'findings.topic_gap.delivery': 'discord',
    'findings.topic_gap.fallback': 'log_only',
    'findings.topic_gap.cooldown_minutes': '1440',
    # Stale-batch reaper (reap_stale_topic_batches). A wedged open batch =
    # niche content-dark, so route the alert to the ops channel. severity is
    # 'warn' when the batch still wedges the niche (pages) and 'info' when the
    # reaper already auto-expired it (dropped by the router floor — no page);
    # 12h cooldown keeps a persistent wedge to twice-daily, not hourly.
    'findings.topic_batch_stuck.delivery': 'discord',
    'findings.topic_batch_stuck.fallback': 'log_only',
    'findings.topic_batch_stuck.cooldown_minutes': '720',
    'findings.topic_batch_stuck.min_severity': 'warn',
    'findings.media_drift.delivery': 'log_only',
    'findings.r2_static_drift.delivery': 'discord',
    'findings.r2_static_drift.fallback': 'log_only',
    'findings.r2_static_drift.cooldown_minutes': '360',
    'findings.r2_static_drift.min_severity': 'warn',
    'findings.post_verification_failure.delivery': 'discord',
    'findings.post_verification_failure.fallback': 'log_only',
    'findings.post_verification_failure.cooldown_minutes': '360',
    'findings.post_verification_failure.min_severity': 'warn',
    'findings.duplicate_post.delivery': 'log_only',
    'findings.stock_image_regenerated.delivery': 'log_only',
    'findings.uncategorized_post_autofixed.delivery': 'log_only',
    'findings.broken_external_link_autofixed.delivery': 'log_only',
    'findings.broken_internal_link_autofixed.delivery': 'log_only',
    'findings.cloud_sync_returned_false.delivery': 'discord',
    'findings.cloud_sync_returned_false.fallback': 'log_only',
    'findings.cloud_sync_returned_false.cooldown_minutes': '360',
    'findings.cloud_sync_returned_false.min_severity': 'warn',
    # SEO Harvest Loop (#763) — routine operator notifications, NOT pages.
    # enqueue_seo_refreshes emits seo_refresh_queued when N refresh tasks are
    # parked at seo_refresh_gate awaiting per-post sign-off; measure_seo_refresh
    # _outcomes emits seo_refresh_outcome when post-refresh GSC deltas land.
    # Both emit at severity='warn' so the router fetches them (it filters out
    # 'info'); delivery='discord' pins the Glad Labs ops channel per
    # feedback_telegram_vs_discord (Telegram=critical, Discord=routine).
    'findings.seo_refresh_queued.delivery': 'discord',
    'findings.seo_refresh_queued.fallback': 'log_only',
    'findings.seo_refresh_queued.cooldown_minutes': '360',
    'findings.seo_refresh_queued.min_severity': 'warn',
    'findings.seo_refresh_outcome.delivery': 'discord',
    'findings.seo_refresh_outcome.fallback': 'log_only',
    'findings.seo_refresh_outcome.cooldown_minutes': '1440',
    'findings.seo_refresh_outcome.min_severity': 'warn',

    # ----- Findings issue labels (content-derived from kind; cite-or-surface) -----
    # Comma-separated labels stamped on the GitHub issue a github_issue-delivery
    # finding opens. Derived from the finding KIND (its content), not a default.
    # Priority/milestone are deliberately omitted — those are the weekly sweep's
    # surfaced judgment axes, never auto-stamped here.
    'findings.quality_regression.labels': 'bug,pipeline',
    'findings.missing_seo.labels': 'bug,pipeline',

    # ----- Prefect stuck-flow queue-backlog detection (#526) -----
    # Distinct from the stuck-run thresholds (seeded in 0000_baseline):
    # page with probe.prefect_queue_backlog_detected when more than this
    # many SCHEDULED runs are overdue (scheduled start in the past) — the
    # backlog symptom of a held concurrency=1 slot.
    'prefect_stuck_flow_queue_depth_threshold': '3',

    # ----- Prefect stuck-flow progress-aware detection -----
    # Minutes a RUNNING content_generation run may go with NO graph-node
    # progress (pipeline_tasks.last_progress_at) before the probe treats it as
    # stuck. Replaces the flat RUNNING-age threshold when a heartbeat exists:
    # a legit media-heavy run that keeps advancing nodes is never crashed, and
    # the same signal suppresses the queue-backlog page while the slot-holder
    # is progressing. Default 20m sits above the longest observed single-node
    # gap (~13m); tune DOWN as confidence builds. NULL heartbeat (pre-migration
    # / write never landed) falls back to prefect_stuck_flow_threshold_minutes.
    'prefect_stuck_flow_progress_stall_minutes': '20',

    # ----- Content-flow concurrency cap (Glad-Labs/poindexter#578) -----
    # The native Prefect work-pool concurrency limit caps how many
    # content_generation_flow runs execute simultaneously. Each run loads
    # an LLM + SDXL onto the single 5090, so this is a direct VRAM lever:
    # the 2026-05-31 stress test found 3 concurrent flows sit at a stable
    # ~60% VRAM (healthy headroom) while 5 pin the GPU at ~98% and risk
    # OOM. ``scripts/deploy_content_flow.py`` reads ``concurrency`` and
    # fails loud if it exceeds the ``max`` ceiling (no silent VRAM
    # exhaustion); raise the ceiling only on a bigger GPU.
    'prefect_content_flow_concurrency': '3',
    'content_flow_max_concurrency': '3',
    # Minutes after which a pipeline_tasks row stuck in status='in_progress'
    # is treated as orphaned (killed flow / OOM / container restart mid-graph).
    # The reclaim step in content_generation_flow resets orphaned rows to
    # 'pending' (or 'failed' if retry_count >= max_retries) and clears the
    # poisoned LangGraph checkpoint so the retry runs a fresh graph.
    # 30 min = ~15 missed 2-min Prefect polling cycles — clearly orphaned.
    'content_flow_stale_inprogress_minutes': '30',

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
    # Niche allowlist publish gate (#729). When 'true', publish_service
    # refuses to publish a task whose niche_slug is not a KNOWN niche
    # (no matching ``niches`` row) or is missing -- the manual-approve
    # backstop against orphan/garbage niches reaching readers
    # (auto_publish_gate already blocks the auto path). A known but
    # discovery-inactive niche (e.g. dev_diary -- website-post only, kept
    # out of the topic sweep + media backfill) is still publishable;
    # ``niches.active`` gates discovery/media, not publishability. Set
    # 'false' to disable the gate entirely.
    'enforce_niche_allowlist': 'true',

    # ----- Gitea / external integrations -----
    'publish_quiet_hours': '',

    # ----- Trusted source domains -----
    'trusted_source_domains': '',

    # ----- Worker runtime -----
    # How often (seconds) the worker writes its capability_registry
    # heartbeat. The brain's "worker offline" threshold is already
    # DB-tunable; this is the emit cadence that pairs with it. Was a
    # hardcoded constant in services/worker_service.py.
    'worker_heartbeat_interval_seconds': '30',

    # ----- URL scraper (web research / topic-from-URL fetches) -----
    # Per-request fetch timeout (seconds) and the safety cap (chars) on
    # extracted page text. Were module-level constants in
    # services/url_scraper.py. NOTE: the SSRF redirect cap (MAX_REDIRECTS)
    # stays a code constant on purpose — it is a security guard, not a knob.
    'url_scraper_timeout_seconds': '15',
    'url_scraper_max_content_chars': '50000',

    # ----- Tap ingestion (RAG corpus) -----
    # Max characters per chunk when the tap runner splits a document before
    # embedding (services/taps/_chunking.py). Was a module-level constant.
    'tap_chunk_max_chars': '6000',
    # Per-tap wall-clock budget (seconds) in services/taps/runner.py::run_all.
    # The auto-embed sidecar loops with no outer deadline, so this bounds a
    # tap wedged on a stalled Ollama embed / hung query to one tap instead of
    # freezing the whole hourly run. Generous — catches an infinite hang, not a
    # tight SLA (slowest real tap, claude_code_sessions, is ~50s and growing).
    'tap_run_timeout_seconds': '300',

    # Documents the tap runner buffers before a batched chunk-0 dedup pre-fetch
    # (services/taps/runner.py). The dedup hash lookup runs one query per
    # source_table per batch instead of one SELECT per document (#735), so this
    # only bounds peak memory / round-trip granularity — it does not affect
    # dedup output.
    'tap_dedup_batch_size': '256',

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

    # ----- MCP HTTP probe recovery (brain/mcp_http_probe.py) -----
    # Empty = HTTP recovery disabled. Set to http://host.docker.internal:9841/recover
    # once the Recovery Agent Task Scheduler task is running on the host.
    'mcp_http_probe_recovery_url': '',
    # Consecutive probe failures required before paging. Default 3 suppresses
    # transient single-shot misses (fast restart, momentary load) while still
    # catching genuine sustained outages (#1301).
    'mcp_http_probe_min_consecutive_failures': '3',

    # ----- Compose-drift host-routed recovery (brain/compose_drift_probe.py) -----
    # On drift, a containerised brain can't `docker compose up` Windows binds
    # itself, so it POSTs {"service":"compose-reapply"} to the host Recovery
    # Agent (the same agent as mcp_http_probe_recovery_url above), which runs
    # start-stack.sh on the host. Default ON — auto-heal — bounded by the cap
    # below so a persistent (unfixable) drift escalates to a page instead of
    # storm-reapplying. SEPARATE from compose_drift_auto_recover_enabled (the
    # brain's own compose-up, which stays off on this Windows host because it
    # mangles C:\ binds). On a fresh install with no agent configured this
    # no-ops (falls through to notify-only).
    'compose_drift_host_recover_enabled': 'true',
    # Max start-stack reapplies per rolling window before escalating to a page.
    'compose_drift_host_recover_cap_per_window': '3',
    'compose_drift_host_recover_window_minutes': '60',
    # Compose `profiles:` the operator activates at `docker compose up` (CSV,
    # e.g. "operator,ci-runner"). A service gated behind a profile NOT listed
    # here is opt-in and legitimately not running, so the drift probe suppresses
    # its container_missing (it still diffs the service if it IS running). Empty
    # default = treat every profiled service as inactive — no false pages out of
    # the box. Incident 2026-06-21: gpu-exporter profiles:[linux-gpu] false-paged
    # CRITICAL every cycle on this Windows host, where the host nvidia-smi
    # exporter (not the profile-gated container) serves GPU metrics. List your
    # active profiles to restore crash-detection for their services.
    'compose_drift_active_profiles': '',

    # ----- Scheduled-tasks probe (brain/health_probes.py::probe_scheduled_tasks) -----
    # The containerised brain can't enumerate the host Windows Task Scheduler, so
    # it asks the host Recovery Agent (GET /tasks — shares mcp_http_probe_recovery_url
    # + _token, same agent) for the status of the host Scheduled Tasks named here
    # (CSV), then pages when one is DISABLED, missing, or its last run failed.
    # Empty default = advisory no-op (fail-open) so an operator without the agent —
    # or on a non-Windows host — never pages. Set it to the host's self-heal task
    # names to enable, e.g.
    # "Poindexter Recovery Agent,Poindexter MCP HTTP,Poindexter-DeployCheckoutSync".
    'scheduled_tasks_probe_watch_tasks': '',

    # ----- API rate limits (slowapi — poindexter#748) -----
    # slowapi limit strings: "<count>/<period>" e.g. "5/minute", "100/hour".
    # Limits are read at request time so operators can tune via app_settings
    # without redeploying. All keys use per-IP keying (get_remote_address).
    'rate_limit_token_per_ip': '10/minute',         # POST /token — OAuth token mint
    'rate_limit_triage_per_ip': '20/minute',        # POST /api/triage — LLM per call
    'rate_limit_topics_from_url_per_ip': '10/minute',  # POST /api/topics/from-url — outbound fetch
    'rate_limit_podcast_generate_per_ip': '5/minute',  # POST /api/podcast/generate/{id} — GPU
    'rate_limit_video_generate_per_ip': '5/minute',    # POST /api/video/generate/{id} — GPU

    # ----- Experiment / variant selection (#361) -----
    # EWMA damping for the outcome→experiment-variant-weight feedback loop.
    # new_weight = (1 - alpha) * old + alpha * signal (approve=1.0 / reject=0.0).
    'router_feedback_alpha': '0.2',
    # When true, pick_variant allocates proportional to experiment_variants.weight
    # (nudged by the feedback loop) instead of uniform random. Default off.
    'experiment_weighted_selection_enabled': 'false',

    # ----- Pipeline approval gates (#363) -----
    # draft_gate: pause after writer stage for operator review. Default off —
    # prod runs are unaffected until `poindexter gates set draft_gate on`.
    'pipeline_gate_draft_gate': 'off',
    # preview_gate: component-scoped regen gate after the draft is persisted.
    # Default OFF — develop behind the flag; flip to 'on' only after end-to-end
    # verification (plan Task 12). When on it IS the review point (operator can
    # approve / regen_images / regen_text / reject). The node is in the graph_def
    # already (a passthrough no-op while off). regen_*_max_attempts bound the
    # per-component loop; the surface (regen_at_gate) refuses past them.
    # See docs/architecture/2026-06-21-component-scoped-regen-gate.md.
    'pipeline_gate_preview_gate': 'off',
    'regen_images_max_attempts': '3',
    'regen_text_max_attempts': '2',

    # ----- SEO Harvest Loop (Phase 1) -----
    # The read-only analyzer is safe-on so the opportunity list populates day
    # one. Content-mutating refresh (Phase 2) gates separately on
    # seo.refresh.enabled (default off). See
    # docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md.
    'seo.harvest.analyzer_enabled': 'true',
    'seo.refresh.enabled': 'false',
    'seo.striking_distance.position_min': '5',
    'seo.striking_distance.position_max': '20',
    'seo.push_candidate.position_min': '3',
    'seo.push_candidate.position_max': '10',
    'seo.push_candidate.min_impressions': '100',
    'seo.low_ctr.min_impressions': '100',
    'seo.low_ctr.max_ctr': '0.01',
    'seo.opportunity.target_ctr': '0.05',
    # ----- SEO Harvest Loop (Phase 2 — seo_refresh, #763) -----
    # meta_only: re-optimize seo_title + seo_description only; never the body.
    'seo.refresh.scope': 'meta_only',
    # Approval-FIRST: the refresh_gate ships ENABLED, so re-publishing a live
    # post pauses for operator sign-off (unlike draft_gate, which ships off).
    # is_gate_enabled reads pipeline_gate_<gate_name>; gate_name='seo_refresh_gate'.
    # Lock-2 graduation flips this to auto-publish once the trailing clean-run
    # count below is met.
    'pipeline_gate_seo_refresh_gate': 'true',
    'seo.refresh.auto_publish_after_clean_runs': '5',
    # Phase-2 / Task-8 forward-decls (seeded for completeness):
    'seo.query_ingestion.enabled': 'false',
    'seo.refresh.outcome_measure_after_days': '14',
    # Phase 2b (#763) — cap on refresh tasks auto-enqueued per run. Conservative:
    # each refresh still needs operator sign-off at seo_refresh_gate. The job
    # schedules themselves are auto-persisted by PluginScheduler from each job's
    # `schedule` class attribute (plugin.job.<name>), not seeded here.
    'seo.refresh.max_per_run': '3',

    # ----- Cloudflare page-views beacon outage probe -----
    # URL of the Cloudflare Worker that the public-site ViewTracker beacon
    # POSTs page-view pings to (infrastructure/cloudflare/page-views-beacon).
    # ProbeCloudflareBeaconJob health-pings this every 5 min and alerts via
    # the poindexter_cloudflare_beacon_reachable gauge if it stops responding.
    # Re-seeded empty here after the key was dropped as an orphan 2026-06-03
    # (no reader then); the probe job is now the reader. Empty ⇒ probe skips
    # and the gauge stays healthy (an unconfigured beacon must not alert).
    # The production beacon URL is also set in Vercel as NEXT_PUBLIC_BEACON_URL
    # for the browser-side beacon; set this app_setting to the same URL.
    'cloudflare_beacon_url': '',

}


# Per-key lifecycle metadata for high-risk settings (poindexter#756).
#
# Keys NOT listed here get NULL for owner/value_type and deprecated=FALSE —
# the schema defaults are safe.  Add entries incrementally as keys are
# annotated; there is no requirement that every key in DEFAULTS has an entry.
#
# Fields:
#   owner        (str)  Module/service that is the primary reader.
#   value_type   (str)  One of: string boolean integer float url model csv
#                       json duration.  Matches the CHECK constraint on the
#                       value_type column.
#   deprecated   (bool) True when the key has been renamed/superseded.
#                       SiteConfig.get() emits a once-per-boot WARNING.
#   superseded_by (str) The replacement key to migrate to (with deprecated).
#
# seed_all_defaults() applies these on every boot via UPDATE … WHERE … IS
# DISTINCT FROM so the pass is a no-op on up-to-date deployments.
METADATA: dict[str, dict[str, str | bool | None]] = {
    # ----- Cost guard (incident: spend-limit rename fallthrough 2026-05-27) -----
    'daily_spend_limit_usd': {'owner': 'cost_guard', 'value_type': 'float'},
    'monthly_spend_limit_usd': {'owner': 'cost_guard', 'value_type': 'float'},
    'electricity_measured_min_coverage_pct': {'owner': 'cost_ledger', 'value_type': 'float'},
    'electricity_source_gap_minutes': {'owner': 'cost_ledger', 'value_type': 'integer'},

    # ----- LLM model selection (writer-flip = canary per feedback_writer_model_canary) -----
    'pipeline_writer_model': {'owner': 'content_router', 'value_type': 'model'},
    'video_director_model': {'owner': 'video_director', 'value_type': 'model'},
    'video_director_timeout_seconds': {'owner': 'video_director', 'value_type': 'integer'},
    'video_shot_qa_enabled': {'owner': 'video', 'value_type': 'boolean'},
    'video_shot_qa_threshold': {'owner': 'video', 'value_type': 'integer'},
    'video_shot_qa_max_retries': {'owner': 'video', 'value_type': 'integer'},
    'video_caption_engine': {'owner': 'caption_providers', 'value_type': 'string'},
    'plugin.caption_provider.speaches.enabled': {
        'owner': 'caption_providers', 'value_type': 'boolean',
    },
    'plugin.caption_provider.speaches.base_url': {
        'owner': 'caption_providers', 'value_type': 'url',
    },
    'plugin.caption_provider.speaches.model': {
        'owner': 'caption_providers', 'value_type': 'model',
    },
    'plugin.caption_provider.speaches.timeout_seconds': {
        'owner': 'caption_providers', 'value_type': 'integer',
    },
    'plugin.caption_provider.speaches.initial_prompt': {
        'owner': 'caption_providers', 'value_type': 'string',
    },
    'pipeline_fallback_model': {'owner': 'content_router', 'value_type': 'model'},
    'qa_fallback_writer_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'structured_extraction_model': {'owner': 'content_router', 'value_type': 'model'},
    'embed_model': {'owner': 'rag_engine', 'value_type': 'model'},
    'niche_embedding_model': {'owner': 'topic_discovery', 'value_type': 'model'},
    'qa_vision_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'qa_preview_vision_model': {'owner': 'multi_model_qa', 'value_type': 'model'},
    'qa_vision_num_predict': {'owner': 'multi_model_qa', 'value_type': 'int'},
    'vision_alt_model': {'owner': 'image_service', 'value_type': 'model'},
    'rag_rerank_model': {'owner': 'rag_engine', 'value_type': 'model'},
    'rag_rerank_device': {'owner': 'rag_engine', 'value_type': 'string'},

    # ----- LLM provider gates (security — paid-API lock) -----
    'plugin.llm_provider.litellm.allow_paid_base_url': {
        'owner': 'litellm_provider', 'value_type': 'boolean',
    },
    'plugin.llm_provider.openai_compat.allow_paid_base_url': {
        'owner': 'openai_compat', 'value_type': 'boolean',
    },

    # ----- QA thresholds -----
    'qa_pass_threshold': {'owner': 'multi_model_qa', 'value_type': 'float'},
    'qa_rewrite_max_attempts': {'owner': 'qa_aggregate', 'value_type': 'integer'},
    'qa_flag_instead_of_reject': {'owner': 'qa_aggregate', 'value_type': 'boolean'},
    'qa_critical_floor': {'owner': 'multi_model_qa', 'value_type': 'float'},
    'deepeval_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},
    'guardrails_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},
    'ragas_enabled': {'owner': 'multi_model_qa', 'value_type': 'boolean'},

    # ----- RAG / retrieval (incident: rag_source_filter empty = corpus pollution 2026-06) -----
    'rag_source_filter': {'owner': 'rag_engine', 'value_type': 'csv'},
    'rag_hybrid_enabled': {'owner': 'rag_engine', 'value_type': 'boolean'},
    'rag_rerank_enabled': {'owner': 'rag_engine', 'value_type': 'boolean'},
    'rag_engine_enabled': {'owner': 'rag_engine', 'value_type': 'boolean'},

    # ----- Auto-publish gate (incident: niche-leak 2026-05-26) -----
    'dev_diary_auto_publish_threshold': {'owner': 'auto_publish_gate', 'value_type': 'float'},
    'dev_diary_auto_publish_dry_run': {'owner': 'auto_publish_gate', 'value_type': 'boolean'},
    'dev_diary_auto_publish_max_edit_distance': {'owner': 'auto_publish_gate', 'value_type': 'integer'},
    'dev_diary_auto_publish_min_clean_runs': {'owner': 'auto_publish_gate', 'value_type': 'integer'},
    'enforce_niche_allowlist': {'owner': 'publish_service', 'value_type': 'boolean'},

    # ----- Pipeline gates -----
    'pipeline_gate_draft_gate': {'owner': 'template_runner', 'value_type': 'string'},
    'pipeline_gate_seo_refresh_gate': {'owner': 'seo_refresh', 'value_type': 'boolean'},
    'pipeline_gate_preview_gate': {'owner': 'approval_gate', 'value_type': 'string'},
    'regen_images_max_attempts': {'owner': 'regen_at_gate', 'value_type': 'integer'},
    'regen_text_max_attempts': {'owner': 'regen_at_gate', 'value_type': 'integer'},

    # ----- Media pipeline master switches -----
    'media_pipeline_trigger_enabled': {'owner': 'dispatch_media_pipeline', 'value_type': 'boolean'},
    'podcast_pipeline_trigger_enabled': {'owner': 'dispatch_podcast_pipeline', 'value_type': 'boolean'},

    # ----- Content pipeline behaviour -----
    'content_flow_stale_inprogress_minutes': {'owner': 'content_generation_flow', 'value_type': 'integer'},
    'template_runner_use_postgres_checkpointer': {'owner': 'template_runner', 'value_type': 'boolean'},

    # ----- Observability -----
    'enable_tracing': {'owner': 'otel', 'value_type': 'boolean'},
    'enable_pyroscope': {'owner': 'pyroscope', 'value_type': 'boolean'},
    'langfuse_tracing_enabled': {'owner': 'prompt_manager', 'value_type': 'boolean'},

    # ----- Brain / migration drift -----
    'migration_drift_auto_sync_enabled': {'owner': 'brain_migration_drift_probe', 'value_type': 'boolean'},
    'migration_drift_defer_while_inflight': {'owner': 'brain_migration_drift_probe', 'value_type': 'boolean'},

    # ----- Deprecated keys — emit warning on read (add new ones here) -----
    # nvidia_exporter_url went dead when PR #1827 moved gpu_scheduler onto
    # Prometheus (gpu_metrics_prometheus_url) for GPU metrics; nothing reads
    # the direct-exporter URL anymore. Kept as a tombstone so SiteConfig.get()
    # warns once-per-boot and points callers at the replacement key.
    'nvidia_exporter_url': {
        'owner': 'gpu_scheduler', 'value_type': 'url',
        'deprecated': True, 'superseded_by': 'gpu_metrics_prometheus_url',
    },
    # Example pattern (uncomment + fill in when retiring a key):
    # 'old_key_name': {
    #     'owner': 'cost_guard', 'value_type': 'float',
    #     'deprecated': True, 'superseded_by': 'new_key_name',
    # },
}


async def seed_all_defaults(pool: Any) -> int:
    """Insert every DEFAULTS entry into app_settings, skipping existing rows.

    Returns the count of rows actually inserted (i.e. fresh-install gap
    closed). On an up-to-date DB this is 0.

    Operator-tuned values survive — the ``ON CONFLICT (key) DO NOTHING``
    clause means an existing row is never overwritten by this seeder.

    A second pass writes lifecycle metadata (owner, value_type, deprecated,
    superseded_by) for keys listed in ``METADATA``.  The UPDATE fires only
    when at least one column differs from the stored value (``IS DISTINCT
    FROM``), so it is a no-op on up-to-date deployments.

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

        # Second pass: write lifecycle metadata where the columns differ.
        # Skips silently if the lifecycle columns don't exist yet (migration
        # hasn't run — e.g. fresh-clone worktree with an older schema).
        try:
            for key, meta in METADATA.items():
                await conn.execute(
                    """
                    UPDATE app_settings SET
                        owner       = $2,
                        value_type  = $3,
                        deprecated  = $4,
                        superseded_by = $5
                    WHERE key = $1
                      AND (
                          owner         IS DISTINCT FROM $2
                       OR value_type    IS DISTINCT FROM $3
                       OR deprecated    IS DISTINCT FROM $4
                       OR superseded_by IS DISTINCT FROM $5
                      )
                    """,
                    key,
                    meta.get('owner'),
                    meta.get('value_type'),
                    meta.get('deprecated', False),
                    meta.get('superseded_by'),
                )
        except Exception:  # silent-ok: lifecycle columns absent (pre-20260618 schema); INSERT pass ran, metadata deferred until migration runs
            pass

    return inserted


def keys() -> list[str]:
    """Return the sorted list of keys this module knows about.

    Useful for diagnostics (``poindexter setup --check`` could compare
    DEFAULTS.keys() against the live DB to flag drift).
    """
    return sorted(DEFAULTS.keys())
