# services/ Audit + Architecture Plan — 2026-05-08

> Comprehensive audit of `src/cofounder_agent/services/`. Triggered after a strategic conversation in which Matt said "we have too much overlap and tech debt right now to be certain of anything." Output is the foundation for the next 4-5 weeks of cleanup work before the official OSS launch.
>
> Filed by: dispatched audit agent (a73f26c79a34b3936) under the `using-superpowers` workflow.
> Cross-references: `project_poindexter_as_business_os.md`, `project_llm_workforce_thesis.md`, `feedback_decision_rubric_money_as_byproduct.md`, `feedback_no_wheel_reinvention.md`, `feedback_finish_migrations.md`.

## Executive summary

1. **Orchestration is 99% legacy / 1% LangGraph.** `template_runner` (LangGraph) ran 15/1430 blog tasks in 30 days, all `dev_diary`. Every other live blog post still flows through `workflow_executor.py` even though the feature surface that routes there (`content_agent` + `custom_workflows_service`) has zero route handlers and zero recent commits. The legacy stack survives because `content_router_service` falls through to it when `template_slug` is null — it's load-bearing by _default_, not by _call site_.
2. **The DI migration in CLAUDE.md is fictional.** CLAUDE.md says "Do NOT write `from services.site_config import site_config` — that module-level singleton was removed in Phase H step 5." The singleton **still exists** at `services/site_config.py:226` and **187 places import it**. The DI seam exists in parallel; both work; nothing forces migration.
3. **Three different "load-bearing" claims in CLAUDE.md are wrong.** (a) `model_router.py`/`usage_tracker.py`/`model_constants.py` were "replaced in #199" but `multi_model_qa.py:28`, `quality_service.py:104`, and `firefighter_service.py:9-20` all still import and call them. ~~(b) `cost_lookup.py` is supposed to wrap `litellm.model_cost`, but `cost_guard.py:74-94` still has 14 hardcoded model prices.~~ **(b) Corrected 2026-05-09 — see Overlap 3 below; the lines are watt-hour energy defaults, not USD prices, so there's nothing to migrate.** (c) `pipeline_gate_history` (#366 Phase 1, "replaces gate-state slice of the dropped pipeline_events table") has 0 rows.
4. **The content pipeline is the only live module — every other "module slot" is empty or scaffolding.** `social_posts=0`, `campaign_email_logs=0`, `voice_messages=0`, `revenue_events=1`, `topic_candidates=3`. `deepeval_rails`/`ragas_eval`/`guardrails_rails`/`rag_engine`/`pipeline_flow` (Prefect) are referenced only by their own tests — pure learning scaffolds per the money-as-byproduct rubric. That's fine, but they should be marked.
5. **The biggest concrete cleanup wins are not in services/.** Three of the largest debt deltas are actually in adjacent dirs: 14 jobs import `utils/gitea_issues.py` (Gitea retired 2026-04-30), the entire `agents/content_agent/` CrewAI tree is a dead path that _forces_ `workflow_executor` to keep existing, and the `content_tasks` view exists only to keep `tasks_db.py:1484-1643` from breaking after #211 Phase 2. These are the leverage points.

## OSS migration status

| OSS adoption                          | Module                                                           | Production traffic                                                                             | Wrapper still re-implements?                                                                                                                                                                                                                    | Status                                                             |
| ------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| **LiteLLM** (provider routing + cost) | `services/llm_providers/litellm_provider.py` (381 LOC)           | All 5 capability tiers cut over per `app_settings` (`plugin.llm_provider.primary.* = litellm`) | PARTIAL — `model_router.py` still routes for `multi_model_qa`/`quality_service`/`firefighter_service`. (Earlier "cost_guard.py:74-94 hardcoded prices" claim was a misread of watt-hour energy defaults — corrected 2026-05-09, see Overlap 3.) | 70% — provider routing partial, cost-tracking done                 |
| **LangGraph** (orchestration)         | `services/template_runner.py` (860 LOC) + `pipeline_templates/`  | 15/1430 = 1.0% (only `dev_diary`)                                                              | YES — `workflow_executor.py` (435 LOC) + `phase_registry.py` (843 LOC) + `phase_mapper.py` + `phases/*` + `task_executor.py` (1376 LOC) all still wired                                                                                         | 1% — see Top overlap #1                                            |
| **Langfuse** (prompt mgmt + traces)   | `services/prompt_manager.py:111-119` (Langfuse client lazy-init) | `langfuse_tracing_enabled=true`, host configured, keys set                                     | NO — but 12 inline-prompt constants bypass it                                                                                                                                                                                                   | 80% — infra wired, prompts not all migrated                        |
| **DeepEval**                          | `services/deepeval_rails.py` (148 LOC)                           | 0 — only test imports                                                                          | NO                                                                                                                                                                                                                                              | Scaffolding (intentional, per `feedback_learning_is_primary_goal`) |
| **Ragas**                             | `services/ragas_eval.py` (142 LOC)                               | 0 — `ragas_judge_model` empty, only test imports                                               | NO                                                                                                                                                                                                                                              | Scaffolding                                                        |
| **LlamaIndex** (`rag_engine`)         | `services/rag_engine.py` (445 LOC)                               | 0 — only test imports                                                                          | NO                                                                                                                                                                                                                                              | Scaffolding                                                        |
| **Prefect**                           | `services/pipeline_flow.py` (396 LOC)                            | 0 — only test imports                                                                          | YES — `content_router_service` runs the same stages without it                                                                                                                                                                                  | Abandoned spike (#206)                                             |
| **Guardrails AI**                     | `services/guardrails_rails.py` (157 LOC)                         | 0 — only test imports                                                                          | NO                                                                                                                                                                                                                                              | Scaffolding                                                        |
| **Pyroscope**                         | (consumed via env, not in services/)                             | `enable_pyroscope=true`                                                                        | n/a                                                                                                                                                                                                                                             | Done                                                               |
| **OpenTelemetry / Sentry**            | `services/sentry_integration.py`, `services/telemetry.py`        | active                                                                                         | n/a                                                                                                                                                                                                                                             | Done                                                               |

## Bucket counts (300 non-migration files)

| Bucket                                  | Count | Headline examples                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ENGINE-CORE (active)                    | ~55   | content*router_service, content_validator, multi_model_qa, qa_gates_db, prompt_manager, settings_service, site_config (the class), cost_guard, cost_lookup, publish_service, quality_service, scheduled_publisher, content_task_store, tasks_db, pipeline_db, content_db, audit_log, embedding_service, embeddings_db, integrations/* registry+dispatcher+secret*resolver, image_service, all 12 stages/*, llm_providers/litellm_provider+ollama_native+dispatcher, ollama_client, ollama_resilience, gpu_scheduler, approval_service, gates/post_approval_gates, posts_approval_service, auto_publish_gate, scheduling_service, idle_worker, worker_service, niche_service, topic\*\*, web_research, research_service, jobs/*morning_brief / db_backup / *, taps/\_ (excl. gitea_issues), template_runner, pipeline_templates/, atom_registry, atoms/\* |
| ENGINE-CORE-MIGRATION-IN-FLIGHT         | 6     | `workflow_executor.py`, `phase_registry.py`, `phase_mapper.py`, `phases/*` (3 files), `custom_workflows_service.py`, `template_execution_service.py`, `workflow_validator.py`, `task_executor.py` (1376 LOC, lives in both worlds), `tasks_db.py` (UPDATEs against the content_tasks view), `pipeline_db.py` (Phase 2 of #211 dual-write still on)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| LEGACY-DEPRECATED (successor exists)    | 7     | `model_router.py` (456 LOC, replaced by `cost_lookup.py` + `llm_providers/litellm_provider.py`), `usage_tracker.py` (237 LOC, same), `model_constants.py` (31 LOC, same), `model_preferences.py` (49 LOC), `social_poster.py` (410 LOC, dead — pipeline_distributions has 0 rows on platform!='gladlabs.io'), `cost_aggregation_service.py` (491 LOC, overlaps `cost_logs` queries already in `cost_lookup`), the dead module-level `site_config = SiteConfig()` in `services/site_config.py:226`                                                                                                                                                                                                                                                                                                                                                        |
| DEAD CODE                               | ~12   | `pipeline_flow.py` (Prefect, 396 LOC, never called), `taps/gitea_issues.py` (Gitea retired), `utils/gitea_issues.py` (Gitea retired, imported by 14 jobs that need rewiring), `media_compositors/` (dir mostly empty stub), `voice_pipecat.py` (247 LOC, replaced by voice_agent_livekit), one of `voice_agent.py` / `voice_agent_claude_code.py` / `voice_agent_webrtc.py` / `voice_agent_livekit.py` is canonical, the other three need a winner, `agents/content_agent/` whole subtree (1500+ LOC), `phases/example_workflows.py` (174 LOC, demo file), `content_tasks` VIEW + the parts of `tasks_db.py` that write through it after #211                                                                                                                                                                                                            |
| OSS-WRAPPER (keep, document)            | 4     | `llm_providers/litellm_provider.py`, `cost_lookup.py`, `prompt_manager.py` Langfuse path, atom_registry+atoms (LangGraph node adapter)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| BUSINESS-OS-MODULE-SLOT (future, empty) | ~6    | The whole `social_adapters/` tree (only Bluesky enabled, 0 actual posts), `newsletter_service.py` (provider=resend but `newsletter_email`/`mastodon_*` empty), `podcast_service.py` (no podcast traffic), `video_service.py` + `video_providers/` (no recent runs), `audio_gen_service.py`, `revenue` slot (revenue_events=1 row)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| PURE-LEARNING SCAFFOLD                  | 5     | `deepeval_rails.py`, `ragas_eval.py`, `guardrails_rails.py`, `rag_engine.py`, parts of `pipeline_architect.py` (#364)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

## Top 10 overlaps

### Overlap 1: orchestration is THREE tracks, not two

- **Path A:** `services/workflow_executor.py:25` (`WorkflowExecutor`) + `services/phase_registry.py` + `services/phase_mapper.py` + `services/phases/*` + `services/custom_workflows_service.py:35` + `services/template_execution_service.py:91`
- **Path B:** `services/task_executor.py` (1376 LOC) — separate legacy executor for the actual blog pipeline path; this is what `content_router_service` ultimately invokes for the 99% legacy traffic
- **Path C:** `services/template_runner.py:1` — `TemplateRunner` (LangGraph)
- **Production-traffic ratio:** Path A = 0%, Path B = 99% (1415/1430 last 30d), Path C = 1% (15/1430)
- **Recommendation:** Path A is dead. Delete `workflow_executor.py`, `phase_registry.py` (843 LOC!), `phase_mapper.py`, `phases/*`, `custom_workflows_service.py`, `template_execution_service.py`, `workflow_validator.py`, `agents/content_agent/` whole subtree. ~3,500 LOC removable in one sweep. Path B (task_executor) keeps running until enough templates exist for Path C to absorb it; that's the real `#356` migration, not the documented one.

### Overlap 2: site_config (Phase H foot-shooting)

- **Path A:** `from services.site_config import site_config` — module-level singleton at `services/site_config.py:226`. **187 imports across the codebase.**
- **Path B:** `services/site_config.py:42` `class SiteConfig` constructed by `main.py` and DI'd via `Depends(get_site_config_dependency)`.
- **Production-traffic ratio:** Both paths run side by side; the singleton is loaded at startup at `services/site_config.py:226` (created empty, never `.load()`d on the singleton path) — meaning every singleton-import callsite reads from a _blank_ config and silently falls back to `os.getenv`. **This is the bug `feedback_module_singleton_gotcha` warns about, materializing at scale.**
- **Recommendation:** Mechanical sweep — replace all 187 `from services.site_config import site_config` with the DI seam. Then **delete line 226** in services/site_config.py. CI guard: lint rule that fails on the import.

### Overlap 3: cost-tracking is dual-headed — CORRECTED 2026-05-09

> **Correction (2026-05-09):** This finding was a misread. Lines 74-94 of
> `cost_guard.py` are NOT hardcoded USD prices — they are watt-hour
> (Wh) inference-energy estimates per 1K tokens, stored in
> `_DEFAULT_CLOUD_ENERGY_WH_PER_1K` and consumed only by
> `_get_energy_per_1k_wh()` → `estimate_cloud_kwh()` for the cost
> dashboard's "is Ollama actually greener than this cloud SKU?" panel.
> The numeric magnitudes (0.1-4.0) are 1-3 orders of magnitude too
> large to be USD-per-1K rates (gpt-4o input is ~$0.0025/1K) — they
> only make sense as Wh.
>
> cost*guard's USD path is separate: `_get_rate()` reads
> `plugin.llm_provider.<provider>.[model.<model>.]cost_per_1k*<dir>\_usd`from app_settings and falls back to`\_FALLBACK_RATE_PER_1K`.
> cost_lookup is the LiteLLM-backed observability path. They no longer
> share a code lane, so there's no drift to reconcile.
>
> Resolution: documented inline in `cost_guard.py:60-83` and
> `cost_lookup.py:19-25`. No production behavior change.

~~**Path A:** `services/cost_guard.py:74-94` — hardcoded model→price dict~~
~~**Path B:** `services/cost_lookup.py:1` — `litellm.model_cost` wrapper~~
~~**Production-traffic ratio:** A is what enforces budgets in the cost_guard middle-ware; B is what publishes to `cost_logs` for observability. They disagree on prices.~~
~~**Recommendation:** `cost_guard` should consult `cost_lookup` instead of its hardcoded table.~~

### Overlap 4: legacy LLM router still load-bearing

- **Path A:** `services/model_router.py` (456 LOC) + `services/usage_tracker.py` (237) + `services/model_constants.py` (31)
- **Path B:** `services/llm_providers/litellm_provider.py` (381 LOC) + `services/llm_providers/dispatcher.py` (176 LOC)
- **Production-traffic ratio:** A is called by `multi_model_qa.py:28-290`, `quality_service.py:104-117`, `firefighter_service.py:9-20`, `atom_registry.py`, `capability_outcomes.py`. B is plumbed through `plugin.llm_provider.primary.*` settings.
- **Recommendation:** Migrate the three load-bearing call sites. Then delete the trio. ~720 LOC removable.

### Overlap 5: approval has 4 surfaces

- approval_service / posts_approval_service / gates/post_approval_gates / auto_publish_gate. Boundaries are real but undocumented.
- **Recommendation:** Document the boundary in `docs/architecture/approval-surfaces.md`. Don't merge — this is real complexity, not duplication.

### Overlap 6: content_tasks view is a backwards-compat zombie

- `tasks_db.py:1484-1643` UPDATE content_tasks against what is now a VIEW.
- pipeline_db.py + content_task_store handle the post-#211 normalized writes.
- **Recommendation:** Migrate `UPDATE content_tasks` to direct pipeline\_\* writes (#211 Phase 3). Then drop the view.

### Overlap 7: notification fan-out is layered (KEEP)

operator_notify (Telegram→Discord→alerts.log) / task_failure_alerts / firefighter_service / brain/alert_dispatcher. Layered, not duplicated. **Recommendation:** Document in `docs/architecture/alerting.md`.

### Overlap 8: voice agents — pick one

`voice_agent.py` (413), `voice_agent_livekit.py` (1002, only one with active 2026-05-06 commits), `voice_agent_claude_code.py` (443), `voice_agent_webrtc.py` (288), plus `voice_pipecat.py` (247).

### Overlap 9: social distribution scaffold

`social_poster.py` + 5 stubs (linkedin/reddit/youtube 30 LOC each = pure shape). 0 social posts ever. Either light it up or delete the stubs.

### Overlap 10: 4 paths writing the same tables

content_db / content_task_store / pipeline_db / tasks_db. After #211 Phase 3, tasks_db disappears.

## Top 10 tech debts

1. `utils/gitea_issues.py:19` + 14 callers — uses retired Gitea tracker.
2. `services/site_config.py:226` + 187 callers — module-level singleton CLAUDE.md says was deleted.
3. ~~`services/cost_guard.py:74-94` — 14 hardcoded model prices.~~ **Withdrawn 2026-05-09:** these are watt-hour energy defaults, not USD prices. See Overlap 3 correction.
4. 12 inline prompt constants violating `feedback_prompts_must_be_db_configurable`.
5. Env-var reads beyond DATABASE_URL violating `feedback_no_env_vars`.
6. `services/pipeline_flow.py` — Prefect adoption (#206) abandoned.
7. `services/agents/content_agent/` — CrewAI legacy keeping `workflow_executor.py` alive.
8. `services/content_tasks` is now a VIEW but `tasks_db.py:1484-1643` writes to it.
9. `pipeline_gate_history` table = 0 rows — typed history migration (#366) created but writes never moved.
10. `services/usage_tracker.py:113` naive pricing parser.

## Inline prompts to migrate to Langfuse

| File:line                                                            | Constant                      |
| -------------------------------------------------------------------- | ----------------------------- |
| `services/multi_model_qa.py:145`                                     | `TOPIC_DELIVERY_PROMPT`       |
| `services/multi_model_qa.py:180`                                     | `CONSISTENCY_PROMPT`          |
| `services/multi_model_qa.py:211`                                     | `QA_PROMPT`                   |
| `services/stages/cross_model_qa.py:65`                               | `QA_AGGREGATE_REWRITE_PROMPT` |
| `services/stages/replace_inline_images.py:78`                        | `SDXL_NEGATIVE_PROMPT`        |
| `services/writer_rag_modes/deterministic_compositor.py:58`           | `_NARRATIVE_SYSTEM_PROMPT`    |
| `services/firefighter_service.py:66`                                 | `_FALLBACK_SYSTEM_PROMPT`     |
| `services/integrations/handlers/retention_summarize_to_table.py:166` | `_SUMMARY_PROMPT`             |
| `services/jobs/collapse_old_embeddings.py:248`                       | `_DEFAULT_SUMMARY_PROMPT`     |
| `services/self_consistency_rail.py:58`                               | `_DEFAULT_SUMMARY_PROMPT`     |
| `services/voice_agent.py:118`                                        | `_DEFAULT_SYSTEM_PROMPT`      |
| `services/podcast_service.py:323`                                    | inline f-string               |

## Phase plan

### Phase 1 — Truth & deletion sweep (~3 days, highest leverage)

CLAUDE.md is wrong about three "completed" migrations. Don't add new code until docs match reality. This unblocks every future session because LLMs read CLAUDE.md as ground truth.

1. **Update CLAUDE.md** so the load-bearing table reflects what's loaded — flag `workflow_executor` as "0 production traffic, deletable", flag the singleton as "still alive, 187 callers, do NOT add new ones."
2. **Delete the dead code:** `services/pipeline_flow.py`, `services/taps/gitea_issues.py`, `services/agents/content_agent/`, `services/phases/example_workflows.py`, 3 of the 4 voice agents, `services/voice_pipecat.py`, `services/social_adapters/{linkedin,reddit,youtube}.py`.
3. **Rewire `utils/gitea_issues.py`** — 14 jobs depend on it; replace with `gh issue create` or no-op stub.
4. **Delete `services/site_config.py:226`** + sweep 187 imports to DI seam.

Estimated LOC removed: ~5,000 — codebase 30% smaller without losing production capability.

### Phase 2 — Finish #199 (~5 days)

Migrate three load-bearing call sites of `model_router` to `LLMProvider` plugin protocol:

- `services/multi_model_qa.py:28,290`
- `services/quality_service.py:104,117,876,886`
- `services/firefighter_service.py:268,285`

Delete `model_router.py` (456) + `usage_tracker.py` (237) + `model_constants.py` (31) + `model_preferences.py` (49) = 773 LOC. ~~Update `cost_guard.py:74-94` to delegate to `cost_lookup.py`.~~ (Withdrawn 2026-05-09 — those lines are watt-hour energy defaults, not USD prices; no change needed. See Overlap 3.)

### Phase 3 — Finish #356 (~7 days)

Cut over remaining 99% of blog traffic to `template_runner` (LangGraph):

1. Build `pipeline_templates/canonical_blog.py` mirroring today's 12-stage pipeline.
2. Flip `content_router_service.py:214` to default to `template_slug='canonical_blog'`.
3. Run a week of dual-write — diff outputs.
4. Delete `task_executor.py` (1376), `workflow_executor.py` (435), `phase_registry.py` (843), `phase_mapper.py` (174), `custom_workflows_service.py` (595), `template_execution_service.py` (395), `workflow_validator.py` (266), `phases/*`. ~5,000 LOC removed. Close #356.

### Phase 4 — Restructure (~3 days)

After Phases 1-3, services/ has ~200 files instead of 300, brain-anatomy boundaries can be drawn cleanly:

```
src/cofounder_agent/services/
├── _engine/              # load-bearing infra
├── content/              # marketing dept (live)
├── ops/                  # operations / observability
├── integrations/         # already isolated
├── _scaffold/            # learning scaffolds (deepeval, ragas, guardrails, rag_engine)
├── _slot_finance/        # empty future module
├── _slot_customer_support/
├── _slot_legal/
├── _slot_sales/
└── migrations/
```

`_slot_*` directories with stub README signal where the next module goes.

### Phase 5 — Module #2 driven by actual pain (ongoing)

Don't pre-build. Triggers:

1. **Finance** — first consulting invoice >15 min manual reconciliation
2. **Customer Support** — first Pro tier subscriber non-trivial question
3. **Legal** — first contract or ToS revision >1 hour
4. **Sales** — passing 2 inbound leads/week

Each module follows the engine-vs-employee contract: under `services/<module>/`, REST + MCP, OAuth scoped, app_settings configurable, Grafana observable, multi-level QA gated.

## Forward architecture proposal

Three principles:

1. **Engine vs employees boundary in code shape.** Engine code under `services/_engine/` with stable contracts. Employee code (Claude Code, OpenClaw, voice agents) lives outside services/ entirely.
2. **Module slots, not modules.** The `_slot_*` directories make the workforce-thesis roadmap a physical artifact.
3. **Scaffolds quarantined, not deleted.** DeepEval/Ragas/Guardrails/LlamaIndex move to `services/_scaffold/` — yes intentional, no traffic, don't load-bear.

## Open questions for Matt

1. **Voice agent canonical pick.** `voice_agent_livekit.py` is the only one with active 2026-05-06 commits, but 5 voice modules coexist.
2. **DeepEval/Ragas/Guardrails — keep as scaffolds or wire into multi-level QA now?**
3. **Social distribution: light up Bluesky or delete the stubs?** All cred infra in place, `social_distribution_platforms=bluesky`, but 0 posts ever.

— Phases 1-4 are decidable from code alone.
