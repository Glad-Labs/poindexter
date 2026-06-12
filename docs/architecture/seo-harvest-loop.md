# SEO Harvest Loop

The harvest loop re-optimizes **existing** published posts instead of only
generating new ones. A post that already earns search impressions but ranks just
off page 1 — or ranks but has a weak click-through rate — is often one
title/meta optimization away from real traffic, which is cheaper and faster than
writing new content. The loop turns Search Console data the system already
collects into action.

Two phases, each shipping value independently:

| Phase           | Job / template                 | Mutates content?       | Master switch                                   |
| --------------- | ------------------------------ | ---------------------- | ----------------------------------------------- |
| **1 — analyze** | `run_seo_opportunity_analyzer` | No (read-only)         | `seo.harvest.analyzer_enabled` (default `true`) |
| **2 — refresh** | `seo_refresh` graph_def        | Yes (meta only, gated) | `seo.refresh.enabled` (default `false`)         |

## Phase 1 — analyze (read-only)

`RunSeoOpportunityAnalyzerJob`
([`services/jobs/run_seo_opportunity_analyzer.py`](../../src/cofounder_agent/services/jobs/run_seo_opportunity_analyzer.py))
runs daily, reads the latest `post_performance` snapshot per published post,
classifies via
[`services/seo/striking_distance.py`](../../src/cofounder_agent/services/seo/striking_distance.py),
and upserts `seo_opportunities` (one row per post, recomputed each run). A
findings summary fires when page-1-push candidates exist. It modifies no content.

Each post is assigned its single highest-priority tier:

- **page1_push** — ranks position 3–10 with real impressions; one optimization
  from page 1. The fastest win.
- **striking_distance** — ranks position 5–20; on page 2, close.
- **low_ctr** — ranks with impressions but the title/meta isn't earning the click.

`gap_score` — estimated clicks/month left on the table,
`impressions × (target_ctr − current_ctr)` — orders the "fix this first" list.

Phase 1 lives in substrate (`services/seo/`) because it is pure analytics over
substrate tables; the content-_mutating_ refresh atoms (Phase 2) live in
`modules/content/atoms/`.

**Tuning (`app_settings`):** `seo.striking_distance.position_min` /
`position_max`, `seo.push_candidate.position_min` / `position_max` /
`min_impressions`, `seo.low_ctr.min_impressions` / `max_ctr`,
`seo.opportunity.target_ctr`.

**Observability:** Grafana → **SEO Harvest** dashboard (`/d/seo-harvest`) — tier
counts, the ranked top-opportunities table, and the sitewide CTR trend.

## Phase 2 — refresh (`seo_refresh` graph_def)

`seo_refresh` is a DB-stored `graph_def` (in `pipeline_templates`, seeded active
by `…_seed_seo_refresh_graph_def.py`), compiled by
`pipeline_architect.build_graph_from_spec` and run by `TemplateRunner` — exactly
like `canonical_blog`. It is **not** a `task_type` branch on the generation
pipeline; it rides the same `template_slug → graph_def → TemplateRunner` seam and
composes only the atoms a meta edit needs. The spec lives in
`services/seo_refresh_spec.py::SEO_REFRESH_GRAPH_DEF`.

Four linear nodes:

1. **`content.load_existing_post`** — the one novel seam. Instead of generating a
   draft, it hydrates pipeline state (`content`, `title`, `post_slug`, `seo_*`,
   `tags`) from the `posts` row named by `post_id`, plus the `target_query` and
   `seo_opportunity_id` from the post's top `seo_opportunities` row. The body is
   carried **verbatim** — it is never regenerated.
2. **`seo.optimize_metadata`** — a query-aware rewrite of `seo_title` +
   `seo_description` for click-through, using the post's `target_query` when
   present (falling back to its topic/primary keyword when not). Reuses the
   shared `_seo_common` LLM-call-with-retry + fallbacks. The title goes through
   `_seo_common.clean_title` (the title twin of `clamp_words`): it drops embedded
   double-quote artifacts and, on a length-truncated clip, trims any dangling
   trailing connective so a 60-char cut never ends on a stray `&`. On LLM/parse
   failure it **keeps the existing live meta** — a failed refresh must never
   worsen a published post.
3. **`atoms.approval_gate`** (`gate_name='seo_refresh_gate'`) — pauses for
   operator sign-off (see below). Its `config.gate_artifact_keys` surface the
   **proposed** `seo_title` / `seo_description` (plus `title` / `post_slug` /
   `target_query`) in `pipeline_tasks.gate_artifact`, so the operator reviews the
   actual change — the default artifact keys omit the SEO fields that a meta
   refresh is all about.
4. **`content.republish_post`** — applies the optimized meta (`seo_title` /
   `seo_description` / `seo_keywords` — never `content`), re-exports the static
   JSON to R2, fires ISR revalidation (a DB update alone does not reach the live
   site), and stamps the `seo_opportunities` row `status='refreshed'` with its
   pre-refresh `baseline_position` / `baseline_ctr`.

### Scope: `meta_only`

The v1 default (`seo.refresh.scope='meta_only'`) rewrites **title + meta
description only** — where click-through lives, and the safest edit to live
content. Deeper scopes (`meta_and_intro`, `full`) are future, opt-in, and **add
atoms** to the graph; they never branch the existing ones. Because the body is
unchanged from an already-QA'd post, the human approval gate is the quality
control — the canonical QA rails are intentionally omitted in v1 (they would,
e.g., false-flag the post's own unchanged title as a duplicate).

### Approval (and earning autonomy)

Unlike `draft_gate` (which ships disabled), `seo_refresh_gate` ships **enabled**
(`pipeline_gate_seo_refresh_gate=true`) — re-publishing a live post pauses for
sign-off. The gate uses a true LangGraph `interrupt()`: the graph durably
checkpoints and pauses; approve with `poindexter pipeline resume <task_id>`.
Auto-publish graduation (re-publishing without sign-off once the trailing
edit-distance trust threshold `seo.refresh.auto_publish_after_clean_runs` is met)
reuses the main auto-publish mechanism and is wired in a later increment.
Sign-off first, autonomy earned.

### Settings (`app_settings`)

| Key                                         | Default     | Meaning                                         |
| ------------------------------------------- | ----------- | ----------------------------------------------- |
| `seo.refresh.enabled`                       | `false`     | Master switch for auto-enqueueing refreshes.    |
| `seo.refresh.scope`                         | `meta_only` | Refresh aggressiveness.                         |
| `pipeline_gate_seo_refresh_gate`            | `true`      | Approval-first gate.                            |
| `seo.refresh.auto_publish_after_clean_runs` | `5`         | Clean-run count before auto-publish graduation. |
| `seo.refresh.outcome_measure_after_days`    | `14`        | Delay before measuring the refresh's effect.    |

### Running one refresh by hand

The refresh task is an ordinary `pipeline_tasks` row. Until auto-enqueue is
enabled, drive one by hand to validate:

1. Pick an `open` opportunity (high `gap_score`) from `seo_opportunities`; note
   its `post_id`.
2. Create a `pipeline_tasks` row with `template_slug='seo_refresh'` and the post
   id in the task metadata (`task_metadata.post_id`,
   `task_metadata.seo_opportunity_id`). `content_router_service._load_task_metadata`
   surfaces those onto the pipeline initial state so the entry atom can hydrate.
3. The graph pauses at `seo_refresh_gate`; review the proposed title/meta and
   `poindexter pipeline resume <task_id>`.
4. Confirm the live post's title/meta changed (R2 export + ISR revalidation) and
   the opportunity flipped to `refreshed`.

### Outcome tracking

`republish` stamps `baseline_position` / `baseline_ctr` from the opportunity's
own current metrics. A later measurement pass (after
`seo.refresh.outcome_measure_after_days`) records `outcome_position` /
`outcome_ctr` / `outcome_measured_at`, so the delta — did the refresh move the
needle? — is queryable. This is the empirical proof the loop works and the
training signal for an eventual successor that learns which refreshes are worth
doing.

## Status

- **Shipped:** the Phase 1 analyzer; the Phase 2 `seo_refresh` graph (4 atoms),
  its entry seam, the approval-first gate, the settings, and full unit coverage
  (analyzer classification, each atom, the graph compile gates, and an in-memory
  end-to-end run).
- **Next:** auto-enqueue from the analyzer (gated on `seo.refresh.enabled`), the
  outcome-measurement job, and Search Console query-dimension ingestion for
  sharper query targeting.
