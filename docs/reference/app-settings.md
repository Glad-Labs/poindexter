# App settings reference

> **Auto-generated from live `app_settings` table on 2026-04-22.**  
> Every runtime-configurable knob in the Poindexter pipeline.
> 295 active rows across 34 categories. 30 stored encrypted via pgcrypto (`is_secret=true`); an additional 3 values are redacted in the preview below as defense-in-depth against secret-shaped strings that weren't classified as secrets in the DB.

> This file is checked into `docs/` which is **excluded from the public Poindexter sync** (`scripts/sync-to-github.sh` strips `docs/`). Safe to regenerate from operator state. Not safe to publish outside the private mirror.

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

- [api_keys](#api-keys) (8 keys)
- [auth](#auth) (3 keys)
- [content](#content) (11 keys)
- [content_qa](#content-qa) (4 keys)
- [cors](#cors) (2 keys)
- [cost](#cost) (8 keys)
- [features](#features) (4 keys)
- [finance](#finance) (8 keys)
- [general](#general) (99 keys)
- [gpu](#gpu) (1 key)
- [identity](#identity) (16 keys)
- [image](#image) (12 keys)
- [integration](#integration) (16 keys)
- [integrations](#integrations) (2 keys)
- [memory](#memory) (4 keys)
- [model_roles](#model-roles) (12 keys)
- [models](#models) (8 keys)
- [monitoring](#monitoring) (4 keys)
- [newsletter](#newsletter) (3 keys)
- [notifications](#notifications) (5 keys)
- [observability](#observability) (4 keys)
- [pipeline](#pipeline) (27 keys)
- [plugins](#plugins) (1 key)
- [podcast](#podcast) (1 key)
- [prometheus](#prometheus) (4 keys)
- [qa_workflows](#qa-workflows) (3 keys)
- [quality](#quality) (5 keys)
- [security](#security) (1 key)
- [seo](#seo) (1 key)
- [site](#site) (1 key)
- [social](#social) (8 keys)
- [system](#system) (2 keys)
- [tokens](#tokens) (5 keys)
- [webhooks](#webhooks) (2 keys)

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

| Key              | Default         | Classification | Description                                      |
| ---------------- | --------------- | -------------- | ------------------------------------------------ |
| `api_token`      | `*(encrypted)*` | encrypted      | API token for frontend-to-backend authentication |
| `jwt_secret_key` | `*(encrypted)*` | encrypted      | JWT signing secret (auto-generated)              |
| `secret_key`     | `*(encrypted)*` | encrypted      | Application secret key (auto-generated)          |

## content

| Key                                        | Default                                    | Classification | Description                                                                                                              |
| ------------------------------------------ | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `alt_text_budget`                          | `120`                                      |                | Character budget for inline <img alt="..."> text. The alt generator produces complete sentences within this budget; o... |
| `auto_append_sources_section`              | `true`                                     |                | Auto-append ## Sources at finalize if missing                                                                            |
| `content_max_refinement_attempts`          | `3`                                        |                | Max attempts to refine content quality                                                                                   |
| `content_min_word_count`                   | `800`                                      |                | Minimum word count for blog posts                                                                                        |
| `content_target_word_count`                | `1500`                                     |                | Target word count for blog posts                                                                                         |
| `default_ollama_model`                     | `auto`                                     |                | Default Ollama model for LLM calls. "auto" → OllamaClient picks the first available pulled model. Override with a spe... |
| `title_originality_cache_ttl_hours`        | `24`                                       |                | GH-87: TTL (hours) for the in-process cache that dedupes repeated DuckDuckGo queries for the same title. DDG rate-lim... |
| `title_originality_external_check_enabled` | `true`                                     |                | GH-87: enable DuckDuckGo HTML search for the exact post title at approval time. Verbatim external matches subtract ti... |
| `title_originality_external_penalty`       | `-50`                                      |                | GH-87: points subtracted from the QA score when the post title appears verbatim in external search results. Stored as... |
| `writing_style_reference`                  | `Matt Gladding writing style traits: S...` |                |                                                                                                                          |
| `writing_styles`                           | `[{"name": "technical", "voice": "prec...` |                | Configurable writing styles for content generation. Same pattern as image_styles.                                        |

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
| `electricity_rate_kwh`                  | `0.2902`   |                | RI Energy Last Resort Service rate $0.14770/kWh (verified by Matt) |
| `gpu_idle_watts`                        | `45`       |                | GPU idle power draw in watts                                       |
| `gpu_inference_watts`                   | `400`      |                | GPU average inference power draw in watts                          |
| `monthly_spend_limit`                   | `10.0`     |                | Maximum monthly AI spend in USD                                    |
| `ollama_electricity_cost_per_1k_tokens` | `0.000256` |                | Ollama electricity cost per 1K tokens (USD)                        |
| `system_idle_watts`                     | `120`      |                | Total system idle power draw in watts (CPU+RAM+disk+GPU)           |

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

## general

| Key                                             | Default                                                                        | Classification | Description                                                                                                         |
| ----------------------------------------------- | ------------------------------------------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------- |
| `api_url`                                       | `http://localhost:8002`                                                        |                | Backend API base URL (legacy alias for api_base_url)                                                                |
| `cloudflare_account_id`                         | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret    |                                                                                                                     |
| `content_validator_warning_reject_threshold`    | `5`                                                                            |                |                                                                                                                     |
| `database_pool_max_size`                        | `20`                                                                           |                | Max DB pool connections                                                                                             |
| `database_pool_min_size`                        | `5`                                                                            |                | Min DB pool connections                                                                                             |
| `development_mode`                              | `true`                                                                         |                | Enable development mode                                                                                             |
| `devto_min_reactions`                           | `20`                                                                           |                |                                                                                                                     |
| `devto_per_page`                                | `15`                                                                           |                |                                                                                                                     |
| `devto_tag`                                     | ``                                                                             |                |                                                                                                                     |
| `devto_top_days`                                | `7`                                                                            |                |                                                                                                                     |
| `disable_auth_for_dev`                          | `true`                                                                         |                | Disable auth in development                                                                                         |
| `enabled_topic_sources`                         | `knowledge,codebase,hackernews,devto,w...`                                     |                |                                                                                                                     |
| `enable_sdxl_warmup`                            | `false`                                                                        |                | Warm up SDXL models on startup                                                                                      |
| `enable_semantic_dedup`                         | `true`                                                                         |                |                                                                                                                     |
| `gpu_busy_threshold_percent`                    | `30`                                                                           |                | GPU utilization % above which gaming is detected                                                                    |
| `gpu_gaming_check_interval`                     | `15`                                                                           |                | Seconds between gaming detection checks                                                                             |
| `gpu_gaming_clear_checks`                       | `3`                                                                            |                | Consecutive low-util checks to resume pipeline                                                                      |
| `gpu_gaming_confirm_checks`                     | `2`                                                                            |                | Consecutive high-util checks to confirm gaming                                                                      |
| `gpu_name`                                      | ``                                                                             |                | GPU model name (auto-detected by detect-hardware.py)                                                                |
| `gpu_vram_gb`                                   | `0`                                                                            |                | GPU VRAM in GB (auto-detected by detect-hardware.py)                                                                |
| `grafana_user`                                  | `admin`                                                                        |                | Grafana admin username                                                                                              |
| `hardware_cost_total`                           | `7877.14`                                                                      |                | Total PC build cost for depreciation calculation                                                                    |
| `hardware_useful_life_months`                   | `60`                                                                           |                | Estimated useful life in months (5 years)                                                                           |
| `hn_min_score`                                  | `50`                                                                           |                |                                                                                                                     |
| `hn_top_stories`                                | `20`                                                                           |                |                                                                                                                     |
| `host_home`                                     | ``                                                                             |                | Host home directory for Docker volume mounts                                                                        |
| `idle_last_run_anomaly_detect`                  | `1776710530.609785`                                                            |                |                                                                                                                     |
| `idle_last_run_auto_embed`                      | `1776714304.5822833`                                                           |                |                                                                                                                     |
| `idle_last_run_context_sync`                    | `1776717772.9571455`                                                           |                |                                                                                                                     |
| `idle_last_run_db_backup`                       | `1776707134.489248`                                                            |                |                                                                                                                     |
| `idle_last_run_devto_crosspost`                 | `1776698746.4172635`                                                           |                |                                                                                                                     |
| `idle_last_run_embedding_refresh`               | `1776706187.2627528`                                                           |                |                                                                                                                     |
| `idle_last_run_expire_stale_approvals`          | `1776702655.306583`                                                            |                |                                                                                                                     |
| `idle_last_run_fix_categories`                  | `1776707103.448257`                                                            |                |                                                                                                                     |
| `idle_last_run_fix_duplicates`                  | `1776707098.4368908`                                                           |                |                                                                                                                     |
| `idle_last_run_fix_external_links`              | `1776707098.428116`                                                            |                |                                                                                                                     |
| `idle_last_run_fix_internal_links`              | `1776707092.7177281`                                                           |                |                                                                                                                     |
| `idle_last_run_fix_seo`                         | `1776707103.454733`                                                            |                |                                                                                                                     |
| `idle_last_run_image_regen`                     | `1776698252.6993651`                                                           |                |                                                                                                                     |
| `idle_last_run_link_check`                      | `1776706157.2373133`                                                           |                |                                                                                                                     |
| `idle_last_run_memory_stale_check`              | `1776717741.557769`                                                            |                |                                                                                                                     |
| `idle_last_run_podcast_backfill`                | `1776710530.59738`                                                             |                |                                                                                                                     |
| `idle_last_run_publish_verify`                  | `1776713267.676449`                                                            |                |                                                                                                                     |
| `idle_last_run_quality_audit`                   | `1776708464.8465457`                                                           |                |                                                                                                                     |
| `idle_last_run_sync_newsletter_subscribers`     | `1776717741.527152`                                                            |                |                                                                                                                     |
| `idle_last_run_sync_page_views`                 | `1776717746.5694222`                                                           |                |                                                                                                                     |
| `idle_last_run_threshold_tune`                  | `1776706157.2429378`                                                           |                |                                                                                                                     |
| `idle_last_run_topic_discovery`                 | `1776830816.9902751`                                                           |                |                                                                                                                     |
| `idle_last_run_topic_gaps`                      | `1776662209.1690526`                                                           |                |                                                                                                                     |
| `idle_last_run_utility_rates`                   | `1775425727.9157252`                                                           |                |                                                                                                                     |
| `idle_last_run_video_backfill`                  | `1776698745.4924042`                                                           |                |                                                                                                                     |
| `image_model`                                   | `sdxl_lightning`                                                               |                | Default image generation model (legacy)                                                                             |
| `internal_api_base_url`                         | `http://localhost:8002`                                                        |                | Base URL for the internal worker API (used for self-calls like the podcast feed regen)                              |
| `location_state`                                | `RI`                                                                           |                | Matt location - Rhode Island                                                                                        |
| `log_to_file`                                   | `true`                                                                         |                | Write logs to file                                                                                                  |
| `media_r2_upload_delay_seconds`                 | `240`                                                                          |                | Wait this many seconds after a post publishes before uploading podcast/video/short to R2 CDN                        |
| `memory_stale_last_alerts`                      | `{"shared-context": "2026-04-15T22:13:...`                                     |                |                                                                                                                     |
| `memory_stale_threshold_seconds_openclaw`       | `2592000`                                                                      |                |                                                                                                                     |
| `memory_stale_threshold_seconds_shared-context` | `2592000`                                                                      |                |                                                                                                                     |
| `model_role_image_decision`                     | `ollama/phi4:14b`                                                              |                |                                                                                                                     |
| `newsletter_email`                              | ``                                                                             |                | Newsletter sender email (legacy)                                                                                    |
| `nvidia_exporter_url`                           | `http://host.docker.internal:9835/metrics`                                     |                | nvidia-smi metrics exporter                                                                                         |
| `ollama_base_url`                               | `http://host.docker.internal:11434`                                            |                | Ollama API endpoint                                                                                                 |
| `ollama_client_timeout_seconds`                 | `1500`                                                                         |                |                                                                                                                     |
| `openclaw_gateway_url`                          | `http://localhost:18789`                                                       |                | OpenClaw gateway URL                                                                                                |
| `operator_id`                                   | `operator`                                                                     |                | Default operator ID                                                                                                 |
| `owner_email`                                   | ``                                                                             |                | Site owner email                                                                                                    |
| `owner_name`                                    | ``                                                                             |                | Site owner display name                                                                                             |
| `podcast_description`                           | `AI-development audio essays from Glad...`                                     |                | Podcast RSS description                                                                                             |
| `podcast_name`                                  | `Glad Labs Podcast`                                                            |                | Podcast title for RSS feeds                                                                                         |
| `preferred_ollama_model`                        | `gemma3:27b`                                                                   |                |                                                                                                                     |
| `qa_consistency_veto_threshold`                 | `30`                                                                           |                |                                                                                                                     |
| `qa_preview_screenshot_enabled`                 | `true`                                                                         |                |                                                                                                                     |
| `qa_vision_check_enabled`                       | `true`                                                                         |                |                                                                                                                     |
| `r2_public_url`                                 | `https://pub-1432fdefa18e47ad98f213a8a...`                                     |                |                                                                                                                     |
| `redis_url`                                     | `*(encrypted)*`                                                                | encrypted      | Redis connection URL                                                                                                |
| `sdxl_server_url`                               | `http://host.docker.internal:9836`                                             |                | SDXL image generation server                                                                                        |
| `semantic_dedup_threshold`                      | `0.75`                                                                         |                |                                                                                                                     |
| `sentry_enabled`                                | `true`                                                                         |                | Enable Sentry error tracking                                                                                        |
| `short_video_post_publish_delay_seconds`        | `180`                                                                          |                | Wait this many seconds after a post publishes before kicking off short-video generation (lets podcast finish first) |
| `site_description`                              | `AI-powered content platform`                                                  |                | Longer site description                                                                                             |
| `site_tagline`                                  | `Technology & Innovation`                                                      |                | Short tagline used in metadata                                                                                      |
| `stage_timeout_draft`                           | `1700`                                                                         |                |                                                                                                                     |
| `storage_access_key`                            | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret    |                                                                                                                     |
| `storage_bucket`                                | `gladlabs-media`                                                               |                |                                                                                                                     |
| `storage_endpoint`                              | `https://01ddb679184ebe59cc7f03f8171d7...`                                     |                |                                                                                                                     |
| `storage_public_url`                            | `https://pub-1432fdefa18e47ad98f213a8a...`                                     |                |                                                                                                                     |
| `storage_secret_key`                            | `*(encrypted)*`                                                                | encrypted      |                                                                                                                     |
| `storage_token`                                 | `*(encrypted)*`                                                                | encrypted      |                                                                                                                     |
| `task_timeout_seconds`                          | `2700`                                                                         |                |                                                                                                                     |
| `topic_discovery_manual_trigger`                | `false`                                                                        |                |                                                                                                                     |
| `topic_discovery_min_cooldown_seconds`          | `1800`                                                                         |                |                                                                                                                     |
| `topic_discovery_queue_low_threshold`           | `2`                                                                            |                |                                                                                                                     |
| `topic_discovery_rejection_streak`              | `3`                                                                            |                |                                                                                                                     |
| `topic_discovery_stale_hours`                   | `6`                                                                            |                |                                                                                                                     |
| `tts_acronym_replacements`                      | `{"SOC":"security operations","CRM":"c...`                                     |                |                                                                                                                     |
| `tts_pronunciations`                            | `{"GitFlow":"git flow","GitHub":"git h...`                                     |                |                                                                                                                     |
| `video_feed_name`                               | `Glad Labs Video`                                                              |                | Video RSS feed title                                                                                                |
| `video_server_url`                              | `http://host.docker.internal:9837`                                             |                | Video generation server                                                                                             |

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

| Key                       | Default                       | Classification | Description                                                    |
| ------------------------- | ----------------------------- | -------------- | -------------------------------------------------------------- |
| `cloudinary_api_key`      | `*(encrypted)*`               | encrypted      | Cloudinary API key for SDXL image hosting                      |
| `cloudinary_api_secret`   | `*(encrypted)*`               | encrypted      | Cloudinary API secret for SDXL image hosting                   |
| `cloudinary_cloud_name`   | `dujk7kdhd`                   |                | Cloudinary cloud name                                          |
| `discord_bot_token`       | `*(encrypted)*`               | encrypted      | Discord bot token (OpenClaw)                                   |
| `discord_voice_bot_token` | `*(encrypted)*`               | encrypted      | Discord voice bot token (Poindexter Voice)                     |
| `elevenlabs_api_key`      | `*(encrypted)*`               | encrypted      | ElevenLabs TTS API key (legacy)                                |
| `gitea_password`          | `*(encrypted)*`               | encrypted      | Gitea admin password                                           |
| `gitea_repo`              | `gladlabs/glad-labs-codebase` |                | Gitea repository (owner/name)                                  |
| `gitea_url`               | `http://localhost:3001`       |                | Gitea server URL                                               |
| `gitea_user`              | `gladlabs`                    |                | Gitea username                                                 |
| `grafana_api_key`         | `*(encrypted)*`               | encrypted      | Self-hosted Grafana service account token (used by alert_sync) |
| `grafana_url`             | `http://localhost:3000`       |                | Self-hosted Grafana URL (Cloud retired 2026-05-03)             |
| `notion_api_key`          | `*(encrypted)*`               | encrypted      | Notion API integration key                                     |
| `patreon_account`         | `active`                      |                | Patreon account active — free podcast hosting available        |
| `telegram_bot_token`      | `*(encrypted)*`               | encrypted      | Telegram bot token (brain notifications)                       |

## integrations

| Key                 | Default         | Classification | Description                      |
| ------------------- | --------------- | -------------- | -------------------------------- |
| `devto_api_key`     | `*(encrypted)*` | encrypted      | Dev.to API key for cross-posting |
| `revalidate_secret` | `*(encrypted)*` | encrypted      |                                  |

## memory

| Key                                | Default                       | Classification | Description                                                                                                              |
| ---------------------------------- | ----------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `embedding_collapse_age_days`      | `90`                          |                | GH-81: embeddings older than this many days are eligible for the collapse job. Tune down to compress sooner, up to re... |
| `embedding_collapse_cluster_size`  | `8`                           |                | GH-81: target cluster count per (source_table, age-group) when the collapse job runs k-means over candidate embedding... |
| `embedding_collapse_enabled`       | `false`                       |                | GH-81: master switch for the embeddings collapse job. When true, the scheduled job clusters old rows per source_table... |
| `embedding_collapse_source_tables` | `claude_sessions,brain,audit` |                | GH-81: comma-separated list of source_table values the collapse job is allowed to touch. posts/issues/memory are deli... |

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

| Key                       | Default                      | Classification | Description                                                      |
| ------------------------- | ---------------------------- | -------------- | ---------------------------------------------------------------- |
| `cloud_api_daily_limit`   | `5`                          |                | Max cloud API calls per day in emergency mode (hard cap)         |
| `cloud_api_mode`          | `emergency_only`             |                | Cloud API usage mode: disabled, emergency_only, fallback, always |
| `cloud_api_notify_on_use` | `true`                       |                | Send Telegram alert when a cloud API is used                     |
| `pipeline_critic_model`   | `ollama/gemma3:27b`          |                | Model for QA/content review                                      |
| `pipeline_fallback_model` | `ollama/gemma3:27b`          |                | Fallback model when primary is unavailable                       |
| `pipeline_seo_model`      | `ollama/qwen3:8b`            |                | Model for SEO title/description generation                       |
| `pipeline_social_model`   | `ollama/qwen3:8b`            |                | Model for social media post generation                           |
| `pipeline_writer_model`   | `ollama/glm-4.7-5090:latest` |                | Model for blog content generation (draft phase)                  |

## monitoring

| Key                                  | Default                          | Classification | Description                                                                                                              |
| ------------------------------------ | -------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `grafana_alert_sync_enabled`         | `true`                           |                | Master switch for the brain daemon's Grafana alert sync loop. Set to 'false' to disable the loop entirely without rem... |
| `grafana_alert_sync_interval_cycles` | `3`                              |                | How many brain cycles (5 min each) between Grafana alert syncs. Default 3 = 15 min. Lowering this makes alert rule ch... |
| `grafana_api_base_url`               | `http://poindexter-grafana:3000` |                | Grafana base URL the brain daemon uses to push alert rules and contact points. Defaults to the docker-compose service... |
| `grafana_api_token`                  | `*(encrypted)*`                  | encrypted      | Grafana service-account token (Administration → Service accounts → Add service account → Add token). Required for the... |

## newsletter

| Key                    | Default     | Classification | Description                          |
| ---------------------- | ----------- | -------------- | ------------------------------------ |
| `newsletter_enabled`   | `true`      |                | Enable newsletter sending on publish |
| `newsletter_from_name` | `Glad Labs` |                | Newsletter sender display name       |
| `newsletter_provider`  | `resend`    |                | Email provider: resend or smtp       |

## notifications

| Key                       | Default                                    | Classification | Description                                                                    |
| ------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------ |
| `discord_ops_webhook_url` | `https://discord.com/api/webhooks/1494...` |                |                                                                                |
| `preview_base_url`        | `http://100.81.93.12:8002`                 |                |                                                                                |
| `telegram_alerts_enabled` | `true`                                     |                | Enable/disable Telegram alert notifications                                    |
| `telegram_alert_types`    | `error,critical,deploy,probe_failure`      |                | Comma-separated alert types to send (error,critical,deploy,probe_failure,info) |
| `telegram_chat_id`        | `5318613610`                               |                | Telegram chat ID for all alerts (Matt DM)                                      |

## observability

| Key                           | Default                       | Classification | Description                                 |
| ----------------------------- | ----------------------------- | -------------- | ------------------------------------------- |
| `enable_pyroscope`            | `true`                        |                | Enable Pyroscope continuous profiling agent |
| `enable_tracing`              | `true`                        |                | Enable OTLP trace export                    |
| `otel_exporter_otlp_endpoint` | `http://tempo:4318/v1/traces` |                | OTLP HTTP traces endpoint                   |
| `pyroscope_server_url`        | `http://pyroscope:4040`       |                | Pyroscope ingestion URL for worker agent    |

## pipeline

| Key                                 | Default                                    | Classification | Description                                                                                                              |
| ----------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `approval_ttl_days`                 | `7`                                        |                | Days before unapproved posts are auto-expired                                                                            |
| `auto_publish_threshold`            | `0`                                        |                | Quality score threshold for auto-publishing (0=disabled)                                                                 |
| `brain_auto_cancel_grace_minutes`   | `10`                                       |                | Extra grace period the brain daemon adds on top of stale_task_timeout_minutes before flipping a stuck task to failed.... |
| `content_quality_minimum`           | `75`                                       |                | Minimum quality score to even queue for approval. Below this = auto-reject.                                              |
| `content_weekly_cap`                | `3`                                        |                | Maximum new posts per week (0=unlimited). Topic discovery respects this.                                                 |
| `daily_budget_usd`                  | `5.00`                                     |                | Daily LLM API spend budget in USD                                                                                        |
| `daily_post_limit`                  | `1`                                        |                | Maximum posts to generate per day                                                                                        |
| `default_model_tier`                | `budget`                                   |                | Default model cost tier (free/budget/standard/premium/flagship)                                                          |
| `max_approval_queue`                | `20`                                       |                | Maximum number of posts awaiting approval before throttling generation                                                   |
| `max_posts_per_day`                 | `3`                                        |                | Maximum posts to publish per day                                                                                         |
| `max_task_retries`                  | `3`                                        |                | Maximum retry attempts for failed tasks                                                                                  |
| `max_tokens_per_request`            | `4000`                                     |                | Maximum output tokens per LLM request                                                                                    |
| `max_tokens_per_task`               | `16000`                                    |                | Maximum total tokens (input+output) per content task                                                                     |
| `min_curation_score`                | `75`                                       |                | Minimum QA score to surface for human review (below this = auto-reject)                                                  |
| `pipeline_factcheck_model`          | `programmatic`                             |                | Model for fact-checking -- programmatic or LLM provider                                                                  |
| `pipeline_refinement_model`         | `ollama/glm-4.7-5090:latest`               |                | Model for content refinement (stage 5)                                                                                   |
| `pipeline_research_model`           | `ollama/glm-4.7-5090:latest`               |                | Model for research stage (stage 1)                                                                                       |
| `pipeline.stages.order`             | `["verify_task", "generate_content", "...` |                | Ordered list of Stage names the content pipeline runs                                                                    |
| `publish_spacing_hours`             | `4`                                        |                | Minimum hours between published posts                                                                                    |
| `require_human_approval`            | `true`                                     |                | When true, all content requires human approval before publishing                                                         |
| `seed_url_fetch_timeout_seconds`    | `10`                                       |                | URL-based topic seeding: total HTTP timeout (seconds) for the seed_url fetch on POST /api/tasks. Short by design — if... |
| `seed_url_max_bytes`                | `1048576`                                  |                | URL-based topic seeding: hard cap (bytes) on the decoded response body. Guards against pathological pages that would ... |
| `seed_url_user_agent`               | `Mozilla/5.0 (Windows NT 10.0; Win64; ...` |                | URL-based topic seeding: User-Agent header for the seed_url fetch. Chrome-ish by default because many news/publisher ... |
| `staging_mode`                      | `true`                                     |                | When true, posts go to draft with preview token instead of publishing                                                    |
| `stale_task_timeout_minutes`        | `180`                                      |                | Minutes before a running task is considered stale                                                                        |
| `task_sweep_interval_seconds`       | `300`                                      |                | Seconds between stale task sweeps                                                                                        |
| `worker_heartbeat_interval_seconds` | `30`                                       |                | Worker heartbeat cadence. While processing a single task the TaskExecutor stamps content_tasks.updated_at = NOW() eve... |

## plugins

| Key                                  | Default                                    | Classification | Description                         |
| ------------------------------------ | ------------------------------------------ | -------------- | ----------------------------------- |
| `plugin.job.render_prometheus_rules` | `{"enabled": true, "interval_seconds":...` |                | Config for RenderPrometheusRulesJob |

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

## qa_workflows

| Key                           | Default                                    | Classification | Description                                          |
| ----------------------------- | ------------------------------------------ | -------------- | ---------------------------------------------------- |
| `qa_workflow_blog_content`    | `{"reviewers": ["programmatic_validato...` |                | Blog content QA workflow chain                       |
| `qa_workflow_premium_content` | `{"reviewers": ["programmatic_validato...` |                | Premium QA with LLM critic - all reviewers must pass |
| `qa_workflow_quick_check`     | `{"reviewers": ["programmatic_validato...` |                | Fast validation for bulk content                     |

## quality

| Key                                      | Default | Classification | Description                                                                                        |
| ---------------------------------------- | ------- | -------------- | -------------------------------------------------------------------------------------------------- |
| `multi_model_qa_max_reviewer_error_rate` | `0.5`   |                | Auto-reject when this fraction (>=) of reviewers errored. 0.5 = reject when >=50% errored (gh#162) |
| `qa_critical_dimension_floor`            | `50`    |                | Minimum score on any single quality dimension                                                      |
| `qa_critic_weight`                       | `0.6`   |                | Weight for LLM critic in final score                                                               |
| `qa_final_score_threshold`               | `80`    |                | Multi-model QA final approval score threshold                                                      |
| `qa_overall_score_threshold`             | `80`    |                | Minimum overall quality score to pass QA (0-100)                                                   |
| `qa_validator_weight`                    | `0.4`   |                | Weight for programmatic validator in final score                                                   |

## security

| Key                          | Default         | Classification | Description                                                      |
| ---------------------------- | --------------- | -------------- | ---------------------------------------------------------------- |
| `alertmanager_webhook_token` | `*(encrypted)*` | encrypted      | Bearer token that Alertmanager must send with every webhook POST |

## seo

| Key            | Default                                                                        | Classification | Description                                             |
| -------------- | ------------------------------------------------------------------------------ | -------------- | ------------------------------------------------------- |
| `indexnow_key` | `*(redacted — looks secret-shaped but not classified `is_secret=true` in DB)*` | look-secret    | IndexNow API key for instant search engine notification |

## site

| Key               | Default                   | Classification | Description |
| ----------------- | ------------------------- | -------------- | ----------- |
| `public_site_url` | `https://www.gladlabs.io` |                |             |

## social

| Key                             | Default                                    | Classification | Description                                                                                                              |
| ------------------------------- | ------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `bluesky_app_password`          | `*(encrypted)*`                            | encrypted      | GH-36: Bluesky APP password (NOT the account password). Generate at https://bsky.app/settings/app-passwords. Store as... |
| `bluesky_identifier`            | `*(encrypted)*`                            | encrypted      | GH-36: Bluesky handle or DID used for direct AT Protocol posting (e.g. 'gladlabs.bsky.social'). Empty = Bluesky distr... |
| `mastodon_access_token`         | `*(encrypted)*`                            | encrypted      | GH-36: Mastodon access token with 'write:statuses' scope. Create via Preferences > Development > New Application on y... |
| `mastodon_instance_url`         | ``                                         |                | GH-36: Full Mastodon instance URL, e.g. 'https://mastodon.social'. Empty = Mastodon distribution skipped.                |
| `social_distribution_platforms` | ``                                         |                | GH-36: Comma-separated list of platforms social_poster should push to after a successful publish. Valid values: 'blue... |
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

## webhooks

| Key                      | Default         | Classification | Description                   |
| ------------------------ | --------------- | -------------- | ----------------------------- |
| `openclaw_webhook_token` | `*(encrypted)*` | encrypted      | OpenClaw webhook auth token   |
| `openclaw_webhook_url`   | ``              |                | OpenClaw webhook delivery URL |
