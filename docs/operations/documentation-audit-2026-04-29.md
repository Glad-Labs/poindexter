# Documentation Audit — 2026-04-29

**Scope:** all public-facing documentation in `Glad-Labs/poindexter` (root MDs + `docs/` tree). Conducted post-relicense (AGPL-3.0 → Apache-2.0), post-pricing-consolidation (single Pro tier), and post-OSS-migration sweep (Prefect / Langfuse / Ragas / RAG / DeepEval / guardrails-ai).

**Auditor:** Claude (subagent), commissioned by Matt.
**Files audited:** 36 markdown files (7 root, 29 under `docs/`).
**Skipped:** `node_modules/`, `.next/`, `worktrees/`, `__pycache__/`, `htmlcov/`, `.mypy_cache/`, `.pytest_cache/`, `logs/`, internal `.shared-context/` state, internal `marketing/` copy, internal `brain/` runbooks, internal session memory under `~/.claude/`.

---

## 1. Executive summary

- **Root docs (README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT, CHANGELOG) are in good shape.** Apache-2.0 has propagated, single Pro tier copy is consistent, and the AGPL→Apache transition note is wired into footers. Two minor exceptions noted below.
- **`docs/ARCHITECTURE.md` is the highest-value rewrite target.** It's 539 lines but has internal contradictions (768-dim vs `VECTOR(1536)`), references a deleted file (`services/model_router.py`), points at the dead `gladlabs.ai/guide` URL, and embeds a stale 2-block fictional ER diagram (`tasks` / `agents` / `memories` tables that don't match reality).
- **`docs/architecture/database-schema.md` describes a database that hasn't existed for ~6 months.** It documents `tasks`, `content`, `admin_logs`, `writing_samples` (current names: `content_tasks`, `posts`, `audit_log`, `writing_style_samples`), shows `embedding bytea` instead of `vector(768)`, and covers ~5 of the 100+ tables in the live schema.
- **`docs/reference/services.md` already has a self-aware DOC-SYNC TODO at the bottom (line 163) acknowledging it's stale.** It still uses `*_database.py` for files that have been `*_db.py` for months, lists modules that don't exist, and has no entries for the 50+ services added since (rag_engine, ragas_eval, deepeval_rails, guardrails_rails, self_consistency_rail, gpu_scheduler, citation_verifier, jwt_blocklist_service, etc.).
- **The five `docs/operations/*-2026-04-27.md` snapshot files are explicitly point-in-time records, not living docs.** They should move to `docs/archive/2026-04-27/` (or be deleted). Same applies to the three `docs/architecture/*-2026-04-24.md` files (RFC + audits).
- **Two missing docs are blocked-on commitments already in flight:** `docs/operations/pro-activation.md` (#223 license-activate flow) and an OSS-stack reference doc covering Prefect / Langfuse / Ragas / DeepEval / guardrails-ai (none of these tools are mentioned anywhere in operator-facing docs despite being core to the QA stack).

---

## 2. Per-doc findings

### Root files

#### `README.md` — UPDATE (small)

- **Bucket:** UPDATE (low-effort)
- **Status:** Mostly current. Apache-2.0 badge present, single-Pro pricing aligned, license footer correct.
- **Findings:**
  - Line 6: `tests-5,000+_passing` badge is stale. Code-check shows ~6,400 unit tests in the suite (test-coverage report, line 7). README line 102 also says "5,000+ unit tests passing." **Fix:** bump to `6,400+`.
  - Line 157: same `5,000+ Tests` row in the feature table.
  - Line 162: claims paid-API connectors (Anthropic / OpenAI / Groq / OpenRouter) are "community-plugin territory." But `services/llm_providers/` already ships at least `openai_compat.py` and `ollama_native.py`, and the codebase has shipped Anthropic/Gemini protocol plugins (per session-2026-04-25 recap). Re-check with `services/llm_providers/__init__.py` and either delete the "community-plugin territory" framing or move it to "shipped but disabled by default."
  - Lines 184-202 (Plugins section) is good and matches the plugin-architecture vision.
  - Line 290 keeps the AGPL→Apache reference — keep it for ~12 months then drop.

#### `CONTRIBUTING.md` — UPDATE (medium)

- **Bucket:** UPDATE
- **Status:** Procedurally correct but the _posture_ is still implicitly hostile/inward. The license is now Apache-2.0 — outside contributors should be invited to fork, integrate, and even resell, but the doc reads like an internal team handbook.
- **Findings:**
  - Line 9: `poindexter setup` — correct.
  - Line 16: pre-commit hook installation works.
  - Lines 54-60 ("What we're looking for") is fine but could explicitly call out "new LLMProviders" and "new Stages" as priority targets given the plugin architecture.
  - **Missing:** no language welcoming forks for SaaS resale or commercial integration. Apache-2.0 explicitly permits this; the doc should say so. SUPPORT.md line 24 already does the right thing — copy that language here.
  - **Missing:** no mention of the Singer-tap protocol as a contribution path — line 57 says "Singer tap format preferred" but doesn't link to `docs/integrations/tap_singer_subprocess.md` for the recipe.
  - Line 80: GitHub Security Advisories URL is correct.

#### `SECURITY.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Status:** Mostly current. Last-updated date `2026-04-18` predates the relicense.
- **Findings:**
  - Line 4: bump `Last Updated: 2026-04-18` → `2026-04-29`.
  - Line 30: "All Poindexter API endpoints are protected by Bearer token authentication using the `API_TOKEN` environment variable" — this is partially wrong. The token lives in `bootstrap.toml` AND `app_settings`; `API_TOKEN` env var is a fallback. CLAUDE.md is more accurate at line 152. Either add the bootstrap.toml reference here or drop the "environment variable" framing.
  - Line 56: "Community-plugin API keys (OpenAI-compat, Anthropic, etc.) if you install paid-provider plugins. The core stack is Ollama-only" — out of date. Per CHANGELOG and session recaps, Anthropic + Gemini provider Protocols shipped 2026-04-25 (in `services/llm_providers/`). They're still off by default but they're not "community-plugin" — they're core code.
  - Line 91: claims rate limit of 100 rpm via `RATE_LIMIT_PER_MINUTE` env var. Verify this is still env-driven vs `app_settings` — most other settings have moved to DB.
  - Line 140: keep `sales@gladlabs.io` row but consider dropping `Commercial licensing` from the line — Apache-2.0 doesn't need a commercial license. Keep `support` framing.

#### `SUPPORT.md` — KEEP

- **Bucket:** KEEP
- **Status:** Current. Correctly describes Pro at $9/mo or $89/yr, drops the Commercial License row, calls out Apache-2.0 patent grant + SaaS-resale permission (line 24), AGPL→Apache footer.
- **Minor:** Discord row (line 12) says invite-only — that's a strategic choice, leave as is.

#### `CODE_OF_CONDUCT.md` — KEEP

- **Bucket:** KEEP
- **Status:** Short, professional, no factual claims to drift. No changes needed.

#### `CHANGELOG.md` — KEEP (with one note)

- **Bucket:** KEEP
- **Status:** Authoritative. The 2026-04-29 entry at the top is well-written.
- **Note:** Lines 105-1180 cover the 1.0.0 (2026-03-30) release auto-generated by Release Please. There's a long tail of duplicated commit lines in there (e.g. lines 110-115 are the same `#1024,#605` entry repeated 3x). That's a Release Please hiccup, not an audit issue — flag for cleanup but don't block.

#### `CLAUDE.md` — UPDATE (already filed: GH#186)

- **Bucket:** UPDATE
- **Status:** Stale counts already tracked at GH#186. This doc is excluded from the public sync (per scripts/sync-to-github.sh per CHANGELOG line 80) so it's an internal concern, but the stale counts also leak into other docs (services.md, README.md test count, environment-variables.md key count).
- **Findings:**
  - Line 41: `313 Python files under src/cofounder_agent/services/` — actual count via `find ... | wc -l` is 337. **Updated count:** 337.
  - Line 44: `310 app_settings keys` — `docs/reference/app-settings.md:5` says 296 (as of 2026-04-22). User context says 328. Live DB query needed to confirm but both numbers are stale.
  - Line 43: `6,800+ Python unit tests passing` — `test-coverage-2026-04-27.md:7` says 6,429. Either CLAUDE.md or test-coverage is wrong. Recommend running pytest --collect-only to settle.

### `docs/` root

#### `docs/README.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Findings:**
  - Line 86: `235+ tuned app_settings` claim conflicts with both CLAUDE.md (310) and app-settings.md (296). All three are wrong if the user-context "328" is current. Pick one source and reconcile.
  - Otherwise the doc is a clean index. Pricing is correct, Apache-2.0 footer present.

#### `docs/ARCHITECTURE.md` — REWRITE (HIGH PRIORITY)

- **Bucket:** REWRITE
- **Reason:** Multiple structural issues, not patchable.
- **Findings (line:column references):**
  - Line 9: `https://www.gladlabs.ai/guide` — points to the **dead $29 guide URL**. The Pro product is at `gladlabs.lemonsqueezy.com/checkout/buy/...` (per docs/README.md:9). Replace.
  - Line 47: "Not multi-tenant" — current. Keep.
  - Lines 75-127: ASCII art block has structural problems — line 96 starts a heading mid-block (`### Backend: FastAPI worker (port 8002)`) which is _inside_ the diagram fence. Reads as malformed Markdown.
  - Line 117: "Data Architecture" header is also inside the diagram block. Section structure is broken.
  - Line 198: `6 dashboards, ~90 panels` — verify against current Grafana set. CHANGELOG mentions a 7th `qa-observability.json` dashboard (commit `9ef8cfa3`).
  - Line 206-208: "Anthropic / OpenAI / Google Gemini removed session 55" — re-added in 2026-04-25 plugin sweep (per session_2026_04_25_overnight memory entry). This text is wrong — it claims they're removed, but plugins for all three exist now (off by default).
  - Line 282: `src/cofounder_agent/routes/cms_routes.py` — verify this file still exists. Recent reorg moved many things.
  - Lines 296-318: Database schema "tags / categories / pages / tasks" tables — **these don't match the actual schema**. There's no `pages` table. The `tasks` table is named `content_tasks`. Schema documentation belongs in `database-schema.md`, not duplicated here in a wrong form.
  - Line 334: `src/cofounder_agent/agents/` — agents directory exists but per content-pipeline.md, agents are stateless wrappers, not the architectural primitive. The whole "Agent System Architecture" section (lines 332-356) duplicates and contradicts content-pipeline.md.
  - Lines 374-379: `services/model_router.py` — **THIS FILE DOES NOT EXIST.** Code search confirms. Was deleted in #199 Phase 2 per user context. The text describing its responsibilities (cost-tier routing, electricity tracking) is now done by `services/cost_lookup.py` + provider plugins. Whole section is fiction.
  - Line 396-401: "Semantic Memory" section — `embedding_service.py` exists, but the writer-segregation list doesn't include `samples` (which database-schema.md:24 does include). Reconcile.
  - Line 411-417: API endpoints — only 7 listed, but the doc says elsewhere there are ~70. Either drop this section (point to api/README.md) or list all.
  - Line 502: `embedding VECTOR(1536)` — **wrong dimension.** Actual is 768 (matches line 398 in the same file, plus database-and-embeddings-plan-2026-04-24.md:136). Internal contradiction.
  - Line 519: `M3: Launch Poindexter Pro` — verify status; per CHANGELOG it's already shipped.
- **Recommended scope of rewrite:**
  - Strip the duplicated "agent system" + "model router" + "schema" sections. Replace with one-paragraph pointers to content-pipeline.md, services.md, and database-schema.md.
  - Fix the ASCII diagrams (currently malformed).
  - Update the LLM provider table to reflect the post-2026-04-25 reality (Ollama default + Anthropic/Gemini opt-in).
  - Rewrite the request-flow section using the actual 12-stage chunk model (already correct in content-pipeline.md).
  - Remove the gladlabs.ai/guide URL.

#### `docs/api/README.md` — UPDATE (medium)

- **Bucket:** UPDATE
- **Findings:**
  - Line 86: `"version": "0.2.0"` — verify against current `pyproject.toml`. (Not load-bearing but stale-version-creep is a smell.)
  - Line 87: timestamp uses 2026-04-17. OK to leave.
  - Lines 60-66 (Public Endpoints) lists 5 endpoints. Spot-check vs `routes/` to ensure all 5 are still public and match current paths.
  - **Per CHANGELOG (line 45):** API endpoint reference was "expanded from 7 rows to 28" on 2026-04-23. Verify the 28 are all still present and accurate against the 70-endpoint surface mentioned in ARCHITECTURE.md:179.
  - Add a section on the new approval-gates / publish-gates routes (per migrations 0098, 0100). These are operator-facing endpoints not yet documented.
  - **Missing:** webhook routes, integrations dispatch, retention, and experiments routes are not covered. The declarative-data-plane RFC introduced a slate of new endpoints (`/api/integrations/*` etc.) — none documented here.

### `docs/architecture/`

#### `docs/architecture/multi-agent-pipeline.md` — KEEP (as pointer)

- **Bucket:** KEEP
- **Status:** Already gutted to a 33-line pointer doc per CHANGELOG line 48. Title says "(retired)". Existence is intentional for backlink stability.
- **No action.**

#### `docs/architecture/content-pipeline.md` — KEEP (one tiny update)

- **Bucket:** KEEP
- **Status:** This is the highest-quality architecture doc in the tree. Code references include line numbers; layout is operator-friendly.
- **Tiny:** Last-updated `2026-04-21` is two weeks stale. Confirm everything still matches latest `stage_runner.py` and update the timestamp. Per migrations and code, the 12-stage chain is still current; no structural drift.

#### `docs/architecture/plugin-architecture.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Status:** Excellent design doc. Last-updated `2026-04-19`. The "v3 locked decisions" section is current and matches the codebase trajectory.
- **Findings:**
  - Line 4: roadmap status — verify Phase A status (was "shipped 2026-04-19") and update what's now in Phase D / Phase H given they were referenced as in-flight.
  - Line 200: "Phase I (deferred)" mentions Langfuse for prompt playground. Per user context, Langfuse is now actively integrated. Update Phase I from "deferred" to "in flight" or "shipped" as appropriate.
  - Line 209: same — "Adding tracing before [the refactor] risks building dashboards for pre-refactor shapes." If Langfuse is now wired up, that decision was reversed.
  - Line 244: "Full observability stack (OTel, Tempo, OpenLLMetry, Langfuse)" listed as "what we're explicitly NOT building yet." If Langfuse shipped, move it out of this list.

#### `docs/architecture/database-schema.md` — REWRITE (HIGH PRIORITY)

- **Bucket:** REWRITE
- **Reason:** Schema diverged so far that patching individual tables is harder than starting over.
- **Findings:**
  - Line 7: "migrations/" path is `src/cofounder_agent/migrations/` — actually the live path is `src/cofounder_agent/services/migrations/` (103 files numbered 0000-0103). Both paths probably exist but the doc points to the wrong one.
  - Line 17: "6 specialized database modules" — services.md says 7 (adds `content_task_store.py`). Reconcile.
  - Line 24: writer segregation includes `samples` — ARCHITECTURE.md:399 omits it. Pick one.
  - Lines 38-55 (`users` table): plausibly current.
  - Lines 62-82 (`content_tasks` table): the `CREATE INDEX` statements at 79-81 reference `tasks(...)` — typo; should be `content_tasks`.
  - Lines 88-114 (`content` table): **does not exist as `content`.** The actual table is `posts` (per CLAUDE.md:175 and search of `INSERT INTO`). Whole section is fiction.
  - Lines 121-135 (`admin_logs` table): no such table — actual is `audit_log`. Fiction.
  - Lines 142-156 (`writing_samples` table): plausibly the `writing_style_samples` table; column `embedding bytea` is wrong — actual is `vector(768)` per migration 0103.
  - **Missing:** posts (the real one), pipeline*tasks, app_settings, embeddings, brain*\*, audit_log, cost_logs, page_views, alert_events, revenue_events, subscriber_events, content_revisions, integration_endpoints, webhook_endpoints, retention_policies, external_taps, qa_gates, experiments, media_assets, prompt_templates, schema_migrations, and ~80 more tables. The doc covers ~5% of the schema.
- **Recommended scope of rewrite:**
  - Generate from live DB via `\d+` against each table.
  - Group by domain (content, infrastructure, observability, plugin-config).
  - Same auto-drift convention as `app-settings.md` (note the regen script).

#### `docs/architecture/database-and-embeddings-plan-2026-04-24.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-24/database-and-embeddings-plan.md`
- **Reason:** Frozen-in-time plan doc. Status header (line 4) says "Phase 1 shipped (migration 0082). Phase 4 → GH-110 (deferred). Phase 5 → GH-111 (deferred)." Useful as reference, not as living doc.

#### `docs/architecture/declarative-data-plane-rfc-2026-04-24.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-24/declarative-data-plane-rfc.md`
- **Reason:** RFC explicitly says "Phase 0 + Phase 1 in flight" (line 4). Once those phases are done, the integrations/ docs will carry the operator content; the RFC stops being load-bearing.

#### `docs/architecture/gh-107-secret-keys-audit-2026-04-24.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-24/gh-107-audit.md`
- **Reason:** Audit snapshot. Line 104 already says "3 of 4 resolved 2026-04-28 (#156); `smtp_password` remains gated." Once #156 ships fully, this doc is purely historical.

### `docs/integrations/`

#### `docs/integrations/README.md` — KEEP

- Index doc for the handler folder. Current.

#### `docs/integrations/setup-gsc-and-ga4.md` — KEEP

- 259-line operator runbook for Google Search Console + GA4 OAuth. Looks complete and procedurally correct. Spot-check the OAuth-helper script path on next manual setup but no obvious drift.

#### `docs/integrations/outbound_discord_post.md` — KEEP

- Handler doc. Code references look current.

#### `docs/integrations/outbound_telegram_post.md` — KEEP (verify next time used)

#### `docs/integrations/outbound_vercel_isr.md` — KEEP (verify against #175)

#### `docs/integrations/retention_downsample.md` — KEEP (matches deferred GH-110)

#### `docs/integrations/retention_ttl_prune.md` — KEEP

#### `docs/integrations/tap_builtin_topic_source.md` — KEEP

#### `docs/integrations/tap_external_metrics_writer.md` — KEEP

#### `docs/integrations/tap_singer_subprocess.md` — KEEP

- Solid recipe doc. Aligns with CONTRIBUTING.md "Singer tap format preferred."

#### `docs/integrations/webhook_alertmanager_dispatch.md` — KEEP

#### `docs/integrations/webhook_revenue_event_writer.md` — KEEP

#### `docs/integrations/webhook_subscriber_event_writer.md` — KEEP

> **Cross-cutting note for integrations/:** all 12 handler docs assume the declarative data plane (RFC 2026-04-24) has shipped. README.md line 17 says "Once Phase 1 lands, expect..." — verify Phase 1 shipped before treating these as current operator-facing docs. If Phase 1 hasn't shipped, mark the headers with a "Status: Design — phase X" banner.

### `docs/operations/`

#### `docs/operations/cli-reference.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Status:** 664 lines, comprehensive, recently regenerated (2026-04-23). Should match the live CLI tree per CHANGELOG line 45.
- **Findings:**
  - Verify `poindexter premium activate` (line 322) flow against the #223 license-activate work-in-progress. If #223 ships a new flow, update.
  - Line 25: `sprint` command — flagged as "Glad Labs internal." Should this stay in the public CLI or be excluded from the public sync? Per the sync-to-github.sh exclusions list in CHANGELOG, internal-only commands should be split.

#### `docs/operations/commit-signing.md` — KEEP

- 138-line walkthrough. Procedurally correct.

#### `docs/operations/disaster-recovery.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Findings:**
  - Last-updated `2026-04-17` is 12 days stale.
  - Line 25: backup path `~/.poindexter/backups/` — verify this is still where db-backup.ps1 writes. Script was renamed during the rebrand from `gladlabs` → `poindexter`.
  - Line 26: PowerShell `db-restore.ps1` script reference — confirm path. `scripts/db-backup.ps1` exists; no `db-restore.ps1` in the listing. **Filed as bug or doc fix?**
  - The 9-container list elsewhere in operations matches actual stack.

#### `docs/operations/environment-variables.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Findings:**
  - Line 10: `310 keys as of April 2026` — same drift as elsewhere; reconcile.
  - Line 50: `DEFAULT_OLLAMA_MODEL=auto` — confirm this is still a valid bootstrap-layer env var; most model selection has moved to app_settings.
  - Line 91: `RATE_LIMIT_PER_MINUTE` env var still required? Or moved to app_settings?
  - Generally the list is short and should stay short — that's the design intent.

#### `docs/operations/extending-poindexter.md` — KEEP (verify next-pass)

- 542 lines, code templates per Protocol. Consistent with plugin-architecture.md.
- Line 526: `5,000+ test suite is the moat` — bump to `6,400+`.

#### `docs/operations/local-development-setup.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Findings:**
  - Line 99: `gladlabs-gitea` and `gladlabs-gitea-runner` — per CHANGELOG line 58, internal containers (gitea, woodpecker) keep their legacy names. OK to keep.
  - Line 150: `Expected: ~5,097 passing` — drift; current count 6,429 per test-coverage report.

#### `docs/operations/troubleshooting.md` — UPDATE (small)

- **Bucket:** UPDATE
- **Findings:**
  - Mostly current with real production issues.
  - Line 56: references `task_id='<the-uuid>'` — verify `content_tasks.task_id` vs `content_tasks.id` (some renames happened).
  - Could add 2-3 entries for issues from the 2026-04-27 silent-failures audit (the brain-daemon heartbeat false-positive bug; #144 wan-server pre-unload).
  - Line 23: still references `npm run test:ci` — verify command still exists in package.json.

#### `docs/operations/ci-deploy-chain.md` — UPDATE (medium)

- **Bucket:** UPDATE
- **Findings:**
  - Line 18: "Gitea main (source of truth, local to Matt)" — per user note, **Matt may drop Gitea soon.** Doc is durable until then.
  - The whole flow diagram (lines 17-37) is currently accurate but will become misleading the moment Gitea is deprecated. Mark with a "current as of date" banner so readers know this can change.
  - Line 77: `~5,097` test count — bump.

#### `docs/operations/migrations-audit-2026-04-27.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-27/migrations-audit.md`
- **Reason:** Snapshot doc. Numbered (0000-0097) is stale already (current head is 0103). Useful as historical reference.

#### `docs/operations/overnight-2026-04-27-summary.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-27/overnight-summary.md`
- **Reason:** Daily session digest. Not a living doc.

#### `docs/operations/public-site-audit-2026-04-27.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-27/public-site-audit.md`
- **Reason:** Audit snapshot.

#### `docs/operations/silent-failures-audit-2026-04-27.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-27/silent-failures-audit.md`
- **Reason:** Audit snapshot.

#### `docs/operations/test-coverage-2026-04-27.md` — ARCHIVE

- **Bucket:** ARCHIVE → `docs/archive/2026-04-27/test-coverage.md`
- **Reason:** Coverage snapshot. The coverage report is regenerated regularly; freezing one in `docs/operations/` is misleading.

### `docs/reference/`

#### `docs/reference/app-settings.md` — UPDATE (medium)

- **Bucket:** UPDATE
- **Findings:**
  - Line 5: `296 active rows across 34 categories` — stale. Live count ~328 per user context.
  - Line 7: notes the doc is `excluded from the public Poindexter sync`. **Verify** — many of these app_settings keys are operator-tunable and there's a real argument for shipping the catalog publicly with secret values redacted. The doc itself even says it's "auto-generated from operator state. Not safe to publish outside the private mirror." If that exclusion is intentional, fine — but the user's task description includes it as "do public users / contributors / customers read this?" Decision needed.
  - Auto-regen script reference (`scripts/regen-app-settings-doc.py`) — confirm script exists and works.

#### `docs/reference/services.md` — REWRITE (HIGH PRIORITY) or AUTO-REGEN

- **Bucket:** REWRITE (best path: build a regen script like app-settings.md has)
- **Status:** Self-aware staleness — line 163 has an HTML comment listing 10+ files that don't match reality.
- **Findings:**
  - Lines 35-41 (database modules): every module name is wrong — `admin_database.py` should be `admin_db.py`, `content_database.py` → `content_db.py`, `tasks_database.py` → `tasks_db.py`, `users_database.py` → `users_db.py`, `writing_style_database.py` → `writing_style_db.py`, `embeddings_database.py` → `embeddings_db.py`. ALL six are wrong.
  - Lines 60-65 (image): `image_generation_config.py`, `image_selection_service.py`, `image_prompt_builder.py`, `image_generation_runner.py` — none exist. Real files: `image_service.py`, `image_decision_agent.py`, `image_style_rotation.py`, plus `image_providers/` subdir.
  - Lines 69-72: `quality_evaluation.py` listed as a top-level service — it's actually `services/stages/quality_evaluation.py` (a stage). `quality_checker.py` doesn't exist (services.md flagged it).
  - Line 78: `html_sanitizer.py` — doesn't exist.
  - Line 79: `slugify_service.py` — doesn't exist as a service file.
  - Line 84: `media_script_generator.py` — verify; not in directory listing.
  - Line 85: `transcription_service.py` — doesn't exist.
  - Line 100-101: `rag_embeddings_service.py`, `vector_similarity_search.py` — don't exist. **The actual RAG layer is `services/rag_engine.py`** (just shipped, not documented).
  - Line 106: `stateless_decision_handler.py` — doesn't exist.
  - **Missing entries (services that exist but aren't catalogued):** `rag_engine.py`, `ragas_eval.py`, `deepeval_rails.py`, `guardrails_rails.py`, `self_consistency_rail.py`, `gpu_scheduler.py`, `citation_verifier.py`, `jwt_blocklist_service.py`, `llm_providers/` (whole subpackage), `topic_sources/`, `taps/`, `tts_providers/`, `audio_gen_providers/`, `caption_providers/`, `media_compositors/`, `publish_adapters/`, `social_adapters/`, `image_providers/`, `video_providers/`, `phases/`, `stages/`, `experiment_service.py`, `webhook_delivery_service.py`, `prometheus_rule_builder.py`, `pipeline_throttle.py`, and ~50 more.
- **Recommended:** rather than hand-rewrite, generate from the live filesystem with a script (`scripts/regen-services-doc.py` mirroring the app-settings approach). This file shouldn't be hand-maintained — there are 337 files and counting.

### `docs/experiments/`

#### `docs/experiments/launch-drafts-2026-04-24.md` — UPDATE (or ARCHIVE)

- **Bucket:** UPDATE → eventually ARCHIVE once launch is shipped.
- **Findings:**
  - Line 254: `Pro: https://gladlabs.ai/guide` — **dead URL**, same one ARCHITECTURE.md hits. Replace.
  - Line 56: `5,000+ tests` — bump.
  - Line 218: same.
  - Line 270: "hid the prompts behind SaaS paywalls" — the launch copy itself critiques paywalls; under Apache-2.0 this is a great selling point. Keep.
  - **Recommendation:** once the actual Show HN launch happens, archive this file with a note about whether the actual posts matched the drafts.

#### `docs/experiments/pipeline-tuning.md` — KEEP

- 507-line living log. Append-only. No drift problems.

---

## 3. Recommended GitHub issue list

Each issue listed against `Glad-Labs/poindexter` (the public OSS repo). All P2 unless flagged.

### P1 (blocks accuracy of public-facing docs)

1. **[P1] Rewrite `docs/architecture/database-schema.md`** — current doc covers ~5% of the live schema and uses tables that haven't existed in 6 months (`tasks`, `content`, `admin_logs`). Acceptance: regenerated doc covers all 100+ tables, organized by domain (content, infra, observability, plugin-config), with `vector(768)` columns shown correctly. Bonus: an auto-regen script.

2. **[P1] Rewrite or regenerate `docs/reference/services.md`** — known-stale per HTML comment at line 163. ALL 6 database module names wrong, ~15 services listed that don't exist, ~50+ services that exist but aren't catalogued. Acceptance: doc covers all 337 files in `services/` (or all top-level services + subdirectory entries), generated by a script that runs on every doc build.

3. **[P1] Rewrite `docs/ARCHITECTURE.md`** — multiple internal contradictions, references deleted code (`services/model_router.py`), points at dead URL (`gladlabs.ai/guide`), embeds wrong dimension (`VECTOR(1536)` vs 768), and duplicates database-schema content with wrong table names. Acceptance: malformed ASCII diagrams fixed, dead URLs removed, model_router section deleted (replaced with pointer to llm_providers/), schema section deleted (point to database-schema.md), VECTOR dimension corrected, "Anthropic/OpenAI/Gemini removed" rewritten to reflect they're back as opt-in plugins.

### P2 (drift cleanup, mostly mechanical)

4. **[P2] Update test-count and service-count drift across all docs** — README.md (5,000+ → 6,400+), CLAUDE.md (313 → 337 services, 310 → 328 settings), local-development-setup.md (~5,097 → 6,429), ci-deploy-chain.md, extending-poindexter.md, app-settings.md (296 → 328), launch-drafts-2026-04-24.md, services.md, environment-variables.md. Acceptance: all numeric drift reconciled to a single source of truth (live DB query for settings, `pytest --collect-only` for tests, `find` for service count) and the values match across every doc.

5. **[P2] Document the OSS stack (Prefect / Langfuse / Ragas / DeepEval / guardrails-ai)** — none of these tools are mentioned in operator-facing docs despite being core to the QA stack as of 2026-04-25. Acceptance: new `docs/architecture/qa-stack.md` covers all five tools, what each one is responsible for, where it sits in the pipeline (which Stages call which library), and how to disable/configure each via app_settings. Also update plugin-architecture.md Phase I status (Langfuse moved from "deferred" to "shipped").

6. **[P2] Add `docs/operations/pro-activation.md`** — promised by #223. Should cover: the `poindexter premium activate <license-key>` CLI flow, what app_settings it sets, what content/dashboards/prompts it unlocks, troubleshooting offline activation failures. Acceptance: doc lands in same PR as the #223 implementation.

7. **[P2] Move 8 dated snapshot docs to `docs/archive/`** — `docs/operations/migrations-audit-2026-04-27.md`, `overnight-2026-04-27-summary.md`, `public-site-audit-2026-04-27.md`, `silent-failures-audit-2026-04-27.md`, `test-coverage-2026-04-27.md`, `docs/architecture/database-and-embeddings-plan-2026-04-24.md`, `declarative-data-plane-rfc-2026-04-24.md`, `gh-107-secret-keys-audit-2026-04-24.md`. Acceptance: files move to `docs/archive/2026-04-27/` and `docs/archive/2026-04-24/`; index doc at `docs/archive/README.md` links each with a one-line purpose; relative links from main docs updated.

8. **[P2] Update CONTRIBUTING.md with welcoming language for Apache-2.0 forking** — explicitly invite forks for SaaS resale and commercial integration, add Singer-tap contribution recipe link, mention LLMProvider plugin family as priority extension target. Acceptance: SUPPORT.md line 24 language ported in; new "Permitted uses" or "Welcome contributions" section.

9. **[P2] Update SECURITY.md last-updated date and three drift items** — `2026-04-18` → `2026-04-29`; clarify api_token comes from bootstrap.toml + app_settings (not just env var); rewrite line 56 to reflect Anthropic/Gemini providers shipping in core (off by default); verify rate-limit env var hasn't moved to app_settings.

10. **[P2] Update `docs/api/README.md` to cover post-2026-04-23 routes** — current doc lists 28 endpoints; ARCHITECTURE.md says ~70 routes total. Add coverage for: webhooks, integrations, retention, experiments, approval-gates, publish-gates. Acceptance: every router file under `routes/` has at least its top-level endpoint catalogued. Bonus: auto-generate from FastAPI's `/api/openapi.json`.

### P3 (housekeeping)

11. **[P3] Add a "Status as of <date>, may change" banner to `docs/operations/ci-deploy-chain.md`** — Matt is considering dropping Gitea. Until then, mark the doc as time-sensitive.

12. **[P3] Verify `scripts/db-restore.ps1` exists** — `disaster-recovery.md` line 26 references it but it's not in `scripts/` listing. Either add the script or fix the doc.

13. **[P3] Decide whether `docs/reference/app-settings.md` ships in public sync** — currently excluded; the file says it's "not safe to publish." But customers benefit from seeing the catalog. Consider a redacted public version + private full version.

14. **[P3] Replace dead `gladlabs.ai/guide` URL in `docs/experiments/launch-drafts-2026-04-24.md` line 254** — same dead URL as ARCHITECTURE.md. Use `gladlabs.lemonsqueezy.com/checkout/buy/...`.

---

## 4. Quick wins (<15 min each)

- ✅ Bump `5,000+` → `6,400+` in README.md (lines 6, 102, 157)
- ✅ Bump `5,000+` → `6,400+` in extending-poindexter.md (line 526)
- ✅ Bump `~5,097` → `6,429` in local-development-setup.md (line 150) and ci-deploy-chain.md (line 77)
- ✅ Bump `5,000+` → `6,400+` in launch-drafts-2026-04-24.md (lines 56, 218)
- ✅ Replace `https://www.gladlabs.ai/guide` → `https://gladlabs.lemonsqueezy.com/checkout/buy/a5713f22-3c57-47ae-b1ee-5fee3a0b43b9` in ARCHITECTURE.md (line 9) and launch-drafts-2026-04-24.md (line 254)
- ✅ Fix `VECTOR(1536)` → `VECTOR(768)` in ARCHITECTURE.md (line 502)
- ✅ Fix `CREATE INDEX idx_tasks_user_id ON tasks(user_id)` → `... ON content_tasks(user_id)` in database-schema.md (lines 79-81)
- ✅ Update SECURITY.md last-updated date (line 4)
- ✅ Update database-schema.md migrations path: `src/cofounder_agent/migrations/` → `src/cofounder_agent/services/migrations/` (line 7)
- ✅ Reconcile `235+ tuned app_settings` (docs/README.md:86), `296 active rows` (app-settings.md:5), `310 app_settings keys` (CLAUDE.md:44, environment-variables.md:10) to a single live count
- ✅ Move 8 dated snapshot files to `docs/archive/<date>/` and update any cross-links

---

## 5. Cross-cutting themes

### Theme A: Numeric drift everywhere

Tests counts (5,000+ vs 6,429), service counts (313 vs 337), settings counts (235+ vs 296 vs 310 vs 328), grafana dashboard counts (5 vs 6 vs 7). Six different docs claim six different numbers for the same thing. **Recommended fix:** a single `docs/STATS.md` (or appendix in CLAUDE.md) that's regenerated from live state. Other docs reference it instead of inlining numbers.

### Theme B: Stale file paths from incomplete renames

Multiple docs still use pre-rename paths: `*_database.py` (now `*_db.py`), `services/model_router.py` (deleted), `gladlabs-*` containers (now `poindexter-*`), `tasks` table (now `content_tasks`), `content` table (now `posts`), `admin_logs` table (now `audit_log`). The rename happened in a single CHANGELOG entry but several docs missed the sweep.

### Theme C: Frozen-in-time snapshots in living-doc directories

8 dated docs (5 in operations/, 3 in architecture/) are frozen-in-time and don't belong in `living` directories. **Recommended pattern:** all date-stamped docs go to `docs/archive/<date>/` from creation. Living docs in `architecture/` and `operations/` should never have a date in the filename.

### Theme D: Self-aware staleness markers ignored

`services.md` has an HTML comment at the bottom listing 10+ files that don't exist. `database-and-embeddings-plan-2026-04-24.md` has a status header tracking deferred phases. These markers exist BECAUSE someone noticed drift. They should generate issues, not block in the doc.

### Theme E: The "agent" framing keeps creeping back

`ARCHITECTURE.md` Section 3 ("Agent System Architecture", lines 332-356) is the last surviving fragment of the pre-Phase-E agent narrative. `multi-agent-pipeline.md` has been gutted to a pointer; `content-pipeline.md` correctly says agents are stateless wrappers. ARCHITECTURE.md is the outlier — it should defer to content-pipeline.md and stop describing the system as agent-driven.

### Theme F: Pricing copy is consistent across the public set

Despite being a recent change, `$9/mo or $89/yr 7-day free trial` is consistent in README, SUPPORT, docs/README, ARCHITECTURE roadmap, launch-drafts, and CLI reference. **No drift here.** Whoever did the pricing-consolidation sweep on 2026-04-23 (per CHANGELOG) was thorough.

### Theme G: Apache-2.0 has propagated cleanly to public-facing docs

LICENSE, README, SUPPORT, CONTRIBUTING, SECURITY (mostly), CHANGELOG all show Apache-2.0. The `src/LICENSE.md` files have the relicense note. **This audit found ZERO docs that still say "AGPL"/"Affero"/"copyleft" in a misleading way** — every remaining mention is in a transition note explaining the change. Strong work on the relicense sweep.

### Theme H: Docs do not yet welcome forks/SaaS-resale

Despite Apache-2.0 explicitly permitting it, only SUPPORT.md (line 24) says so. CONTRIBUTING.md, README.md, and the tagline copy don't yet pivot from "self-host" to "fork-and-deploy-anywhere-including-as-a-service." This is a posture gap, not a factual error — but the license is now permissive and the docs should reflect that opportunity.

---

**End of audit.**

Generated 2026-04-29 by Claude (subagent), commissioned by Matt.
