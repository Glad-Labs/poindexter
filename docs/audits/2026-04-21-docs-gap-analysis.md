# Docs gap analysis (GH-17)

**Scope:** Survey of every living doc under `docs/` plus top-level
`README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`. Goal is
to answer three questions:

1. What docs exist today and who are they for?
2. What critical paths have no doc at all?
3. Which existing docs have drifted from the code they describe?

The audit excludes `docs/audits/` (point-in-time reports — they
shouldn't be maintained as living docs) and `docs/brand/` (binary
palette previews, not prose).

---

## Inventory

### Top-level

| File                                | Lines | Audience    | Last dated in doc |
| ----------------------------------- | ----- | ----------- | ----------------- |
| `README.md`                         | 349   | operator    | —                 |
| `CONTRIBUTING.md`                   | 119   | contributor | —                 |
| `SECURITY.md`                       | 189   | operator    | —                 |
| `SUPPORT.md`                        | 236   | operator    | —                 |
| `CODE_OF_CONDUCT.md`                | 16    | contributor | —                 |
| `docs/README.md`                    | 93    | all         | —                 |
| `docs/ARCHITECTURE.md`              | 594   | developer   | 2026-04-11        |
| `docs/feature-status.md`            | 152   | operator    | 2026-04-17        |
| `docs/quick-start-guide-outline.md` | ~200  | operator    | —                 |

### `docs/architecture/`

| File                      | Lines | Audience  | Last dated in doc |
| ------------------------- | ----- | --------- | ----------------- |
| `multi-agent-pipeline.md` | 544   | developer | 2026-03-10        |
| `database-schema.md`      | 206   | developer | 2026-04-11        |
| `plugin-architecture.md`  | 258   | developer | 2026-04-19        |

### `docs/operations/`

| File                         | Lines | Audience    | Last dated in doc |
| ---------------------------- | ----- | ----------- | ----------------- |
| `local-development-setup.md` | 178   | operator    | 2026-04-17        |
| `environment-variables.md`   | 134   | operator    | —                 |
| `troubleshooting.md`         | 241   | operator    | —                 |
| `ci-deploy-chain.md`         | 100   | operator    | 2026-04-17        |
| `disaster-recovery.md`       | 287   | operator    | 2026-04-17        |
| `commit-signing.md`          | 138   | contributor | —                 |

### `docs/api/`

| File        | Lines | Audience  | Last dated in doc |
| ----------- | ----- | --------- | ----------------- |
| `README.md` | 227   | developer | 2026-04-17        |

**Total tracked:** 20 living docs, ~4,000 lines of prose.

---

## Audience split

- **Operator** (running Poindexter on own machine): 8 docs, ~1,700
  lines. Reasonable coverage. Weakest spot is app_settings — the
  env-vars doc covers the 4 bootstrap values but the 86+ DB-backed
  settings have no reference doc.
- **Developer** (working on Poindexter internals): 5 docs, ~1,600
  lines. Decent at the "big picture" level (`ARCHITECTURE.md`,
  `plugin-architecture.md`) and the SQL layer (`database-schema.md`).
  Thin on subsystem-level detail — how the brain daemon actually
  works, how the 12-stage pipeline is composed, how the approval
  queue state machine transitions.
- **Contributor** (submitting PRs): 2 docs, ~140 lines — barely
  anything. `CONTRIBUTING.md` is generic OSS boilerplate without
  code-style specifics or a "how to add a Stage" walkthrough.

---

## Stale / inaccurate docs

### High-severity (actively misleading)

**`docs/architecture/multi-agent-pipeline.md`** — dated 2026-03-10,
version 0.1.0, pre-dates the Phase E Stage refactor by roughly
six weeks. Specific problems:

- Describes a 6-agent pipeline (Research → Creative → QA →
  Creative-refined → Image → Publishing). The actual pipeline is a
  12-stage `Stage` plugin chain run by `StageRunner`, discovered via
  `plugins.registry.get_core_samples()` in
  `src/cofounder_agent/plugins/registry.py:236-247`.
- Claims Research Agent, Creative Agent, Publishing Agent exist
  under `src/cofounder_agent/agents/content_agent/`. The actual
  stage code lives under `src/cofounder_agent/services/stages/`
  with filenames like `generate_content.py`, `quality_evaluation.py`.
- The "Specialized Agents" section (Financial Agent, Market Insight
  Agent, Compliance Agent) describes code that does not exist in
  the repo today — no `financial_agent.py`, no `compliance_agent.py`.
- Footer links to `System-Design.md`, `../01-Getting-Started/`,
  `../04-Development/`, `../00-README.md` — **none of those files
  exist** in this repo. They are survivors of a prior numbered-
  directory scheme.

**`docs/architecture/database-schema.md`** — dated 2026-04-11.
Drifted:

- Claims "5 specialized database modules" (`UsersDatabase`,
  `TasksDatabase`, `ContentDatabase`, `AdminDatabase`,
  `WritingStyleDatabase`). There are now 13+ `_db.py` modules under
  `src/cofounder_agent/services/` (admin_db, content_db, embeddings_db,
  pipeline_db, settings_db, site_db, social_db, static_export_db,
  urls_db, and more).
- The schema excerpt shows a `tasks` table with `CREATE INDEX
idx_tasks_user_id ON tasks(user_id)` — but the body text of the
  same doc notes production code actually writes to `content_tasks`.
  The index reference is flat-out wrong.
- No mention of the 75+ numbered migrations under `migrations/`
  that have shipped since 2026-04-11, including the 4-state
  approval statuses (`rejected_retry` / `rejected_final`) introduced
  around migration 0068.

### Medium-severity (incomplete but not wrong)

**`docs/ARCHITECTURE.md`** — dated 2026-04-11. The high-level
sections still read correctly (purpose, principles, non-goals).
Specifically outdated parts:

- "50+ REST endpoints" — actual count is closer to 160+ per
  `docs/feature-status.md` and matches the route module count.
- "Memory System (`memory_system.py`)" section describes a module
  that `docs/architecture/plugin-architecture.md:243` flags as
  superseded dead code (966 lines slated for removal). The
  architecture doc should call it deprecated.
- Same `System-Design.md` phantom link at the bottom as the pipeline
  doc — that file doesn't exist.

**`docs/operations/troubleshooting.md`** — Has an inline reference:

> See `docs/operations/CI-Deploy-Chain.md` for the full chain
> diagram.

The actual filename is `ci-deploy-chain.md` (lower-case). Case-
sensitive filesystems (Linux / CI runners) won't resolve the link.

**Top-level `README.md`** — Has not been audited against the Phase
E landing. Not blocker-stale, but the "Architecture" blurb still
describes the 6-agent model. Worth a sweep when a developer next
updates it.

### Low-severity (minor gloss)

- `docs/feature-status.md` line 125 says "Quick Start Guide DRAFT
  — docs/quick-start-guide.md". That file is not in this repo —
  it lives in the separate `glad-labs-prompts` repo. The reference
  should be either removed or clarified as cross-repo.
- `docs/feature-status.md` line 131 says the troubleshooting doc is
  in "glad-labs-prompts". It is in fact in this repo at
  `docs/operations/troubleshooting.md`. Two docs disagreeing with
  each other is classic drift.
- `docs/operations/environment-variables.md` links to
  `../reference/app-settings.md` which does not exist. The `docs/`
  root README does the same thing, listing four files under
  `reference/` as "Coming soon."

---

## Missing docs (prioritized gap list)

### Tier 1 — blocks a new operator's first day

1. **Pipeline stage diagram + overview.** No single doc explains
   the 12-stage `Stage` plugin pipeline, the three chunk phases
   (content → QA → SEO/finalize), or how stages halt the pipeline
   vs. continue past errors. `multi-agent-pipeline.md` is stale
   and describes the pre-refactor shape. Critical for anyone
   diagnosing a task stuck in a specific stage.
2. **`app_settings` reference.** 86 keys in the free-tier seed
   (`brain/seed_app_settings.json`), 270+ in Matt's production DB
   per `CLAUDE.md` context. No single doc lists them with default
   value, category, and the code that reads each one. Operators
   change settings blindly today.
3. **Approval queue state machine.** `awaiting_approval` →
   `approved` → `published`, or `rejected_retry` (auto-retry) /
   `rejected_final` (archived). Also `failed_revisions_requested`
   legacy state, `approval_status` column separate from the main
   `status` column, the `max_approval_queue` throttle. Scattered
   across `routes/approval_routes.py`, `services/task_executor.py`,
   and migration 0068 — no single narrative.

### Tier 2 — high-value for contributors

4. **Brain daemon role + responsibilities.** `brain/brain_daemon.py`
   has a docstring; there's no user-facing doc explaining what it
   monitors, the probe contract, how it escalates to Telegram vs.
   Discord, or its relationship to Grafana Alertmanager (per
   plugin-architecture.md Phase D). Disaster-recovery.md covers
   "how to restart it" but not "what it does."
5. **DB-first config principle + operator how-to.** The environment-
   variables doc hints at this in its opening callout, but there's
   no "change a threshold without a redeploy" walkthrough, no
   explanation of `site_config.get_secret()` vs.
   `site_config.get()`, no discussion of the `_DEFAULTS` fallback.
6. **"How to add a new Stage."** Tier-2 contributor onboarding.
   With 12 stages shipped and the Phase E refactor explicitly
   designed to make this cheap, there's no walkthrough.

### Tier 3 — polish

7. Architecture README as a true nav doc (current 19-line version
   doesn't exist here — the `docs/architecture/` dir has no index).
8. Plugin author guide — companion to `plugin-architecture.md` but
   concrete: "here's what a Tap's `pyproject.toml` looks like".
9. API reference filled out — current `api/README.md` covers ~15
   endpoints, `feature-status.md` says there are 160+.
10. Cross-link audit — broken links to `../reference/...` and
    `../01-Getting-Started/` should be removed or replaced.

---

## Prioritized recommendation

Fix the Tier 1 drift first. The biggest single win is replacing
`docs/architecture/multi-agent-pipeline.md` with a current Stage-
pipeline doc, because:

- It is actively misleading (not just incomplete).
- Every operator who wants to understand "why did my task halt"
  needs it.
- It unblocks the Tier 2 "how to add a Stage" walkthrough — that
  doc can link into this one rather than re-explain the runner.
- The replacement slots directly into `docs/architecture/` with a
  new, correct filename; readers of the old stale doc will land on
  the new one.

The companion gap-fill doc shipped alongside this audit is
`docs/architecture/content-pipeline.md`. The `app_settings`
reference and the approval-queue state-machine doc are the next
two logical fills and should be tracked as follow-up issues.

---

## Methodology

Each doc was read end-to-end, compared against the code it
references, and cross-checked against the current symbol names in
`src/cofounder_agent/services/`, `src/cofounder_agent/plugins/`,
and `brain/`. Line counts are from `wc -l` at HEAD
`4b7e485e` (2026-04-21). Drift claims are grounded in the specific
file paths and module names shown above — if the code moves, the
audit goes stale with it, which is why audits live under
`docs/audits/` and do not get maintained.
