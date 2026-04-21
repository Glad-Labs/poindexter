# Plugin Architecture

**Last Updated:** April 19, 2026
**Status:** 📐 Design — umbrella [#64](https://github.com/Glad-Labs/poindexter/issues/64) v3 locked; child phases [#65-#72](https://github.com/Glad-Labs/poindexter/issues/64#issuecomment-4276031164) rewritten to match
**Scope:** Canonical in-repo reference for how Poindexter is evolving from a handful of god-files into a plugin-shaped system.

> **v3 locked decisions (2026-04-19):**
>
> - **Secrets encryption-at-rest** is core scope (Phase A delivers via pgcrypto), not "known gap."
> - **Tracing + observability instrumentation** (OTel, Tempo, OpenLLMetry) is **deferred** to a post-refactor Phase I. We'll know what to monitor after the refactor is done.
> - **Phase J (`LLMProvider` plugin family)** confirmed as core scope. Swap Ollama → vllm/llama.cpp/TGI/LocalAI by one app_settings row.
> - **Phase A0 (integration harness, #21)** shipped 2026-04-19. Phase A is unblocked.
> - Other v2 gaps (config schema versioning, resource limits, plugin-to-plugin deps) → Phase I.

---

## Problem

Poindexter keeps inventing "feature parent + sources as children" informally in config (`enabled_topic_sources`, `image_primary_source`, `qa_workflow_*`) but the code doesn't match. The result is god files that make every new integration a painful edit:

| File                                            | Lines | What it does                     |
| ----------------------------------------------- | ----- | -------------------------------- |
| `services/content_router_service.py`            | 2776  | Pipeline orchestration           |
| `services/idle_worker.py`                       | 2169  | 20+ housekeeping jobs, hardcoded |
| `scripts/auto-embed.py`                         | 1157  | 6 embedding phases, each bespoke |
| `services/image_service.py`                     | 1143  | Pexels + SDXL + AI-gen fused     |
| `services/topic_discovery.py`                   | 955   | Sources dispatched via if-chain  |
| `brain/health_probes.py` + `business_probes.py` | ~1000 | Flat functions; Protocol unused  |

Every new Tap (Slack, Notion, Gmail), Provider (Midjourney, Flux), Reviewer (plagiarism check), or Adapter (new social platform) requires editing a 1000+ line file instead of dropping in a file. That blocks the plugin ecosystem vision and the $9.99/mo premium overlay.

## Core insight: adopt standards, don't invent

Every custom system originally proposed has a mature, free, OSS equivalent already in-stack or trivially added. The refactor does **not** build a custom plugin framework. Each Protocol wraps a standard.

| What we could build                     | What already exists                             | Chosen because                                      |
| --------------------------------------- | ----------------------------------------------- | --------------------------------------------------- |
| Custom Tap Protocol + pkgutil discovery | **Singer spec** + **setuptools entry_points**   | 100+ published Singer taps; `pip install` semantics |
| Custom Job scheduler                    | **apscheduler** (stores in Postgres)            | One library, no new daemon                          |
| Custom probe framework                  | **Prometheus + Alertmanager** (already running) | Zero new services; Grafana already wired            |
| Custom plugin installer CLI             | **pip + pyproject.toml entry_points**           | Install, update, uninstall, version-pin all free    |
| Custom config overlay format            | **pypi package**                                | Premium overlay = private pypi + license check      |

Net result: ~3000 lines of god-file code deleted, Singer catalog unlocked, zero new services to run, observability consolidated in the Grafana stack.

## The six plugin Protocols

All live under `src/cofounder_agent/plugins/` as the canonical contracts. All discovered via `importlib.metadata.entry_points`.

### 1. `Tap` — data ingestion

```python
class Tap(Protocol):
    name: str
    interval_seconds: int  # 0 = on-demand only
    async def extract(self, pool, config) -> AsyncIterator[Document]: ...
```

**Two flavors of implementation:**

- **Native Python Taps** — for internal sources (our own posts, our own memory files, audit log). Fast, no subprocess overhead.
- **SingerTap wrapper** — wraps any Singer binary (tap-github, tap-slack, tap-gmail, tap-google-analytics). Subprocess + stdout JSON-lines. Unlocks the public Singer catalog with zero per-source code.

Replaces `scripts/auto-embed.py`'s six hardcoded phases.

### 2. `Probe` — state checking

Probes emit **Prometheus metrics**. Alerting moves to **Alertmanager** rules (YAML in `infrastructure/prometheus/alerts/`). The brain daemon becomes an **Alertmanager webhook consumer** — it interprets alerts and decides on auto-remediation versus human escalation; it no longer runs the check loop itself.

This is the biggest conceptual shift in the refactor. Phase D handles it **additive-first**: expose metrics alongside existing probes, verify Alertmanager fires identically, then delete the legacy.

### 3. `Job` — scheduled maintenance

```python
class Job(Protocol):
    name: str
    schedule: str  # cron or interval, handed to apscheduler
    async def run(self, pool, config) -> JobResult: ...
```

apscheduler is the runner. Each Job registers via entry_points. `idle_worker.py` becomes a thin bootstrap that hands jobs to apscheduler's async scheduler. State (`last_run_at`) persisted in Postgres — survival across restarts is free.

### 4. `Stage` — pipeline transformer

Promotes the existing `services/phases/base_phase.py` contract. Stage specializations already partly shipped:

- **`Reviewer(Stage)`** — scores content (programmatic_validator, llm_critic, seo_checker, url_verifier)
- **`Adapter(Stage)`** — publishes to a platform (`social_adapters/` already clean)
- **`Provider(Stage)`** — generates media (Pexels, SDXL, AI-generation, future Midjourney/Flux)

No workflow-engine adoption in this refactor. Pipeline stays hand-rolled. Temporal/Dagster revisit when managing multiple customer pipelines.

### 5. `Pack` — bundled prompts + styles + configs

Not code; data. Distribution via pip:

- **Free (community):** `poindexter-pack-community` on public pypi, AGPL-3
- **Premium:** `glad-labs-pack` on private pypi, license-gated

`pip` already handles install/update/uninstall/version-pin. No custom overlay CLI needed.

### 6. `LLMProvider` — inference backend

```python
class LLMProvider(Protocol):
    name: str
    async def complete(messages, model, **kwargs) -> Completion: ...
    async def stream(messages, model, **kwargs) -> AsyncIterator[Token]: ...
    async def embed(text, model) -> list[float]: ...
```

**Current state:** `services/ollama_client.py` is concrete; `ModelProvider` enum has only OLLAMA + HUGGINGFACE. Hardcoded instantiation.

**After refactor:** Three plugins cover the OSS landscape:

1. **`OpenAICompatProvider`** — generic HTTP client with configurable `base_url` + `model`. One plugin covers Ollama (`/v1` endpoint), llama.cpp server, vllm, SGLang, HuggingFace TGI, LM Studio, LocalAI, and the LiteLLM gateway — all by changing one `app_settings` row. Because OSS inference converged on OpenAI-compat.
2. **`OllamaNativeProvider`** — keeps Ollama-specific features (electricity cost tracking, `/api/embed`, model pull). Default for the out-of-box experience.
3. **`HuggingFaceProvider`** — uses the transformers library directly for on-machine model hosting without a separate server. Lower priority.

Core ships OSS-only. Community plugins can wrap paid providers (Anthropic, OpenAI, Google Gemini, Groq, OpenRouter, etc.) and distribute via pypi. The `OpenAICompatProvider` already reaches some paid vendors by config (OpenRouter, Groq, Together, Fireworks; Anthropic has an OpenAI-compat mode). "No paid APIs" is a default shipping policy, not a contract constraint.

## Plugin discovery: setuptools entry_points

Plugins declare themselves in their `pyproject.toml`:

```toml
[project.entry-points."poindexter.taps"]
gitea = "poindexter_tap_gitea:GiteaTap"
memory = "poindexter_tap_memory:MemoryFilesTap"

[project.entry-points."poindexter.llm_providers"]
openai_compat = "poindexter_llm_openai_compat:OpenAICompatProvider"
```

Poindexter loads them via `importlib.metadata.entry_points(group="poindexter.taps")`. No pkgutil scan, no custom registry, no decorators. This is the same pattern pytest, click, flask use.

- **Install:** `pip install poindexter-tap-slack`
- **Uninstall:** `pip uninstall poindexter-tap-slack`
- **Update:** `pip install -U poindexter-tap-slack`
- **List:** `pip list | grep poindexter-` or `importlib.metadata.entry_points()`

## Config boundary: DB vs file

Poindexter's rule is "everything in the database," with a practical exception for long-standing infra rules.

| Config type                                    | Where                                                                     | Why                                                |
| ---------------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------- |
| Plugin enable / disable / per-install settings | `app_settings` under `plugin.<type>.<name>`                               | Customer-editable at runtime                       |
| Alert thresholds                               | `infrastructure/prometheus/alerts/*.yml`                                  | Product; CI-validated                              |
| Grafana dashboards                             | `infrastructure/grafana/provisioning/`                                    | Product                                            |
| Prometheus scrape targets                      | `infrastructure/prometheus/prometheus.yml`                                | Product                                            |
| Prompt Packs                                   | pypi package; contents loaded into `prompt_templates` DB table on install | Bulk data; user edits individual rows              |
| Secrets (API keys)                             | `app_settings` with `is_secret=true`                                      | Current pattern; encryption-at-rest is future work |
| `bootstrap.toml`                               | `~/.poindexter/bootstrap.toml`                                            | Only `DATABASE_URL` — the chicken-and-egg          |

This split matches Prometheus/Grafana community conventions. Customers forking the repo inherit the canonical setup.

Plugin config shape in `app_settings`:

```json
{
  "key": "plugin.tap.gitea",
  "value": "{\"enabled\": true, \"interval_seconds\": 3600, \"config\": {\"repo\": \"gladlabs/glad-labs-codebase\"}}",
  "category": "plugins"
}
```

One row per plugin instance. Enable/disable is a toggle. Inventory-ing installed plugins is one SELECT.

## Architectural invariants

1. **Everything configurable** — every plugin reads from `app_settings` (runtime) or repo files (infra rules); no hardcoded defaults that can't be overridden.
2. **Everything an adapter of a core functionality** — six Protocols; every feature is one of them.
3. **Everything in the database** — runtime config, plugin state, secrets, metrics history. Only `DATABASE_URL` lives outside.
4. **Drop-in additions** — `pip install` + container restart picks up the new plugin. No central switch statements, no hardcoded plugin lists.
5. **Core is OSS-only, community can extend** — default shipping policy ships free/OSS backends (Ollama, Singer community taps, Prometheus). Community plugins can wrap paid providers and ship on their own pypi packages.
6. **Standards over inventions** — when an OSS standard exists (Singer, OpenAI-compat, apscheduler, Prometheus, entry_points), we wrap it, not reinvent.

## Migration phases

Refer to GitHub issues for the actionable scope. Suggested execution order:

| Phase | Issue                                                    | Dependency | Purpose                                                                                                                                                                                                                             |
| ----- | -------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A0    | [#21](https://github.com/Glad-Labs/poindexter/issues/21) | —          | ✅ **SHIPPED 2026-04-19.** Integration test harness with real Postgres + real Ollama. 6 smoke tests pass.                                                                                                                           |
| A     | [#65](https://github.com/Glad-Labs/poindexter/issues/65) | A0         | Plugin foundation: Protocols, entry_points discovery, PluginConfig reader, apscheduler wrapper, **pgcrypto secrets encryption**, sample migration per type.                                                                         |
| B     | [#66](https://github.com/Glad-Labs/poindexter/issues/66) | A          | Taps: split `auto-embed.py`. Internal Python Taps + SingerTap wrapper shipping a working `tap-gitea` demo.                                                                                                                          |
| C     | [#67](https://github.com/Glad-Labs/poindexter/issues/67) | A          | Jobs: port `idle_worker.py` onto apscheduler. DIY scheduler deleted.                                                                                                                                                                |
| D     | [#68](https://github.com/Glad-Labs/poindexter/issues/68) | A          | Probes: Prometheus metrics + Alertmanager rules, brain daemon becomes Alertmanager webhook consumer. **Additive-first** — 1-2 weeks parallel-run before deleting legacy.                                                            |
| E     | [#69](https://github.com/Glad-Labs/poindexter/issues/69) | A          | Pipeline: #20 split of `content_router`. Stage / Reviewer / Adapter / Provider specializations.                                                                                                                                     |
| F     | [#70](https://github.com/Glad-Labs/poindexter/issues/70) | A, B       | Topic Sources: split `topic_discovery.py`.                                                                                                                                                                                          |
| G     | [#71](https://github.com/Glad-Labs/poindexter/issues/71) | A          | Image Providers: split `image_service.py`.                                                                                                                                                                                          |
| J     | [#72](https://github.com/Glad-Labs/poindexter/issues/72) | A          | LLMProvider plugin family. `OpenAICompatProvider` + `OllamaNativeProvider` as core. Exit criterion: swap Ollama → vllm/llama.cpp/TGI/LocalAI/LiteLLM by one app_settings row.                                                       |
| I     | (future)                                                 | A–J        | Post-refactor observability upgrade (OTel, Tempo, OpenLLMetry, Langfuse eval) + plugin config schema versioning + per-plugin resource limits + plugin-to-plugin deps. **Deferred** — we'll know what to monitor after the refactor. |
| H     | (future)                                                 | A–J        | SaaS lifecycle. Subprocess/container isolation for untrusted community plugins. Web UI CRUD. Deferred until managed SaaS is on the calendar.                                                                                        |

Phase A must be fully shipped before any other phase merges. A half-done plugin framework is worse than the current god files because it creates ambiguity about where new code lives.

## Observability strategy

**In-scope for the refactor (Phase D):** Prometheus metrics + Alertmanager rules + existing Grafana dashboards. Brain daemon pivots from running a probe loop to consuming Alertmanager webhooks. Zero new services to run — everything leans on infra we already have.

**Deferred to post-refactor Phase I:** OpenTelemetry instrumentation, Tempo for distributed traces, OpenLLMetry conventions for LLM-specific spans, Langfuse for prompt playground + eval tooling. Rationale (Matt's 2026-04-19 decision): we'll know what to trace after the refactor is done and we can see where the pain actually lives. Adding tracing before that risks building dashboards for pre-refactor shapes.

**In the meantime:**

- **Logs:** structlog (already in stack). Request-ID propagation added as part of Phase D.
- **LLM cost:** native `cost_logs` table stays — cheapest source of truth for electricity + token counts.

One pane of glass (Grafana). No new vendor UIs.

## Dependency audit

| Dep                                                                                           | Status                                                   | Notes                                        |
| --------------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------- |
| Grafana, Prometheus, Alertmanager                                                             | ✅ In-theme, already running                             | Role unchanged                               |
| Postgres + pgvector, Redis, Ollama, SDXL                                                      | ✅ In-theme                                              | Unchanged                                    |
| apscheduler (new), Singer runtime (new), prometheus_client (new), importlib.metadata (stdlib) | ✅ Apache / MIT / stdlib                                 | Net-new; all tiny libs, zero new daemons     |
| Lemon Squeezy                                                                                 | 💰 Kept                                                  | Payments — intentional business-model choice |
| Vercel                                                                                        | 💰 Kept (free tier)                                      | Public site hosting                          |
| Cloudflare R2                                                                                 | 💰 Kept for Glad Labs; MinIO documented for self-hosters | CDN for podcast/image assets                 |
| Resend                                                                                        | 💰 Kept (free tier 3K/mo)                                | Email                                        |
| Google Analytics                                                                              | ⚠️ Consider Plausible / Matomo for pure-OSS theme        | Only remaining non-OSS piece                 |

Removed in prior sessions: Anthropic, OpenAI, Google Gemini, Railway, Woodpecker. dlvr.it is partial — retired for Bluesky and Mastodon (direct adapters now), retained for X/Twitter cross-posting. Sentry is NOT removed — it's still active in the public-site and worker as the error-tracking layer.

## Known gaps (tracked, not blocking)

**Moved into core scope (Phase A):**

- ~~Secrets encryption-at-rest~~ — now core (pgcrypto) per 2026-04-19 lock-in.

**Deferred to Phase I (future, after refactor ships):**

- Plugin config schema versioning + migrations
- Per-plugin resource limits (CPU/memory quotas)
- Plugin-to-plugin dependency resolution
- Full observability stack (OTel, Tempo, OpenLLMetry, Langfuse)

**Other standing debt (becomes its own issue when needed):**

- Bare `except Exception:` sweep (125 swallowing blocks across services + routes)
- `memory_system.py` dead code (966 lines — pyproject.toml marks it superseded by pgvector)
- `unified_orchestrator.py` + `task_executor.py` vs `content_router_service.py` — which is canonical? Resolve before Phase E.
- Routes decomposition (`task_publishing_routes.py` 1049 lines, `cms_routes.py` 959 lines)
- 50-migration consolidation pass
- Dependency license audit
- Swap Google Analytics for Plausible/Matomo (only remaining non-OSS surface)
- Dependency license audit
- Swap GA for Plausible/Matomo

## Further reading

- [GitHub #64 — umbrella (v2)](https://github.com/Glad-Labs/poindexter/issues/64)
- [GitHub #53 — original plugin ecosystem vision (Taps / Probes / Prompt Packs)](https://github.com/Glad-Labs/poindexter/issues/53)
- [GitHub #20 — content_router split](https://github.com/Glad-Labs/poindexter/issues/20)
- [GitHub #56 — brain daemon pluggable watchdog](https://github.com/Glad-Labs/poindexter/issues/56)
- Brain memory under `plugin refactor v2` tag — `mcp__poindexter__search_memory`
