# OSS Migration Plan — 2026-05-09

> "Make a plan to fully migrate everything over to our OSS stack." — Matt, 2026-05-09 19:25 UTC.
> "Perform another iteration on your plan, make sure to include anything else pertinent here, like alert manager or grafana, how will that still be handled?" — Matt, 2026-05-09 19:33 UTC.
>
> Picks up from `.shared-context/audits/2026-05-08-services-folder-audit.md`. Iteration 2 expands the scope from "the four sweepy lanes" to "the whole OSS landscape," including observability, alerting, storage, frontend, payments, and the SaaS-bridged surfaces.
>
> **Pick-up pointer at the bottom is the only line future-Claude needs.**

## Why this exists

Per `feedback_no_wheel_reinvention.md` + `feedback_learning_is_primary_goal.md` + `feedback_keep_codebase_current.md`: Poindexter's edge is the _operator system_, not the orchestration / model routing / prompts / eval / observability plumbing. Every line of bespoke logic that does what mature OSS already does is a line we maintain forever. The 2026-05-08 audit identified ~5,000 LOC of deletable / migration-eligible surface; ~1,800 LOC has landed. This plan finishes the rest _and_ explicitly addresses the OSS surfaces that are already done so they don't get accidentally re-implemented during the sweep.

## Comprehensive OSS landscape

Every OSS surface running in the stack today, with status and lane assignment:

### Already done — no migration needed (don't touch unless intentional)

| Surface                    | OSS / version                                                                                     | Status                                                                                               | Why settled                                                                                                                                                  |
| -------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Metrics**                | Prometheus (self-hosted, container)                                                               | ✅ Done                                                                                              | Rules rendered from `app_settings` by `RenderPrometheusRulesJob` every 5 min — ALREADY data-driven                                                           |
| **Alert routing**          | Alertmanager → webhook → `webhook_endpoints` row                                                  | ✅ Done                                                                                              | One inbound webhook route, dispatch logic in Python (`alertmanager_dispatch` handler), persists to `alert_events`. Routes are declarative table rows         |
| **Dashboards**             | Grafana 7 dashboards as JSON in `infrastructure/grafana/dashboards/` + provisioning YAML          | ✅ Done (file-as-code, git-versioned)                                                                | JSON-as-code is the standard Grafana-OSS pattern. Runtime-tunable variants would only matter if operators were editing dashboards from the CLI — they aren't |
| **Logs**                   | Loki + Promtail (containers)                                                                      | ✅ Done                                                                                              | Standard Grafana stack, containers running                                                                                                                   |
| **Traces**                 | Tempo                                                                                             | ✅ Done                                                                                              | Wired to Grafana data source                                                                                                                                 |
| **Profiling**              | Pyroscope (per #406)                                                                              | ✅ Done                                                                                              | Worker, brain, voice all ship CPU profiles under `service_name` tags                                                                                         |
| **Errors**                 | GlitchTip (Sentry-OSS-compatible)                                                                 | 🔶 Wired but rejecting events (worker logs show "Unexpected status code: 403, Denied" on every send) | Auth token mismatch — see Cross-cutting #1                                                                                                                   |
| **Uptime**                 | Uptime-Kuma                                                                                       | ✅ Container running                                                                                 | Operator dashboards monitor; no sweep needed                                                                                                                 |
| **VPN**                    | Tailscale                                                                                         | ✅ Done                                                                                              | SaaS, no migration story                                                                                                                                     |
| **Object storage**         | S3-compatible (R2 / B2 / S3 / MinIO)                                                              | ✅ Done                                                                                              | `storage_*` keys in app_settings; provider-agnostic                                                                                                          |
| **Frontend deploy**        | Vercel (Next.js 15 ISR)                                                                           | ✅ Done                                                                                              | Static/SSR slice; OSS framework + SaaS host                                                                                                                  |
| **Public docs**            | Mintlify                                                                                          | ✅ Done                                                                                              | SaaS host                                                                                                                                                    |
| **CI/CD**                  | GitHub Actions                                                                                    | ✅ Done                                                                                              | Cross-repo sync workflow, test/migration smoke, link-rot                                                                                                     |
| **Auth**                   | OAuth 2.1 Client Credentials (custom impl over JWT)                                               | ✅ Done                                                                                              | Dual-auth window closed 2026-05-05 (#249)                                                                                                                    |
| **DB**                     | Postgres (asyncpg) + pgvector                                                                     | ✅ Done                                                                                              | Single source of truth; vector embeddings live in same DB                                                                                                    |
| **Background work**        | APScheduler (`PluginScheduler`)                                                                   | ✅ Done                                                                                              | 28 jobs registered post-2026-05-09; mature lib                                                                                                               |
| **Payments**               | Lemon Squeezy → `webhook_endpoints` inbound                                                       | ✅ Bridged                                                                                           | SaaS provider; inbound webhook handler `revenue_event_writer` writes to `revenue_events`                                                                     |
| **Email**                  | Resend → `webhook_endpoints` inbound                                                              | ✅ Bridged                                                                                           | SaaS provider; inbound webhook handler `subscriber_event_writer` writes to `subscriber_events`                                                               |
| **Voice**                  | LiveKit + voice-agent-livekit container                                                           | ✅ Done                                                                                              | OSS WebRTC + custom agent; ts.net Funnel for public access                                                                                                   |
| **Image generation**       | SDXL (self-hosted GPU container)                                                                  | ✅ Done                                                                                              | OSS model, custom container                                                                                                                                  |
| **Video generation**       | WAN-server (self-hosted)                                                                          | 🔶 Container running, no production traffic                                                          | Per audit: dormant module slot                                                                                                                               |
| **Auto-embedding**         | `auto-embed` container                                                                            | ✅ Done                                                                                              | Periodically embeds posts/issues/audit/memory/brain/claude_sessions                                                                                          |
| **Backups**                | `backup-hourly` + `backup-daily` containers                                                       | ✅ Done                                                                                              | Schedule + offsite documented in `2026-05-08-self-healing-and-backups-audit.md`                                                                              |
| **Declarative data-plane** | 5 tables: external_taps / retention_policies / webhook_endpoints / publishing_adapters / qa_gates | ✅ Done                                                                                              | All sweep landings of the last week (poindexter#103/110/111/112). 14 handlers across 5 surfaces                                                              |

### In flight — sweep needed (the four lanes)

| Surface                    | Current state                                                          | Lane | Effort                            |
| -------------------------- | ---------------------------------------------------------------------- | ---- | --------------------------------- |
| **Prompt management**      | UnifiedPromptManager wired (Langfuse → DB → YAML); 7+ inline prompts   | A    | ~6 focused hours                  |
| **Model routing**          | LiteLLM provider exists; 36 files reference specific model names       | B    | ~2 focused days                   |
| **Pipeline orchestration** | LangGraph template_runner at 1% traffic; 99% on legacy `task_executor` | C    | ~7 focused days + 7 calendar      |
| **Eval rails**             | DeepEval/Ragas/Guardrails scaffolds; 1 advisory rail wired             | D    | ~3-5 focused days + 2 wk advisory |

### Abandoned / candidates for removal

| Surface                                                                                                         | Why                                                                                        | Action                                                              |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| **Prefect** (`prefect-server`/`-redis`/`-services` containers + `pipeline_flow.py`)                             | Per 2026-05-08 audit: "Abandoned spike (#206)." Zero production traffic, only test imports | Add to deletion-candidates with explicit decision before next prune |
| **gitea-runner-data volume**                                                                                    | Gitea retired 2026-04-30                                                                   | Volume + container can be dropped                                   |
| **OpenClaw webhook delivery** (`webhook_events` table + `WebhookDeliveryService` + `emit_webhook_event` helper) | 3,795 undelivered rows; `openclaw_webhook_url` empty; consumer never built                 | Already in `deletion-candidates.md`; awaiting Matt's call           |

## How alerting + observability stay handled (Matt's question)

> "Like alertmanager or grafana, how will that still be handled?"

**Short answer: they're the most data-driven surfaces in the stack already. The migration doesn't touch them.**

### Alertmanager flow (data-driven end-to-end)

```
Prometheus rules (RENDERED from app_settings every 5 min)
            ↓
Alertmanager (single webhook receiver — config in infrastructure/prometheus/alertmanager.yml)
            ↓
POST /api/webhooks/alertmanager (worker)
            ↓
webhook_endpoints row "alertmanager" (declarative — disable by toggling enabled=FALSE)
            ↓
alertmanager_dispatch handler (writes to alert_events table)
            ↓
fan-out via outbound webhook_endpoints rows: discord_ops + telegram_ops
            ↓
Discord + Telegram messages
```

**What stays static-as-file:** `alertmanager.yml` itself (the receiver wiring + group/repeat intervals). That's intentional — Alertmanager doesn't have a runtime-tunable config story without third-party tooling, and the file is 50 lines that change ~once per quarter.

**What's runtime-tunable:**

- Alert thresholds — `app_settings` keys consumed by `prometheus_rule_builder.build_current()`
- Which channels page — `webhook_endpoints` enabled flag per outbound row
- The dispatch logic itself — the Python handler reads `plugin.remediation.<alertname>` from `app_settings` for auto-fix paths

**Result:** the operator can change which alerts page where, what thresholds fire, and what auto-remediation runs, all without touching code. The single static file is the Alertmanager → worker bridge, which is correct because changing it requires a container reload anyway.

### Grafana flow (file-as-code, intentional)

```
Dashboards: 7 JSON files in infrastructure/grafana/dashboards/, provisioned at boot
Datasources: infrastructure/grafana/provisioning/datasources/*.yml
Alerting (Grafana side): infrastructure/grafana/provisioning/alerting/*.yml
```

**Why JSON-as-code, not app_settings-driven:** dashboards are read-mostly artifacts. Operators don't tune them at runtime — they edit JSON, commit, and Grafana picks up on next provision pass. Moving them to app_settings would let the system _generate_ dashboards from settings (e.g., "every new tap row gets a per-tap latency panel") — which IS a thing worth doing eventually, but it's a Lane E concern (see below), not a Lane A/B/C blocker.

**What's already runtime-tunable in Grafana:** dashboard variables (datasource pickers, time ranges, label filters). Standard Grafana feature; no work needed.

## Lane A — Prompts → Langfuse

(unchanged from iteration 1 — keeping it inline so the doc is self-contained)

**Current state:**

- `services/prompt_manager.py` — `UnifiedPromptManager`: Langfuse → DB override → YAML default cascade
- 7 inline prompt constants left in production code:
  - `services/multi_model_qa.py` — `TOPIC_DELIVERY_PROMPT`, `CONSISTENCY_PROMPT`, `QA_PROMPT`
  - `services/stages/cross_model_qa.py` — `QA_AGGREGATE_REWRITE_PROMPT`
  - `services/image_decision_agent.py` — inline f-string
  - `services/topic_ranking.py` — inline f-string
  - `services/writer_rag_modes/deterministic_compositor.py` — `_NARRATIVE_SYSTEM_PROMPT`
- Plus ~5 sub-prompts inside the same files (per CLAUDE.md "~12 prompt constants")

**Per-prompt migration shape:**

1. Move constant to `prompts/<surface>/<name>.yaml`
2. Replace call site with `await prompt_manager.get_prompt("<surface>.<name>", variables=...)`
3. Push to Langfuse via existing path (or lazy-create on first read)
4. Snapshot test pinning rendered output

**Sequencing:**

1. `topic_delivery` + `consistency` + `qa` (multi_model_qa.py) — one batch, one agent
2. `qa_aggregate_rewrite` (stages/cross_model_qa.py)
3. `image_decision`, `topic_ranking`, `narrative_system` — three small parallel dispatches

**Effort:** 30-45 min/prompt. ~6 focused hours total. Up to 3 parallel agents per `feedback_max_3_agents.md`.

**Gates:**

- Snapshot test per prompt
- Langfuse dashboard shows each prompt firing post-migration
- No regression in qa_gate pass/fail rate over 24h

## Lane B — Model routing → LiteLLM cost-tier

**Why parallel to A:** Independent surface — model literal `gemma3:27b` doesn't care if its prompt is YAML or inline.

**Current state:**

- `services/llm_providers/litellm_provider.py` — wrapper exists (381 LOC)
- `services/cost_lookup.py` — wraps `litellm.model_cost`; canonical for cost tracking
- `model_router.py` / `usage_tracker.py` / `model_constants.py` — **deleted 2026-05-08** but vestigial `model_router=None` ctor params remain in `quality_service.py:104` + `firefighter_service.py:268`
- 36 non-test files still embed model strings

**Migration shape:**

1. **Inventory pass.** Categorize the 36 files into:
   - **call sites** that should use `cost_tier=` runtime selection
   - **defaults / fallback constants** that move to `app_settings`
   - **test/util references** (no migration needed)
2. **Settings keys.** Establish 4 canonical tier mappings in `app_settings`:
   - `model.tier.free` (Ollama, ~zero cost)
   - `model.tier.budget` (heavier Ollama OR cheap cloud)
   - `model.tier.standard` (Claude Haiku / Gemini Flash class)
   - `model.tier.premium` (Claude Sonnet / GPT-4 class)
3. **Sweep agents** — one worktree-isolated agent per logical surface (qa, content_router, research, image_decision, etc.)
4. **Cost-guard verification.** Confirm `cost_guard.py` gates all four tiers; smoke test the daily cap with each tier

**Sequencing:**

1. Inventory pass (1-2 hours, single read-only agent)
2. Settings seeding (1 commit, baseline.seeds.sql)
3. Surface-by-surface migration (3-5 dispatches across 1 day)
4. Cost-guard smoke + delete vestigial `model_router=None` params

**Effort:** ~1-2 focused days. **Largest risk:** a sweep that misses a fallback codepath and the system silently falls back to a hardcoded model the operator hasn't tuned. Per `feedback_no_silent_defaults.md`, missing a tier mapping must fail loudly, not pick a default.

**Gates:**

- Inventory categorization committed before any sweep starts
- Each surface ships a regression test that the call site reads from settings, not a literal
- New audit-log event `model_tier_resolved` fires per call; dashboard shows per-tier mix; no tier should be 0% post-migration
- Cost-guard cap test: lower cap to $0.01, fire a publish, confirm rejection

## Lane C — Pipeline orchestration → LangGraph template_runner

**Why it waits for A and B:** template_runner instantiates LangGraph nodes that wrap stage functions. Today those stages read prompts inline and call models inline. Migrating the orchestrator while the inner stages are still being rewritten = rewriting twice. Land A and B; the LangGraph wrap becomes purely structural.

**Current state:**

- `services/template_runner.py` (860 LOC) — LangGraph runner, 1% traffic (`dev_diary` only). Postgres checkpointer enabled.
- `services/pipeline_templates/__init__.py` — has `dev_diary`; needs `canonical_blog`
- `services/task_executor.py` (1376 LOC) — runs 99% of blog production through hardcoded stage chain
- `services/workflow_executor.py` (435 LOC) — 0% traffic but still imported by `content_agent` + `custom_workflows_service` + `template_execution_service`
- Combined deletable surface post-cutover: ~3,500 LOC

**Migration shape:**

1. **Build `pipeline_templates/canonical_blog.py`** — mirrors today's 12-stage chain as LangGraph nodes. Stages already exist as `services/stages/*` modules; the template wires them.
2. **Dual-write phase.** Both `task_executor` (legacy) and `template_runner` (canonical_blog) run every task; `pipeline_diff` audit-log event records mismatches. Run for 7 days minimum.
3. **Cutover.** Flip `content_router_service.py:214` to default `template_slug='canonical_blog'`.
4. **Delete pass.** Remove `workflow_executor.py` chain; delete `task_executor.py` once nothing imports it; update CLAUDE.md "load-bearing services" table.

**Sequencing:**

1. canonical_blog template + nodes (2-3 days)
2. Dual-write infrastructure (1 day)
3. 7-day diff window (calendar)
4. Cutover commit (1 hour)
5. Delete pass (1-2 days, includes test cleanup)

**Effort:** ~7 focused days + 7 calendar dual-write.

**Gates:**

- canonical_blog passes existing pipeline integration tests
- 24h dual-write produces zero diff on at least 5 successful publishes
- 7-day dual-write produces < 1% diff rate (or all diffs explained)
- Cutover gates on /pipeline dashboard panel showing 100% template_runner traffic

## Lane D — Eval rails → DeepEval / Ragas / Guardrails

**Why it waits for C:** Eval results land in Langfuse traces. Until orchestration is unified, traces split across two execution paths. After C, every pipeline run produces one trace all rails hook into.

**Current state:**

- `services/deepeval_rails.py` (148 LOC) — `deepeval_brand_fabrication` advisory rail wired (qa_gates row, 2026-05-08)
- `services/ragas_eval.py` (142 LOC) — scaffold; `ragas_judge_model` empty
- `services/guardrails_rails.py` (157 LOC) — scaffold
- `services/rag_engine.py` (445 LOC) — LlamaIndex wrapper; scaffold

**Migration shape:**

1. Wire each rail behind a `qa_gates` row (mirror brand_fabrication pattern)
2. Each rail starts `required_to_pass=false` (advisory) — feeds weighted score, never vetoes
3. After 2 weeks of advisory data, decide which graduate to required
4. Drop `_scaffold/` prefix per 2026-05-08 audit Phase 4

**Effort:** ~3-5 focused days across the four rails.

**Gates:**

- Each rail produces a Langfuse trace span on every pipeline run
- 2-week advisory data + Matt's call before any rail goes required

## Lane E — Dashboards-as-data (lower priority, parallel-safe)

**Optional follow-on if Matt wants Grafana dashboards generated from app_settings rather than hand-edited.** Not on the critical path.

**Why eventually worth it:**

- Every new tap row could auto-spawn a per-tap latency + records-per-run panel
- Every new publishing adapter could auto-spawn a per-platform success-rate panel
- Operators tune dashboard variables via the CLI: `poindexter dashboards set-tile-threshold ...`

**Why NOT on the critical path:**

- Operators don't currently tune dashboards from the CLI; they grab a JSON snippet from the UI and commit it
- The Grafana terraform-provider + grafonnet ecosystem is mature but invasive — would consume a week of focused work for marginal lift
- Better revisited after Lane C cutover when the dashboard surface is more stable

**Effort:** ~3-5 days if pursued. Defer the decision to post-Lane C.

## Cross-cutting cleanup (alongside, not blocking)

| #   | Cleanup item                                                                                   | Effort | Dependency / lane                                                                                             |
| --- | ---------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------- |
| 1   | Fix GlitchTip 403 errors — worker spamming "Unexpected status code: 403, Denied" on every send | 1 hr   | Auth token mismatch; verify `glitchtip_dsn` + project config, OR drop GlitchTip if redundant with Sentry SaaS |
| 2   | Decide `webhook_events` → OpenClaw queue (3,795 undelivered rows)                              | 1 hr   | Matt's call: kill or configure                                                                                |
| 3   | Re-register or delete 9 orphan CLI modules in `poindexter/cli/`                                | 2 hrs  | Walk the list                                                                                                 |
| 4   | Verify or remove 7 unscheduled jobs (`deletion-candidates.md`)                                 | 3 hrs  | Walk each job's call graph                                                                                    |
| 5   | Delete vestigial `model_router=None` ctor params                                               | 30 min | END of Lane B (when nothing routes through them)                                                              |
| 6   | Drop `from services.site_config import site_config` legacy import                              | 1 hr   | glad-labs-stack#333 — orthogonal but parallel-safe                                                            |
| 7   | Remove `agents/content_agent/` dead tree                                                       | 1 hr   | Lane C cutover                                                                                                |
| 8   | Decide Prefect's fate — retire containers + delete `pipeline_flow.py`?                         | 30 min | Add to deletion-candidates, get Matt's call                                                                   |
| 9   | Drop `gitea-runner` container + volume                                                         | 30 min | Independent — gitea retired 2026-04-30                                                                        |

## Total scope

- **Lane A (Prompts):** ~6 focused hours
- **Lane B (Model routing):** ~2 focused days
- **Lane C (Orchestration):** ~7 focused days + 7 calendar dual-write
- **Lane D (Eval):** ~3-5 focused days + 2 weeks advisory calendar
- **Lane E (Dashboards-as-data):** ~3-5 days, optional, post-C
- **Cross-cutting:** ~1 day spread across the window

**Critical path A → B → C → D = ~3 calendar weeks** assuming gates approve within a day. ~2 weeks if A and B run fully in parallel. Lane E is a deliberate "later" decision.

## What stays bespoke (and why)

Not everything should migrate to OSS. Per `feedback_design_for_llm_consumers.md` + the operator-OS thesis, Poindexter's _edge_ is in the operator UX: the CLI surface, the Telegram/Discord ops loop, the auto-curator + auto_publish_gate decisioning, the niche-discovery + topic_ranking flow. Those are bespoke on purpose. The OSS sweep targets the commodity infra that sits _under_ the operator system, not the operator system itself.

## Pick up here next session

**Status as of 2026-05-09 19:45 UTC:** plan iterated, no lane started.

**Next concrete action:** Dispatch Lane A batch 1 — migrate `multi_model_qa.py`'s three inline prompts (`TOPIC_DELIVERY_PROMPT`, `CONSISTENCY_PROMPT`, `QA_PROMPT`) to YAML + UnifiedPromptManager. Single worktree-isolated agent. Spec includes the existing `prompts/` structure + a snapshot test for each prompt body.

When that lands, dispatch Lane A batch 2 (`cross_model_qa`) + Lane B inventory pass (read-only categorization of 36 model-name files) in parallel.

Cross-cutting #1 (GlitchTip 403) is small enough to slot in alongside Lane A — worker logs are noisy until it's fixed.
