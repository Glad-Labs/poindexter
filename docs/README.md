# Poindexter documentation

This directory holds the full Poindexter documentation set — operator
runbooks, architecture references, integration handbooks, and the
auto-generated config catalog.

If you're reading on the Mintlify docs site, the rendered version with
search and navigation lives at the [welcome page](welcome.mdx). If
you're on GitHub, this index is the table of contents.

The engine is Apache 2.0 and free. A Pro add-on (tuned prompts,
extra dashboards, the full book) lives at
[gladlabs.io/product](https://gladlabs.io/product).

## Start here

- [welcome.mdx](welcome.mdx) — overview + curated reading path
- [architecture/overview.md](architecture/overview.md) — system end-to-end (regions,
  data flow, technology choices)
- [../README.md](../README.md) — project README (quick-start, status)

## Architecture

The shape of the system — pipelines, plugins, modules, retrieval.

- [architecture/overview.md](architecture/overview.md) — top-level architecture
- [architecture/multi-agent-pipeline.md](architecture/multi-agent-pipeline.md) —
  cross-model QA + writer/critic pipeline
- [architecture/anti-hallucination.md](architecture/anti-hallucination.md) —
  prompts + LLM QA + programmatic validator (three independent layers)
- [architecture/database-schema.md](architecture/database-schema.md) —
  every table + migration system
- [architecture/plugin-architecture.md](architecture/plugin-architecture.md) —
  the plugin Protocols (Tap, Probe, Job, Stage, LLMProvider, etc.)
- [architecture/module-v1.md](architecture/module-v1.md) — business
  modules (ContentModule, FinanceModule overlay)
- [architecture/rag-retrieval-stack.md](architecture/rag-retrieval-stack.md) —
  LlamaIndex `BaseRetriever` over pgvector (`rag_engine_enabled`)
- [architecture/niches-and-rag-modes.md](architecture/niches-and-rag-modes.md) —
  per-niche writer modes (TOPIC_ONLY / CITATION_BUDGET / STORY_SPINE /
  TWO_PASS)
- [architecture/cost-tier-routing.md](architecture/cost-tier-routing.md) —
  `cost_tier.{free,budget,standard,premium}.model` resolution
- [architecture/prompt-management.md](architecture/prompt-management.md) —
  UnifiedPromptManager (Langfuse-first, YAML fallback)
- [architecture/poindexter-as-engine.md](architecture/poindexter-as-engine.md) —
  mental model: engine + employees
- [architecture/static-export-pipeline.md](architecture/static-export-pipeline.md) —
  push-only JSON export to S3-compatible storage
- [architecture/url-scraper-ssrf-guard.md](architecture/url-scraper-ssrf-guard.md) —
  SSRF protection on outbound HTTP
- [architecture/worker-container-filesystem.md](architecture/worker-container-filesystem.md) —
  filesystem layout inside the worker container

### Service reference

Per-service deep dives in `architecture/services/`. The catalog index
is in [reference/services.md](reference/services.md).

- [services/content_router_service.md](architecture/services/content_router_service.md)
- [services/content_validator.md](architecture/services/content_validator.md)
- [services/cost_guard.md](architecture/services/cost_guard.md)
- [services/internal_link_coherence.md](architecture/services/internal_link_coherence.md)
- [services/litellm_provider.md](architecture/services/litellm_provider.md)
- [services/model_router.md](architecture/services/model_router.md) —
  tombstone (service deleted 2026-05-08; doc points at replacements)
- [services/multi_model_qa.md](architecture/services/multi_model_qa.md)
- [services/newsletter_service.md](architecture/services/newsletter_service.md)
- [services/prompt_manager.md](architecture/services/prompt_manager.md)
- [services/publish_service.md](architecture/services/publish_service.md)
- [services/qa_gates_db.md](architecture/services/qa_gates_db.md)
- [services/quality_service.md](architecture/services/quality_service.md)
- [services/research-and-web-research.md](architecture/services/research-and-web-research.md)
- [services/settings_service.md](architecture/services/settings_service.md)
- [services/site_config.md](architecture/services/site_config.md)
- [services/social_poster.md](architecture/services/social_poster.md)
- [services/template_runner.md](architecture/services/template_runner.md)
- [services/topic_batch_service.md](architecture/services/topic_batch_service.md)

## Operations (running Poindexter on your machine)

- [operations/local-development-setup.md](operations/local-development-setup.md) —
  end-to-end walkthrough (`poindexter setup`, model pulls, stack startup)
- [operations/fresh-db-setup.md](operations/fresh-db-setup.md) — verifying
  migrations against an empty Postgres
- [operations/cli-reference.md](operations/cli-reference.md) — every
  `poindexter` subcommand
- [operations/environment-variables.md](operations/environment-variables.md) —
  the small set of env vars used for Docker bootstrap (everything else
  lives in `app_settings`)
- [operations/troubleshooting.md](operations/troubleshooting.md) —
  real production incidents we've hit, with symptoms and fixes
- [operations/incident-response.md](operations/incident-response.md) —
  on-call runbook
- [operations/disaster-recovery.md](operations/disaster-recovery.md) —
  per-service recovery, ordered by severity
- [operations/backups.md](operations/backups.md) — Postgres backup +
  restore
- [operations/secret-rotation.md](operations/secret-rotation.md) —
  per-secret runbook (OAuth client secrets, storage credentials, etc.)
- [operations/oauth-grafana.md](operations/oauth-grafana.md) —
  Grafana JWT issuance via the OAuth bridge
- [operations/commit-signing.md](operations/commit-signing.md) — GPG
  signing for contributors
- [operations/ci-deploy-chain.md](operations/ci-deploy-chain.md) —
  how Poindexter itself ships (GitHub Actions + Vercel + local worker)
- [operations/extending-poindexter.md](operations/extending-poindexter.md) —
  how to add Stages, Reviewers, Adapters, Providers, Taps, Probes,
  Jobs, Modules
- [operations/migrations.md](operations/migrations.md) — `YYYYMMDD_HHMMSS_`
  prefix convention + runner contract
- [operations/niche-topic-discovery-operator-guide.md](operations/niche-topic-discovery-operator-guide.md) —
  `poindexter topics` workflow
- [operations/litellm-cutover-rollback.md](operations/litellm-cutover-rollback.md) —
  rollback procedure for the LiteLLM cutover
- [operations/ports.md](operations/ports.md) — the local-services port
  map
- [operations/voice-bridge.md](operations/voice-bridge.md) — LiveKit +
  Whisper + Kokoro voice loop
- [operations/voice-stt-tts.md](operations/voice-stt-tts.md) — STT/TTS
  provider configuration
- [operations/claude-code-permissions.md](operations/claude-code-permissions.md) —
  Claude Code allowlist for autonomous operation

## Integrations

Surface-by-surface integration handbooks — outbound delivery, taps,
publishing adapters, webhooks, retention.

- [integrations/index.mdx](integrations/index.mdx) — integrations
  framework overview (handler registry + declarative tables)
- [integrations/outbound_discord_post.md](integrations/outbound_discord_post.md)
- [integrations/outbound_telegram_post.md](integrations/outbound_telegram_post.md)
- [integrations/outbound_vercel_isr.md](integrations/outbound_vercel_isr.md)
- [integrations/publishing_bluesky.md](integrations/publishing_bluesky.md)
- [integrations/publishing_mastodon.md](integrations/publishing_mastodon.md)
- [integrations/tap_builtin_topic_source.md](integrations/tap_builtin_topic_source.md)
- [integrations/tap_external_metrics_writer.md](integrations/tap_external_metrics_writer.md)
- [integrations/tap_singer_subprocess.md](integrations/tap_singer_subprocess.md)
- [integrations/retention_downsample.md](integrations/retention_downsample.md)
- [integrations/retention_ttl_prune.md](integrations/retention_ttl_prune.md)
- [integrations/webhook_alertmanager_dispatch.md](integrations/webhook_alertmanager_dispatch.md)
- [integrations/webhook_revenue_event_writer.md](integrations/webhook_revenue_event_writer.md)
- [integrations/webhook_subscriber_event_writer.md](integrations/webhook_subscriber_event_writer.md)
- [integrations/setup-gsc-and-ga4.md](integrations/setup-gsc-and-ga4.md) —
  Google Search Console + Analytics 4 setup

## Reference

- [reference/app-settings.md](reference/app-settings.md) — every
  `app_settings` key with default + category + secret classification.
  Auto-generated; rerun `python scripts/regen-app-settings-doc.py`
  to refresh.
- [reference/services.md](reference/services.md) — catalog of every
  service in `src/cofounder_agent/services/`, grouped by responsibility
- [api/index.mdx](api/index.mdx) — REST endpoint inventory

## Where the docs are NOT

Some directories under `docs/` are stripped from the public mirror by
`scripts/sync-to-github.sh` (see
`scripts/ci/check_public_mirror_safety.py::_STRIP_DIR_PREFIXES`):

- `docs/brand/` — brand assets (private)
- `docs/experiments/` — internal experiments scratchpad
- `docs/superpowers/` — internal plans + specs

Several individual operator-overlay docs are also stripped — see the
`_STRIP_FILES` tuple in the same script for the full list.
