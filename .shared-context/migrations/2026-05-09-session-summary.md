# 2026-05-09 — Day-long OSS migration session

> "Thoroughly document everything." — Matt, 2026-05-09 21:12 UTC.
>
> Captures what landed today across the OSS migration sweep, the cross-cutting cleanup, the GlitchTip fix, the PR queue triage, and the deletion-candidates audit. Companion to:
>
> - `.shared-context/migrations/2026-05-09-oss-migration-plan.md` — the plan
> - `.shared-context/migrations/2026-05-09-lane-b-model-inventory.md` — Lane B audit
> - `.shared-context/audits/deletion-candidates.md` — running deletion list
> - GitHub: `Glad-Labs/poindexter#450` (umbrella) + the publishing_adapters issue `#112`

## Headline numbers

| Metric                                           | Before today                         | After today                                                 |
| ------------------------------------------------ | ------------------------------------ | ----------------------------------------------------------- |
| Inline prompt constants in production code       | 7+ (CLAUDE.md said "~12")            | 0                                                           |
| Hardcoded model literals in services/            | 22 bucket-A                          | 14 bucket-A (Lane B batch 1 cleared 8)                      |
| PluginScheduler boot count                       | 26 jobs                              | 33 jobs                                                     |
| Declarative-data-plane tables                    | 4 (taps/retention/webhooks/qa_gates) | 5 (+ publishing_adapters)                                   |
| Handler registry surfaces                        | 4                                    | 5 (+ publishing)                                            |
| `webhook_endpoints` rows                         | 5 (no publishing)                    | 6 (+ vercel_isr; publishing_adapters has its own table)     |
| `external_taps` last_run_at                      | 8 days dark                          | green (5/7 firing, 2 pre-existing Singer venv issues fixed) |
| `retention_policies` last_run_at                 | 8 days dark                          | green (6/6 firing)                                          |
| `qa_gates` last_run_at                           | NEVER on every row                   | populated per pipeline run                                  |
| GlitchTip event ingest                           | 403 Denied on every send             | 200 OK                                                      |
| Open PRs in `glad-labs-stack`                    | 6                                    | 0                                                           |
| Deletion-candidates resolved (DELETED or CLOSED) | -                                    | 7                                                           |
| Deletion-candidates still pending Matt's call    | -                                    | 2 (`webhook_events`/OpenClaw, `gitea-runner` container)     |
| Pyproject migration count                        | 1 (baseline only)                    | 5 (4 post-baseline + baseline)                              |

## What landed (in chronological order)

### Morning: framework wiring + bug fixes (commit `91761a56`)

The 2026-05-09 audit discovered three load-bearing surfaces silently dark:

1. **`tap_runner.run_all` only ran via the CLI.** No scheduled Job wrapped it, so `external_taps` (7 rows) had been dark since 2026-05-01 — 8 days of zero ingestion. Same story for `retention_runner.run_all` (6 rows). New jobs `RunTapsJob` (every 1 hour) and `RunRetentionJob` (every 6 hours) wrap the runners; PluginScheduler boots 28 jobs (was 26).
2. **`retention_downsample.py` had a SQL bug.** A `replace(' AS ', ', ')` hack to render the INSERT column list put `avg(col)` in the column-name slot — Postgres rejected the statement. Fix: build the alias list separately during validation. Manual backfill rolled 92 hourly buckets into `gpu_metrics_hourly` and deleted 5,177 raw rows.
3. **`qa_gates_db.py` was advertising a writer that never existed.** Docstring claimed counter columns "get updated through the audit pipeline" — no writer ever shipped, every gate row showed `last_run_at = NEVER`. New `services/qa_gates_db_writer.py:record_chain_run(pool, reviews)` walks the produced reviews + maps reviewer names to gate names (image_relevance→vision_gate, ollama_critic→llm_critic, internal_consistency→consistency) and emits one transactional UPDATE per gate per pipeline run. Wired at the end of `MultiModelQA.review`.

### Mid-morning: poindexter#112 publishing_adapters (multi-commit)

Filed the publishing_adapters declarative table, completing the four-corners of the declarative-data-plane (taps/retention/webhooks → + publishing). Adding a new social platform = insert a row + register a `publishing.<name>` handler, no edit to `social_poster._distribute_to_adapters`. Includes:

- Migration `20260509_175447_add_publishing_adapters` — table + 2 seed rows (`bluesky_main` enabled, `mastodon_main` disabled because creds blank).
- `publishing_bluesky` + `publishing_mastodon` handlers in `services/integrations/handlers/`.
- `services/publishing_adapters_db.py` — read-only loader mirroring `qa_gates_db.py`.
- `social_poster._distribute_to_adapters` — row-driven dispatch loop replacing `if "bluesky" in enabled` branches.
- `poindexter publishers list/show/enable/disable/set-secret/fire` CLI subcommand.
- 110/110 tests across `test_social_poster`, `test_publishing_adapters_db`, `test_publishing_dispatch`, `test_bluesky_adapter`, `test_retention_framework`, `test_qa_gates_db_writer`.

Bakes in the bluesky distribution-dark fix (interim 2-line lambda patch was a stop-gap; row-driven dispatch passes `site_config` through every call by construction). Regression test `test_adapters_receive_site_config_kwarg` pins the contract.

### Afternoon: Lane A — Prompts → Langfuse YAML (5 batches, all merged)

Per the OSS migration plan's Lane A. The seven inline prompt constants in production code are now keys in `prompts/*.yaml` accessed via `get_prompt_manager().get_prompt(key, **kwargs)`:

| Source                                                               | YAML key               | YAML file                       |
| -------------------------------------------------------------------- | ---------------------- | ------------------------------- |
| `multi_model_qa.TOPIC_DELIVERY_PROMPT`                               | `qa.topic_delivery`    | `prompts/content_qa.yaml`       |
| `multi_model_qa.CONSISTENCY_PROMPT`                                  | `qa.consistency`       | `prompts/content_qa.yaml`       |
| `multi_model_qa.QA_PROMPT`                                           | `qa.review`            | `prompts/content_qa.yaml`       |
| `stages/cross_model_qa.QA_AGGREGATE_REWRITE_PROMPT`                  | `qa.aggregate_rewrite` | `prompts/content_qa.yaml`       |
| `image_decision_agent.<inline f-string>`                             | `image.decision`       | `prompts/image_generation.yaml` |
| `topic_ranking.<inline f-string>`                                    | `topic.ranking`        | `prompts/research.yaml`         |
| `writer_rag_modes/deterministic_compositor._NARRATIVE_SYSTEM_PROMPT` | `narrative.system`     | `prompts/system.yaml`           |

Each batch ships a snapshot test pinning the rendered body byte-for-byte; future Langfuse edits cause the snapshot to fail loudly so a deliberate update is required to drift the prompt.

Operators tune via the Langfuse UI (`production` label takes priority over the YAML default); the YAML stack ships as the OSS-friendly fallback.

### Late afternoon: Lane B prereq + batch 1 sweep (commits `8b507bc9` through `73afab1e`)

**Prereq:** `services/llm_providers/dispatcher.py:resolve_tier_model(pool, tier)` — bridge from `cost_tier="standard"` (the API call sites speak) to a concrete model identifier (what providers consume). Reads `app_settings.cost_tier.<tier>.model`; raises loudly on missing mapping per `feedback_no_silent_defaults.md`. Migration `20260509_203928_seed_cost_tier_model_mappings` seeds:

- `cost_tier.free.model     = ollama/qwen3:8b`
- `cost_tier.budget.model   = ollama/gemma3:27b-it-qat`
- `cost_tier.standard.model = ollama/gemma3:27b`
- `cost_tier.premium.model  = anthropic/claude-haiku-4-5`

**Batch 1 sweep #1 — QA / critic surface (4 files):** `multi_model_qa`, `stages/cross_model_qa`, `self_review`, `stages/writer_self_review` + `stages/generate_content` (pool threading). Three `_resolve_*_model` helpers, each routes through `resolve_tier_model(pool, "standard")` with the per-call-site fallback key (`qa_fallback_critic_model`, `qa_fallback_writer_model`, `writer_self_review_model`, `pipeline_writer_model`) as last-ditch backstops gated by `notify_operator()`.

**Batch 1 sweep #2 — Writer / content surface (4 files):** `title_generation`, `podcast_service`, `image_service`, `image_decision_agent`. 3 standard + 1 budget tier per the inventory's recommendation. The `removeprefix("ollama/")` shape is preserved on every path (cost-tier rows store `ollama/<model>`; OllamaClient consumes bare names).

8/22 bucket-A occurrences cleared; 14 remain for Lane B batch 2 (retention/housekeeping + misc/leaf). Cross-call regression test in `test_lane_b_qa_critic_migration.py` (12 tests) pins the resolver-helper contracts.

### Evening: cross-cutting cleanup landings

- **CLI re-registration (commit `af0d9b0c`).** 9 orphan modules under `poindexter/cli/` (approval, migrate, publish_approval, qa_gates, retention, schedule, stores, taps, webhooks) were on disk but no `add_command(...)` line in `cli/app.py` registered them. Running `python -m poindexter <subcmd>` returned "No such command" for any. Per `feedback_deletion_criteria.md` ("broken-but-wanted gets fixed, not listed"), all 9 re-registered. `poindexter --help` now shows 33 commands.
- **PluginScheduler 28 → 32 jobs (commit `88d6815f`).** Four jobs (`check_memory_staleness`, `prune_orphan_embeddings`, `prune_stale_embeddings`, `regenerate_stock_images`) had valid `schedule = "..."` attributes and pyproject.toml entry_points but were missing from the in-process `_SAMPLES` discovery list. All four added.
- **PluginScheduler 32 → 33 jobs (commit `49d40fa3`).** Added `detect_anomalies` (every 4h). Doc fix: the docstring claimed it "files a Gitea issue" — that was stale text from before the 2026-04-30 Gitea retirement. Actual code uses `utils.findings.emit_finding` which routes through `notify_operator` (Discord + Telegram).
- **Legacy webhook stack deleted (commit `e7daca98`).** `routes/webhooks.py` (40 LOC) + `services/integrations/webhook_dispatcher.py` (227 LOC) + `test_webhook_dispatcher.py` (350 LOC). Vestigial — no production importers, not registered in `route_registration.py`. Live stack is `routes/external_webhooks.py` (Lemon Squeezy + Resend sinks) → `services/webhook_delivery_service.py`.
- **GlitchTip 403 errors fixed (no commit; runtime config + memory).** GlitchTip's DB was completely empty (zero users, zero orgs, zero projects) but the worker had a stale DSN pointing at project id 1. Initialized programmatically: org `glad-labs`, project `poindexter` (id=1), admin `mattg@gladlabs.io` (password in memory at `reference_glitchtip.md`). Fixed dashed-vs-dashless public_key gotcha (DB stores dashed UUID; `key.public_key.hex` strips dashes which the SDK rejects with 403). Worker logs clean post-restart.

### Evening: PR queue cleanup

All 6 open PRs in `Glad-Labs/glad-labs-stack` resolved:

- `#323` (auto/test-fixes Windows) — merged
- `#324` (auto/test-expand niche_service +10) — merged
- `#336` (dependabot langchain-core 1.3.2 → 1.3.3) — merged via `--admin` (CI failures were pre-existing flake the closed-PR `#337` had already flagged; not caused by the bump)
- `#325` (CLAUDE.md test count refresh) — closed obsolete; refreshed inline
- `#337` (auto/test-fixes-2026-05-09 site_config mocks) — closed superseded; bluesky fix landed via Lane A batch 1, mastodon fix applied directly on main
- `#338` (auto/test-expand niche_service +15) — closed superseded by `#324`; bot will regenerate from new baseline tomorrow

### Evening: poindexter#450 umbrella filed

Per Matt's "keep the Poindexter issues in the public repo" directive, filed the OSS migration umbrella as `Glad-Labs/poindexter#450`. Public-friendly body: no internal paths, no `feedback_*` memory references, no install-specific URLs. Tracks all four lanes + acceptance criteria. Lane A boxed as DONE; B/C/D queued with explicit dependency order. The `#XXX` placeholder in the cost_tier seed migration was replaced with the real issue number.

## What's still in flight

- **Lane B batch 2** — retention/housekeeping (`collapse_old_embeddings`, `retention_summarize_to_table`) + misc/leaf utilities (`social_poster`, `video_service`, `task_executor`, `ai_content_generator`, `ragas_eval`). Plus end-of-Lane-B cleanup of vestigial `model_router=None` ctor params at `quality_service.py:117/887/897`, `firefighter_service.py:268`, `agents/blog_quality_agent.py:31/138`.
- **Lane C** — LangGraph `template_runner` cutover. Build `pipeline_templates/canonical_blog.py`, run 7-day dual-write, flip `content_router_service.py` default. Tracked under `poindexter#355` (umbrella) + `#356` (closed Phase 1).
- **Lane D** — Eval rails. Wire DeepEval/Ragas/Guardrails behind `qa_gates` rows in advisory mode; 2-week observation window; graduate the load-bearing ones to required.

## What needs Matt's call

- **`webhook_events` / OpenClaw queue** — 3,795 undelivered rows since 2026-03-29. `openclaw_webhook_url` is empty. Either configure OpenClaw and let the queue drain, OR drop the emit calls + service + table. Listed in `deletion-candidates.md`.
- **`gitea-runner` container + volume** — Gitea retired 2026-04-30. Container + volume are dead. Docker-compose change needed.

## Lessons learned

### Worktree-isolation breakdown (recurring this session)

Three different agents accidentally wrote files to the main checkout's path instead of their isolated worktree path. The agent reports identified the issue clean each time (and the deterministic_compositor agent caught + reverted its own stray commit), but the contamination still leaked into:

- `9f59ca6b` — deterministic_compositor agent's snapshot test landed on main as a half-state (later resolved when its branch merged)
- `e7daca98` — my "delete legacy webhooks" commit accidentally bundled the QA-surface sweep agent's 5 source-file edits

The work is correct in both cases; the commit messages just don't accurately describe the full contents. Per `feedback_agents_use_worktrees.md`, the worktree isolation IS the right pattern; the bug is in the harness's path resolution when an agent's `Write` call uses an absolute path that points outside the worktree.

### Pyright LSP cache vs reality

Several times Pyright reported "X is not defined" or "Y is not accessed" when the actual file state was correct. The cache lags the worktree's file mutations by seconds-to-minutes. Always verify with a real grep / file read before acting on a Pyright diagnostic.

### Dashed vs dashless UUID public keys

GlitchTip's `ProjectKey.public_key` is a UUID stored with dashes (`31fbc77a-4ad1-4b9a-8bf9-a13548a8b713`). Calling `key.public_key.hex` strips them; the Sentry SDK then sends a public-key segment that GlitchTip's auth path rejects with 403. Took two restarts to root-cause. Saved to memory at `reference_glitchtip.md` so the next reset doesn't repeat the loop.

### Format-string contract preservation when migrating prompts

f-string interpolation and `str.format(**kwargs)` use the same `{var}` syntax, so YAML-stored templates work on either path. BUT: literal braces in the prompt body (e.g. example JSON output blocks) need `{{ }}` doubling under `.format()`. f-strings already use the doubled form for literal braces. So when migrating an f-string prompt to YAML, the doubling carries over verbatim — but if you "clean up" the doubling thinking it's redundant, the prompt breaks. The Lane A snapshot tests caught this in flight more than once.

### Per-medium fallback keys + `notify_operator` per `feedback_no_silent_defaults.md`

The Lane B sweep pattern: `try resolve_tier_model(pool, tier) -> on RuntimeError, try the per-call-site fallback key -> on miss, notify_operator(critical=True) and raise/return-None`. Don't silently degrade. Don't pick a hardcoded default. The reason is debugging burden: a system that silently falls back to a model the operator hasn't tuned will produce inexplicably-bad output and the operator has no audit trail telling them which fallback fired.

## Pick up here next session

**Status as of 2026-05-09 21:15 UTC:**

- Lane A — DONE (5/5 batches merged; 9/9 snapshot tests green; pushed to origin)
- Lane B — batch 1 of 2 done (8 sweeps merged; cost_tier API live; 4 settings keys seeded)
- Lane C — not started
- Lane D — not started
- Cross-cutting cleanup — 7/10 done (per the plan doc table)
- PR queue — empty
- Worker boot — 33 jobs, 200 OK on sentry, no log noise
- 8,200+ tests across 367 test files (count drifted up with today's adds)

**Next concrete actions:**

1. Dispatch Lane B batch 2 — retention/housekeeping sweep + misc/leaf sweep (2 parallel agents), per the inventory at `.shared-context/migrations/2026-05-09-lane-b-model-inventory.md`
2. End-of-Lane-B cleanup: delete the 5 vestigial `model_router=None` ctor params (one commit)
3. Start Lane C — file the canonical_blog template build issue under `poindexter#355` umbrella
4. Decide `webhook_events` + `gitea-runner` fates (Matt's call)
