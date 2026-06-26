# App settings reference

> **Auto-generated from live `app_settings` table on 2026-06-25.**  
> Every runtime-configurable knob in the Poindexter pipeline.
> 736 active rows across 60 categories. 2 stored encrypted via pgcrypto (`is_secret=true`); 1 additional values redacted as secret-shaped (defense-in-depth); 10 values redacted as operator-specific (Tailnet IPs, financial reality, etc.) so this file is safe to ship to the public OSS mirror.

> Generated values are example/per-operator. Set yours via `poindexter settings set <key> <value>` (add `--secret` to store the value encrypted with `is_secret=true`).

> **To regenerate:** `python scripts/regen-app-settings-doc.py`

To change any value:

```sql
-- Read
SELECT key, value, updated_at FROM app_settings WHERE key = 'content_quality_minimum';

-- Write (non-secret)
UPDATE app_settings SET value = '78', updated_at = NOW() WHERE key = 'content_quality_minimum';

-- Write (secret) — raw SQL can't encrypt; use the CLI instead:
--   poindexter settings set <key> <value> --secret
```

The worker re-reads on every poll; no restart needed.

---

## Table of contents

- [alerts](#alerts) (5 keys)
- [api_keys](#api-keys) (1 key)
- [backup](#backup) (31 keys)
- [brain](#brain) (13 keys)
- [brain-probes](#brain-probes) (7 keys)
- [cli](#cli) (5 keys)
- [cloudflare](#cloudflare) (2 keys)
- [content](#content) (19 keys)
- [content_qa](#content-qa) (4 keys)
- [cors](#cors) (1 key)
- [cost](#cost) (8 keys)
- [experiments](#experiments) (4 keys)
- [features](#features) (7 keys)
- [finance](#finance) (4 keys)
- [firefighter](#firefighter) (7 keys)
- [gates](#gates) (3 keys)
- [general](#general) (321 keys)
- [gpu](#gpu) (1 key)
- [identity](#identity) (16 keys)
- [image](#image) (5 keys)
- [infrastructure](#infrastructure) (1 key)
- [integration](#integration) (2 keys)
- [integrations](#integrations) (9 keys)
- [llm_routing](#llm-routing) (3 keys)
- [logging](#logging) (2 keys)
- [media](#media) (9 keys)
- [memory_alerts](#memory-alerts) (3 keys)
- [memory_compression](#memory-compression) (3 keys)
- [model_roles](#model-roles) (4 keys)
- [models](#models) (4 keys)
- [monitoring](#monitoring) (45 keys)
- [newsletter](#newsletter) (3 keys)
- [niche_pivot](#niche-pivot) (8 keys)
- [notifications](#notifications) (3 keys)
- [observability](#observability) (14 keys)
- [ops-triage](#ops-triage) (1 key)
- [orchestration](#orchestration) (1 key)
- [performance](#performance) (4 keys)
- [pipeline](#pipeline) (34 keys)
- [plugins](#plugins) (39 keys)
- [plugin_telemetry](#plugin-telemetry) (1 key)
- [podcast](#podcast) (2 keys)
- [prometheus](#prometheus) (5 keys)
- [publishing](#publishing) (5 keys)
- [qa](#qa) (8 keys)
- [qa_workflows](#qa-workflows) (3 keys)
- [quality](#quality) (7 keys)
- [rag](#rag) (1 key)
- [scheduling](#scheduling) (1 key)
- [security](#security) (1 key)
- [site](#site) (2 keys)
- [skills](#skills) (1 key)
- [social](#social) (5 keys)
- [system](#system) (2 keys)
- [tokens](#tokens) (5 keys)
- [topic_discovery](#topic-discovery) (1 key)
- [voice](#voice) (22 keys)
- [voice_agent](#voice-agent) (2 keys)
- [webhooks](#webhooks) (1 key)
- [writer_rag](#writer-rag) (5 keys)

## alerts

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `alert_force_telegram_event_types` | `` |  | Comma-separated list of event_type (or alertname) values that always route to Telegram regardless of severity. Use fo... |
| `alert_repeat_summarize_threshold_minutes` | `30` |  | After a fingerprint has been firing continuously for this many minutes (now - first_seen_at), the dispatcher escalate... |
| `alert_repeat_suppress_window_minutes` | `120` |  | When a brain alert with the same fingerprint (source\|severity\|normalized_message) last fired inside this window, th... |
| `task_failure_alert_dedup_window_seconds` | `900` |  | Suppress duplicate task-failure alerts for the same (task_id, error_message_hash) within this window. Default 900 (15... |
| `task_failure_alert_severity` | `discord` |  | Channel routine task-failure alerts route to: 'discord' (default, the spam channel) or 'telegram' (escalates to opera... |

## api_keys

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `sentry_dsn` | `http://31fbc77a-4ad1-4b9a-8bf9-a13548...` |  | Sentry DSN for error tracking |

## backup

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `backup_daily_enabled` | `true` |  | When false, the backup-daily container takes no dumps. |
| `backup_daily_interval` | `24h` |  | Cadence between daily dumps. |
| `backup_daily_retention` | `7` |  | Number of daily dumps to keep. |
| `backup_hourly_enabled` | `true` |  | When false, the backup-hourly container takes no dumps. Loop keeps running so toggling back on is instant. |
| `backup_hourly_interval` | `1h` |  | Cadence between hourly dumps. Format: <N>{s\|m\|h\|d}. Read fresh each tick — no restart needed. |
| `backup_hourly_retention` | `24` |  | Number of hourly dumps to keep. Older dumps are pruned after each successful run. |
| `backup_watcher_backup_dir` | `/host-backups/auto` |  | Host path the backup containers bind-mount their dumps into. Override when POINDEXTER_BACKUP_DIR points somewhere non... |
| `backup_watcher_daily_max_age_hours` | `26` |  | Daily tier staleness threshold (mirrors the compose healthcheck slack of 90 min beyond the 24 h cadence). |
| `backup_watcher_enabled` | `true` |  | Master switch for the brain backup-watcher probe (#388). When false, the probe short-circuits without stat-ing dumps ... |
| `backup_watcher_hourly_max_age_minutes` | `90` |  | Hourly tier staleness threshold. Matches the compose healthcheck so the watcher fires at the same instant the contain... |
| `backup_watcher_max_retries` | `2` |  | Consecutive `docker restart` attempts before the watcher gives up and lets the dispatcher page the operator. Cumulati... |
| `backup_watcher_poll_interval_minutes` | `5` |  | Cadence at which the watcher re-checks backup freshness. Matches the brain cycle by default; bump higher only if the ... |
| `backup_watcher_retry_delay_seconds` | `120` |  | How long the watcher waits after `docker restart` before re-stat-ing the dump directory. Long enough for postgres rec... |
| `backup_watcher_sentinel_dir` | `/host-backup-logs` |  | Container path the brain bind-mounts ~/.poindexter/logs into (read-only). brain/backup_watcher.py scans this director... |
| `offsite_backup_enabled` | `true` |  | Master switch for the off-machine (Tier 2) restic loop. The backup-offsite container stays running but idles when fal... |
| `offsite_backup_interval` | `24h` |  | Cadence between offsite restic backups. Format <N>{s\|m\|h\|d}. Read fresh each tick. |
| `offsite_backup_keep_daily` | `7` |  | restic forget --keep-daily (only consulted when offsite_backup_prune_enabled=true). |
| `offsite_backup_keep_monthly` | `6` |  | restic forget --keep-monthly (only consulted when offsite_backup_prune_enabled=true). |
| `offsite_backup_keep_weekly` | `4` |  | restic forget --keep-weekly (only consulted when offsite_backup_prune_enabled=true). |
| `offsite_backup_max_age_hours` | `26` |  | Staleness threshold for the brain offsite_backup_watch probe (daily cadence + 2h slack). |
| `offsite_backup_prune_enabled` | `false` |  | When false (default) the runner never forget/prunes — keeps an append-only S3 key safe. Enable ONLY with a privileged... |
| `offsite_backup_repository` | `` |  | restic repository URL, e.g. s3:https://s3.us-west-002.backblazeb2.com/<bucket>/<path>. Empty = Tier 2 inert. Set by `... |
| `offsite_backup_restic_image` | `restic/restic:0.16.4` |  | Pinned restic image the wizard + brain verify use via `docker run`. Matches the alpine restic baked into the backup i... |
| `offsite_backup_s3_region` | `` |  | SigV4 signing region for the S3 endpoint (e.g. B2 us-east-005). restic signs with us-east-1 by default, which a non-u... |
| `offsite_backup_source_tier` | `daily` |  | Which Tier 1 dump subdir under /backups to ship off-machine. |
| `offsite_backup_verify_enabled` | `true` |  | Master switch for the weekly remote `restic check`. |
| `offsite_backup_verify_interval_hours` | `168` |  | How often the runner runs `restic check` against the remote repo (weekly). |
| `offsite_backup_verify_read_data_subset_percent` | `5` |  | Percentage of pack data `restic check --read-data-subset` re-reads to catch bit-rot. |
| `offsite_backup_watch_enabled` | `true` |  | Master switch for the brain auto-retry watch on the offsite tier. |
| `offsite_backup_watch_max_retries` | `2` |  | docker restart attempts before the watch escalates and emits offsite_backup_stale. |
| `offsite_backup_watch_retry_delay_seconds` | `120` |  | Wait between docker restart and re-checking the heartbeat. |

## brain

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `discord_bot_probe_dedup_hours` | `1` |  | Minimum hours between repeat alert_events writes while the Discord bot returns 401/403. Default 1h — one page per hou... |
| `discord_bot_probe_enabled` | `true` |  | Master switch for brain/discord_bot_probe.py (poindexter#435). When false, the probe is skipped entirely. |
| `discord_bot_probe_interval_minutes` | `5` |  | Minutes between real Discord /users/@me round-trips. Probe is dispatched every brain cycle but skips inside the inter... |
| `discord_bot_probe_timeout_seconds` | `5` |  | httpx timeout for the Discord /users/@me round-trip. |
| `mcp_http_probe_base_url` | `http://host.docker.internal:8004` |  | Base URL of the Poindexter MCP HTTP server. Probe appends the discovery path. Default http://127.0.0.1:8004. |
| `mcp_http_probe_dedup_hours` | `1` |  | Minimum hours between repeat alert_events writes while the MCP server stays unreachable. Default 1h. |
| `mcp_http_probe_discovery_path` | `/healthz` |  | Discovery endpoint path the probe GETs. Returns 200 when the MCP server is alive. |
| `mcp_http_probe_enabled` | `true` |  | Master switch for brain/mcp_http_probe.py (poindexter#434). |
| `mcp_http_probe_interval_minutes` | `5` |  | Minutes between real probe round-trips. Default 5. |
| `mcp_http_probe_launcher_path` | `` |  | Absolute path to a launcher script (.cmd on Windows, .sh on POSIX) that restarts the MCP HTTP server. Empty (default)... |
| `mcp_http_probe_restart_cap_per_window` | `3` |  | Max launcher invocations within the rolling restart window. Prevents busy-loop when the underlying problem is persist... |
| `mcp_http_probe_restart_window_minutes` | `60` |  | Rolling-window minutes for the restart cap above. Default 60. |
| `mcp_http_probe_timeout_seconds` | `3` |  | httpx timeout for the localhost probe. Default 3. |

## brain-probes

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `prefect_api_base_url` | `http://prefect-server:4200/api` |  | Where the brain probes hit the Prefect REST API. Defaults to the in-stack compose hostname; docker_utils.localize_url... |
| `prefect_stuck_flow_auto_crash` | `true` |  | When true, the probe force-CRASHED stuck flow runs via Prefect's /set_state API so subsequent scheduled dispatches re... |
| `prefect_stuck_flow_flow_names` | `content_generation` |  | Comma-separated list of Prefect flow names the stuck-flow probe should watch. Add additional flow names if you spawn ... |
| `prefect_stuck_flow_pending_threshold_minutes` | `5` |  | A flow run that has been PENDING/Submitting longer than this is considered stranded. Captured 2026-05-25: a PENDING r... |
| `prefect_stuck_flow_probe_enabled` | `true` |  | Master kill switch for brain/prefect_stuck_flow_probe. Set to false to disable detection of stuck Prefect flow runs (... |
| `prefect_stuck_flow_queue_depth_threshold` | `3` |  | Brain prefect_stuck_flow_probe: page with a distinct probe.prefect_queue_backlog_detected signal when MORE than this ... |
| `prefect_stuck_flow_threshold_minutes` | `30` |  | A content_generation flow run RUNNING longer than this is considered stuck. Default 30m is ~5-6x the typical 5-min du... |

## cli

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `cli_post_approve_bulk_max_count` | `100` |  | Hard ceiling for matched-post count in a single 'poindexter post approve --no-dry-run --filter ...' invocation. Refus... |
| `cli_post_approve_bulk_require_confirm` | `true` |  | When true, 'poindexter post approve --filter ... --no-dry-run' always prompts y/N before approving — even if --yes wa... |
| `cli_post_create_idempotency_enabled` | `true` |  | Master switch for `poindexter post create` idempotency (#338). When 'true', a second invocation with the same compute... |
| `cli_post_create_idempotency_strategy` | `slug_or_content_hash` |  | Reserved for future variants of the `poindexter post create` (#338) idempotency-key derivation. Today only 'slug_or_c... |
| `cli_post_create_idempotency_window_minutes` | `30` |  | Dedup window for `poindexter post create` (#338). A second invocation with the same idempotency key WITHIN this many ... |

## cloudflare

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `cloudflare_analytics_api_token` | `*(encrypted)*` | encrypted | Cloudflare API token scoped to Account Analytics Read. Consumed by the sync_cloudflare_analytics job. Operator fills ... |
| `cloudflare_analytics_last_sync` | `1970-01-01T00:00:00Z` |  | High-water mark (ISO-8601 UTC) for the sync_cloudflare_analytics job. Advanced atomically after each successful batch... |

## content

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `alt_text_budget` | `120` |  | Character budget for inline <img alt="..."> text. The alt generator produces complete sentences within this budget; o... |
| `auto_append_sources_section` | `true` |  | Auto-append ## Sources at finalize if missing |
| `code_density_check_enabled` | `true` |  | GH-234: enable the code-block density quality gate. When true, tech-tagged posts that ship without enough fenced code... |
| `code_density_long_post_floor_words` | `300` |  | GH-234: prose word-count threshold above which the line-ratio sub-check kicks in. Per the issue spec, short posts (<3... |
| `code_density_min_blocks_per_700w` | `1` |  | GH-234: minimum fenced code blocks expected per 700 prose words in a tech post. The check skips posts under 200 prose... |
| `code_density_min_line_ratio_pct` | `20` |  | GH-234: minimum percentage of non-empty content lines that must live inside a fenced code block, applied only to post... |
| `code_density_tag_filter` | `technical,ai,programming,ml,python,ja...` |  | GH-234: comma-separated list of tag/topic tokens that qualify a post as 'tech' for the code-block density rule. Match... |
| `content_max_refinement_attempts` | `3` |  | Max attempts to refine content quality |
| `content_min_word_count` | `800` |  | Minimum word count for blog posts |
| `content_target_word_count` | `1500` |  | Target word count for blog posts |
| `default_ollama_model` | `auto` |  | Default Ollama model for LLM calls. "auto" → OllamaClient picks the first available pulled model. Override with a spe... |
| `local_llm_api_url` | `http://host.docker.internal:11434` |  | Ollama API base URL for local LLM calls (e.g. http://localhost:11434). Empty value means 'Ollama not configured' — ca... |
| `title_originality_cache_ttl_hours` | `24` |  | GH-87: TTL (hours) for the in-process cache that dedupes repeated DuckDuckGo queries for the same title. DDG rate-lim... |
| `title_originality_external_check_enabled` | `true` |  | GH-87: enable DuckDuckGo HTML search for the exact post title at approval time. Verbatim external matches subtract ti... |
| `title_originality_external_penalty` | `-50` |  | GH-87: points subtracted from the QA score when the post title appears verbatim in external search results. Stored as... |
| `topic_discovery_category_searches` | `{}` |  | JSON object mapping category name -> list of keyword search strings. Used by TopicDiscovery._classify_category to buc... |
| `topic_discovery_news_patterns` | `[]` |  | JSON array of regex strings (case-insensitive). When non-empty, TopicDiscovery uses these patterns to reject titles a... |
| `writing_style_reference` | `*(per-operator)*` | per-operator | Operator-specific writing style traits injected into content-generation prompts. Set via `poindexter settings set wri... |
| `writing_styles` | `[{"name": "technical", "voice": "prec...` |  | Configurable writing styles for content generation. Same pattern as image_styles. |

## content_qa

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `qa_citation_max_dead_ratio` | `0.30` |  | Max proportion of dead citations before verifier rejects |
| `qa_citation_min_count` | `0` |  | Minimum external citations required per post. 0 = disabled |
| `qa_citation_timeout_seconds` | `8.0` |  | Per-URL HEAD timeout seconds |
| `qa_citation_verify_enabled` | `true` |  | HTTP HEAD every external URL in post content; surface dead links as QA reviewer |

## cors

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `allowed_origins` | `` |  | Comma-separated allowed CORS origins |

## cost

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `cost_alert_threshold_pct` | `80` |  | Alert when spend exceeds this % of limit |
| `daily_spend_limit` | `1.00` |  | Hard cap on daily AI spend in USD |
| `daily_spend_limit_usd` | `2.0` |  | Maximum daily AI spend in USD (read by services/cost_guard.py) |
| `electricity_rate_kwh` | `0.2579` |  | RI Energy Last Resort Service rate $0.14770/kWh (verified by Matt) |
| `gpu_inference_watts` | `400` |  | GPU average inference power draw in watts |
| `monthly_spend_limit` | `20.00` |  | Hard cap on monthly AI spend in USD |
| `monthly_spend_limit_usd` | `10.0` |  | Maximum monthly AI spend in USD (read by services/cost_guard.py) |
| `ollama_electricity_cost_per_1k_tokens` | `0.000256` |  | Ollama electricity cost per 1K tokens (USD) |

## experiments

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `active_pipeline_experiment_key` | `` |  | Experiment key the content pipeline routes through (matches experiments.key in the experiments table). Empty = disabl... |
| `experiment_weighted_selection_enabled` | `false` |  | When true, experiment_runner.pick_variant allocates proportional to experiment_variants.weight (the column the #361 f... |
| `premium_active` | `false` |  | When 'true', UnifiedPromptManager loads prompt_templates rows where source='premium' on top of source='default'. When... |
| `router_feedback_alpha` | `0.2` |  | EWMA damping for the outcome→experiment-variant-weight feedback loop (#361). new_weight = (1 - alpha) * old + alpha *... |

## features

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `enable_mcp_server` | `true` |  | Enable Model Context Protocol server |
| `enable_memory_system` | `true` |  | Enable agent memory system |
| `enable_training_capture` | `true` |  | Enable training data capture from pipeline runs |
| `redis_enabled` | `false` |  | Enable Redis for caching and pub/sub |
| `topic_auto_resolve_enabled` | `true` |  | Master switch for topic_auto_resolve job (poindexter#504 follow-up). When true, the job auto-resolves open topic_batc... |
| `topic_auto_resolve_max_per_cycle` | `1` |  | Max number of topic_batches the auto-resolver promotes per cycle (every 2h). Default 1 — one new pipeline task every ... |
| `topic_auto_resolve_niche_cooldown_hours` | `12` |  | Minimum hours between auto-resolutions for the same niche. Prevents one niche from monopolizing the pipeline if its b... |

## finance

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `finance_poll_interval_seconds` | `3600` |  | Expected Mercury poll cadence in seconds (matches PollMercuryJob hourly schedule). Staleness window = this × finance_... |
| `finance_poll_stale_multiplier` | `3` |  | How many missed poll intervals to tolerate before the finance brain probe pages. Window = finance_poll_interval_secon... |
| `prometheus.rule.FinanceMercuryPollStale` | `{"enabled": true, "group": "poindexte...` |  | DB-sourced Prometheus alert rule for a stalled Mercury poll (Glad-Labs/poindexter#565). Rendered into rules/*.yml by ... |
| `prometheus.threshold.finance_poll_stale_seconds` | `10800` |  | Prometheus staleness threshold (seconds) for the FinanceMercuryPollStale alert. Default 10800 = 3h. Referenced as {th... |

## firefighter

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `ops_triage_cache_ttl_seconds` | `3600` |  | TTL (seconds) for the POST /api/triage process-local idempotency cache (#347 step 3). Repeat calls for the same alert... |
| `ops_triage_enabled` | `true` |  | Master kill-switch for the firefighter ops LLM (#347). When false, alert_dispatcher skips the parallel triage task an... |
| `ops_triage_max_context_tokens` | `4000` |  | Cap on the pre-fetched context size handed to the LLM. When the assembled context (alert + history + audit_log + pipe... |
| `ops_triage_max_diagnosis_tokens` | `400` |  | Cap on the diagnosis output length (Telegram-friendly). The service truncates with a '[...]' marker if the LLM exceed... |
| `ops_triage_model_class` | `ops_triage` |  | model_router tier the firefighter_service uses for triage (#347). Defaults to a dedicated 'ops_triage' class which ma... |
| `ops_triage_retry_backoff_seconds` | `[10, 30, 90]` |  | JSON list of per-attempt sleep durations (seconds) the brain uses between retries when worker /api/triage is unreacha... |
| `ops_triage_retry_max` | `3` |  | Maximum retry attempts when the brain can't reach the worker /api/triage endpoint. Retries are scheduled with the bac... |

## gates

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `gate_auto_expire_batch_size` | `50` |  | Cap per-cycle expiry to this many gates to avoid huge batches. Excess rolls over to the next cycle. |
| `gate_auto_expire_enabled` | `true` |  | Master switch for the brain gate auto-expire probe (#338). When false, the probe short-circuits without scanning gates. |
| `gate_auto_expire_notify_threshold` | `1` |  | Only ping the operator (Telegram coalesced) when batch size >= this. Default 1 = always notify on any expiry. |

## general

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `alertmanager_url` | `http://alertmanager:9093` |  |  |
| `api_url` | `http://localhost:8002` |  | Backend API base URL (legacy alias for api_base_url) |
| `approval_gate_topic_decision_reject_status` | `dismissed` |  | Status set on pipeline_tasks when a topic-decision gate rejects the topic (vs. the global default 'rejected'). Distin... |
| `app_version` | `3.0.1` |  | Auto-seeded by services.settings_defaults (#379) |
| `atom_runs_capture_enabled` | `true` |  |  |
| `audio_gen_engine` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `brain_anomaly_baseline_window_days` | `30` |  | Auto-seeded by services.settings_defaults (#379) |
| `brain_anomaly_current_window_hours` | `24` |  | Auto-seeded by services.settings_defaults (#379) |
| `brand_keywords` | `` |  | Comma-separated brand-relevance keywords used by topic_discovery to filter discovered topics to the site's niche. Emp... |
| `cadence_slo_enabled` | `true` |  |  |
| `cadence_slo_expected_posts_per_day` | `1` |  |  |
| `cadence_slo_shortfall_ratio` | `0.5` |  |  |
| `cadence_slo_window_hours` | `24` |  |  |
| `cloudflare_account_id` | `` |  |  |
| `compose_drift_auto_recover_enabled` | `true` |  |  |
| `content_flow_max_concurrency` | `3` |  |  |
| `content_router_contradiction_review_max_tokens` | `1500` |  | Auto-seeded by services.settings_defaults (#379) |
| `content_router_contradiction_revise_max_tokens` | `8000` |  | Auto-seeded by services.settings_defaults (#379) |
| `content_router_contradiction_timeout_seconds` | `120` |  | Auto-seeded by services.settings_defaults (#379) |
| `content_router_qa_rewrite_max_tokens` | `8000` |  | Auto-seeded by services.settings_defaults (#379) |
| `content_router_qa_rewrite_timeout_seconds` | `600` |  | Auto-seeded by services.settings_defaults (#379) |
| `content_router_seo_title_max_tokens` | `4000` |  | Auto-seeded by services.settings_defaults (#379) |
| `content_validator_warning_reject_threshold` | `5` |  |  |
| `database_pool_max_size` | `20` |  | Max DB pool connections |
| `database_pool_min_size` | `5` |  | Min DB pool connections |
| `deepeval_enabled` | `true` |  |  |
| `default_media_to_generate` | `` |  | Comma-separated list of media to generate alongside each new post when --media isn't passed. Empty = blog post only. ... |
| `default_workflow_gates` | `topic,draft,final` |  | Comma-separated gate sequence applied to new posts when --gates isn't passed. Empty string = fully autonomous (no hum... |
| `development_mode` | `false` |  | Enable development mode |
| `devto_api_base` | `https://dev.to/api` |  | Auto-seeded by services.settings_defaults (#379) |
| `devto_min_reactions` | `20` |  |  |
| `devto_per_page` | `15` |  |  |
| `devto_tag` | `` |  |  |
| `devto_top_days` | `7` |  |  |
| `disable_auth_for_dev` | `true` |  | Disable auth in development |
| `docker_port_forward_watch_list` | `[{"container": "poindexter-pyroscope"...` |  |  |
| `embedding_retention_days.memory` | `` |  | Empty = no TTL. Memory embeddings are never auto-pruned — operator's curated state. |
| `embedding_retention_days.posts` | `` |  | Empty = no TTL. Post embeddings are never auto-pruned — feed live RAG retrieval. |
| `embed_model` | `nomic-embed-text` |  | Auto-seeded by services.settings_defaults (#379) |
| `enabled_topic_sources` | `knowledge,codebase,hackernews,devto,w...` |  |  |
| `enable_image_gen_warmup` | `true` |  | Warm up image-gen models on startup |
| `enable_writer_self_review` | `true` |  | Auto-seeded by services.settings_defaults (#379) |
| `environment` | `development` |  | Auto-seeded by services.settings_defaults (#379) |
| `findings.anomaly.cooldown_minutes` | `60` |  |  |
| `findings.anomaly.delivery` | `telegram` |  |  |
| `findings.anomaly.fallback` | `discord` |  |  |
| `findings.anomaly.min_severity` | `critical` |  |  |
| `findings.broken_external_link_autofixed.delivery` | `log_only` |  |  |
| `findings.broken_external_link.cooldown_minutes` | `60` |  |  |
| `findings.broken_external_link.delivery` | `auto_fix` |  |  |
| `findings.broken_external_link.fallback` | `discord` |  |  |
| `findings.broken_external_link.min_severity` | `warn` |  |  |
| `findings.broken_internal_link_autofixed.delivery` | `log_only` |  |  |
| `findings.broken_internal_link.cooldown_minutes` | `60` |  |  |
| `findings.broken_internal_link.delivery` | `auto_fix` |  |  |
| `findings.broken_internal_link.fallback` | `discord` |  |  |
| `findings.broken_internal_link.min_severity` | `warn` |  |  |
| `findings.broken_link.cooldown_minutes` | `360` |  |  |
| `findings.broken_link.delivery` | `discord` |  |  |
| `findings.broken_link.fallback` | `log_only` |  |  |
| `findings.broken_link.min_severity` | `warn` |  |  |
| `findings.cloud_sync_returned_false.cooldown_minutes` | `360` |  |  |
| `findings.cloud_sync_returned_false.delivery` | `discord` |  |  |
| `findings.cloud_sync_returned_false.fallback` | `log_only` |  |  |
| `findings.cloud_sync_returned_false.min_severity` | `warn` |  |  |
| `findings.default.cooldown_minutes` | `1440` |  |  |
| `findings.default.delivery` | `log_only` |  |  |
| `findings.default.fallback` | `log_only` |  |  |
| `findings.default.min_severity` | `warn` |  |  |
| `findings.duplicate_post.delivery` | `log_only` |  |  |
| `findings.media_drift.delivery` | `log_only` |  |  |
| `findings.missing_seo.cooldown_minutes` | `1440` |  |  |
| `findings.missing_seo.delivery` | `auto_fix` |  |  |
| `findings.missing_seo.fallback` | `github_issue` |  |  |
| `findings.missing_seo.labels` | `bug,pipeline` |  | Auto-seeded by services.settings_defaults (#379) |
| `findings.missing_seo.min_severity` | `warn` |  |  |
| `findings.post_verification_failure.cooldown_minutes` | `360` |  |  |
| `findings.post_verification_failure.delivery` | `discord` |  |  |
| `findings.post_verification_failure.fallback` | `log_only` |  |  |
| `findings.post_verification_failure.min_severity` | `warn` |  |  |
| `findings.quality_regression.cooldown_minutes` | `1440` |  |  |
| `findings.quality_regression.delivery` | `github_issue` |  |  |
| `findings.quality_regression.fallback` | `discord` |  |  |
| `findings.quality_regression.labels` | `bug,pipeline` |  | Auto-seeded by services.settings_defaults (#379) |
| `findings.quality_regression.min_severity` | `warn` |  |  |
| `findings.r2_static_drift.cooldown_minutes` | `360` |  |  |
| `findings.r2_static_drift.delivery` | `discord` |  |  |
| `findings.r2_static_drift.fallback` | `log_only` |  |  |
| `findings.r2_static_drift.min_severity` | `warn` |  |  |
| `findings.stock_image_regenerated.delivery` | `log_only` |  |  |
| `findings.topic_gap.cooldown_minutes` | `1440` |  |  |
| `findings.topic_gap.delivery` | `discord` |  |  |
| `findings.topic_gap.fallback` | `log_only` |  |  |
| `findings.uncategorized_post_autofixed.delivery` | `log_only` |  |  |
| `flux_schnell_server_url` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `glitchtip_api_token` | `` |  |  |
| `glitchtip_triage_alert_freshness_hours` | `24` |  |  |
| `glitchtip_triage_default_resolve_max_count` | `50` |  |  |
| `google_sitemap_ping_url` | `https://www.google.com/ping` |  | Auto-seeded by services.settings_defaults (#379) |
| `gpu_busy_threshold_percent` | `30` |  | GPU utilization % above which gaming is detected |
| `gpu_gaming_check_interval` | `15` |  | Seconds between gaming detection checks |
| `gpu_gaming_clear_checks` | `3` |  | Consecutive low-util checks to resume pipeline |
| `gpu_gaming_confirm_checks` | `2` |  | Consecutive high-util checks to confirm gaming |
| `gpu_metrics_staleness_threshold_minutes` | `15` |  |  |
| `gpu_name` | `` |  | GPU model name (auto-detected by detect-hardware.py) |
| `gpu_vram_gb` | `0` |  | GPU VRAM in GB (auto-detected by detect-hardware.py) |
| `grafana_alert_sync_interval_cycles` | `3` |  |  |
| `grafana_user` | `admin` |  | Grafana admin username |
| `guardrails_enabled` | `true` |  |  |
| `hardware_cost_total` | `*(per-operator)*` | per-operator | Total PC build cost for depreciation calculation |
| `hn_min_score` | `50` |  |  |
| `hn_top_stories` | `20` |  |  |
| `host_home` | `` |  | Host home directory for Docker volume mounts |
| `image_model` | `sdxl_lightning` |  | Default image generation model (legacy) |
| `indexnow_ping_url` | `https://api.indexnow.org/indexnow` |  | Auto-seeded by services.settings_defaults (#379) |
| `internal_api_base_url` | `http://localhost:8002` |  | Base URL for the internal worker API (used for self-calls like the podcast feed regen) |
| `media_approval_discord_notify_enabled` | `true` |  | Master switch — when true, a Discord ops ping fires when a newly-generated podcast/video/short lands in media_approva... |
| `media_upload_delay_seconds` | `240` |  | Wait this many seconds after a post publishes before uploading podcast/video/short to the object-store CDN |
| `memory_stale_last_alerts` | `{"shared-context": "2026-04-15T22:13:...` |  |  |
| `memory_stale_threshold_seconds_openclaw` | `2592000` |  |  |
| `memory_stale_threshold_seconds_shared-context` | `2592000` |  |  |
| `migration_drift_auto_recover_enabled` | `true` |  |  |
| `migration_drift_auto_sync_enabled` | `false` |  | When true, the migration-drift probe resyncs the deploy checkout (git reset --hard origin/main + clean -fd) before re... |
| `migration_drift_deploy_checkout_path` | `/host-deploy` |  | In-brain-container path where the dedicated deploy checkout is mounted RW. The probe runs git here. Nothing else touc... |
| `migration_drift_recover_max_attempts` | `3` |  | Max consecutive recovery attempts (sync+restart) for one drift episode before the probe gives up, pages once, and sup... |
| `model_role_image_decision` | `ollama/phi4:14b` |  |  |
| `newsletter_batch_delay_seconds` | `2` |  | Auto-seeded by services.settings_defaults (#379) |
| `newsletter_batch_size` | `50` |  | Auto-seeded by services.settings_defaults (#379) |
| `newsletter_email` | `` |  | Newsletter sender email (legacy) |
| `nvidia_exporter_url` | `http://poindexter-gpu-exporter:9835/m...` |  | DEPRECATED (PR #1827) — superseded by gpu_metrics_prometheus_url. gpu_scheduler now reads GPU metrics from Prometheus... |
| `ollama_base_url` | `http://host.docker.internal:11434` |  | Ollama API endpoint |
| `ollama_client_timeout_seconds` | `1500` |  |  |
| `openclaw_gateway_url` | `http://localhost:18789` |  | OpenClaw gateway URL |
| `operator_id` | `operator` |  | Default operator ID |
| `operator_url_probe_skip_keys` | `gitea_url,google_sitemap_ping_url,ind...` |  |  |
| `owner_email` | `` |  | Site owner email |
| `owner_name` | `*(per-operator)*` | per-operator | Site owner display name |
| `pexels_api_base` | `https://api.pexels.com/v1` |  | Auto-seeded by services.settings_defaults (#379) |
| `pipeline_dry_run_mode` | `false` |  |  |
| `pipeline_writer_unload_before_image_gen` | `true` |  | Auto-seeded by services.settings_defaults (#379) |
| `pipeline_gate_final_publish_approval` | `off` |  | HITL approval gate 'final_publish_approval': on/off (auto-managed by approval_service) |
| `pipeline_use_graph_def` | `true` |  |  |
| `pipeline_writer_model` | `ollama/gemma-4-31B-it-qat:latest` |  |  |
| `pipeline_writer_unload_grace_seconds` | `2` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.audio_gen_provider.stable-audio-open-1.0.default_duration_s` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.audio_gen_provider.stable-audio-open-1.0.output_format` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.audio_gen_provider.stable-audio-open-1.0.sample_rate` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.audio_gen_provider.stable-audio-open-1.0.server_url` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.image_provider.flux_schnell.server_url` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.job.media_reconciliation` | `{"enabled": true, "interval_seconds":...` |  |  |
| `plugin.job.verify_published_posts` | `{"enabled":true,"interval_seconds":0,...` |  |  |
| `plugin.llm_provider.gemini.enabled` | `false` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.llm_provider.litellm.allow_paid_base_url` | `false` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.llm_provider.openai_compat.allow_paid_base_url` | `false` |  | Auto-seeded by services.settings_defaults (#379) |
| `plugin.video_provider.wan2.1-1.3b.server_url` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `podcast_description` | `AI-development audio essays from Glad...` |  | Podcast RSS description |
| `podcast_name` | `Glad Labs Podcast` |  | Podcast title for RSS feeds |
| `podcast_tts_engine` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `prefect_content_flow_concurrency` | `3` |  |  |
| `prefect_stuck_flow_queue_overdue_min_minutes` | `5` |  | Minimum minutes a SCHEDULED Prefect run must be overdue before it counts toward the queue-depth backlog threshold. Pr... |
| `preferred_ollama_model` | `gemma-4-31B-it-qat:latest` |  |  |
| `publish_quiet_hours` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_bad_link_max_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_bad_link_penalty` | `0.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_baseline` | `7.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_citation_bonus` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_first_person_max_penalty` | `3.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_first_person_penalty` | `1.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_good_link_bonus` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_good_link_max_bonus` | `1.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_meta_commentary_max_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_accuracy_meta_commentary_penalty` | `0.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_artifact_penalty_max` | `20.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_artifact_penalty_per` | `5.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_clarity_good_max_wps` | `25` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_clarity_good_min_wps` | `10` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_clarity_ideal_max_wps` | `20` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_clarity_ideal_min_wps` | `15` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_clarity_ok_max_wps` | `30` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_clarity_ok_min_wps` | `8` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_heading_bonus` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_heading_max_bonus` | `1.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_truncation_penalty` | `3.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_word_1000_score` | `5.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_word_1500_score` | `6.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_word_2000_score` | `6.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_word_500_score` | `3.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_completeness_word_min_score` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_consistency_veto_threshold` | `30` |  |  |
| `qa_critical_floor` | `50.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_engagement_baseline` | `6.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_fallback_writer_model` | `ollama/gemma-4-31B-it-qat:latest` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_fk_target_max` | `12.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_fk_target_min` | `8.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_gate_weight` | `0` |  |  |
| `qa_llm_buzzword_fail_threshold` | `5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_buzzword_max_penalty` | `5.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_buzzword_penalty_per` | `0.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_buzzword_warn_max_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_buzzword_warn_penalty_per` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_buzzword_warn_threshold` | `3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_exclamation_max_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_exclamation_penalty_per` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_exclamation_threshold` | `5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_filler_fail_threshold` | `4` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_filler_max_penalty` | `4.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_filler_penalty_per` | `0.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_filler_warn_penalty_per` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_filler_warn_threshold` | `2` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_formulaic_min_avg_words` | `50` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_formulaic_structure_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_formulaic_variance` | `0.2` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_hedge_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_hedge_ratio_threshold` | `0.02` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_listicle_title_penalty` | `2.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_opener_penalty` | `5.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_patterns_enabled` | `true` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_repetitive_min_count` | `3` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_repetitive_starter_max_penalty` | `4.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_repetitive_starter_penalty_per` | `1.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_transition_min_count` | `2` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_llm_transition_penalty_per` | `1.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_pass_threshold` | `70.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_preview_screenshot_enabled` | `true` |  |  |
| `qa_relevance_high_coverage_score` | `8.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_relevance_low_coverage_score` | `5.5` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_relevance_med_coverage_score` | `7.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_relevance_none_coverage_score` | `3.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_relevance_no_topic_default` | `6.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_relevance_stuffing_hard_density` | `5.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_relevance_stuffing_soft_density` | `3.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_rewrite_max_attempts` | `2` |  | QA rescue cycle: max bounded rewrite passes before a salvageable reject is hard-rejected (write->qa->revise->qa->revi... |
| `qa_rewrite_model` | `ollama/glm-4.7-5090:latest` |  | Cross-model rescue reviser (qa.rewrite); empty = use the writer model |
| `qa_seo_baseline` | `6.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_title_originality_enabled` | `true` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_title_similarity_threshold` | `0.6` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_topic_dedup_hours` | `48` |  | Auto-seeded by services.settings_defaults (#379) |
| `qa_vision_check_enabled` | `true` |  |  |
| `ragas_enabled` | `true` |  |  |
| `ragas_judge_model` | `ollama/glm-4.7-5090:latest` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_default_top_k` | `5` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_embed_retry_attempts` | `3` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_embed_retry_base_delay_seconds` | `0.25` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_hybrid_enabled` | `true` |  |  |
| `rag_min_similarity` | `0.3` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_rerank_enabled` | `true` |  |  |
| `rag_rerank_model` | `cross-encoder/ms-marco-MiniLM-L-6-v2` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_rrf_k` | `60` |  | Auto-seeded by services.settings_defaults (#379) |
| `rag_source_filter` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `resend_audience_id` | `33b1580d-cfda-4428-9890-d52f443b023b` |  |  |
| `restore_test_backup_dir` | `/host-backups/auto` |  |  |
| `restore_test_critical_tables` | `posts,app_settings,audit_log` |  |  |
| `restore_test_enabled` | `true` |  |  |
| `restore_test_interval_hours` | `24` |  |  |
| `restore_test_min_row_count` | `1` |  |  |
| `restore_test_pg_ready_timeout_seconds` | `60` |  |  |
| `restore_test_postgres_image` | `pgvector/pgvector:pg16` |  |  |
| `restore_test_restore_timeout_seconds` | `300` |  |  |
| `restore_test_run_migrations_smoke` | `true` |  |  |
| `restore_test_smoke_timeout_seconds` | `180` |  |  |
| `restore_test_tier` | `daily` |  |  |
| `scheduled_publisher_poll_seconds` | `60` |  | Auto-seeded by services.settings_defaults (#379) |
| `scheduler_alert_on_job_failure` | `true` |  | Auto-seeded by services.settings_defaults (#379) |
| `sdxl_enabled` | `true` |  | Master toggle for the image-gen featured/inline image pipeline. When false, source_featured_image skips the image-gen HTTP serv... |
| `image_gen_server_url` | `http://host.docker.internal:9836` |  | image-gen server |
| `self_consistency_enabled` | `true` |  |  |
| `sentry_enabled` | `true` |  | Enable Sentry error tracking |
| `shared_http_client_max_connections` | `100` |  | Auto-seeded by services.settings_defaults (#379) |
| `shared_http_client_max_keepalive` | `20` |  | Auto-seeded by services.settings_defaults (#379) |
| `shared_http_client_timeout_seconds` | `30.0` |  | Auto-seeded by services.settings_defaults (#379) |
| `short_video_post_publish_delay_seconds` | `180` |  | Wait this many seconds after a post publishes before kicking off short-video generation (lets podcast finish first) |
| `site_description` | `AI-powered content platform` |  | Longer site description |
| `site_tagline` | `Technology & Innovation` |  | Short tagline used in metadata |
| `smtp_host` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `smtp_port` | `587` |  | Auto-seeded by services.settings_defaults (#379) |
| `smtp_use_tls` | `true` |  | Auto-seeded by services.settings_defaults (#379) |
| `stable_audio_open_server_url` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `stage_timeout_draft` | `1700` |  |  |
| `storage_access_key` | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret |  |
| `storage_bucket` | `gladlabs-media` |  |  |
| `storage_endpoint` | `` |  |  |
| `storage_public_url` | `https://pub-1432fdefa18e47ad98f213a8a...` |  |  |
| `structured_extraction_model` | `ollama/gemma-4-31B-it-qat:latest` |  |  |
| `topic_dedup_engine` | `word_overlap` |  |  |
| `topic_discovery_ideation_lookback_days` | `30` |  | Auto-seeded by services.settings_defaults (#379) |
| `topic_discovery_length_distribution` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `topic_discovery_manual_trigger` | `false` |  |  |
| `topic_discovery_min_cooldown_seconds` | `1800` |  |  |
| `topic_discovery_queue_low_threshold` | `999` |  |  |
| `topic_discovery_rejection_streak` | `999` |  |  |
| `topic_discovery_stale_hours` | `8760` |  |  |
| `topic_discovery_streak_window_hours` | `6` |  | Auto-seeded by services.settings_defaults (#379) |
| `topic_discovery_style_distribution` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `trusted_source_domains` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `tts_acronym_replacements` | `{"SOC":"security operations","CRM":"c...` |  |  |
| `tts_pronunciations` | `{"I/O": "I O", "CI/CD": "CI CD", "Dev...` |  |  |
| `use_ollama` | `false` |  | Auto-seeded by services.settings_defaults (#379) |
| `video_compositor` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `video_feed_name` | `Glad Labs Video` |  | Video RSS feed title |
| `video_negative_prompt` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `video_server_url` | `http://host.docker.internal:9837` |  | Video generation server |
| `video_tts_engine` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `vision_alt_enabled` | `true` |  |  |
| `vision_alt_max_tokens` | `2048` |  |  |
| `vision_alt_model` | `ollama/qwen3-vl:30b` |  |  |
| `voice_agent_brain` | `ollama` |  | Auto-seeded by services.settings_defaults (#379) |
| `voice_agent_brain_mode` | `ollama` |  |  |
| `voice_agent_identity` | `poindexter-bot` |  | Bot identity inside the LiveKit room. Multiple bots in one room need distinct identities. Defaults to 'poindexter-bot... |
| `voice_agent_livekit_enabled` | `true` |  | Toggle for the always-on voice-agent-livekit container. 'true' (default) keeps the bot joined to the configured room.... |
| `voice_agent_livekit_url` | `ws://livekit:7880` |  | WebSocket URL the in-network voice bot uses to reach the LiveKit SFU. 'livekit' is the docker-compose service name; o... |
| `voice_agent_llm_model` | `glm-4.7-5090:latest` |  | Ollama model tag the voice agent uses for its LLM step. Same daily-driver as pipeline_writer_model by default. |
| `voice_agent_ollama_url` | `http://host.docker.internal:11434/v1` |  | Ollama base URL. Default targets the host's Ollama from inside Docker; running voice_agent.py directly on the host ca... |
| `voice_agent_recall_k` | `3` |  | Top-K most-similar prior voice_messages turns to inject into the qwen3:8b system prompt as 'recalled context' on each... |
| `voice_agent_recall_min_similarity` | `0.5` |  | Cosine-similarity floor for voice_messages recall. Hits below this threshold are filtered out before the top-K cut, s... |
| `voice_agent_room_name` | `poindexter` |  | LiveKit room the always-on voice-agent-livekit container joins on boot. Operator clients (https://meet.livekit.io, mo... |
| `voice_agent_tts_speed` | `1.0` |  | Kokoro playback speed multiplier. 1.0 = natural; 0.95 = slightly slower (helpful for technical content); 1.1 = brisker. |
| `voice_agent_tts_voice` | `bf_emma` |  | Kokoro voice id. bf_emma is the top-graded British female in the Kokoro-82M catalog (B-). Other UK female options: bf... |
| `voice_agent_user_speech_timeout` | `1.3` |  |  |
| `voice_agent_vad_stop_secs` | `0.4` |  | Silero VAD end-of-speech silence window in seconds. Lower = snappier turn-taking but more risk of cutting the user of... |
| `wan_server_url` | `` |  | Auto-seeded by services.settings_defaults (#379) |
| `webhook_freshness_revenue_threshold_days` | `90` |  |  |

## gpu

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `ollama_num_ctx` | `8192` |  | Ollama context window size — limits KV cache VRAM usage |

## identity

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `api_base_url` | `http://worker:8002` |  | Backend API base URL |
| `company_age_months` | `6` |  | Company age in months (update periodically) |
| `company_founded_date` | `2025-09-25` |  | Company founding date |
| `company_founded_year` | `2025` |  | Company founding year |
| `company_founder_name` | `*(per-operator)*` | per-operator | Founder name |
| `company_name` | `Glad Labs` |  | Legal company name |
| `company_products` | `` |  | Known real products (for hallucination checks) |
| `company_team_size` | `1` |  | Team size for content validation |
| `discord_ops_channel_id` | `` |  | Discord channel for ops notifications |
| `gpu_model` | `NVIDIA RTX 5090 (32GB VRAM)` |  | GPU model for brain knowledge |
| `newsletter_from_email` | `` |  | Newsletter sender address |
| `privacy_email` | `` |  | Privacy/GDPR contact email |
| `site_domain` | `` |  | Production domain (no protocol) |
| `site_name` | `Glad Labs` |  | Brand/site name used across all services |
| `site_url` | `` |  | Full production URL with protocol |
| `support_email` | `` |  | Support contact email |

## image

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `enable_featured_image` | `true` |  | Generate/search featured images for posts |
| `image_negative_prompt` | `text, words, letters, numbers, waterm...` |  | Negative prompt for all image-gen generations |
| `image_primary_source` | `ai_generation` |  | Primary image source: pexels or ai_generation |
| `image_style_default` | `professional digital art, abstract te...` |  | Default image-gen style for uncategorized posts |
| `image_styles` | `[     {"name": "flat_vector", "scene"...` |  | JSON array of image styles for image-gen featured/inline image generation. Each has name, scene, and tags. |

## infrastructure

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `compose_project_name` | `glad-labs-website` |  | Docker Compose project name used by compose drift auto-recover |

## integration

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `cloudinary_cloud_name` | `` |  | Cloudinary cloud name |
| `grafana_url` | `http://localhost:3000` |  | Grafana Cloud instance URL |

## integrations

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `gh_repo` | `Glad-Labs/glad-labs-stack` |  | GitHub repository (``owner/name``) the dev_diary topic source queries for merged PRs and notable commits when assembl... |
| `google_oauth_client_id` | `206722606964-lt75101b5surs28ede8t7d3j...` |  | Google OAuth client ID — shared by GSC + GA4 Singer taps |
| `igdb_twitch_client_id` | `` |  | Twitch app client ID for IGDB access. Get it from https://dev.twitch.tv/console/apps. Public — not a secret. Used by ... |
| `mcp_http_probe_recovery_token` | `*(encrypted)*` | encrypted | Bearer token shared between brain probe and host recovery agent (port 9841). Set to output of: python -c "import secr... |
| `telegram_cli_audit_logged` | `true` |  | When 'true' (default), every /cli invocation writes one row to the audit_log table (event_type='telegram_cli_invoked'... |
| `telegram_cli_enabled` | `true` |  | Global kill-switch for the Telegram /cli passthrough. When 'true' (default), '/cli <args>' messages from the configur... |
| `telegram_cli_max_output_chars` | `3500` |  | Maximum characters of combined stdout+stderr the Telegram /cli passthrough will reply with. Telegram's hard per-messa... |
| `telegram_cli_safe_commands` | `post,settings,validators,auth,check_h...` |  | Comma-separated allowlist of top-level poindexter CLI subcommands the Telegram /cli passthrough will execute. The fir... |
| `telegram_cli_timeout_seconds` | `30` |  | Wall-clock timeout (seconds) for a /cli subprocess. After this many seconds the passthrough kills the process group a... |

## llm_routing

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `social_poster_fallback_model` | `ollama/llama3:latest` |  | Per-step model pin for services.social_poster X/LinkedIn post generation; read directly by _resolve_social_model (fai... |
| `thinking_model_substrings` | `["qwen3","qwen3.5","glm-4","glm-4.7",...` |  | JSON array of substring needles used by services.llm_providers.thinking_models.is_thinking_model() to classify a mode... |
| `video_slideshow_prompt_model` | `ollama/llama3:latest` |  | Per-call-site backstop for services.video_service image-gen prompt-gen. Deliberately non-thinking — qwen3/glm-4 thinking v... |

## logging

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `max_log_backup_count` | `3` |  | Number of rotated log backups to retain. Default 3 matches the historical env-var fallback. Ref: GH-175. |
| `max_log_size_mb` | `5` |  | Maximum size in MB of a rotating log file before it's rolled over. Default 5 MB matches the historical env-var fallba... |

## media

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `podcast_cover_url` | `https://pub-1432fdefa18e47ad98f213a8a...` |  | Square podcast cover art URL for itunes:image element (Apple/Spotify require 1400-3000px) |
| `podcast_tts_base_url` | `http://speaches:8000/v1` |  | Speaches OpenAI-compatible base URL for podcast TTS. Compose-internal URL by default. Use http://host.docker.internal... |
| `podcast_tts_enabled` | `true` |  | Enable TTS narration for podcast scripts via Speaches. Converts the LLM-generated podcast script to a .wav file using... |
| `podcast_tts_format` | `wav` |  | Output audio format for podcast narration files. Options: wav, mp3, opus, flac. wav is lossless and universally playa... |
| `podcast_tts_model` | `speaches-ai/Kokoro-82M-v1.0-ONNX` |  | Kokoro model id passed to Speaches for podcast TTS. Keep in sync with voice_agent_tts_model unless a different model ... |
| `podcast_tts_voice` | `bf_emma` |  | Kokoro voice id for podcast narration. Options: bf_emma, bf_isabella, am_michael, etc. (matches voice_agent_tts_voice... |
| `preferred_ai_video_style` | `flat_vector,isometric,isometric_voxel...` |  | Comma-list of stylized AI-video shot styles (drawn from image_styles pool). Director rotates per-shot. Matt 2026-05-2... |
| `stable_audio_open_default_duration_s` | `5.0` |  | Default audio clip duration in seconds for Stable Audio Open. Capped at 47s (model maximum). 5s is typical for intro ... |
| `stable_audio_open_output_format` | `wav` |  | Output format for Stable Audio Open clips: wav, mp3, ogg, flac. wav is lossless and preferred for video muxing. |

## memory_alerts

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `memory_stale_threshold_seconds_audit-legacy` | `31536000` |  | Backfilled label for pre-writer-tagging audit embeddings |
| `memory_stale_threshold_seconds_poindexter-samples` | `31536000` |  | One-off seed writer; not expected to refresh |
| `memory_stale_threshold_seconds_worker` | `31536000` |  | Legacy writer label, replaced by auto-embed/brain-daemon |

## memory_compression

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `memory_compression_excerpts_per_bucket` | `12` |  | How many sample rows feed the LLM prompt and land in the {event_type}_excerpts JSONB column for each day-bucket. 12 i... |
| `memory_compression_summary_model` | `ollama/phi4:14b` |  | Ollama model used by retention.summarize_to_table for the per-day summary paragraph. Same default as embedding_collap... |
| `memory_compression_summary_timeout_seconds` | `60` |  | Per-call timeout (seconds) for the LLM summary generation in retention.summarize_to_table. On timeout the handler fal... |

## model_roles

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `inline_image_prompt_model` | `llama3:latest` |  | Ollama model used to craft image-gen prompts for inline images in blog posts |
| `podcast_script_model` | `ollama/gemma4:31b` |  | Ollama model used to generate podcast scripts from article content |
| `qa_fallback_critic_model` | `ollama/gemma4:31b` |  | Fallback critic model used when pipeline_critic_model returns empty or errors |
| `video_scene_model` | `llama3:latest` |  | Ollama model used to generate video scene descriptions from article text |

## models

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `cloud_api_daily_limit` | `5` |  | Max cloud API calls per day in emergency mode (hard cap) |
| `cloud_api_mode` | `emergency_only` |  | Cloud API usage mode: disabled, emergency_only, fallback, always |
| `pipeline_critic_model` | `ollama/glm-4.7-5090:latest` |  | Model for QA/content review |
| `pipeline_fallback_model` | `ollama/gemma-4-31B-it-qat:latest` |  | Fallback model when primary is unavailable |

## monitoring

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `branch_drift_dedup_hours` | `6` |  | Re-page interval (hours) for an unchanged branch-drift state. Dedup is keyed on (repo, local HEAD, origin/main SHA); ... |
| `branch_drift_git_dir` | `/host-git` |  | git --git-dir path inside the brain container for reading the running checkout's HEAD. Matches the read-only ./.git:/... |
| `branch_drift_poll_interval_minutes` | `15` |  | Internal cadence gate (minutes) for the branch-drift canary's GitHub round-trip. The probe is dispatched every brain ... |
| `branch_drift_probe_enabled` | `true` |  | Master switch for the brain branch-drift deploy canary (#942). When true, the brain pages the operator if the bind-mo... |
| `branch_drift_repo` | `Glad-Labs/glad-labs-stack` |  | owner/name of the source-of-truth repo the branch-drift canary compares against. Paired with the gh_token secret for ... |
| `compose_drift_skip_services` | `` |  | Comma-separated list of compose service names the brain drift probe (#213) should skip. Useful for services with inte... |
| `compose_spec_path` | `/app/docker-compose.local.yml` |  | Path to the docker-compose.yml the brain compose-drift probe (#213) reads. The brain container bind-mounts the host's... |
| `docker_port_forward_poll_interval_minutes` | `5` |  | Cadence at which the brain runs the port-forward probe. Default 5 min matches the brain cycle so it runs every cycle. |
| `docker_port_forward_probe_enabled` | `true` |  | Master switch for the brain Docker port-forward stuck-state probe (#222). When false the probe short-circuits without... |
| `docker_port_forward_probe_timeout_seconds` | `3` |  | Per-HTTP-probe timeout in seconds. Kept tight (3s) so a stuck service can't block the brain cycle on probes. |
| `docker_port_forward_recovery_wait_seconds` | `45` |  | How long the probe waits after a docker restart before re-probing to confirm recovery. Default 5s lets Docker Desktop... |
| `docker_port_forward_restart_cap_per_window` | `3` |  | Maximum number of times a single container may be restarted within the rolling window. Prevents runaway restart loops... |
| `docker_port_forward_restart_cap_window_minutes` | `60` |  | Rolling window length in minutes for the per-container restart cap. Default 60 min — combined with the cap of 3 means... |
| `glitchtip_base_url` | `http://glitchtip-web:8000` |  | Base URL for the GlitchTip API the brain triage probe queries. Default is the compose-internal hostname; brain.docker... |
| `glitchtip_triage_alert_threshold_count` | `10` |  | Brain triage probe pages via notify_operator() when a GlitchTip issue has count >= this AND matches no entry in glitc... |
| `glitchtip_triage_auto_resolve_patterns` | `[{"title_pattern": "Error while fetch...` |  | JSONB array of triage rules for the brain GlitchTip probe. Each entry: {title_pattern: <regex>, action: 'resolve' or ... |
| `glitchtip_triage_enabled` | `true` |  | Master enable for the brain GlitchTip triage probe. When 'true' (default), the probe runs every cycle (5-min), pulls ... |
| `glitchtip_triage_org_slug` | `glad-labs` |  | GlitchTip organization slug the brain triage probe queries. Default 'glad-labs' matches the org the bootstrap install... |
| `gpu_temperature_high_threshold_c` | `85` |  | GPU core temperature (C) above which the brain `gpu_temperature` probe alerts. RTX 5090 hard-throttles around 90C; 85... |
| `grafana_alert_folder_uid` | `cfl5ofidejh8ge` |  | Grafana folder UID under which brain alert_sync pushes alert rules. Per-install — get yours from the folder URL in Gr... |
| `grafana_alert_sync_enabled` | `true` |  | Master switch for the brain daemon's Grafana alert sync loop. Set to 'false' to disable the loop entirely without rem... |
| `grafana_api_base_url` | `http://poindexter-grafana:3000` |  | Grafana base URL the brain daemon uses to push alert rules and contact points. Defaults to the docker-compose service... |
| `morning_brief_enabled` | `true` |  | Master switch for the morning_brief scheduled job. When false the job short-circuits and never queries Postgres. |
| `morning_brief_hour_local` | `7` |  | Local-time hour the morning_brief job fires (informational; the active schedule lives in the Job class cron expressio... |
| `morning_brief_lookback_hours` | `24` |  | Lookback window in hours used to roll up published posts, awaiting_approval entries, failed tasks, alert counts, cost... |
| `morning_brief_telegram_critical_only` | `true` |  | When true the brief only pings Telegram on critical-severity alerts or failed tasks (Discord still always receives th... |
| `probe_webhook_freshness_enabled` | `true` |  | Master switch for the brain's webhook-freshness probe. When true, the probe checks revenue_events / subscriber_events... |
| `probe_webhook_freshness_interval_minutes` | `1440` |  | How often the webhook-freshness probe runs (minutes). Default 1440 = once a day. The probe is cheap (two SELECT MAX q... |
| `pr_staleness_dedup_hours` | `12` |  | Quiet period (in hours) after a stale-PR alert fires before the same PR can re-page. Per-PR dedup is anchored on the ... |
| `pr_staleness_max_prs_per_alert` | `5` |  | Cap on the number of PRs surfaced in a single Discord-ops message body. Keeps the alert under Discord's per-message c... |
| `pr_staleness_min_hours` | `24` |  | Minimum age (in hours) before an open PR is considered stale by the brain PR staleness probe. PRs younger than this a... |
| `pr_staleness_poll_interval_minutes` | `60` |  | Internal cadence gate for the brain PR staleness probe. The brain dispatches the probe every cycle (~5 min); the actu... |
| `pr_staleness_probe_enabled` | `true` |  | Master switch for the brain PR staleness probe. When false the probe short-circuits without hitting GitHub. See brain... |
| `pr_staleness_repo` | `Glad-Labs/glad-labs-stack` |  | GitHub repository (``owner/name``) the brain PR staleness probe scans for open PRs. Future-proofs for multi-repo. Pai... |
| `smart_monitor_alert_dedup_minutes` | `360` |  | Don't re-fire the same (drive, attribute) alert within this many minutes. Default 360 (6 h) matches the default poll ... |
| `smart_monitor_current_pending_threshold` | `0` |  | Inclusive threshold for Current_Pending_Sector. Anything strictly greater fires a warning. 0 = any pending sector at ... |
| `smart_monitor_drive_filter` | `` |  | Optional comma-separated list of drive names (e.g. /dev/sda,/dev/nvme0) to restrict scanning to. Empty = scan everyth... |
| `smart_monitor_enabled` | `true` |  | Master switch for the brain SMART monitor probe (#387). When false, the probe short-circuits without scanning drives. |
| `smart_monitor_poll_interval_hours` | `6` |  | Cadence at which the brain runs `smartctl -a` against each detected drive. Default 6h matches typical SMART attribute... |
| `smart_monitor_power_on_hours_info_threshold` | `50000` |  | Power_On_Hours threshold above which the probe emits an info-severity FYI alert. ~50k h = ~5.7 years; useful for repl... |
| `smart_monitor_reallocated_sector_threshold` | `0` |  | Inclusive threshold for Reallocated_Sector_Ct. Anything strictly greater fires a warning alert. 0 = any reallocated s... |
| `smart_monitor_smartctl_path` | `` |  | Absolute path to the smartctl binary. Empty = use shutil.which("smartctl"). Override when smartmontools is installed ... |
| `smart_monitor_wear_leveling_warn_percent` | `90` |  | Used-life percentage for SSD Wear_Leveling_Count above which the probe fires a warning. Computed as (100 - normalized... |
| `uptime_kuma_admin_username` | `admin` |  | Kuma admin username (set by scripts/kuma_bootstrap.py) |
| `webhook_freshness_subscriber_threshold_days` | `7` |  | Notify operator if no row has been added to subscriber_events in this many days. Default 7 because Resend should see ... |

## newsletter

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `newsletter_enabled` | `true` |  | Enable newsletter sending on publish |
| `newsletter_from_name` | `Glad Labs` |  | Newsletter sender display name |
| `newsletter_provider` | `resend` |  | Email provider: resend or smtp |

## niche_pivot

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `niche_batch_expires_days` | `7` |  | Number of days a topic_batch row stays open before its expires_at watermark trips. Default 7 matches the prior hardco... |
| `niche_carry_forward_decay_factor` | `0.7` |  | Multiplicative decay applied to a candidate's decay_factor each time it survives a batch unpicked. Default 0.7 matche... |
| `niche_embedding_model` | `nomic-embed-text` |  | Ollama embedding model used for niche topic ranking and writer-mode RAG snippet retrieval. Default 'nomic-embed-text'... |
| `niche_goal_descriptions` | `{"TRAFFIC": "Topic likely to attract ...` |  | JSON blob mapping each goal_type (TRAFFIC, EDUCATION, BRAND, AUTHORITY, REVENUE, COMMUNITY, NICHE_DEPTH) to the prose... |
| `niche_internal_rag_per_kind_limit` | `4` |  | Per-source-kind limit passed to InternalRagSource.generate by TopicBatchService._discover_internal. Default 4 matches... |
| `niche_internal_rag_snippet_max_chars` | `600` |  | Per-snippet character cap when joining raw snippets into the topic/angle distillation prompt in InternalRagSource._di... |
| `niche_ollama_chat_timeout_seconds` | `300` |  | HTTP timeout (seconds) for direct Ollama /api/chat calls made by topic_ranking._ollama_chat_json — used by the LLM sc... |
| `niche_top_n_per_pool` | `5` |  | Top N candidates per pool (external + internal) carried forward from the embedding pre-rank into the LLM final-score ... |

## notifications

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `preview_base_url` | `*(per-operator)*` | per-operator |  |
| `telegram_alerts_enabled` | `true` |  | Telegram is for severity=critical infra alerts only. Discord receives all routine pipeline events (awaiting approval,... |
| `telegram_chat_id` | `` |  | Telegram chat ID for all alerts |

## observability

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `data_fabric_loki_url` | `http://loki:3100` |  | Loki HTTP API base URL used by DataFabric.LokiClient. Defaults to compose-service DNS so it resolves from inside the ... |
| `data_fabric_prometheus_url` | `http://prometheus:9090` |  | Prometheus HTTP API base URL used by DataFabric.PrometheusClient. Defaults to compose-service DNS (prometheus listens... |
| `data_fabric_pyroscope_url` | `http://pyroscope:4040` |  | Pyroscope HTTP API base URL used by DataFabric.PyroscopeClient. Defaults to compose-service DNS so it resolves from i... |
| `data_fabric_tempo_url` | `http://tempo:3200` |  | Tempo HTTP API base URL used by DataFabric.TempoClient. Defaults to compose-service DNS so it resolves from inside th... |
| `enable_pyroscope` | `true` |  | When true, services/profiling.py:setup_pyroscope() configures the pyroscope-io agent at worker / brain / voice-agent ... |
| `enable_tracing` | `true` |  | Master switch for OpenTelemetry tracing. When true, services.tracing.setup_tracing initializes the TracerProvider + O... |
| `langfuse_host` | `http://langfuse-web:3000` |  | Langfuse base URL for prompt management + tracing. Default empty = Langfuse disabled, prompts resolve via DB+YAML fal... |
| `langfuse_tracing_enabled` | `true` |  | When true (default), LiteLLMProvider registers Langfuse as a success/failure callback so every LLM call emits a span ... |
| `operator_url_probe_target_overrides` | `{"cloudflare_beacon_url": {"alive_cod...` |  | Per-URL probe behavior overrides for the operator-url probe. JSON map keyed by app_setting key (e.g. 'google_sitemap_... |
| `otel_exporter_otlp_endpoint` | `http://tempo:4318/v1/traces` |  | OTLP gRPC endpoint that the worker pushes spans to. Default points at the docker-compose tempo service on its OTLP gR... |
| `pyroscope_server_url` | `http://pyroscope:4040` |  | Pyroscope ingestion URL for worker agent |
| `sentry_profiles_sample_rate` | `0.1` |  | Fraction of transactions to capture as CPU profiles. Default 0.1. Same hardcoding bug as traces sample rate. |
| `sentry_sdk_debug` | `false` |  | Forces Sentry SDK to log internal debug messages to stderr. Default false — was hardcoded true in dev which flooded l... |
| `sentry_traces_sample_rate` | `0.1` |  | Fraction of FastAPI requests sampled as Sentry traces (0.0-1.0). Default 0.1 (10%). Previously hardcoded to 1.0 in de... |

## ops-triage

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `ops_triage_writer_model` | `ollama/gemma-4-31B-it-qat:latest` |  | Local Ollama model used for brain alert triage (the /api/triage endpoint). Defaults to gemma-4-31B-it-qat:latest beca... |

## orchestration

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `use_prefect_orchestration` | `true` |  | Prefect orchestration cutover flag (#410 Stage 4 complete 2026-05-16). Permanently true — services/task_executor.py w... |

## performance

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `ollama_concurrency_limit` | `10` |  | Maximum concurrent in-flight Ollama requests this worker process will issue — passed to aiolimiter.AsyncLimiter. Prev... |
| `ollama_max_retries` | `3` |  | Maximum number of attempts (initial + retries) for Ollama generate_with_retry. Passed to tenacity's stop_after_attemp... |
| `ollama_retry_initial_seconds` | `1` |  | Initial backoff delay (seconds) for Ollama retries — passed to tenacity's wait_exponential_jitter(initial). Doubles o... |
| `ollama_retry_max_seconds` | `30` |  | Upper bound (seconds) on Ollama retry backoff — passed to tenacity's wait_exponential_jitter(max). Caps the exponenti... |

## pipeline

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `approval_ttl_days` | `7` |  | Days before unapproved posts are auto-expired |
| `auto_publish_threshold` | `0` |  | Quality score threshold for auto-publishing (0=disabled) |
| `brain_auto_cancel_grace_minutes` | `10` |  | Extra grace period the brain daemon adds on top of stale_task_timeout_minutes before flipping a stuck task to failed.... |
| `content_quality_minimum` | `75` |  | Minimum quality score to even queue for approval. Below this = auto-reject. |
| `content_weekly_cap` | `50` |  | Maximum new posts per week (0=unlimited). Topic discovery respects this. |
| `create_post_dedup_threshold` | `0.75` |  | Cosine similarity at/above which a caller-supplied topic (create_post MCP tool / POST /api/tasks) is refused as a nea... |
| `daily_budget_usd` | `1.00` |  | Daily LLM spend budget in USD (ignored if cloud_api_mode=disabled) |
| `daily_post_limit` | `4` |  | Maximum posts to generate per day |
| `default_template_slug` | `canonical_blog` |  | Lane C cutover switch: when set, every new pipeline_tasks row without an explicit caller-supplied template_slug gets ... |
| `max_approval_queue` | `100` |  | Restored 2026-04-24 after backlog cleared |
| `max_posts_per_day` | `8` |  | Maximum posts to publish per day |
| `max_task_retries` | `3` |  | Maximum retry attempts for failed tasks |
| `max_tokens_per_request` | `4000` |  | Maximum output tokens per LLM request |
| `max_tokens_per_task` | `16000` |  | Maximum total tokens (input+output) per content task |
| `min_curation_score` | `75` |  | Minimum QA score to surface for human review (below this = auto-reject) |
| `pipeline_architect_model` | `ollama/glm-4.7-5090:latest` |  | Local Ollama model the architect-LLM uses to compose pipelines from intent + atom catalog. Cloud models are opt-in on... |
| `pipeline_architect_timeout_seconds` | `120.0` |  | Max seconds to wait for the architect LLM to emit its JSON graph spec before timing out and falling back to a default... |
| `pipeline_gate_draft_gate` | `off` |  | HITL approval gate 'draft_gate': on/off. When on, the canonical_blog pipeline pauses after the writer stage via LangG... |
| `pipeline.stages.order` | `["verify_task", "generate_content", "...` |  | Ordered list of Stage names the content pipeline runs. Operators can disable (drop from list), reorder, or insert thi... |
| `pipeline_streaming_channel` | `discord` |  | Where TemplateRunner.run streams per-node progress via its on_event callback: 'discord' (default — existing Discord p... |
| `pipeline_streaming_min_edit_interval_s` | `5` |  | Minimum seconds between Telegram editMessageText calls when pipeline_streaming_channel='telegram'. Rapid node complet... |
| `publish_spacing_hours` | `4` |  | Minimum hours between published posts |
| `require_human_approval` | `true` |  | When true, all content requires human approval before publishing |
| `seed_url_fetch_timeout_seconds` | `10` |  | URL-based topic seeding: total HTTP timeout (seconds) for the seed_url fetch on POST /api/tasks. Short by design — if... |
| `seed_url_max_bytes` | `1048576` |  | URL-based topic seeding: hard cap (bytes) on the decoded response body. Guards against pathological pages that would ... |
| `seed_url_user_agent` | `Mozilla/5.0 (Windows NT 10.0; Win64; ...` |  | URL-based topic seeding: User-Agent header for the seed_url fetch. Chrome-ish by default because many news/publisher ... |
| `staging_mode` | `false` |  | When true, posts go to draft with preview token instead of publishing |
| `stale_task_timeout_minutes` | `180` |  | Minutes before a running task is considered stale |
| `task_sweep_interval_seconds` | `300` |  | Seconds between stale task sweeps |
| `template_runner_progress_streaming` | `true` |  | When on, TemplateRunner emits per-node progress to Discord (NOT Telegram) via notify_operator(critical=False). Defaul... |
| `template_runner_use_postgres_checkpointer` | `true` |  | When true, services/template_runner.py compiles each LangGraph with an AsyncPostgresSaver checkpointer (durable state... |
| `topic_dedup_existing_threshold` | `0.7` |  | Word-overlap ratio above which a candidate topic is treated as a duplicate of an existing published post or in-flight... |
| `topic_dedup_intra_batch_threshold` | `0.65` |  | Word-overlap ratio above which two candidates from the same scrape batch are treated as duplicates. Range 0.0-1.0. Sl... |
| `worker_heartbeat_interval_seconds` | `30` |  | Worker heartbeat cadence. While processing a single task the Prefect content_generation_flow stamps pipeline_tasks.up... |

## plugins

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `plugin.job.analyze_topic_gaps` | `{"enabled": true, "interval_seconds":...` |  | Config for job analyze_topic_gaps — tune cadence via config.schedule |
| `plugin.job.audit_published_quality` | `{"enabled": true, "interval_seconds":...` |  | Config for job audit_published_quality — tune cadence via config.schedule |
| `plugin.job.backfill_post_performance_gsc` | `{"enabled": true, "interval_seconds":...` |  | Config for job backfill_post_performance_gsc — tune cadence via config.schedule |
| `plugin.job.check_memory_staleness` | `{"enabled": true, "interval_seconds":...` |  | Config for job check_memory_staleness — tune cadence via config.schedule |
| `plugin.job.check_published_links` | `{"enabled": true, "interval_seconds":...` |  | Config for job check_published_links — tune cadence via config.schedule |
| `plugin.job.crosspost_to_devto` | `{"enabled": true, "interval_seconds":...` |  | Config for job crosspost_to_devto — tune cadence via config.schedule |
| `plugin.job.db_backup` | `{"enabled": true, "interval_seconds":...` |  | Config for job db_backup — tune cadence via config.schedule |
| `plugin.job.detect_anomalies` | `{"enabled": true, "interval_seconds":...` |  | Config for job detect_anomalies — tune cadence via config.schedule |
| `plugin.job.detect_duplicate_posts` | `{"enabled": true, "interval_seconds":...` |  | Config for job detect_duplicate_posts — tune cadence via config.schedule |
| `plugin.job.expire_stale_approvals` | `{"enabled": true, "interval_seconds":...` |  | Config for job expire_stale_approvals — tune cadence via config.schedule |
| `plugin.job.findings_alert_router` | `{"enabled": true, "interval_seconds":...` |  | Config for job findings_alert_router — tune cadence via config.schedule |
| `plugin.job.fix_broken_external_links` | `{"enabled": true, "interval_seconds":...` |  | Config for job fix_broken_external_links — tune cadence via config.schedule |
| `plugin.job.fix_broken_internal_links` | `{"enabled": true, "interval_seconds":...` |  | Config for job fix_broken_internal_links — tune cadence via config.schedule |
| `plugin.job.fix_uncategorized_posts` | `{"enabled": true, "interval_seconds":...` |  | Config for job fix_uncategorized_posts — tune cadence via config.schedule |
| `plugin.job.flag_missing_seo` | `{"enabled": true, "interval_seconds":...` |  | Config for job flag_missing_seo — tune cadence via config.schedule |
| `plugin.job.morning_brief` | `{"enabled": true, "interval_seconds":...` |  | Config for job morning_brief — tune cadence via config.schedule |
| `plugin.job.poll_mercury` | `{"enabled": true, "interval_seconds":...` |  | Config for job poll_mercury — tune cadence via config.schedule |
| `plugin.job.postgres_vacuum` | `{"enabled": true, "interval_seconds":...` |  | Config for job postgres_vacuum — tune cadence via config.schedule |
| `plugin.job.reload_site_config` | `{"enabled": true, "interval_seconds":...` |  | Config for job reload_site_config — tune cadence via config.schedule |
| `plugin.job.render_alertmanager_config` | `{"enabled": true, "interval_seconds":...` |  | Config for RenderAlertmanagerConfigJob (#524) — renders alertmanager.yml.tmpl with telegram_chat_id and reloads Alert... |
| `plugin.job.render_prometheus_rules` | `{"enabled": true, "interval_seconds":...` |  | Config for RenderPrometheusRulesJob |
| `plugin.job.rollup_post_performance` | `{"enabled": true, "interval_seconds":...` |  | Config for job rollup_post_performance — tune cadence via config.schedule |
| `plugin.job.run_dev_diary_post` | `{"enabled": true, "interval_seconds":...` |  | Config for job run_dev_diary_post — tune cadence via config.schedule |
| `plugin.job.run_niche_topic_sweep` | `{"enabled": true, "interval_seconds":...` |  | Config for job run_niche_topic_sweep — tune cadence via config.schedule |
| `plugin.job.run_retention` | `{"enabled": true, "interval_seconds":...` |  | Config for job run_retention — tune cadence via config.schedule |
| `plugin.job.run_taps` | `{"enabled": true, "interval_seconds":...` |  | Config for job run_taps — tune cadence via config.schedule |
| `plugin.job.static_export_orphan_sweep` | `{"enabled": true, "interval_seconds":...` |  | Config for job static_export_orphan_sweep — tune cadence via config.schedule |
| `plugin.job.static_export_reconciliation` | `{"enabled": true, "interval_seconds":...` |  | Config for job static_export_reconciliation — tune cadence via config.schedule |
| `plugin.job.sync_cloudflare_analytics` | `{"enabled": true, "interval_seconds":...` |  | Config for job sync_cloudflare_analytics — tune cadence via config.schedule |
| `plugin.job.topic_auto_resolve` | `{"enabled": true, "interval_seconds":...` |  | Config for job topic_auto_resolve — tune cadence via config.schedule |
| `plugin.job.tune_publish_threshold` | `{"enabled": true, "interval_seconds":...` |  | Config for job tune_publish_threshold — tune cadence via config.schedule |
| `plugin.job.update_utility_rates` | `{"enabled": true, "interval_seconds":...` |  | Config for job update_utility_rates — tune cadence via config.schedule |
| `plugin.llm_provider.litellm` | `{"enabled": true, "interval_seconds":...` |  | LiteLLM dispatcher config (api_base + timeout) |
| `plugin.llm_provider.primary.budget` | `litellm` |  | Default LLMProvider for the 'budget' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call ti... |
| `plugin.llm_provider.primary.flagship` | `litellm` |  | Default LLMProvider for the 'flagship' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call ... |
| `plugin.llm_provider.primary.free` | `litellm` |  | Default LLMProvider for the 'free' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call time... |
| `plugin.llm_provider.primary.premium` | `litellm` |  | Default LLMProvider for the 'premium' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call t... |
| `plugin.llm_provider.primary.standard` | `litellm` |  | Default LLMProvider for the 'standard' cost tier. Resolved by services/llm_providers/dispatcher.get_provider at call ... |
| `plugin.topic_source.igdb` | `{"enabled": false, "interval_seconds"...` |  | IGDB indie-games topic source. Disabled by default — set Twitch credentials (igdb_twitch_client_id + igdb_twitch_clie... |

## plugin_telemetry

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `findings_alert_route_watermark` | `76339` |  | Highest audit_log.id forwarded to alert_events by FindingsAlertRouterJob. Pre-seeded 2026-05-15 to current max to ski... |

## podcast

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `podcast_spotify_show_id` | `033obxyUXdxhXyQ6erC07G` |  | Spotify show ID for the Glad Labs Podcast (the bit after /show/ in the public URL). |
| `podcast_spotify_url` | `https://open.spotify.com/show/033obxy...` |  | Public Spotify URL for the Glad Labs Podcast. Surface this on the About page and at the bottom of dev_diary posts + i... |

## prometheus

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `prometheus.threshold.daily_spend_critical_usd` | `5.0` |  | Daily LLM spend critical threshold |
| `prometheus.threshold.daily_spend_warning_usd` | `4.0` |  | Daily LLM spend warning threshold |
| `prometheus.threshold.embeddings_stale_seconds` | `21600` |  | Seconds without an embeddings_total change before EmbeddingsStale fires |
| `prometheus.threshold.monthly_spend_warning_usd` | `35.0` |  | Monthly spend warning threshold (USD). Includes ALL cost_logs rows — local Ollama electricity (~$30/mo baseline) AND ... |
| `prometheus.threshold.qa_rail_skip_ratio` | `1` |  | Per-rail skip ratio that fires QaRailFullySkipped (1 = skipped 100% of the last N QA passes; lower e.g. 0.9 to page e... |

## publishing

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `dev_diary_auto_publish_dry_run` | `false` |  | When true (default), auto-publish gate runs in observe-only mode: logs 'would have auto-published Y/N' for each final... |
| `dev_diary_auto_publish_max_edit_distance` | `50` |  | Char-level edit distance threshold for the 'clean run' criterion. Default 50 — trivial typo fixes pass; substantive r... |
| `dev_diary_auto_publish_min_clean_runs` | `3` |  | Trailing N publishes that must have edit_distance < auto_publish_max_edit_distance for the gate to fire. Default 3 — ... |
| `dev_diary_auto_publish_threshold` | `70` |  | Quality_score floor for dev_diary auto-publish. Default -1 disables the gate entirely. Set to a value 0-100 (e.g. 85)... |
| `plugin.publish_adapter.youtube.enabled` | `true` |  | Master switch — flip to true ONCE the publish-video stage is wired (PR pending). |

## qa

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `deepeval_g_eval_criterion` | `The output is well-grounded in the in...` |  | Criterion text the DeepEval g-eval judge model uses to grade the post. Operators can rewrite this to emphasize differ... |
| `deepeval_judge_model` | `ollama/glm-4.7-5090:latest` |  | LLM model identifier used by the DeepEval g-eval and faithfulness reviewers. Default 'glm-4.7-5090' (Matt's local thi... |
| `deepeval_threshold_faithfulness` | `0.8` |  | Threshold (0–1) above which the DeepEval faithfulness reviewer marks the post as approved. Default 0.8 — at least 80%... |
| `deepeval_threshold_g_eval` | `0.7` |  | Threshold (0–1) above which the DeepEval g-eval reviewer marks the post as approved. Default 0.7 — anything below mea... |
| `guardrails_competitor_list` | `Jasper, Copy.ai, Writesonic, Article ...` |  | Comma-separated list of competitor brand names to flag if they appear in a post body (case-insensitive, word-boundary... |
| `qa_rail_skip_window_passes` | `20` |  | How many recent QA passes the poindexter_qa_rail_skip_ratio gauge measures a rail's skip rate over — poindexter#553 |
| `self_consistency_sample_count` | `3` |  | Number of summary samples for the self-consistency rail. Higher = more accurate signal, higher Ollama cost. Default 3. |
| `self_consistency_threshold` | `0.55` |  | Minimum mean pairwise cosine similarity to pass the self-consistency rail. Range [0, 1]. Default 0.55. |

## qa_workflows

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `qa_workflow_blog_content` | `{"reviewers": ["programmatic_validato...` |  | Blog content QA workflow chain |
| `qa_workflow_premium_content` | `{"reviewers": ["programmatic_validato...` |  | Premium QA with LLM critic - all reviewers must pass |
| `qa_workflow_quick_check` | `{"reviewers": ["programmatic_validato...` |  | Fast validation for bulk content |

## quality

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `content_validator_warning_qa_penalty` | `3` |  | Points subtracted from final QA score per validator warning (GH-91) |
| `qa_allow_first_person_niches` | `dev_diary,glad-labs` |  | Comma-separated list of niche slugs that bypass the first_person_claims validator in quality_scorers.py. Per Matt's v... |
| `qa_critical_dimension_floor` | `50` |  | Minimum score on any single quality dimension |
| `qa_critic_weight` | `0.6` |  | Weight for LLM critic in final score |
| `qa_final_score_threshold` | `80` |  | Multi-model QA final approval score threshold |
| `qa_overall_score_threshold` | `80` |  | Minimum overall quality score to pass QA (0-100) |
| `qa_validator_weight` | `0.4` |  | Weight for programmatic validator in final score |

## rag

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `rag_engine_enabled` | `true` |  | Master switch for the LlamaIndex retriever path (services/rag_engine.py wired into MemoryClient.search per Lane D #32... |

## scheduling

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `dev_diary_last_run_date` | `2026-06-06` |  | YYYY-MM-DD (UTC) of the last successful dev-diary job run. Idempotency marker — the job no-ops if this matches today. |

## security

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `oauth_issuer_url` | `*(per-operator)*` | per-operator | Public-facing issuer URL advertised in RFC 8414 metadata. Falls back to request.url when empty (e.g. localhost dev). |

## site

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `public_site_revalidate_url` | `` |  | Full URL of the Next.js public site's /api/revalidate endpoint. POSTed by services/revalidation_service.py to bust th... |
| `public_site_url` | `` |  |  |

## skills

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `skill_importer_allowed_licenses` | `MIT,Apache-2.0,BSD-2-Clause,BSD-3-Cla...` |  | Comma-separated list of SPDX license identifiers that ``poindexter skills import`` accepts.  Add identifiers to permi... |

## social

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `mastodon_instance_url` | `` |  | GH-36: Full Mastodon instance URL, e.g. 'https://mastodon.social'. Empty = Mastodon distribution skipped. |
| `social_distribution_platforms` | `` |  | GH-36: Comma-separated list of platforms social_poster should push to after a successful publish. Valid values: 'mast... |
| `social_linkedin_url` | `*(per-operator)*` | per-operator | LinkedIn profile URL |
| `social_x_handle` | `*(per-operator)*` | per-operator | X/Twitter handle |
| `social_x_url` | `*(per-operator)*` | per-operator | X/Twitter profile URL |

## system

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `local_database_url` | `postgresql://poindexter:poindexter-br...` |  | Local brain DB connection string (Docker internal) |
| `repo_root` | `/app` |  | Root path of the codebase (for running scripts inside container) |

## tokens

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `content_temperature` | `0.7` |  | Temperature for content generation |
| `max_tokens_default` | `800` |  | Default max tokens for general tasks |
| `qa_standard_max_tokens` | `1500` |  | Max tokens for standard models in QA |
| `qa_temperature` | `0.3` |  | Temperature for QA review generation |
| `qa_thinking_model_max_tokens` | `8000` |  | Max tokens for thinking models (qwen3.5, glm-4.7) in QA |

## topic_discovery

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `topic_discovery_auto_enabled` | `true` |  | Master kill-switch for the LEGACY auto-firing topic discovery loop in services/idle_worker.py. When 'true' (default, ... |

## voice

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `voice_agent_claude_code_enabled` | `true` |  | Master on/off for the claude-code voice room container (#1006). false/0/no/off = the container exits 0 and docker lea... |
| `voice_agent_claude_code_host_brain_url` | `http://host.docker.internal:8123/turn` |  | Host-brain daemon URL for the voice room (#1006), e.g. http://host.docker.internal:8123/turn. Empty = run claude in-c... |
| `voice_agent_claude_code_identity` | `claude-code-bot` |  | Participant identity for the claude-code voice bot (#1006). Distinct from the poindexter bot so both can coexist. |
| `voice_agent_claude_code_room_name` | `claude-code` |  | LiveKit room the claude-code voice bot joins (#1006). Must match an allowed room in routes/voice_routes.py so /voice/... |
| `voice_agent_claude_code_session_id` | `287e7fc4-5147-4bf4-92b5-c3df15d25aa6` |  | Pinned claude -p voice session for the always-on claude-code room (#1006) |
| `voice_agent_claude_code_session_max_age_seconds` | `14400` |  | Rotate the pinned claude -p voice session once it is older than this many seconds (#1006). 14400 = 4h. |
| `voice_agent_claude_code_session_token_budget` | `200000` |  | Rotate the pinned claude -p voice session once cumulative input+output tokens exceed this (#1006). |
| `voice_agent_claude_code_transcript_enabled` | `true` |  | Master on/off for mirroring claude-code voice turns to Discord (#1006). false/0/no/off disables the mirror. |
| `voice_agent_claude_code_tts_voice` | `bf_isabella` |  | Kokoro voice id for the claude-code voice room only (#1006). Empty = fall back to the shared voice_agent_tts_voice. L... |
| `voice_agent_public_join_url` | `*(per-operator)*` | per-operator | Public URL the operator (or Claude, via the start_voice_call MCP tool) taps to join the always-on LiveKit voice room.... |
| `voice_agent_stt_base_url` | `http://speaches:8000/v1` |  | Speaches STT endpoint (OpenAI-compatible) used when voice_agent_stt_mode=sidecar. Compose service name on the shared ... |
| `voice_agent_stt_mode` | `sidecar` |  | STT backend for the voice pipeline (#1088): 'sidecar' = thin client of the warm Speaches container; 'inprocess' = loa... |
| `voice_agent_stt_model` | `Systran/faster-whisper-medium` |  | faster-whisper model id passed to Speaches when voice_agent_stt_mode=sidecar. NOTE: an HF id, not the Pipecat Whisper... |
| `voice_agent_tts_base_url` | `http://speaches:8000/v1` |  | Speaches TTS endpoint used when voice_agent_tts_mode=sidecar. Same Speaches service as STT by default; separate key s... |
| `voice_agent_tts_mode` | `sidecar` |  | TTS backend for the voice pipeline (#1088): 'sidecar' = thin client of the warm Speaches container; 'inprocess' = run... |
| `voice_agent_tts_model` | `speaches-ai/Kokoro-82M-v1.0-ONNX` |  | Kokoro model id passed to Speaches when voice_agent_tts_mode=sidecar. The voice id (bf_emma / bf_isabella) still come... |
| `voice_bridge_chunk_max_chars` | `500` |  | Maximum characters per TTS chunk emitted by voice_speak. Long replies are split at sentence boundaries so the operato... |
| `voice_bridge_enabled` | `true` |  | Master switch for the LiveKit MCP bridge — the architecturally-correct alternative to the subprocess-spawn voice_agen... |
| `voice_bridge_max_session_seconds` | `1800` |  | Hard upper bound on a single bridge session, in seconds. The worker auto-leaves the LiveKit room after this many seco... |
| `voice_bridge_stt_model` | `base.en` |  | faster-whisper model id loaded by the future Pipecat audio plane in the bridge worker. Defaults to base.en (CPU-frien... |
| `voice_bridge_tts_voice` | `bf_isabella` |  | Kokoro voice id used by the bridge worker's TTS path. Matches the always-on voice-agent-livekit container default so ... |
| `voice_default_room` | `poindexter` |  | Default LiveKit room name when voice_join_room is called without an explicit channel_id. Distinct from voice_agent_ro... |

## voice_agent

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `voice_agent_system_prompt` | `You are Emma, a concise voice assista...` |  | System prompt for the voice agent. Mentions tools so glm-4.7-5090 actually invokes them. |
| `voice_agent_whisper_model` | `medium` |  | faster-whisper model size. tiny/base/small/medium/large-v3. medium is the sweet spot for real voice accuracy on a 5090. |

## webhooks

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `openclaw_webhook_url` | `` |  | OpenClaw webhook delivery URL |

## writer_rag

| Key | Default | Classification | Description |
| --- | --- | --- | --- |
| `writer_rag_context_snippet_max_chars` | `500` |  | Per-snippet character cap when building the snippet block for generate_with_context and generate_with_outline (the tw... |
| `writer_rag_research_topic_max_sources` | `2` |  | Default max_sources for the module-level research_topic() shim used by the TWO_PASS writer mode. Default 2 matches th... |
| `writer_rag_two_pass_max_revision_loops` | `3` |  | Hard cap on revise → detect_needs → research_each → revise loops in the TWO_PASS LangGraph state machine. Default 3 m... |
| `writer_rag_two_pass_research_max_sources` | `2` |  | max_sources passed to research_topic for each [EXTERNAL_NEEDED: ...] marker the TWO_PASS draft surfaces. Default 2 ma... |
| `writer_rag_two_pass_snippet_limit` | `20` |  | Top-N pgvector snippets fetched up-front in the TWO_PASS internal-first draft. Default 20 matches the prior hardcoded... |
