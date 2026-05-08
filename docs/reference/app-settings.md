# App settings reference

> **Auto-generated from live `app_settings` table on 2026-05-08.**  
> Every runtime-configurable knob in the Poindexter pipeline.
> 699 active rows across 54 categories. 55 stored encrypted via pgcrypto (`is_secret=true`); an additional 3 values are redacted in the preview below as defense-in-depth against secret-shaped strings that weren't classified as secrets in the DB.

> This file is checked into `docs/` which is **excluded from the public Poindexter sync** (`scripts/sync-to-github.sh` strips `docs/`). Safe to regenerate from operator state. Not safe to publish outside the private mirror.

> **To regenerate:** `python scripts/regen-app-settings-doc.py`

To change any value:

```sql
-- Read
SELECT key, value, updated_at FROM app_settings WHERE key = 'content_quality_minimum';

-- Write (non-secret)
UPDATE app_settings SET value = '78', updated_at = NOW() WHERE key = 'content_quality_minimum';

-- Write (secret — use the helper so pgcrypto encrypts on write)
-- See services/plugins/secrets.py::set_secret() for the Python API.
```

The worker re-reads on every poll; no restart needed.

---

## Table of contents

- [alerts](#alerts) (8 keys)
- [api_keys](#api-keys) (8 keys)
- [auth](#auth) (3 keys)
- [backup](#backup) (13 keys)
- [cli](#cli) (5 keys)
- [content](#content) (20 keys)
- [content_qa](#content-qa) (4 keys)
- [cors](#cors) (2 keys)
- [cost](#cost) (8 keys)
- [experiments](#experiments) (2 keys)
- [external_apis](#external-apis) (1 key)
- [features](#features) (4 keys)
- [finance](#finance) (8 keys)
- [firefighter](#firefighter) (9 keys)
- [gates](#gates) (10 keys)
- [general](#general) (273 keys)
- [gpu](#gpu) (1 key)
- [identity](#identity) (16 keys)
- [image](#image) (12 keys)
- [integration](#integration) (13 keys)
- [integrations](#integrations) (12 keys)
- [logging](#logging) (2 keys)
- [memory](#memory) (4 keys)
- [memory_alerts](#memory-alerts) (6 keys)
- [memory_compression](#memory-compression) (6 keys)
- [model_roles](#model-roles) (12 keys)
- [models](#models) (7 keys)
- [monitoring](#monitoring) (48 keys)
- [newsletter](#newsletter) (3 keys)
- [niche_pivot](#niche-pivot) (8 keys)
- [notifications](#notifications) (5 keys)
- [observability](#observability) (8 keys)
- [performance](#performance) (4 keys)
- [pipeline](#pipeline) (34 keys)
- [plugins](#plugins) (6 keys)
- [plugin_telemetry](#plugin-telemetry) (34 keys)
- [podcast](#podcast) (1 key)
- [prometheus](#prometheus) (4 keys)
- [publishing](#publishing) (4 keys)
- [qa_workflows](#qa-workflows) (3 keys)
- [quality](#quality) (7 keys)
- [scheduling](#scheduling) (1 key)
- [secrets](#secrets) (18 keys)
- [security](#security) (2 keys)
- [seo](#seo) (1 key)
- [site](#site) (2 keys)
- [social](#social) (8 keys)
- [system](#system) (2 keys)
- [tokens](#tokens) (5 keys)
- [topic_discovery](#topic-discovery) (1 key)
- [voice](#voice) (7 keys)
- [voice_agent](#voice-agent) (2 keys)
- [webhooks](#webhooks) (2 keys)
- [writer_rag](#writer-rag) (10 keys)

## alerts

| Key                                        | Default   | Classification | Description                                                                                                               |
| ------------------------------------------ | --------- | -------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `alert_dedup_state_retention_hours`        | `168`     |                | Retention horizon for alert_dedup_state rows. The retention janitor deletes rows whose last_seen_at aged past this ma...  |
| `alert_force_telegram_event_types`         | ``        |                | Comma-separated list of event_type (or alertname) values that always route to Telegram regardless of severity. Use fo...  |
| `alert_repeat_summarize_threshold_minutes` | `30`      |                | After a fingerprint has been firing continuously for this many minutes (now - first_seen_at), the dispatcher escalate...  |
| `alert_repeat_suppress_window_minutes`     | `30`      |                | When a brain alert with the same fingerprint (source\|severity\|normalized_message) last fired inside this window, th...  |
| `task_failure_alert_dedup_window_seconds`  | `900`     |                | Suppress duplicate task-failure alerts for the same (task_id, error_message_hash) within this window. Default 900 (15...  |
| `task_failure_alert_severity`              | `discord` |                | Channel routine task-failure alerts route to: 'discord' (default, the spam channel) or 'telegram' (escalates to opera...  |
| `task_retry_backoff_initial_seconds`       | `60`      |                | Exponential-backoff base for auto-retry. Attempt N becomes eligible only after backoff \* 2^(N-1) seconds have passed ... |
| `task_retry_max_attempts`                  | `0`       |                | Max automatic re-claims of a 'failed' task by \_auto_retry_failed_tasks. Default 0 disables auto-retry entirely; opera... |

## api_keys

| Key                 | Default                                    | Classification | Description                   |
| ------------------- | ------------------------------------------ | -------------- | ----------------------------- |
| `anthropic_api_key` | `*(encrypted)*`                            | encrypted      | Anthropic Claude API key      |
| `gemini_api_key`    | `*(encrypted)*`                            | encrypted      | Google Gemini API key         |
| `google_api_key`    | `*(encrypted)*`                            | encrypted      | Google AI API key             |
| `mercury_api_token` | `*(encrypted)*`                            | encrypted      | Mercury banking API token     |
| `openai_api_key`    | `*(encrypted)*`                            | encrypted      | OpenAI API key                |
| `pexels_api_key`    | `*(encrypted)*`                            | encrypted      | Pexels image search API key   |
| `resend_api_key`    | `*(encrypted)*`                            | encrypted      | Resend email delivery API key |
| `sentry_dsn`        | `http://248e191e2f24492e887a5b403cbc66...` |                | Sentry DSN for error tracking |

## auth

| Key              | Default         | Classification | Description                                                                    |
| ---------------- | --------------- | -------------- | ------------------------------------------------------------------------------ |
| `api_token`      | `*(encrypted)*` | encrypted      | API Bearer token for the Poindexter worker. Rotated via feat/rotate-api-token. |
| `jwt_secret_key` | `*(encrypted)*` | encrypted      | JWT signing secret (auto-generated)                                            |
| `secret_key`     | `*(encrypted)*` | encrypted      | Application secret key (auto-generated)                                        |

## backup

| Key                                     | Default              | Classification | Description                                                                                                              |
| --------------------------------------- | -------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `backup_daily_enabled`                  | `true`               |                | When false, the backup-daily container takes no dumps.                                                                   |
| `backup_daily_interval`                 | `24h`                |                | Cadence between daily dumps.                                                                                             |
| `backup_daily_retention`                | `7`                  |                | Number of daily dumps to keep.                                                                                           |
| `backup_hourly_enabled`                 | `true`               |                | When false, the backup-hourly container takes no dumps. Loop keeps running so toggling back on is instant.               |
| `backup_hourly_interval`                | `1h`                 |                | Cadence between hourly dumps. Format: <N>{s\|m\|h\|d}. Read fresh each tick — no restart needed.                         |
| `backup_hourly_retention`               | `24`                 |                | Number of hourly dumps to keep. Older dumps are pruned after each successful run.                                        |
| `backup_watcher_backup_dir`             | `/host-backups/auto` |                | Host path the backup containers bind-mount their dumps into. Override when POINDEXTER_BACKUP_DIR points somewhere non... |
| `backup_watcher_daily_max_age_hours`    | `26`                 |                | Daily tier staleness threshold (mirrors the compose healthcheck slack of 90 min beyond the 24 h cadence).                |
| `backup_watcher_enabled`                | `true`               |                | Master switch for the brain backup-watcher probe (#388). When false, the probe short-circuits without stat-ing dumps ... |
| `backup_watcher_hourly_max_age_minutes` | `90`                 |                | Hourly tier staleness threshold. Matches the compose healthcheck so the watcher fires at the same instant the contain... |
| `backup_watcher_max_retries`            | `2`                  |                | Consecutive `docker restart` attempts before the watcher gives up and lets the dispatcher page the operator. Cumulati... |
| `backup_watcher_poll_interval_minutes`  | `5`                  |                | Cadence at which the watcher re-checks backup freshness. Matches the brain cycle by default; bump higher only if the ... |
| `backup_watcher_retry_delay_seconds`    | `120`                |                | How long the watcher waits after `docker restart` before re-stat-ing the dump directory. Long enough for postgres rec... |

## cli

| Key                                          | Default                | Classification | Description                                                                                                              |
| -------------------------------------------- | ---------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `cli_post_approve_bulk_max_count`            | `100`                  |                | Hard ceiling for matched-post count in a single 'poindexter post approve --no-dry-run --filter ...' invocation. Refus... |
| `cli_post_approve_bulk_require_confirm`      | `true`                 |                | When true, 'poindexter post approve --filter ... --no-dry-run' always prompts y/N before approving — even if --yes wa... |
| `cli_post_create_idempotency_enabled`        | `true`                 |                | Master switch for `poindexter post create` idempotency (#338). When 'true', a second invocation with the same compute... |
| `cli_post_create_idempotency_strategy`       | `slug_or_content_hash` |                | Reserved for future variants of the `poindexter post create` (#338) idempotency-key derivation. Today only 'slug_or_c... |
| `cli_post_create_idempotency_window_minutes` | `30`                   |                | Dedup window for `poindexter post create` (#338). A second invocation with the same idempotency key WITHIN this many ... |

## content

| Key                                        | Default                                    | Classification | Description                                                                                                               |
| ------------------------------------------ | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `alt_text_budget`                          | `120`                                      |                | Character budget for inline <img alt="..."> text. The alt generator produces complete sentences within this budget; o...  |
| `auto_append_sources_section`              | `true`                                     |                | Auto-append ## Sources at finalize if missing                                                                             |
| `code_density_check_enabled`               | `true`                                     |                | GH-234: enable the code-block density quality gate. When true, tech-tagged posts that ship without enough fenced code...  |
| `code_density_long_post_floor_words`       | `300`                                      |                | GH-234: prose word-count threshold above which the line-ratio sub-check kicks in. Per the issue spec, short posts (<3...  |
| `code_density_min_blocks_per_700w`         | `1`                                        |                | GH-234: minimum fenced code blocks expected per 700 prose words in a tech post. The check skips posts under 200 prose...  |
| `code_density_min_line_ratio_pct`          | `20`                                       |                | GH-234: minimum percentage of non-empty content lines that must live inside a fenced code block, applied only to post...  |
| `code_density_tag_filter`                  | `technical,ai,programming,ml,python,ja...` |                | GH-234: comma-separated list of tag/topic tokens that qualify a post as 'tech' for the code-block density rule. Match...  |
| `content_max_refinement_attempts`          | `3`                                        |                | Max attempts to refine content quality                                                                                    |
| `content_min_word_count`                   | `800`                                      |                | Minimum word count for blog posts                                                                                         |
| `content_target_word_count`                | `1500`                                     |                | Target word count for blog posts                                                                                          |
| `default_ollama_model`                     | `auto`                                     |                | Default Ollama model for LLM calls. "auto" → OllamaClient picks the first available pulled model. Override with a spe...  |
| `local_llm_api_url`                        | `http://host.docker.internal:11434`        |                | Ollama API base URL for local LLM calls (e.g. http://localhost:11434). Empty value means 'Ollama not configured' — ca...  |
| `local_llm_model_name`                     | `auto`                                     |                | Ollama model fallback used by agents/content_agent/config when no per-task model is configured. 'auto' lets OllamaCli...  |
| `title_originality_cache_ttl_hours`        | `24`                                       |                | GH-87: TTL (hours) for the in-process cache that dedupes repeated DuckDuckGo queries for the same title. DDG rate-lim...  |
| `title_originality_external_check_enabled` | `true`                                     |                | GH-87: enable DuckDuckGo HTML search for the exact post title at approval time. Verbatim external matches subtract ti...  |
| `title_originality_external_penalty`       | `-50`                                      |                | GH-87: points subtracted from the QA score when the post title appears verbatim in external search results. Stored as...  |
| `topic_discovery_category_searches`        | `{}`                                       |                | JSON object mapping category name -> list of keyword search strings. Used by TopicDiscovery.\_classify_category to buc... |
| `topic_discovery_news_patterns`            | `[]`                                       |                | JSON array of regex strings (case-insensitive). When non-empty, TopicDiscovery uses these patterns to reject titles a...  |
| `writing_style_reference`                  | `Matt Gladding writing style traits: S...` |                |                                                                                                                           |
| `writing_styles`                           | `[{"name": "technical", "voice": "prec...` |                | Configurable writing styles for content generation. Same pattern as image_styles.                                         |

## content_qa

| Key                           | Default | Classification | Description                                                                     |
| ----------------------------- | ------- | -------------- | ------------------------------------------------------------------------------- |
| `qa_citation_max_dead_ratio`  | `0.30`  |                | Max proportion of dead citations before verifier rejects                        |
| `qa_citation_min_count`       | `0`     |                | Minimum external citations required per post. 0 = disabled                      |
| `qa_citation_timeout_seconds` | `8.0`   |                | Per-URL HEAD timeout seconds                                                    |
| `qa_citation_verify_enabled`  | `true`  |                | HTTP HEAD every external URL in post content; surface dead links as QA reviewer |

## cors

| Key                     | Default                                    | Classification | Description                            |
| ----------------------- | ------------------------------------------ | -------------- | -------------------------------------- |
| `allowed_origins`       | `https://gladlabs.io,https://www.gladl...` |                | Comma-separated allowed CORS origins   |
| `rate_limit_per_minute` | `100`                                      |                | Max API requests per minute per client |

## cost

| Key                                     | Default    | Classification | Description                                                        |
| --------------------------------------- | ---------- | -------------- | ------------------------------------------------------------------ |
| `cost_alert_threshold_pct`              | `80`       |                | Alert when spend exceeds this % of limit                           |
| `daily_spend_limit`                     | `2.0`      |                | Maximum daily AI spend in USD                                      |
| `electricity_rate_kwh`                  | `0.2579`   |                | RI Energy Last Resort Service rate $0.14770/kWh (verified by Matt) |
| `gpu_idle_watts`                        | `45`       |                | GPU idle power draw in watts                                       |
| `gpu_inference_watts`                   | `400`      |                | GPU average inference power draw in watts                          |
| `monthly_spend_limit`                   | `10.0`     |                | Maximum monthly AI spend in USD                                    |
| `ollama_electricity_cost_per_1k_tokens` | `0.000256` |                | Ollama electricity cost per 1K tokens (USD)                        |
| `system_idle_watts`                     | `120`      |                | Total system idle power draw in watts (CPU+RAM+disk+GPU)           |

## experiments

| Key                              | Default | Classification | Description                                                                                                              |
| -------------------------------- | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `active_pipeline_experiment_key` | ``      |                | Experiment key the content pipeline routes through (matches experiments.key in the experiments table). Empty = disabl... |
| `premium_active`                 | `false` |                | When 'true', UnifiedPromptManager loads prompt_templates rows where source='premium' on top of source='default'. When... |

## external_apis

| Key              | Default         | Classification | Description                                                                                                              |
| ---------------- | --------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `serper_api_key` | `*(encrypted)*` | encrypted      | Serper search API key for real-time web-search capability in the content agent. Empty value disables web search witho... |

## features

| Key                       | Default | Classification | Description                                     |
| ------------------------- | ------- | -------------- | ----------------------------------------------- |
| `enable_mcp_server`       | `true`  |                | Enable Model Context Protocol server            |
| `enable_memory_system`    | `true`  |                | Enable agent memory system                      |
| `enable_training_capture` | `false` |                | Enable training data capture from pipeline runs |
| `redis_enabled`           | `false` |                | Enable Redis for caching and pub/sub            |

## finance

| Key                         | Default  | Classification | Description                                                 |
| --------------------------- | -------- | -------------- | ----------------------------------------------------------- |
| `google_gemini_credit`      | `1000`   |                | Google developer program Gemini credit (unused)             |
| `investment_business_setup` | `200`    |                | Business formation, fees, misc setup costs                  |
| `investment_cloud_mistakes` | `300`    |                | Gemini API overspend incident                               |
| `investment_hardware`       | `10000`  |                | Total hardware investment (PC build with trial/error)       |
| `investment_time_estimate`  | `25000`  |                | Estimated value of 6 months founder time (~500hrs @ $50/hr) |
| `investment_total_estimate` | `35500`  |                | Total estimated investment to date (March 2026)             |
| `mercury_balance`           | `362.75` |                | Mercury checking balance as of March 30 2026                |
| `monthly_insurance`         | `15`     |                | Business insurance monthly cost                             |

## firefighter

| Key                                | Default                                    | Classification | Description                                                                                                              |
| ---------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `ops_triage_audit_logged`          | `true`                                     |                | When true, every triage call writes an audit_log row tagged source='ops_triage' (success or failure). When false, onl... |
| `ops_triage_cache_ttl_seconds`     | `3600`                                     |                | TTL (seconds) for the POST /api/triage process-local idempotency cache (#347 step 3). Repeat calls for the same alert... |
| `ops_triage_enabled`               | `true`                                     |                | Master kill-switch for the firefighter ops LLM (#347). When false, alert_dispatcher skips the parallel triage task an... |
| `ops_triage_max_context_tokens`    | `4000`                                     |                | Cap on the pre-fetched context size handed to the LLM. When the assembled context (alert + history + audit_log + pipe... |
| `ops_triage_max_diagnosis_tokens`  | `400`                                      |                | Cap on the diagnosis output length (Telegram-friendly). The service truncates with a '[...]' marker if the LLM exceed... |
| `ops_triage_model_class`           | `ops_triage`                               |                | model_router tier the firefighter_service uses for triage (#347). Defaults to a dedicated 'ops_triage' class which ma... |
| `ops_triage_retry_backoff_seconds` | `[10, 30, 90]`                             |                | JSON list of per-attempt sleep durations (seconds) the brain uses between retries when worker /api/triage is unreacha... |
| `ops_triage_retry_max`             | `3`                                        |                | Maximum retry attempts when the brain can't reach the worker /api/triage endpoint. Retries are scheduled with the bac... |
| `ops_triage_system_prompt`         | `You are the Poindexter operator. The ...` |                | Operator-persona system prompt the triage LLM sees. Iterable without redeploy. Keep <=400 tokens; the prompt sets the... |

## gates

| Key                                              | Default | Classification | Description                                                                                                              |
| ------------------------------------------------ | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `gate_auto_expire_batch_size`                    | `50`    |                | Cap per-cycle expiry to this many gates to avoid huge batches. Excess rolls over to the next cycle.                      |
| `gate_auto_expire_enabled`                       | `true`  |                | Master switch for the brain gate auto-expire probe (#338). When false, the probe short-circuits without scanning gates.  |
| `gate_auto_expire_notify_threshold`              | `1`     |                | Only ping the operator (Telegram coalesced) when batch size >= this. Default 1 = always notify on any expiry.            |
| `gate_auto_expire_poll_interval_minutes`         | `30`    |                | Cadence at which the brain runs the auto-expire probe. Stale gates aren't time-sensitive, so 30-min default is sparse... |
| `gate_pending_max_age_hours`                     | `168`   |                | Pending gates older than this many hours get auto-rejected with a sentinel reason. Default 168h = 7 days, per the #33... |
| `gate_pending_summary_enabled`                   | `true`  |                | Master switch for the brain gate-pending-summary probe (#338). When false, the probe short-circuits without scanning ... |
| `gate_pending_summary_min_age_minutes`           | `60`    |                | Grace window after the OLDEST pending gate's creation before the first Telegram page fires. Prevents paging the opera... |
| `gate_pending_summary_poll_interval_minutes`     | `60`    |                | Cadence at which the probe re-scans the pending queue. Hourly per #338 spec. Brain cycle is 5 min so the probe intern... |
| `gate_pending_summary_telegram_dedup_minutes`    | `60`    |                | Suppress duplicate Telegram pings within this window when the queue size has not grown past gate_pending_summary_tele... |
| `gate_pending_summary_telegram_growth_threshold` | `3`     |                | Re-fire the coalesced Telegram ping inside the dedup window if the pending queue grew by STRICTLY MORE than this many... |

## general

| Key                                                                  | Default                                                                        | Classification | Description                                                                                                              |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `api_url`                                                            | `http://localhost:8002`                                                        |                | Backend API base URL (legacy alias for api_base_url)                                                                     |
| `approval_gate_draft_enabled`                                        | `true`                                                                         |                | Whether the draft gate is enabled by default.                                                                            |
| `approval_gate_final_enabled`                                        | `true`                                                                         |                | Whether the final pre-distribution gate is enabled by default.                                                           |
| `approval_gate_media_generation_failed_enabled`                      | `true`                                                                         |                | Whether the auto-escalation gate fires when per-medium generation hits the retry limit.                                  |
| `approval_gate_podcast_enabled`                                      | `true`                                                                         |                | Whether the podcast gate is enabled by default.                                                                          |
| `approval_gate_short_enabled`                                        | `true`                                                                         |                | Whether the short-video gate is enabled by default.                                                                      |
| `approval_gate_topic_decision_reject_status`                         | `dismissed`                                                                    |                | Status set on pipeline_tasks when a topic-decision gate rejects the topic (vs. the global default 'rejected'). Distin... |
| `approval_gate_topic_enabled`                                        | `true`                                                                         |                | Whether the topic gate is enabled by default. Legacy-style feature flag kept for parity with the existing approval_ga... |
| `approval_gate_video_enabled`                                        | `true`                                                                         |                | Whether the video gate is enabled by default.                                                                            |
| `app_version`                                                        | `3.0.1`                                                                        |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `audio_gen_engine`                                                   | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `brain_anomaly_baseline_window_days`                                 | `30`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `brain_anomaly_current_window_hours`                                 | `24`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `brand_keywords`                                                     | ``                                                                             |                | Comma-separated brand-relevance keywords used by topic_discovery to filter discovered topics to the site's niche. Emp... |
| `cloudflare_account_id`                                              | `01ddb679184ebe59cc7f03f8171d76ee`                                             |                |                                                                                                                          |
| `content_router_contradiction_review_max_tokens`                     | `1500`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `content_router_contradiction_revise_max_tokens`                     | `8000`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `content_router_contradiction_timeout_seconds`                       | `120`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `content_router_qa_rewrite_max_tokens`                               | `8000`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `content_router_qa_rewrite_timeout_seconds`                          | `240`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `content_router_seo_title_max_tokens`                                | `4000`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `content_validator_warning_reject_threshold`                         | `5`                                                                            |                |                                                                                                                          |
| `database_pool_max_size`                                             | `20`                                                                           |                | Max DB pool connections                                                                                                  |
| `database_pool_min_size`                                             | `5`                                                                            |                | Min DB pool connections                                                                                                  |
| `deepeval_enabled`                                                   | `true`                                                                         |                |                                                                                                                          |
| `default_media_to_generate`                                          | ``                                                                             |                | Comma-separated list of media to generate alongside each new post when --media isn't passed. Empty = blog post only. ... |
| `default_workflow_gates`                                             | `topic,draft,final`                                                            |                | Comma-separated gate sequence applied to new posts when --gates isn't passed. Empty string = fully autonomous (no hum... |
| `development_mode`                                                   | `false`                                                                        |                | Enable development mode                                                                                                  |
| `devto_api_base`                                                     | `https://dev.to/api`                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `devto_min_reactions`                                                | `20`                                                                           |                |                                                                                                                          |
| `devto_per_page`                                                     | `15`                                                                           |                |                                                                                                                          |
| `devto_tag`                                                          | ``                                                                             |                |                                                                                                                          |
| `devto_top_days`                                                     | `7`                                                                            |                |                                                                                                                          |
| `disable_auth_for_dev`                                               | `true`                                                                         |                | Disable auth in development                                                                                              |
| `discord_bot_token`                                                  | `*(encrypted)*`                                                                | encrypted      |                                                                                                                          |
| `discord_voice_bot_token`                                            | `*(encrypted)*`                                                                | encrypted      |                                                                                                                          |
| `docker_port_forward_watch_list`                                     | `[{"container": "poindexter-pyroscope"...`                                     |                |                                                                                                                          |
| `embedding_model`                                                    | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `embedding_retention_days.audit`                                     | `90`                                                                           |                | Days to keep audit_log embeddings before prune_stale_embeddings drops them. 90d covers the typical operator post-mort... |
| `embedding_retention_days.brain`                                     | `365`                                                                          |                | Days to keep brain_knowledge embeddings before prune. Brain memory is designed to compound; long horizon by design.      |
| `embedding_retention_days.claude_sessions`                           | `21`                                                                           |                | Days to keep Claude Code session embeddings before prune_stale_embeddings drops them. 21d balances semantic-search re... |
| `embedding_retention_days.issues`                                    | ``                                                                             |                | Empty = no TTL. Issue embeddings are never auto-pruned — irreplaceable pipeline state.                                   |
| `embedding_retention_days.memory`                                    | ``                                                                             |                | Empty = no TTL. Memory embeddings are never auto-pruned — operator's curated state.                                      |
| `embedding_retention_days.posts`                                     | ``                                                                             |                | Empty = no TTL. Post embeddings are never auto-pruned — feed live RAG retrieval.                                         |
| `embed_model`                                                        | `nomic-embed-text`                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `enabled_topic_sources`                                              | `knowledge,codebase,hackernews,devto,w...`                                     |                |                                                                                                                          |
| `enable_sdxl_warmup`                                                 | `false`                                                                        |                | Warm up SDXL models on startup                                                                                           |
| `enable_semantic_dedup`                                              | `true`                                                                         |                |                                                                                                                          |
| `enable_writer_self_review`                                          | `false`                                                                        |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `environment`                                                        | `development`                                                                  |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `flux_schnell_server_url`                                            | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `gate_pending_summary_discord_per_cycle`                             | `false`                                                                        |                |                                                                                                                          |
| `google_sitemap_ping_url`                                            | `https://www.google.com/ping`                                                  |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `gpu_busy_threshold_percent`                                         | `30`                                                                           |                | GPU utilization % above which gaming is detected                                                                         |
| `gpu_gaming_check_interval`                                          | `15`                                                                           |                | Seconds between gaming detection checks                                                                                  |
| `gpu_gaming_clear_checks`                                            | `3`                                                                            |                | Consecutive low-util checks to resume pipeline                                                                           |
| `gpu_gaming_confirm_checks`                                          | `2`                                                                            |                | Consecutive high-util checks to confirm gaming                                                                           |
| `gpu_name`                                                           | ``                                                                             |                | GPU model name (auto-detected by detect-hardware.py)                                                                     |
| `gpu_vram_gb`                                                        | `0`                                                                            |                | GPU VRAM in GB (auto-detected by detect-hardware.py)                                                                     |
| `grafana_user`                                                       | `admin`                                                                        |                | Grafana admin username                                                                                                   |
| `guardrails_enabled`                                                 | `true`                                                                         |                |                                                                                                                          |
| `hardware_cost_total`                                                | `7877.14`                                                                      |                | Total PC build cost for depreciation calculation                                                                         |
| `hardware_useful_life_months`                                        | `60`                                                                           |                | Estimated useful life in months (5 years)                                                                                |
| `hn_min_score`                                                       | `50`                                                                           |                |                                                                                                                          |
| `hn_top_stories`                                                     | `20`                                                                           |                |                                                                                                                          |
| `host_home`                                                          | ``                                                                             |                | Host home directory for Docker volume mounts                                                                             |
| `idle_last_run_anomaly_detect`                                       | `1776710530.609785`                                                            |                |                                                                                                                          |
| `idle_last_run_auto_embed`                                           | `1776714304.5822833`                                                           |                |                                                                                                                          |
| `idle_last_run_context_sync`                                         | `1776717772.9571455`                                                           |                |                                                                                                                          |
| `idle_last_run_db_backup`                                            | `1776707134.489248`                                                            |                |                                                                                                                          |
| `idle_last_run_devto_crosspost`                                      | `1776698746.4172635`                                                           |                |                                                                                                                          |
| `idle_last_run_embedding_refresh`                                    | `1776706187.2627528`                                                           |                |                                                                                                                          |
| `idle_last_run_expire_stale_approvals`                               | `1776702655.306583`                                                            |                |                                                                                                                          |
| `idle_last_run_fix_categories`                                       | `1776707103.448257`                                                            |                |                                                                                                                          |
| `idle_last_run_fix_duplicates`                                       | `1776707098.4368908`                                                           |                |                                                                                                                          |
| `idle_last_run_fix_external_links`                                   | `1776707098.428116`                                                            |                |                                                                                                                          |
| `idle_last_run_fix_internal_links`                                   | `1776707092.7177281`                                                           |                |                                                                                                                          |
| `idle_last_run_fix_seo`                                              | `1776707103.454733`                                                            |                |                                                                                                                          |
| `idle_last_run_image_regen`                                          | `1776698252.6993651`                                                           |                |                                                                                                                          |
| `idle_last_run_link_check`                                           | `1776706157.2373133`                                                           |                |                                                                                                                          |
| `idle_last_run_memory_stale_check`                                   | `1776717741.557769`                                                            |                |                                                                                                                          |
| `idle_last_run_podcast_backfill`                                     | `1776710530.59738`                                                             |                |                                                                                                                          |
| `idle_last_run_publish_verify`                                       | `1776713267.676449`                                                            |                |                                                                                                                          |
| `idle_last_run_quality_audit`                                        | `1776708464.8465457`                                                           |                |                                                                                                                          |
| `idle_last_run_sync_newsletter_subscribers`                          | `1776717741.527152`                                                            |                |                                                                                                                          |
| `idle_last_run_sync_page_views`                                      | `1776717746.5694222`                                                           |                |                                                                                                                          |
| `idle_last_run_threshold_tune`                                       | `1776706157.2429378`                                                           |                |                                                                                                                          |
| `idle_last_run_topic_discovery`                                      | `1778207418.0156078`                                                           |                |                                                                                                                          |
| `idle_last_run_topic_gaps`                                           | `1776662209.1690526`                                                           |                |                                                                                                                          |
| `idle_last_run_utility_rates`                                        | `1775425727.9157252`                                                           |                |                                                                                                                          |
| `idle_last_run_video_backfill`                                       | `1776698745.4924042`                                                           |                |                                                                                                                          |
| `image_model`                                                        | `sdxl_lightning`                                                               |                | Default image generation model (legacy)                                                                                  |
| `indexnow_ping_url`                                                  | `https://api.indexnow.org/indexnow`                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `internal_api_base_url`                                              | `http://localhost:8002`                                                        |                | Base URL for the internal worker API (used for self-calls like the podcast feed regen)                                   |
| `location_state`                                                     | `RI`                                                                           |                | Matt location - Rhode Island                                                                                             |
| `log_to_file`                                                        | `true`                                                                         |                | Write logs to file                                                                                                       |
| `media_generation_retry_limit`                                       | `2`                                                                            |                | How many times each per-medium generation may fail before the system escalates to a media_generation_failed gate that... |
| `media_r2_upload_delay_seconds`                                      | `240`                                                                          |                | Wait this many seconds after a post publishes before uploading podcast/video/short to R2 CDN                             |
| `memory_stale_last_alerts`                                           | `{"shared-context": "2026-04-15T22:13:...`                                     |                |                                                                                                                          |
| `memory_stale_threshold_seconds_openclaw`                            | `2592000`                                                                      |                |                                                                                                                          |
| `memory_stale_threshold_seconds_shared-context`                      | `2592000`                                                                      |                |                                                                                                                          |
| `model_role_image_decision`                                          | `ollama/phi4:14b`                                                              |                |                                                                                                                          |
| `newsletter_batch_delay_seconds`                                     | `2`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `newsletter_batch_size`                                              | `50`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `newsletter_email`                                                   | ``                                                                             |                | Newsletter sender email (legacy)                                                                                         |
| `nvidia_exporter_url`                                                | `http://host.docker.internal:9835/metrics`                                     |                | nvidia-smi metrics exporter                                                                                              |
| `ollama_base_url`                                                    | `http://host.docker.internal:11434`                                            |                | Ollama API endpoint                                                                                                      |
| `ollama_client_timeout_seconds`                                      | `1500`                                                                         |                |                                                                                                                          |
| `openclaw_gateway_url`                                               | `http://localhost:18789`                                                       |                | OpenClaw gateway URL                                                                                                     |
| `operator_id`                                                        | `operator`                                                                     |                | Default operator ID                                                                                                      |
| `operator_url_probe_skip_keys`                                       | `social_x_url,social_linkedin_url,oaut...`                                     |                |                                                                                                                          |
| `owner_email`                                                        | ``                                                                             |                | Site owner email                                                                                                         |
| `owner_name`                                                         | ``                                                                             |                | Site owner display name                                                                                                  |
| `pexels_api_base`                                                    | `https://api.pexels.com/v1`                                                    |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `pipeline_dry_run_mode`                                              | `false`                                                                        |                |                                                                                                                          |
| `pipeline_gate_final_publish_approval`                               | `off`                                                                          |                | HITL approval gate 'final_publish_approval': on/off (auto-managed by approval_service)                                   |
| `pipeline_writer_model`                                              | `ollama/glm-4.7-5090:latest`                                                   |                |                                                                                                                          |
| `plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s` | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `plugin.audio_gen_provider.stable-audio-open-1.0.output_format`      | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate`        | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `plugin.audio_gen_provider.stable-audio-open-1.0.server_url`         | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `plugin.image_provider.flux_schnell.server_url`                      | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `plugin.job.run_dev_diary_post.enabled`                              | `true`                                                                         |                |                                                                                                                          |
| `plugin.llm_provider.gemini.enabled`                                 | `false`                                                                        |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `plugin.video_provider.wan2.1-1.3b.server_url`                       | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `podcast_description`                                                | `AI-development audio essays from Glad...`                                     |                | Podcast RSS description                                                                                                  |
| `podcast_name`                                                       | `Glad Labs Podcast`                                                            |                | Podcast title for RSS feeds                                                                                              |
| `podcast_tts_engine`                                                 | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `preferred_ollama_model`                                             | `gemma3:27b`                                                                   |                |                                                                                                                          |
| `publish_quiet_hours`                                                | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_bad_link_max_penalty`                                   | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_bad_link_penalty`                                       | `0.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_baseline`                                               | `7.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_citation_bonus`                                         | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_first_person_max_penalty`                               | `3.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_first_person_penalty`                                   | `1.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_good_link_bonus`                                        | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_good_link_max_bonus`                                    | `1.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_meta_commentary_max_penalty`                            | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_accuracy_meta_commentary_penalty`                                | `0.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_artifact_penalty_max`                                            | `20.0`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_artifact_penalty_per`                                            | `5.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_clarity_good_max_wps`                                            | `25`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_clarity_good_min_wps`                                            | `10`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_clarity_ideal_max_wps`                                           | `20`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_clarity_ideal_min_wps`                                           | `15`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_clarity_ok_max_wps`                                              | `30`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_clarity_ok_min_wps`                                              | `8`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_heading_bonus`                                      | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_heading_max_bonus`                                  | `1.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_truncation_penalty`                                 | `3.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_word_1000_score`                                    | `5.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_word_1500_score`                                    | `6.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_word_2000_score`                                    | `6.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_word_500_score`                                     | `3.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_completeness_word_min_score`                                     | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_consistency_veto_threshold`                                      | `30`                                                                           |                |                                                                                                                          |
| `qa_critical_floor`                                                  | `50.0`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_engagement_baseline`                                             | `6.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_fallback_writer_model`                                           | `gemma3:27b`                                                                   |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_fk_target_max`                                                   | `12.0`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_fk_target_min`                                                   | `8.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_gate_weight`                                                     | `0`                                                                            |                |                                                                                                                          |
| `qa_llm_buzzword_fail_threshold`                                     | `5`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_buzzword_max_penalty`                                        | `5.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_buzzword_penalty_per`                                        | `0.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_buzzword_warn_max_penalty`                                   | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_buzzword_warn_penalty_per`                                   | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_buzzword_warn_threshold`                                     | `3`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_exclamation_max_penalty`                                     | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_exclamation_penalty_per`                                     | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_exclamation_threshold`                                       | `5`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_filler_fail_threshold`                                       | `4`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_filler_max_penalty`                                          | `4.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_filler_penalty_per`                                          | `0.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_filler_warn_penalty_per`                                     | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_filler_warn_threshold`                                       | `2`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_formulaic_min_avg_words`                                     | `50`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_formulaic_structure_penalty`                                 | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_formulaic_variance`                                          | `0.2`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_hedge_penalty`                                               | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_hedge_ratio_threshold`                                       | `0.02`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_listicle_title_penalty`                                      | `2.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_opener_penalty`                                              | `5.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_patterns_enabled`                                            | `true`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_repetitive_min_count`                                        | `3`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_repetitive_starter_max_penalty`                              | `4.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_repetitive_starter_penalty_per`                              | `1.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_transition_min_count`                                        | `2`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_llm_transition_penalty_per`                                      | `1.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_pass_threshold`                                                  | `70.0`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_preview_screenshot_enabled`                                      | `true`                                                                         |                |                                                                                                                          |
| `qa_relevance_high_coverage_score`                                   | `8.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_relevance_low_coverage_score`                                    | `5.5`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_relevance_med_coverage_score`                                    | `7.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_relevance_none_coverage_score`                                   | `3.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_relevance_no_topic_default`                                      | `6.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_relevance_stuffing_hard_density`                                 | `5.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_relevance_stuffing_soft_density`                                 | `3.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_seo_baseline`                                                    | `6.0`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_title_originality_enabled`                                       | `true`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_title_similarity_threshold`                                      | `0.6`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_topic_dedup_hours`                                               | `48`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `qa_vision_check_enabled`                                            | `true`                                                                         |                |                                                                                                                          |
| `r2_public_url`                                                      | `https://pub-1432fdefa18e47ad98f213a8a...`                                     |                |                                                                                                                          |
| `ragas_enabled`                                                      | `true`                                                                         |                |                                                                                                                          |
| `ragas_judge_model`                                                  | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `rag_default_top_k`                                                  | `5`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `rag_enabled_for_research`                                           | `true`                                                                         |                |                                                                                                                          |
| `rag_hybrid_enabled`                                                 | `true`                                                                         |                |                                                                                                                          |
| `rag_min_similarity`                                                 | `0.3`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `rag_rerank_enabled`                                                 | `true`                                                                         |                |                                                                                                                          |
| `rag_rerank_model`                                                   | `cross-encoder/ms-marco-MiniLM-L-6-v2`                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `rag_rrf_k`                                                          | `60`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `rag_source_filter`                                                  | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `redis_url`                                                          | `*(encrypted)*`                                                                | encrypted      | Redis connection URL                                                                                                     |
| `scheduled_publisher_poll_seconds`                                   | `60`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `sdxl_server_url`                                                    | `http://host.docker.internal:9836`                                             |                | SDXL image generation server                                                                                             |
| `self_consistency_enabled`                                           | `true`                                                                         |                |                                                                                                                          |
| `semantic_dedup_threshold`                                           | `0.92`                                                                         |                |                                                                                                                          |
| `sentry_enabled`                                                     | `true`                                                                         |                | Enable Sentry error tracking                                                                                             |
| `short_video_post_publish_delay_seconds`                             | `180`                                                                          |                | Wait this many seconds after a post publishes before kicking off short-video generation (lets podcast finish first)      |
| `site_description`                                                   | `AI-powered content platform`                                                  |                | Longer site description                                                                                                  |
| `site_tagline`                                                       | `Technology & Innovation`                                                      |                | Short tagline used in metadata                                                                                           |
| `smtp_host`                                                          | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `smtp_port`                                                          | `587`                                                                          |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `smtp_use_tls`                                                       | `true`                                                                         |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `stable_audio_open_server_url`                                       | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `stage_timeout_draft`                                                | `1700`                                                                         |                |                                                                                                                          |
| `storage_access_key`                                                 | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret    |                                                                                                                          |
| `storage_bucket`                                                     | `gladlabs-media`                                                               |                |                                                                                                                          |
| `storage_endpoint`                                                   | `https://01ddb679184ebe59cc7f03f8171d7...`                                     |                |                                                                                                                          |
| `storage_public_url`                                                 | `https://pub-1432fdefa18e47ad98f213a8a...`                                     |                |                                                                                                                          |
| `storage_secret_key`                                                 | `*(encrypted)*`                                                                | encrypted      |                                                                                                                          |
| `storage_token`                                                      | `*(encrypted)*`                                                                | encrypted      |                                                                                                                          |
| `task_timeout_seconds`                                               | `2700`                                                                         |                |                                                                                                                          |
| `topic_dedup_engine`                                                 | `word_overlap`                                                                 |                |                                                                                                                          |
| `topic_discovery_cooldown_minutes`                                   | `5`                                                                            |                |                                                                                                                          |
| `topic_discovery_ideation_lookback_days`                             | `30`                                                                           |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `topic_discovery_length_distribution`                                | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `topic_discovery_manual_trigger`                                     | `false`                                                                        |                |                                                                                                                          |
| `topic_discovery_min_cooldown_seconds`                               | `1800`                                                                         |                |                                                                                                                          |
| `topic_discovery_queue_low_threshold`                                | `999`                                                                          |                |                                                                                                                          |
| `topic_discovery_rejection_streak`                                   | `999`                                                                          |                |                                                                                                                          |
| `topic_discovery_stale_hours`                                        | `8760`                                                                         |                |                                                                                                                          |
| `topic_discovery_streak_window_hours`                                | `6`                                                                            |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `topic_discovery_style_distribution`                                 | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `trusted_source_domains`                                             | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `tts_acronym_replacements`                                           | `{"SOC":"security operations","CRM":"c...`                                     |                |                                                                                                                          |
| `tts_pronunciations`                                                 | `{"GitFlow":"git flow","GitHub":"git h...`                                     |                |                                                                                                                          |
| `use_ollama`                                                         | `false`                                                                        |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `video_compositor`                                                   | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `video_feed_name`                                                    | `Glad Labs Video`                                                              |                | Video RSS feed title                                                                                                     |
| `video_negative_prompt`                                              | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `video_server_url`                                                   | `http://host.docker.internal:9837`                                             |                | Video generation server                                                                                                  |
| `video_tts_engine`                                                   | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |
| `voice_agent_brain`                                                  | `ollama`                                                                       |                | LLM stage the always-on voice agent uses. 'ollama' (default) wires the local glm-4.7-5090 + three read-only Poindexte... |
| `voice_agent_brain_mode`                                             | `claude-code`                                                                  |                |                                                                                                                          |
| `voice_agent_identity`                                               | `poindexter-bot`                                                               |                | Bot identity inside the LiveKit room. Multiple bots in one room need distinct identities. Defaults to 'poindexter-bot... |
| `voice_agent_livekit_enabled`                                        | `true`                                                                         |                | Toggle for the always-on voice-agent-livekit container. 'true' (default) keeps the bot joined to the configured room.... |
| `voice_agent_livekit_url`                                            | `ws://livekit:7880`                                                            |                | WebSocket URL the in-network voice bot uses to reach the LiveKit SFU. 'livekit' is the docker-compose service name; o... |
| `voice_agent_llm_model`                                              | `glm-4.7-5090:latest`                                                          |                | Ollama model tag the voice agent uses for its LLM step. Same daily-driver as pipeline_writer_model by default.           |
| `voice_agent_ollama_url`                                             | `http://host.docker.internal:11434/v1`                                         |                | Ollama base URL. Default targets the host's Ollama from inside Docker; running voice_agent.py directly on the host ca... |
| `voice_agent_recall_k`                                               | `3`                                                                            |                | Top-K most-similar prior voice_messages turns to inject into the qwen3:8b system prompt as 'recalled context' on each... |
| `voice_agent_recall_min_similarity`                                  | `0.5`                                                                          |                | Cosine-similarity floor for voice_messages recall. Hits below this threshold are filtered out before the top-K cut, s... |
| `voice_agent_room_name`                                              | `poindexter`                                                                   |                | LiveKit room the always-on voice-agent-livekit container joins on boot. Operator clients (https://meet.livekit.io, mo... |
| `voice_agent_tts_speed`                                              | `1.0`                                                                          |                | Kokoro playback speed multiplier. 1.0 = natural; 0.95 = slightly slower (helpful for technical content); 1.1 = brisker.  |
| `voice_agent_tts_voice`                                              | `bf_emma`                                                                      |                | Kokoro voice id. bf_emma is the top-graded British female in the Kokoro-82M catalog (B-). Other UK female options: bf... |
| `voice_agent_vad_stop_secs`                                          | `0.2`                                                                          |                | Silero VAD end-of-speech silence window in seconds. Lower = snappier turn-taking but more risk of cutting the user of... |
| `voice_agent_webrtc_enabled`                                         | `true`                                                                         |                | Toggle for the always-on voice-agent-webrtc container. 'true' (default) serves the SmallWebRTC prebuilt UI on voice_a... |
| `voice_agent_webrtc_host`                                            | `0.0.0.0`                                                                      |                | Bind host for the voice WebRTC service. 0.0.0.0 makes the agent reachable from any Tailscale device on the tailnet. U... |
| `voice_agent_webrtc_port`                                            | `8003`                                                                         |                | Bind port for the voice WebRTC service. Sits above worker API (8002) and below typical dev tools.                        |
| `wan_server_url`                                                     | ``                                                                             |                | Auto-seeded by services.settings_defaults (#379)                                                                         |

## gpu

| Key              | Default | Classification | Description                                             |
| ---------------- | ------- | -------------- | ------------------------------------------------------- |
| `ollama_num_ctx` | `8192`  |                | Ollama context window size — limits KV cache VRAM usage |

## identity

| Key                      | Default                                 | Classification | Description                                    |
| ------------------------ | --------------------------------------- | -------------- | ---------------------------------------------- |
| `api_base_url`           | `http://worker:8002`                    |                | Backend API base URL                           |
| `company_age_months`     | `6`                                     |                | Company age in months (update periodically)    |
| `company_founded_date`   | `2025-09-25`                            |                | Company founding date                          |
| `company_founded_year`   | `2025`                                  |                | Company founding year                          |
| `company_founder_name`   | `Matt`                                  |                | Founder name                                   |
| `company_name`           | `Glad Labs`                             |                | Legal company name                             |
| `company_products`       | `gladlabs.io,content pipeline,openclaw` |                | Known real products (for hallucination checks) |
| `company_team_size`      | `1`                                     |                | Team size for content validation               |
| `discord_ops_channel_id` | `1487683559065125055`                   |                | Discord channel for ops notifications          |
| `gpu_model`              | `NVIDIA RTX 5090 (32GB VRAM)`           |                | GPU model for brain knowledge                  |
| `newsletter_from_email`  | `Glad Labs <newsletter@gladlabs.io>`    |                | Newsletter sender address                      |
| `privacy_email`          | `privacy@gladlabs.io`                   |                | Privacy/GDPR contact email                     |
| `site_domain`            | `gladlabs.io`                           |                | Production domain (no protocol)                |
| `site_name`              | `Glad Labs`                             |                | Brand/site name used across all services       |
| `site_url`               | `https://www.gladlabs.io`               |                | Full production URL with protocol              |
| `support_email`          | `support@gladlabs.io`                   |                | Support contact email                          |

## image

| Key                       | Default                                    | Classification | Description                                                                                           |
| ------------------------- | ------------------------------------------ | -------------- | ----------------------------------------------------------------------------------------------------- |
| `enable_featured_image`   | `true`                                     |                | Generate/search featured images for posts                                                             |
| `image_generation_model`  | `sdxl_lightning`                           |                | AI image generation model (sdxl_base, sdxl_lightning, flux_schnell)                                   |
| `image_negative_prompt`   | `text, words, letters, numbers, waterm...` |                | Negative prompt for all SDXL generations                                                              |
| `image_primary_source`    | `ai_generation`                            |                | Primary image source: pexels or ai_generation                                                         |
| `image_style_business`    | `watercolor illustration, warm golden ...` |                | SDXL style prompt for Business posts                                                                  |
| `image_style_default`     | `professional digital art, abstract te...` |                | Default SDXL style for uncategorized posts                                                            |
| `image_style_engineering` | `technical blueprint style, white line...` |                | SDXL style prompt for Engineering posts                                                               |
| `image_style_insights`    | `abstract data visualization art, flow...` |                | SDXL style prompt for Insights posts                                                                  |
| `image_styles`            | `[     {"name": "flat_vector", "scene"...` |                | JSON array of image styles for SDXL featured/inline image generation. Each has name, scene, and tags. |
| `image_style_security`    | `dark dramatic digital art, geometric ...` |                | SDXL style prompt for Security posts                                                                  |
| `image_style_startup`     | `colorful flat illustration, modern ve...` |                | SDXL style prompt for Startup posts                                                                   |
| `image_style_technology`  | `digital art, abstract glowing circuit...` |                | SDXL style prompt for Technology posts                                                                |

## integration

| Key                     | Default                       | Classification | Description                                             |
| ----------------------- | ----------------------------- | -------------- | ------------------------------------------------------- |
| `cloudinary_api_key`    | `*(encrypted)*`               | encrypted      | Cloudinary API key for SDXL image hosting               |
| `cloudinary_api_secret` | `*(encrypted)*`               | encrypted      | Cloudinary API secret for SDXL image hosting            |
| `cloudinary_cloud_name` | `dujk7kdhd`                   |                | Cloudinary cloud name                                   |
| `elevenlabs_api_key`    | `*(encrypted)*`               | encrypted      | ElevenLabs TTS API key (legacy)                         |
| `gitea_password`        | `*(encrypted)*`               | encrypted      | Gitea admin password                                    |
| `gitea_repo`            | `gladlabs/glad-labs-codebase` |                | Gitea repository (owner/name)                           |
| `gitea_url`             | `http://localhost:3001`       |                | Gitea server URL                                        |
| `gitea_user`            | `gladlabs`                    |                | Gitea username                                          |
| `grafana_api_key`       | `*(encrypted)*`               | encrypted      | Grafana Cloud service account token (sa-1-claude-api)   |
| `grafana_url`           | `http://localhost:3000`       |                | Grafana Cloud instance URL                              |
| `notion_api_key`        | `*(encrypted)*`               | encrypted      | Notion API integration key                              |
| `patreon_account`       | `active`                      |                | Patreon account active — free podcast hosting available |
| `telegram_bot_token`    | `*(encrypted)*`               | encrypted      | Telegram bot token (brain notifications)                |

## integrations

| Key                              | Default                                    | Classification | Description                                                                                                              |
| -------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `devto_api_key`                  | `*(encrypted)*`                            | encrypted      | Dev.to API key for cross-posting                                                                                         |
| `gh_repo`                        | `Glad-Labs/glad-labs-stack`                |                | GitHub repository (`owner/name`) the dev_diary topic source queries for merged PRs and notable commits when assembl...   |
| `gh_token`                       | `*(encrypted)*`                            | encrypted      | GitHub Personal Access Token used by the dev_diary topic source (services/topic_sources/dev_diary_source.py) to authe... |
| `google_oauth_client_id`         | `206722606964-lt75101b5surs28ede8t7d3j...` |                | Google OAuth client ID — shared by GSC + GA4 Singer taps                                                                 |
| `integrations_framework_version` | `1`                                        |                | Declarative integrations framework version.                                                                              |
| `lemon_squeezy_api_key`          | `*(encrypted)*`                            | encrypted      | Lemon Squeezy Store API key (JWT). Used by the Pro-tier activate flow (gitea#225) to fetch license-validated download... |
| `revalidate_secret`              | `*(encrypted)*`                            | encrypted      |                                                                                                                          |
| `telegram_cli_audit_logged`      | `true`                                     |                | When 'true' (default), every /cli invocation writes one row to the audit_log table (event_type='telegram_cli_invoked'... |
| `telegram_cli_enabled`           | `true`                                     |                | Global kill-switch for the Telegram /cli passthrough. When 'true' (default), '/cli <args>' messages from the configur... |
| `telegram_cli_max_output_chars`  | `3500`                                     |                | Maximum characters of combined stdout+stderr the Telegram /cli passthrough will reply with. Telegram's hard per-messa... |
| `telegram_cli_safe_commands`     | `post,settings,validators,auth,check_h...` |                | Comma-separated allowlist of top-level poindexter CLI subcommands the Telegram /cli passthrough will execute. The fir... |
| `telegram_cli_timeout_seconds`   | `30`                                       |                | Wall-clock timeout (seconds) for a /cli subprocess. After this many seconds the passthrough kills the process group a... |

## logging

| Key                    | Default | Classification | Description                                                                                                              |
| ---------------------- | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `max_log_backup_count` | `3`     |                | Number of rotated log backups to retain. Default 3 matches the historical env-var fallback. Ref: GH-175.                 |
| `max_log_size_mb`      | `5`     |                | Maximum size in MB of a rotating log file before it's rolled over. Default 5 MB matches the historical env-var fallba... |

## memory

| Key                                | Default                       | Classification | Description                                                                                                              |
| ---------------------------------- | ----------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `embedding_collapse_age_days`      | `14`                          |                | Days before raw embeddings get clustered + summarized into a single is_summary=TRUE row by CollapseOldEmbeddingsJob. ... |
| `embedding_collapse_cluster_size`  | `8`                           |                | GH-81: target cluster count per (source_table, age-group) when the collapse job runs k-means over candidate embedding... |
| `embedding_collapse_enabled`       | `true`                        |                | GH-81: master switch for the embeddings collapse job. When true, the scheduled job clusters old rows per source_table... |
| `embedding_collapse_source_tables` | `claude_sessions,brain,audit` |                | GH-81: comma-separated list of source_table values the collapse job is allowed to touch. posts/issues/memory are deli... |

## memory_alerts

| Key                                                  | Default    | Classification | Description                                                         |
| ---------------------------------------------------- | ---------- | -------------- | ------------------------------------------------------------------- |
| `memory_stale_threshold_seconds_audit-legacy`        | `31536000` |                | Backfilled label for pre-writer-tagging audit embeddings            |
| `memory_stale_threshold_seconds_collapse_job`        | `1209600`  |                | CollapseOldEmbeddingsJob runs every 7d; threshold 14d (2x schedule) |
| `memory_stale_threshold_seconds_gitea`               | `31536000` |                | Gitea decommissioned 2026-04-30; effectively silenced               |
| `memory_stale_threshold_seconds_gitea-issues-legacy` | `31536000` |                | Backfilled label for old gitea issues embedding                     |
| `memory_stale_threshold_seconds_poindexter-samples`  | `31536000` |                | One-off seed writer; not expected to refresh                        |
| `memory_stale_threshold_seconds_worker`              | `31536000` |                | Legacy writer label, replaced by auto-embed/brain-daemon            |

## memory_compression

| Key                                          | Default             | Classification | Description                                                                                                               |
| -------------------------------------------- | ------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `embedding_collapse_summary_model`           | `gemma3:27b-it-qat` |                | Ollama model used for cluster-summary generation. Picked empirically: factually dense, no thinking-trace overhead, ~1...  |
| `embedding_collapse_summary_provider`        | `ollama`            |                | Summarization backend for CollapseOldEmbeddingsJob. 'ollama' calls the local LLM to produce a real summary; 'joined_p...  |
| `embedding_collapse_summary_timeout_seconds` | `60`                |                | Per-call timeout for the LLM summary generation. 60s allows headroom over the ~12s typical run; on timeout the cluste...  |
| `memory_compression_excerpts_per_bucket`     | `12`                |                | How many sample rows feed the LLM prompt and land in the {event_type}\_excerpts JSONB column for each day-bucket. 12 i... |
| `memory_compression_summary_model`           | `gemma3:27b-it-qat` |                | Ollama model used by retention.summarize_to_table for the per-day summary paragraph. Same default as embedding_collap...  |
| `memory_compression_summary_timeout_seconds` | `60`                |                | Per-call timeout (seconds) for the LLM summary generation in retention.summarize_to_table. On timeout the handler fal...  |

## model_roles

| Key                         | Default                      | Classification | Description                                                                                                          |
| --------------------------- | ---------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------- |
| `inline_image_prompt_model` | `llama3:latest`              |                | Ollama model used to craft SDXL prompts for inline images in blog posts                                              |
| `model_role_code_review`    | `ollama/qwen3-coder:30b`     |                | Best at: code snippets in posts, technical accuracy, API examples.                                                   |
| `model_role_creative`       | `ollama/glm-4.7-5090:latest` |                | Best at: engaging hooks, narrative structure, diverse writing styles.                                                |
| `model_role_critic`         | `ollama/gemma3:27b`          |                | Best at: quality scoring, detecting issues, structured JSON output. Shootout score: 96 standalone.                   |
| `model_role_factchecker`    | `ollama/gemma3:27b`          |                | Best at: factual accuracy, catching hallucinated claims, conservative reviewer. Different training data from writer. |
| `model_role_image_prompt`   | `ollama/qwen3:8b`            |                | Best at: generating SDXL prompts, visual descriptions. Fast.                                                         |
| `model_role_seo`            | `ollama/qwen3:8b`            |                | Best at: concise output, title generation, keyword extraction. Fast for metadata tasks.                              |
| `model_role_summarizer`     | `ollama/phi3:latest`         |                | Best at: fast summaries, social media copy, short-form content. Lightweight.                                         |
| `model_role_writer`         | `ollama/glm-4.7-5090:latest` |                | Best at: long-form content, structured output, follows instructions. Shootout score: 96 in hybrid config.            |
| `podcast_script_model`      | `gemma3:27b`                 |                | Ollama model used to generate podcast scripts from article content                                                   |
| `qa_fallback_critic_model`  | `gemma3:27b`                 |                | Fallback critic model used when pipeline_critic_model returns empty or errors                                        |
| `video_scene_model`         | `llama3:latest`              |                | Ollama model used to generate video scene descriptions from article text                                             |

## models

| Key                       | Default             | Classification | Description                                                      |
| ------------------------- | ------------------- | -------------- | ---------------------------------------------------------------- |
| `cloud_api_daily_limit`   | `5`                 |                | Max cloud API calls per day in emergency mode (hard cap)         |
| `cloud_api_mode`          | `emergency_only`    |                | Cloud API usage mode: disabled, emergency_only, fallback, always |
| `cloud_api_notify_on_use` | `true`              |                | Send Telegram alert when a cloud API is used                     |
| `pipeline_critic_model`   | `ollama/gemma3:27b` |                | Model for QA/content review                                      |
| `pipeline_fallback_model` | `ollama/gemma3:27b` |                | Fallback model when primary is unavailable                       |
| `pipeline_seo_model`      | `ollama/qwen3:8b`   |                | Model for SEO title/description generation                       |
| `pipeline_social_model`   | `ollama/qwen3:8b`   |                | Model for social media post generation                           |

## monitoring

| Key                                              | Default                                    | Classification | Description                                                                                                              |
| ------------------------------------------------ | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `approval_queue_alert_threshold`                 | `15`                                       |                | Awaiting-approval task count above which the brain `approval_queue` probe alerts. Default 15. Was hardcoded to 5; rai... |
| `compose_drift_auto_recover_enabled`             | `false`                                    |                | Brain compose-drift probe auto-recover toggle (#213). When 'false' (default, safe), the probe only notifies the opera... |
| `compose_drift_skip_services`                    | ``                                         |                | Comma-separated list of compose service names the brain drift probe (#213) should skip. Useful for services with inte... |
| `compose_spec_path`                              | `/app/docker-compose.local.yml`            |                | Path to the docker-compose.yml the brain compose-drift probe (#213) reads. The brain container bind-mounts the host's... |
| `docker_port_forward_poll_interval_minutes`      | `5`                                        |                | Cadence at which the brain runs the port-forward probe. Default 5 min matches the brain cycle so it runs every cycle.    |
| `docker_port_forward_probe_enabled`              | `true`                                     |                | Master switch for the brain Docker port-forward stuck-state probe (#222). When false the probe short-circuits without... |
| `docker_port_forward_probe_timeout_seconds`      | `3`                                        |                | Per-HTTP-probe timeout in seconds. Kept tight (3s) so a stuck service can't block the brain cycle on probes.             |
| `docker_port_forward_recovery_wait_seconds`      | `5`                                        |                | How long the probe waits after a docker restart before re-probing to confirm recovery. Default 5s lets Docker Desktop... |
| `docker_port_forward_restart_cap_per_window`     | `3`                                        |                | Maximum number of times a single container may be restarted within the rolling window. Prevents runaway restart loops... |
| `docker_port_forward_restart_cap_window_minutes` | `60`                                       |                | Rolling window length in minutes for the per-container restart cap. Default 60 min — combined with the cap of 3 means... |
| `glitchtip_base_url`                             | `http://glitchtip-web:8000`                |                | Base URL for the GlitchTip API the brain triage probe queries. Default is the compose-internal hostname; brain.docker... |
| `glitchtip_triage_alert_threshold_count`         | `100`                                      |                | Brain triage probe pages via notify_operator() when a GlitchTip issue has count >= this AND matches no entry in glitc... |
| `glitchtip_triage_api_token`                     | `*(encrypted)*`                            | encrypted      | GlitchTip API token for brain triage probe (PR #159)                                                                     |
| `glitchtip_triage_auto_resolve_patterns`         | `[{"title_pattern": "Failed to export ...` |                | JSONB array of triage rules for the brain GlitchTip probe. Each entry: {title_pattern: <regex>, action: 'resolve' or ... |
| `glitchtip_triage_enabled`                       | `true`                                     |                | Master enable for the brain GlitchTip triage probe. When 'true' (default), the probe runs every cycle (5-min), pulls ... |
| `glitchtip_triage_org_slug`                      | `glad-labs`                                |                | GlitchTip organization slug the brain triage probe queries. Default 'glad-labs' matches the org the bootstrap install... |
| `gpu_temperature_high_threshold_c`               | `85`                                       |                | GPU core temperature (C) above which the brain `gpu_temperature` probe alerts. RTX 5090 hard-throttles around 90C; 85... |
| `grafana_alert_sync_enabled`                     | `true`                                     |                | Master switch for the brain daemon's Grafana alert sync loop. Set to 'false' to disable the loop entirely without rem... |
| `grafana_alert_sync_interval_cycles`             | `3`                                        |                | How many brain cycles (5 min each) between Grafana alert syncs. Default 3 = 15 min. Lowering this makes alert rule ch... |
| `grafana_api_base_url`                           | `http://poindexter-grafana:3000`           |                | Grafana base URL the brain daemon uses to push alert rules and contact points. Defaults to the docker-compose service... |
| `grafana_api_token`                              | `*(encrypted)*`                            | encrypted      | Grafana service-account token (Administration → Service accounts → Add service account → Add token). Required for the... |
| `migration_drift_auto_recover_enabled`           | `false`                                    |                | Brain migration-drift probe behavior (#228). When 'false' (default, safe), the probe ONLY notifies the operator when ... |
| `morning_brief_enabled`                          | `true`                                     |                | Master switch for the morning_brief scheduled job. When false the job short-circuits and never queries Postgres.         |
| `morning_brief_hour_local`                       | `7`                                        |                | Local-time hour the morning_brief job fires (informational; the active schedule lives in the Job class cron expressio... |
| `morning_brief_lookback_hours`                   | `24`                                       |                | Lookback window in hours used to roll up published posts, awaiting_approval entries, failed tasks, alert counts, cost... |
| `morning_brief_telegram_critical_only`           | `true`                                     |                | When true the brief only pings Telegram on critical-severity alerts or failed tasks (Discord still always receives th... |
| `probe_webhook_freshness_enabled`                | `true`                                     |                | Master switch for the brain's webhook-freshness probe. When true, the probe checks revenue_events / subscriber_events... |
| `probe_webhook_freshness_interval_minutes`       | `1440`                                     |                | How often the webhook-freshness probe runs (minutes). Default 1440 = once a day. The probe is cheap (two SELECT MAX q... |
| `pr_staleness_dedup_hours`                       | `12`                                       |                | Quiet period (in hours) after a stale-PR alert fires before the same PR can re-page. Per-PR dedup is anchored on the ... |
| `pr_staleness_max_prs_per_alert`                 | `5`                                        |                | Cap on the number of PRs surfaced in a single Discord-ops message body. Keeps the alert under Discord's per-message c... |
| `pr_staleness_min_hours`                         | `24`                                       |                | Minimum age (in hours) before an open PR is considered stale by the brain PR staleness probe. PRs younger than this a... |
| `pr_staleness_poll_interval_minutes`             | `60`                                       |                | Internal cadence gate for the brain PR staleness probe. The brain dispatches the probe every cycle (~5 min); the actu... |
| `pr_staleness_probe_enabled`                     | `true`                                     |                | Master switch for the brain PR staleness probe. When false the probe short-circuits without hitting GitHub. See brain... |
| `pr_staleness_repo`                              | `Glad-Labs/glad-labs-stack`                |                | GitHub repository (`owner/name`) the brain PR staleness probe scans for open PRs. Future-proofs for multi-repo. Pai...   |
| `smart_monitor_alert_dedup_minutes`              | `360`                                      |                | Don't re-fire the same (drive, attribute) alert within this many minutes. Default 360 (6 h) matches the default poll ... |
| `smart_monitor_current_pending_threshold`        | `0`                                        |                | Inclusive threshold for Current_Pending_Sector. Anything strictly greater fires a warning. 0 = any pending sector at ... |
| `smart_monitor_drive_filter`                     | ``                                         |                | Optional comma-separated list of drive names (e.g. /dev/sda,/dev/nvme0) to restrict scanning to. Empty = scan everyth... |
| `smart_monitor_enabled`                          | `true`                                     |                | Master switch for the brain SMART monitor probe (#387). When false, the probe short-circuits without scanning drives.    |
| `smart_monitor_poll_interval_hours`              | `6`                                        |                | Cadence at which the brain runs `smartctl -a` against each detected drive. Default 6h matches typical SMART attribute... |
| `smart_monitor_power_on_hours_info_threshold`    | `50000`                                    |                | Power_On_Hours threshold above which the probe emits an info-severity FYI alert. ~50k h = ~5.7 years; useful for repl... |
| `smart_monitor_reallocated_sector_threshold`     | `0`                                        |                | Inclusive threshold for Reallocated_Sector_Ct. Anything strictly greater fires a warning alert. 0 = any reallocated s... |
| `smart_monitor_smartctl_path`                    | ``                                         |                | Absolute path to the smartctl binary. Empty = use shutil.which("smartctl"). Override when smartmontools is installed ... |
| `smart_monitor_wear_leveling_warn_percent`       | `90`                                       |                | Used-life percentage for SSD Wear_Leveling_Count above which the probe fires a warning. Computed as (100 - normalized... |
| `uptime_kuma_admin_password`                     | `*(encrypted)*`                            | encrypted      | Kuma admin password (set by scripts/kuma_bootstrap.py)                                                                   |
| `uptime_kuma_admin_username`                     | `admin`                                    |                | Kuma admin username (set by scripts/kuma_bootstrap.py)                                                                   |
| `uptime_kuma_api_key`                            | `*(encrypted)*`                            | encrypted      | Kuma metrics-scrape API key (set by scripts/kuma_bootstrap.py)                                                           |
| `webhook_freshness_revenue_threshold_days`       | `30`                                       |                | Notify operator if no row has been added to revenue_events in this many days. Default 30 because Lemon Squeezy is int... |
| `webhook_freshness_subscriber_threshold_days`    | `7`                                        |                | Notify operator if no row has been added to subscriber_events in this many days. Default 7 because Resend should see ... |

## newsletter

| Key                    | Default     | Classification | Description                          |
| ---------------------- | ----------- | -------------- | ------------------------------------ |
| `newsletter_enabled`   | `true`      |                | Enable newsletter sending on publish |
| `newsletter_from_name` | `Glad Labs` |                | Newsletter sender display name       |
| `newsletter_provider`  | `resend`    |                | Email provider: resend or smtp       |

## niche_pivot

| Key                                    | Default                                    | Classification | Description                                                                                                               |
| -------------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `niche_batch_expires_days`             | `7`                                        |                | Number of days a topic_batch row stays open before its expires_at watermark trips. Default 7 matches the prior hardco...  |
| `niche_carry_forward_decay_factor`     | `0.7`                                      |                | Multiplicative decay applied to a candidate's decay_factor each time it survives a batch unpicked. Default 0.7 matche...  |
| `niche_embedding_model`                | `nomic-embed-text`                         |                | Ollama embedding model used for niche topic ranking and writer-mode RAG snippet retrieval. Default 'nomic-embed-text'...  |
| `niche_goal_descriptions`              | `{"TRAFFIC": "Topic likely to attract ...` |                | JSON blob mapping each goal_type (TRAFFIC, EDUCATION, BRAND, AUTHORITY, REVENUE, COMMUNITY, NICHE_DEPTH) to the prose...  |
| `niche_internal_rag_per_kind_limit`    | `4`                                        |                | Per-source-kind limit passed to InternalRagSource.generate by TopicBatchService.\_discover_internal. Default 4 matches... |
| `niche_internal_rag_snippet_max_chars` | `600`                                      |                | Per-snippet character cap when joining raw snippets into the topic/angle distillation prompt in InternalRagSource.\_di... |
| `niche_ollama_chat_timeout_seconds`    | `60`                                       |                | HTTP timeout (seconds) for direct Ollama /api/chat calls made by topic_ranking.\_ollama_chat_json — used by the LLM sc... |
| `niche_top_n_per_pool`                 | `5`                                        |                | Top N candidates per pool (external + internal) carried forward from the embedding pre-rank into the LLM final-score ...  |

## notifications

| Key                       | Default                                    | Classification | Description                                                                                                              |
| ------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `discord_ops_webhook_url` | `https://discord.com/api/webhooks/1494...` |                |                                                                                                                          |
| `preview_base_url`        | `http://100.81.93.12:8002`                 |                |                                                                                                                          |
| `telegram_alerts_enabled` | `false`                                    |                | Telegram is for severity=critical infra alerts only. Discord receives all routine pipeline events (awaiting approval,... |
| `telegram_alert_types`    | `error,critical,deploy,probe_failure`      |                | Comma-separated alert types to send (error,critical,deploy,probe_failure,info)                                           |
| `telegram_chat_id`        | `5318613610`                               |                | Telegram chat ID for all alerts (Matt DM)                                                                                |

## observability

| Key                           | Default                                                                        | Classification | Description                                                                                                              |
| ----------------------------- | ------------------------------------------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `enable_pyroscope`            | `true`                                                                         |                | When true, services/profiling.py:setup_pyroscope() configures the pyroscope-io agent at worker / brain / voice-agent ... |
| `enable_tracing`              | `true`                                                                         |                | Master switch for OpenTelemetry tracing. When true, services.tracing.setup_tracing initializes the TracerProvider + O... |
| `langfuse_host`               | `http://localhost:3010`                                                        |                | Langfuse base URL for prompt management + tracing. Default empty = Langfuse disabled, prompts resolve via DB+YAML fal... |
| `langfuse_public_key`         | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret    | Langfuse project public key (pk-lf-...). Pair with langfuse_secret_key to authenticate prompt-management + tracing AP... |
| `langfuse_secret_key`         | `*(encrypted)*`                                                                | encrypted      | Langfuse project secret key (sk-lf-...). Encrypted at rest via the app_settings auto-encrypt trigger.                    |
| `langfuse_tracing_enabled`    | `true`                                                                         |                | When true (default), LiteLLMProvider registers Langfuse as a success/failure callback so every LLM call emits a span ... |
| `otel_exporter_otlp_endpoint` | `http://tempo:4317`                                                            |                | OTLP gRPC endpoint that the worker pushes spans to. Default points at the docker-compose tempo service on its OTLP gR... |
| `pyroscope_server_url`        | `http://pyroscope:4040`                                                        |                | Pyroscope ingestion URL for worker agent                                                                                 |

## performance

| Key                            | Default | Classification | Description                                                                                                              |
| ------------------------------ | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `ollama_concurrency_limit`     | `10`    |                | Maximum concurrent in-flight Ollama requests this worker process will issue — passed to aiolimiter.AsyncLimiter. Prev... |
| `ollama_max_retries`           | `3`     |                | Maximum number of attempts (initial + retries) for Ollama generate_with_retry. Passed to tenacity's stop_after_attemp... |
| `ollama_retry_initial_seconds` | `1`     |                | Initial backoff delay (seconds) for Ollama retries — passed to tenacity's wait_exponential_jitter(initial). Doubles o... |
| `ollama_retry_max_seconds`     | `30`    |                | Upper bound (seconds) on Ollama retry backoff — passed to tenacity's wait_exponential_jitter(max). Caps the exponenti... |

## pipeline

| Key                                          | Default                                    | Classification | Description                                                                                                              |
| -------------------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `approval_ttl_days`                          | `7`                                        |                | Days before unapproved posts are auto-expired                                                                            |
| `auto_publish_threshold`                     | `0`                                        |                | Quality score threshold for auto-publishing (0=disabled)                                                                 |
| `brain_auto_cancel_grace_minutes`            | `10`                                       |                | Extra grace period the brain daemon adds on top of stale_task_timeout_minutes before flipping a stuck task to failed.... |
| `content_quality_minimum`                    | `75`                                       |                | Minimum quality score to even queue for approval. Below this = auto-reject.                                              |
| `content_weekly_cap`                         | `3`                                        |                | Maximum new posts per week (0=unlimited). Topic discovery respects this.                                                 |
| `daily_budget_usd`                           | `5.00`                                     |                | Daily LLM API spend budget in USD                                                                                        |
| `daily_post_limit`                           | `1`                                        |                | Maximum posts to generate per day                                                                                        |
| `default_model_tier`                         | `budget`                                   |                | Default model cost tier (free/budget/standard/premium/flagship)                                                          |
| `max_approval_queue`                         | `100`                                      |                | Restored 2026-04-24 after backlog cleared                                                                                |
| `max_posts_per_day`                          | `3`                                        |                | Maximum posts to publish per day                                                                                         |
| `max_task_retries`                           | `3`                                        |                | Maximum retry attempts for failed tasks                                                                                  |
| `max_tokens_per_request`                     | `4000`                                     |                | Maximum output tokens per LLM request                                                                                    |
| `max_tokens_per_task`                        | `16000`                                    |                | Maximum total tokens (input+output) per content task                                                                     |
| `min_curation_score`                         | `75`                                       |                | Minimum QA score to surface for human review (below this = auto-reject)                                                  |
| `pipeline_architect_model`                   | `glm-4.7-5090:latest`                      |                | Local Ollama model the architect-LLM uses to compose pipelines from intent + atom catalog. Cloud models are opt-in on... |
| `pipeline_architect_timeout_seconds`         | `120.0`                                    |                | Max seconds to wait for the architect LLM to emit its JSON graph spec before timing out and falling back to a default... |
| `pipeline_factcheck_model`                   | `programmatic`                             |                | Model for fact-checking -- programmatic or LLM provider                                                                  |
| `pipeline_refinement_model`                  | `ollama/glm-4.7-5090:latest`               |                | Model for content refinement (stage 5)                                                                                   |
| `pipeline_research_model`                    | `ollama/glm-4.7-5090:latest`               |                | Model for research stage (stage 1)                                                                                       |
| `pipeline.stages.order`                      | `["verify_task", "generate_content", "...` |                | Ordered list of Stage names the content pipeline runs                                                                    |
| `publish_spacing_hours`                      | `4`                                        |                | Minimum hours between published posts                                                                                    |
| `require_human_approval`                     | `true`                                     |                | When true, all content requires human approval before publishing                                                         |
| `seed_url_fetch_timeout_seconds`             | `10`                                       |                | URL-based topic seeding: total HTTP timeout (seconds) for the seed_url fetch on POST /api/tasks. Short by design — if... |
| `seed_url_max_bytes`                         | `1048576`                                  |                | URL-based topic seeding: hard cap (bytes) on the decoded response body. Guards against pathological pages that would ... |
| `seed_url_user_agent`                        | `Mozilla/5.0 (Windows NT 10.0; Win64; ...` |                | URL-based topic seeding: User-Agent header for the seed_url fetch. Chrome-ish by default because many news/publisher ... |
| `staging_mode`                               | `false`                                    |                | When true, posts go to draft with preview token instead of publishing                                                    |
| `stale_task_timeout_minutes`                 | `180`                                      |                | Minutes before a running task is considered stale                                                                        |
| `task_executor_idle_alert_threshold_seconds` | `1800`                                     |                | Seconds the executor may sit on pending tasks without starting one before logging CRITICAL. Default 300 was firing ev... |
| `task_sweep_interval_seconds`                | `300`                                      |                | Seconds between stale task sweeps                                                                                        |
| `template_runner_progress_streaming`         | `true`                                     |                | When on, TemplateRunner emits per-node progress to Discord (NOT Telegram) via notify_operator(critical=False). Defaul... |
| `template_runner_use_postgres_checkpointer`  | `true`                                     |                | When true, services/template_runner.py compiles each LangGraph with an AsyncPostgresSaver checkpointer (durable state... |
| `topic_dedup_existing_threshold`             | `0.7`                                      |                | Word-overlap ratio above which a candidate topic is treated as a duplicate of an existing published post or in-flight... |
| `topic_dedup_intra_batch_threshold`          | `0.65`                                     |                | Word-overlap ratio above which two candidates from the same scrape batch are treated as duplicates (gitea#279). Range... |
| `worker_heartbeat_interval_seconds`          | `30`                                       |                | Worker heartbeat cadence. While processing a single task the TaskExecutor stamps content_tasks.updated_at = NOW() eve... |

## plugins

| Key                                    | Default                                    | Classification | Description                                                                                                              |
| -------------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `plugin.job.render_prometheus_rules`   | `{"enabled": true, "interval_seconds":...` |                | Config for RenderPrometheusRulesJob                                                                                      |
| `plugin.llm_provider.primary.budget`   | `litellm`                                  |                | Default LLMProvider for the 'budget' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call ti... |
| `plugin.llm_provider.primary.flagship` | `litellm`                                  |                | Default LLMProvider for the 'flagship' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call ... |
| `plugin.llm_provider.primary.free`     | `litellm`                                  |                | Default LLMProvider for the 'free' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call time... |
| `plugin.llm_provider.primary.premium`  | `litellm`                                  |                | Default LLMProvider for the 'premium' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call t... |
| `plugin.llm_provider.primary.standard` | `litellm`                                  |                | Default LLMProvider for the 'standard' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call ... |

## plugin_telemetry

| Key                                                  | Default      | Classification | Description                                                                                            |
| ---------------------------------------------------- | ------------ | -------------- | ------------------------------------------------------------------------------------------------------ |
| `plugin_job_last_run_audit_published_quality`        | `1778195486` |                | Unix epoch of last fire for plugin job 'audit_published_quality' (auto-written by PluginScheduler)     |
| `plugin_job_last_run_auto_embed_posts`               | `1778209889` |                | Unix epoch of last fire for plugin job 'auto_embed_posts' (auto-written by PluginScheduler)            |
| `plugin_job_last_run_check_published_links`          | `1778195486` |                | Unix epoch of last fire for plugin job 'check_published_links' (auto-written by PluginScheduler)       |
| `plugin_job_last_run_crosspost_to_devto`             | `1778202686` |                | Unix epoch of last fire for plugin job 'crosspost_to_devto' (auto-written by PluginScheduler)          |
| `plugin_job_last_run_db_backup`                      | `1778055256` |                | Unix epoch of last fire for plugin job 'db_backup' (auto-written by PluginScheduler)                   |
| `plugin_job_last_run_expire_stale_approvals`         | `1778195486` |                | Unix epoch of last fire for plugin job 'expire_stale_approvals' (auto-written by PluginScheduler)      |
| `plugin_job_last_run_flag_missing_seo`               | `1778055256` |                | Unix epoch of last fire for plugin job 'flag_missing_seo' (auto-written by PluginScheduler)            |
| `plugin_job_last_run_noop`                           | `1778209886` |                | Unix epoch of last fire for plugin job 'noop' (auto-written by PluginScheduler)                        |
| `plugin_job_last_run_postgres_vacuum`                | `1778195488` |                | Unix epoch of last fire for plugin job 'postgres_vacuum' (auto-written by PluginScheduler)             |
| `plugin_job_last_run_reload_site_config`             | `1778211986` |                | Unix epoch of last fire for plugin job 'reload_site_config' (auto-written by PluginScheduler)          |
| `plugin_job_last_run_render_prometheus_rules`        | `1778211986` |                | Unix epoch of last fire for plugin job 'render_prometheus_rules' (auto-written by PluginScheduler)     |
| `plugin_job_last_run_run_dev_diary_post`             | `1778158810` |                | Unix epoch of last fire for plugin job 'run_dev_diary_post' (auto-written by PluginScheduler)          |
| `plugin_job_last_run_run_niche_topic_sweep`          | `1778211686` |                | Unix epoch of last fire for plugin job 'run_niche_topic_sweep' (auto-written by PluginScheduler)       |
| `plugin_job_last_run_sync_newsletter_subscribers`    | `1778211686` |                | Unix epoch of last fire for plugin job 'sync_newsletter_subscribers' (auto-written by PluginScheduler) |
| `plugin_job_last_run_sync_page_views`                | `1778211686` |                | Unix epoch of last fire for plugin job 'sync_page_views' (auto-written by PluginScheduler)             |
| `plugin_job_last_run_tune_publish_threshold`         | `1778195486` |                | Unix epoch of last fire for plugin job 'tune_publish_threshold' (auto-written by PluginScheduler)      |
| `plugin_job_last_run_verify_published_posts`         | `1778211687` |                | Unix epoch of last fire for plugin job 'verify_published_posts' (auto-written by PluginScheduler)      |
| `plugin_job_last_status_audit_published_quality`     | `ok`         |                | Outcome of last fire for plugin job 'audit_published_quality': 'ok' or 'err'                           |
| `plugin_job_last_status_auto_embed_posts`            | `ok`         |                | Outcome of last fire for plugin job 'auto_embed_posts': 'ok' or 'err'                                  |
| `plugin_job_last_status_check_published_links`       | `ok`         |                | Outcome of last fire for plugin job 'check_published_links': 'ok' or 'err'                             |
| `plugin_job_last_status_crosspost_to_devto`          | `ok`         |                | Outcome of last fire for plugin job 'crosspost_to_devto': 'ok' or 'err'                                |
| `plugin_job_last_status_db_backup`                   | `err`        |                | Outcome of last fire for plugin job 'db_backup': 'ok' or 'err'                                         |
| `plugin_job_last_status_expire_stale_approvals`      | `ok`         |                | Outcome of last fire for plugin job 'expire_stale_approvals': 'ok' or 'err'                            |
| `plugin_job_last_status_flag_missing_seo`            | `ok`         |                | Outcome of last fire for plugin job 'flag_missing_seo': 'ok' or 'err'                                  |
| `plugin_job_last_status_noop`                        | `ok`         |                | Outcome of last fire for plugin job 'noop': 'ok' or 'err'                                              |
| `plugin_job_last_status_postgres_vacuum`             | `ok`         |                | Outcome of last fire for plugin job 'postgres_vacuum': 'ok' or 'err'                                   |
| `plugin_job_last_status_reload_site_config`          | `ok`         |                | Outcome of last fire for plugin job 'reload_site_config': 'ok' or 'err'                                |
| `plugin_job_last_status_render_prometheus_rules`     | `ok`         |                | Outcome of last fire for plugin job 'render_prometheus_rules': 'ok' or 'err'                           |
| `plugin_job_last_status_run_dev_diary_post`          | `ok`         |                | Outcome of last fire for plugin job 'run_dev_diary_post': 'ok' or 'err'                                |
| `plugin_job_last_status_run_niche_topic_sweep`       | `ok`         |                | Outcome of last fire for plugin job 'run_niche_topic_sweep': 'ok' or 'err'                             |
| `plugin_job_last_status_sync_newsletter_subscribers` | `ok`         |                | Outcome of last fire for plugin job 'sync_newsletter_subscribers': 'ok' or 'err'                       |
| `plugin_job_last_status_sync_page_views`             | `ok`         |                | Outcome of last fire for plugin job 'sync_page_views': 'ok' or 'err'                                   |
| `plugin_job_last_status_tune_publish_threshold`      | `ok`         |                | Outcome of last fire for plugin job 'tune_publish_threshold': 'ok' or 'err'                            |
| `plugin_job_last_status_verify_published_posts`      | `ok`         |                | Outcome of last fire for plugin job 'verify_published_posts': 'ok' or 'err'                            |

## podcast

| Key               | Default                                    | Classification | Description                         |
| ----------------- | ------------------------------------------ | -------------- | ----------------------------------- |
| `podcast_rss_url` | `https://pub-1432fdefa18e47ad98f213a8a...` |                | Podcast RSS feed URL (hosted on R2) |

## prometheus

| Key                                              | Default | Classification | Description                                                             |
| ------------------------------------------------ | ------- | -------------- | ----------------------------------------------------------------------- |
| `prometheus.threshold.daily_spend_critical_usd`  | `5.0`   |                | Daily LLM spend critical threshold                                      |
| `prometheus.threshold.daily_spend_warning_usd`   | `4.0`   |                | Daily LLM spend warning threshold                                       |
| `prometheus.threshold.embeddings_stale_seconds`  | `21600` |                | Seconds without an embeddings_total change before EmbeddingsStale fires |
| `prometheus.threshold.monthly_spend_warning_usd` | `15.0`  |                | Monthly LLM spend warning threshold                                     |

## publishing

| Key                                        | Default | Classification | Description                                                                                                              |
| ------------------------------------------ | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `dev_diary_auto_publish_dry_run`           | `true`  |                | When true (default), auto-publish gate runs in observe-only mode: logs 'would have auto-published Y/N' for each final... |
| `dev_diary_auto_publish_max_edit_distance` | `50`    |                | Char-level edit distance threshold for the 'clean run' criterion. Default 50 — trivial typo fixes pass; substantive r... |
| `dev_diary_auto_publish_min_clean_runs`    | `3`     |                | Trailing N publishes that must have edit_distance < auto_publish_max_edit_distance for the gate to fire. Default 3 — ... |
| `dev_diary_auto_publish_threshold`         | `-1`    |                | Quality_score floor for dev_diary auto-publish. Default -1 disables the gate entirely. Set to a value 0-100 (e.g. 85)... |

## qa_workflows

| Key                           | Default                                    | Classification | Description                                          |
| ----------------------------- | ------------------------------------------ | -------------- | ---------------------------------------------------- |
| `qa_workflow_blog_content`    | `{"reviewers": ["programmatic_validato...` |                | Blog content QA workflow chain                       |
| `qa_workflow_premium_content` | `{"reviewers": ["programmatic_validato...` |                | Premium QA with LLM critic - all reviewers must pass |
| `qa_workflow_quick_check`     | `{"reviewers": ["programmatic_validato...` |                | Fast validation for bulk content                     |

## quality

| Key                                    | Default     | Classification | Description                                                                                                              |
| -------------------------------------- | ----------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `content_validator_warning_qa_penalty` | `3`         |                | Points subtracted from final QA score per validator warning (GH-91)                                                      |
| `qa_allow_first_person_niches`         | `dev_diary` |                | Comma-separated list of niche slugs that bypass the first_person_claims validator in quality_scorers.py. Per Matt's v... |
| `qa_critical_dimension_floor`          | `50`        |                | Minimum score on any single quality dimension                                                                            |
| `qa_critic_weight`                     | `0.6`       |                | Weight for LLM critic in final score                                                                                     |
| `qa_final_score_threshold`             | `80`        |                | Multi-model QA final approval score threshold                                                                            |
| `qa_overall_score_threshold`           | `80`        |                | Minimum overall quality score to pass QA (0-100)                                                                         |
| `qa_validator_weight`                  | `0.4`       |                | Weight for programmatic validator in final score                                                                         |

## scheduling

| Key                       | Default      | Classification | Description                                                                                                           |
| ------------------------- | ------------ | -------------- | --------------------------------------------------------------------------------------------------------------------- |
| `dev_diary_last_run_date` | `2026-05-07` |                | YYYY-MM-DD (UTC) of the last successful dev-diary job run. Idempotency marker — the job no-ops if this matches today. |

## secrets

| Key                                | Default         | Classification | Description                                                  |
| ---------------------------------- | --------------- | -------------- | ------------------------------------------------------------ |
| `brain_oauth_client_id`            | `*(encrypted)*` | encrypted      | OAuth client_id for brain-daemon (Phase 2 #241)              |
| `brain_oauth_client_secret`        | `*(encrypted)*` | encrypted      | OAuth client_secret for brain-daemon (Phase 2 #241)          |
| `cli_oauth_client_id`              | `*(encrypted)*` | encrypted      | OAuth client_id for poindexter-cli (Phase 2 #241)            |
| `cli_oauth_client_secret`          | `*(encrypted)*` | encrypted      | OAuth client_secret for poindexter-cli (Phase 2 #241)        |
| `google_oauth_client_secret`       | `*(encrypted)*` | encrypted      | Google OAuth client secret — shared by GSC + GA4 Singer taps |
| `google_oauth_refresh_token`       | `*(encrypted)*` | encrypted      | Google OAuth refresh token — shared by GSC + GA4 Singer taps |
| `grafana_oauth_client_id`          | `*(encrypted)*` | encrypted      | OAuth client_id for grafana-alerts (Phase 2 #241)            |
| `grafana_oauth_client_secret`      | `*(encrypted)*` | encrypted      | OAuth client_secret for grafana-alerts (Phase 2 #241)        |
| `lemon_squeezy_webhook_secret`     | `*(encrypted)*` | encrypted      | Lemon Squeezy webhook HMAC-SHA256 signing secret             |
| `mcp_gladlabs_oauth_client_id`     | `*(encrypted)*` | encrypted      | OAuth client_id for gladlabs-mcp (Phase 2 #241)              |
| `mcp_gladlabs_oauth_client_secret` | `*(encrypted)*` | encrypted      | OAuth client_secret for gladlabs-mcp (Phase 2 #241)          |
| `mcp_oauth_client_id`              | `*(encrypted)*` | encrypted      | OAuth client_id for poindexter-mcp (Phase 2 #241)            |
| `mcp_oauth_client_secret`          | `*(encrypted)*` | encrypted      | OAuth client_secret for poindexter-mcp (Phase 2 #241)        |
| `openclaw_oauth_client_id`         | `*(encrypted)*` | encrypted      | OAuth client_id for openclaw-skills (Phase 2 #241)           |
| `openclaw_oauth_client_secret`     | `*(encrypted)*` | encrypted      | OAuth client_secret for openclaw-skills (Phase 2 #241)       |
| `resend_webhook_secret`            | `*(encrypted)*` | encrypted      | Resend webhook Svix signing secret                           |
| `scripts_oauth_client_id`          | `*(encrypted)*` | encrypted      | OAuth client_id for scripts-shared (Phase 2 #241)            |
| `scripts_oauth_client_secret`      | `*(encrypted)*` | encrypted      | OAuth client_secret for scripts-shared (Phase 2 #241)        |

## security

| Key                          | Default                                | Classification | Description                                                                                                          |
| ---------------------------- | -------------------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------- |
| `alertmanager_webhook_token` | `*(encrypted)*`                        | encrypted      | Bearer token that Alertmanager must send with every webhook POST                                                     |
| `oauth_issuer_url`           | `https://nightrider.taild4f626.ts.net` |                | Public-facing issuer URL advertised in RFC 8414 metadata. Falls back to request.url when empty (e.g. localhost dev). |

## seo

| Key            | Default                                                                        | Classification | Description                                             |
| -------------- | ------------------------------------------------------------------------------ | -------------- | ------------------------------------------------------- |
| `indexnow_key` | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret    | IndexNow API key for instant search engine notification |

## site

| Key                          | Default                                  | Classification | Description                                                                                                              |
| ---------------------------- | ---------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `public_site_revalidate_url` | `https://www.gladlabs.io/api/revalidate` |                | Full URL of the Next.js public site's /api/revalidate endpoint. POSTed by services/revalidation_service.py to bust th... |
| `public_site_url`            | `https://www.gladlabs.io`                |                |                                                                                                                          |

## social

| Key                             | Default                                    | Classification | Description                                                                                                              |
| ------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `bluesky_app_password`          | `*(encrypted)*`                            | encrypted      | Bluesky app password — bsky.app -> Settings -> App Passwords                                                             |
| `bluesky_identifier`            | `*(encrypted)*`                            | encrypted      | Bluesky handle (custom domain) for the Glad Labs account                                                                 |
| `mastodon_access_token`         | `*(encrypted)*`                            | encrypted      | GH-36: Mastodon access token with 'write:statuses' scope. Create via Preferences > Development > New Application on y... |
| `mastodon_instance_url`         | ``                                         |                | GH-36: Full Mastodon instance URL, e.g. 'https://mastodon.social'. Empty = Mastodon distribution skipped.                |
| `social_distribution_platforms` | `bluesky`                                  |                | GH-36: Comma-separated list of platforms social_poster should push to after a successful publish. Valid values: 'blue... |
| `social_linkedin_url`           | `https://www.linkedin.com/in/matthew-g...` |                | LinkedIn profile URL                                                                                                     |
| `social_x_handle`               | `@_gladlabs`                               |                | X/Twitter handle                                                                                                         |
| `social_x_url`                  | `https://x.com/_gladlabs`                  |                | X/Twitter profile URL                                                                                                    |

## system

| Key                  | Default                                    | Classification | Description                                                      |
| -------------------- | ------------------------------------------ | -------------- | ---------------------------------------------------------------- |
| `local_database_url` | `postgresql://poindexter:poindexter-br...` |                | Local brain DB connection string (Docker internal)               |
| `repo_root`          | `/app`                                     |                | Root path of the codebase (for running scripts inside container) |

## tokens

| Key                            | Default | Classification | Description                                             |
| ------------------------------ | ------- | -------------- | ------------------------------------------------------- |
| `content_temperature`          | `0.7`   |                | Temperature for content generation                      |
| `max_tokens_default`           | `800`   |                | Default max tokens for general tasks                    |
| `qa_standard_max_tokens`       | `1500`  |                | Max tokens for standard models in QA                    |
| `qa_temperature`               | `0.3`   |                | Temperature for QA review generation                    |
| `qa_thinking_model_max_tokens` | `8000`  |                | Max tokens for thinking models (qwen3.5, glm-4.7) in QA |

## topic_discovery

| Key                            | Default | Classification | Description                                                                                                              |
| ------------------------------ | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `topic_discovery_auto_enabled` | `false` |                | Master kill-switch for the LEGACY auto-firing topic discovery loop in services/idle_worker.py. When 'true' (default, ... |

## voice

| Key                                | Default                                    | Classification | Description                                                                                                              |
| ---------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `voice_agent_public_join_url`      | `https://nightrider.taild4f626.ts.net/...` |                | Public URL the operator (or Claude, via the start_voice_call MCP tool) taps to join the always-on LiveKit voice room.... |
| `voice_bridge_chunk_max_chars`     | `500`                                      |                | Maximum characters per TTS chunk emitted by voice_speak. Long replies are split at sentence boundaries so the operato... |
| `voice_bridge_enabled`             | `true`                                     |                | Master switch for the LiveKit MCP bridge — the architecturally-correct alternative to the subprocess-spawn voice_agen... |
| `voice_bridge_max_session_seconds` | `1800`                                     |                | Hard upper bound on a single bridge session, in seconds. The worker auto-leaves the LiveKit room after this many seco... |
| `voice_bridge_stt_model`           | `base.en`                                  |                | faster-whisper model id loaded by the future Pipecat audio plane in the bridge worker. Defaults to base.en (CPU-frien... |
| `voice_bridge_tts_voice`           | `af_bella`                                 |                | Kokoro voice id used by the bridge worker's TTS path. Matches the always-on voice-agent-livekit container default so ... |
| `voice_default_room`               | `poindexter`                               |                | Default LiveKit room name when voice_join_room is called without an explicit channel_id. Distinct from voice_agent_ro... |

## voice_agent

| Key                         | Default                                    | Classification | Description                                                                                                             |
| --------------------------- | ------------------------------------------ | -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `voice_agent_system_prompt` | `You are Emma, a concise voice assista...` |                | System prompt for the voice agent. Mentions tools so glm-4.7-5090 actually invokes them.                                |
| `voice_agent_whisper_model` | `medium`                                   |                | faster-whisper model size. tiny/base/small/medium/large-v3. medium is the sweet spot for real voice accuracy on a 5090. |

## webhooks

| Key                      | Default         | Classification | Description                   |
| ------------------------ | --------------- | -------------- | ----------------------------- |
| `openclaw_webhook_token` | `*(encrypted)*` | encrypted      | OpenClaw webhook auth token   |
| `openclaw_webhook_url`   | ``              |                | OpenClaw webhook delivery URL |

## writer_rag

| Key                                        | Default | Classification | Description                                                                                                              |
| ------------------------------------------ | ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `writer_rag_citation_budget_min_citations` | `3`     |                | Minimum number of internal sources the CITATION_BUDGET writer must cite by [source/ref] tag. Default 3 matches the pr... |
| `writer_rag_citation_budget_snippet_limit` | `12`    |                | Top-N pgvector snippets fetched in the CITATION_BUDGET writer mode. Default 12 matches the prior hardcoded LIMIT 12 i... |
| `writer_rag_context_snippet_max_chars`     | `500`   |                | Per-snippet character cap when building the snippet block for generate_with_context and generate_with_outline (the tw... |
| `writer_rag_research_topic_max_sources`    | `2`     |                | Default max_sources for the module-level research_topic() shim used by the TWO_PASS writer mode. Default 2 matches th... |
| `writer_rag_story_spine_snippet_limit`     | `15`    |                | Top-N pgvector snippets fed into the STORY_SPINE outline preprocessing pass. Default 15 matches the prior hardcoded L... |
| `writer_rag_story_spine_snippet_max_chars` | `600`   |                | Per-snippet character cap when assembling the snippet block for the STORY_SPINE outline prompt. Default 600 matches t... |
| `writer_rag_topic_only_snippet_limit`      | `8`     |                | Top-N pgvector snippets fetched and dumped into the TOPIC_ONLY writer prompt as background context. Default 8 matches... |
| `writer_rag_two_pass_max_revision_loops`   | `3`     |                | Hard cap on revise → detect_needs → research_each → revise loops in the TWO_PASS LangGraph state machine. Default 3 m... |
| `writer_rag_two_pass_research_max_sources` | `2`     |                | max_sources passed to research_topic for each [EXTERNAL_NEEDED: ...] marker the TWO_PASS draft surfaces. Default 2 ma... |
| `writer_rag_two_pass_snippet_limit`        | `20`    |                | Top-N pgvector snippets fetched up-front in the TWO_PASS internal-first draft. Default 20 matches the prior hardcoded... |
