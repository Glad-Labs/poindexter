# Content-Type Classifier — Design

**Date:** 2026-07-13
**Status:** Approved (design), pending implementation plan
**Branch:** `claude/indiehackers-devto-posting-27bb1a`
**Related:** WS1 spec `2026-07-12-devto-selective-syndication-design.md` + PR #2415 (the
consumer this unblocks); precedent `services/jobs/fix_uncategorized_posts.py` (batched
published-post sweep) + `services/jobs/crosspost_to_devto.py`; memory `feedback_brand_niches`,
`feedback_calculated_vs_generated`, `feedback_prompts_must_be_db_configurable`,
`feedback_reasoning_models_empty_json`.

## Problem

The system has no way to express **what a post is about**. The `niches` table holds only
two slugs — `glad-labs` (active; bundles AI/ML + gaming + PC-hardware) and `dev_diary`
(founder) — and `category` is 127/145 just `technology`. The `tags`/`post_tags` folksonomy
is LLM-generated sprawl (`ai-ml`=13 posts next to `model`=8, `models`=3, `llms`=2,
`large-language-models`=2, `rtx-5090-cooling`=1) — inconsistent and low-coverage, not a
usable signal.

This surfaced building the Dev.to selective-syndication gate (WS1, PR #2415): the operator
wanted "syndicate AI/ML + founder content, exclude gaming/PC-hardware," but **no field can
separate AI/ML from gaming/hardware** — they're all `glad-labs`. The brand is explicitly
those subject areas (`feedback_brand_niches`: AI/ML, gaming, PC hardware), yet nothing in
the data models them. Operator's call (2026-07-13): "a content-type classifier is more
forward-thinking; it should be doing that anyway."

## Goals / Non-goals

**Goals:** an **additive, multi-label content-type axis** — a classifier that tags each
published post with 0–N of a DB-configurable label set (default:
`ai-ml, pc-hardware, gaming, software-engineering, founder-meta`), stored in a dedicated
table, applied to existing posts (backfill) and new ones (ongoing sweep). First consumer:
repoint the Dev.to gate to filter on content-type (closing PR #2415). Ship a content-type
distribution Grafana panel.

**Non-goals (this spec):**

- **Reworking the niche taxonomy** (promoting ai-ml/gaming/hardware to real `niches` that
  drive topic generation) — high blast radius; content-type stays a _descriptive label_,
  not a generation driver. (Chosen 2026-07-13 over the niche-rework option.)
- **Pre-publish classification** — no pipeline atom / `PipelineState` handoff / publish-path
  change. Classification is a **post-publish cron sweep** (see Assignment). Refinement from
  the verbally-approved "atom + backfill job" sketch: a single job is simpler, has one code
  path, avoids a pre-publish state→table handoff, and the only consumers (Dev.to gate,
  dashboards) act on published posts anyway. Small lag (post publish → next sweep) self-heals.
- **Cleaning up the `tags` folksonomy** — tags are a classifier _hint_, left as-is.
- **Consumers beyond the Dev.to gate + one dashboard panel** (RAG facets, per-niche
  analytics, topic-balancing) — the table makes them cheap later; not now.

## Label vocabulary (DB-configurable)

`app_settings.content_type_labels` — CSV, default
`ai-ml,pc-hardware,gaming,software-engineering,founder-meta` (`feedback_db_first_config`:
every taxonomy is a setting). The classifier **only emits labels from this set**;
anything else the model returns is dropped (deterministic guardrail). Multi-label: a post
gets 0..N (e.g. "RTX 5090 for local LLM inference" → `{ai-ml, pc-hardware}`).

## The classifier (job-based)

New `services/jobs/classify_content_types.py::ClassifyContentTypesJob`, registered in
`plugins/registry.py` `_SAMPLES`, mirroring the `fix_uncategorized_posts` / `crosspost_to_devto`
sweep shape. Cadence: `every 6 hours` (DB-tunable via `plugin.job.classify_content_types`).

Each run: fetch up to `batch_size` (default 10) published posts with **no** `post_content_types`
row (so it both backfills the 145 existing and keeps up with new posts — one path), classify
each, write labels. Idempotent (a labeled post is never re-fetched).

- **Method:** local LLM (`feedback_no_paid_apis` — local default), via the existing
  `llm_text` / `dispatch_complete` seam. Pinned to the **structured-extraction model**, not
  a reasoner: this is JSON output and reasoners leak `<think>`/empty JSON
  (`feedback_reasoning_models_empty_json`). Setting `content_type_classifier_model`
  (default `""` → falls back to the existing `structured_extraction_model`).
- **Input:** post `title` + a bounded `content` excerpt + the post's existing `tags` (the
  folksonomy as a _hint_, per `feedback_survey_data_shapes` — never the source of truth).
- **Prompt:** a SKILL.md pack (`skills/content/content-type-classifier/SKILL.md`) — prompts
  are DB-configurable via `UnifiedPromptManager`, SKILL.md authoritative
  (`feedback_prompts_must_be_db_configurable`, `project_langfuse_catalog_drift_policy`). The
  prompt states the allowed labels + definitions and asks for a JSON array subset with a
  per-label confidence.
- **Output validation (deterministic):** parse JSON → keep only labels ∈
  `content_type_labels`, drop dupes, clamp confidence. A post the model returns nothing valid
  for gets **zero** rows (it stays "unclassified" → the Dev.to gate fail-closes it out; the
  next content improvement or a manual label can fix it). No silent default label.
- **Master switch:** `classify_content_types_enabled` (default `true`).

## Storage — dedicated `post_content_types` table

New migration (`scripts/new-migration.py "add post_content_types table"` — DDL belongs in a
migration, not `settings_defaults`):

```sql
CREATE TABLE IF NOT EXISTS public.post_content_types (
    id           bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    post_id      uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    content_type text NOT NULL,
    confidence   real,
    source       text NOT NULL DEFAULT 'classifier',  -- classifier | manual | backfill
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (post_id, content_type)
);
CREATE INDEX IF NOT EXISTS idx_post_content_types_type ON post_content_types (content_type);
CREATE INDEX IF NOT EXISTS idx_post_content_types_post ON post_content_types (post_id);
```

Clean multi-label, trivial `GROUP BY content_type` for dashboards, `ON DELETE CASCADE` so a
deleted post drops its labels.

- **Rejected:** reusing `tags` with a `kind='content_type'` discriminator — reuses
  `post_tags`, but risks content-type labels rendering as public post tags on the site and
  couples to the folksonomy. A dedicated table is cleaner separation.
- **Rejected:** a `posts.metadata->>'content_types'` jsonb array — lower friction but worse
  for `GROUP BY` analytics and per-label confidence/source.

## Consumers

### 1. Dev.to gate repoint (finishes PR #2415)

The WS1 gate currently filters `pipeline_tasks.niche_slug = ANY(:niches)`. Repoint it to
content-type:

- Rename setting `devto_syndicate_niches` → `devto_syndicate_content_types` (PR #2415 is an
  unmerged draft, so a clean rename — no backcompat shim needed). Default stays `""`
  (opt-in). Operator value becomes `ai-ml,founder-meta` — **the operator's literal original
  intent**, now expressible.
- Candidate query: drop the `niche_slug` filter, keep the `pipeline_versions` quality join
  (via `posts.metadata->>'pipeline_task_id'`), add
  `AND EXISTS (SELECT 1 FROM post_content_types pct WHERE pct.post_id = p.id AND pct.content_type = ANY($2::text[]))`.
  Fail-closed property is preserved: a post with no content-type rows is excluded.
- Update the two count queries (`_UNIVERSE_COUNT_SQL` / `_ELIGIBLE_COUNT_SQL`) the same way,
  and the gate's unit + integration tests.

### 2. Grafana panel

Content-type distribution (posts per label, published-post coverage) — a direct
`post_content_types` SQL query, so **independent of** the job-metrics→Grafana sink task
(`task_c6448109`) running separately. Add to the Cost & Analytics (or Integrations) board.

## Settings (seeded in `settings_defaults.py`)

| Key                              | Default                                                      | Meaning                                                   |
| -------------------------------- | ------------------------------------------------------------ | --------------------------------------------------------- |
| `content_type_labels`            | `ai-ml,pc-hardware,gaming,software-engineering,founder-meta` | Allowed content-type labels (classifier output set).      |
| `content_type_classifier_model`  | `""`                                                         | Model pin; empty → `structured_extraction_model`.         |
| `classify_content_types_enabled` | `true`                                                       | Master switch for the classifier job.                     |
| `devto_syndicate_content_types`  | `""`                                                         | Dev.to allowlist (renamed from `devto_syndicate_niches`). |

(`devto_syndicate_min_quality` from WS1 is unchanged.)

## Testing (`feedback_docs_and_tests_default`)

**Unit** (`tests/unit/services/jobs/test_classify_content_types_job.py`, mocked pool + LLM):

1. LLM returns valid labels → written to `post_content_types`.
2. LLM returns a label ∉ the allowed set → dropped (only valid ones persist).
3. LLM returns nothing valid → zero rows (post stays unclassified, no default label).
4. `classify_content_types_enabled=false` → no-op.
5. Batch limit honored; already-labeled posts not re-fetched.
6. Malformed/`<think>`-wrapped JSON → parsed defensively (`maybe_unwrap_json`), no crash.

**Integration_db** (`tests/integration_db/test_content_type_classifier.py`): seed published
posts, run the job with a stubbed LLM, assert `post_content_types` rows; then repoint-gate
test — seed posts + content-types, assert the Dev.to gate selects only allowlisted-type
posts (extends the WS1 integration test).

**Dev.to gate tests:** update the WS1 unit + integration tests for the new content-type
query + setting name.

**Docs:** the SKILL.md prompt pack; `app-settings.md` auto-regens (DB-generated); a short
`docs/architecture/content-type-classification.md`.

## Verification (before merge)

1. Unit + integration green (worktree venv, `-o addopts=""`).
2. Run the classifier once against prod (read the label distribution over the 145 posts) —
   sanity-check it's not collapsing everything into one label or mislabeling obvious cases.
3. Repoint dry-run: eligible-count for `content_types ∈ {ai-ml, founder-meta}` at
   `min_quality=80` — confirm a sensible non-empty forward set.
4. Set operator `devto_syndicate_content_types=ai-ml,founder-meta`; flip PR #2415 + this
   PR ready; report label distribution + dry-run numbers.

## Decomposition / scope

This spec = classifier job + `post_content_types` table + SKILL.md prompt + backfill (same
job) + Dev.to gate repoint (closes #2415) + one Grafana panel. One coherent PR (can build on
the existing #2415 branch or a fresh one). WS2 (IndieHackers draft-assistant) stays a
separate later cycle.
