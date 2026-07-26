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
Sign-off first, autonomy earned.

**Auto-publish graduation (Lock 2 — wired 2026-07-25).** The gate node opts in
via its spec config (`graduation_setting:
'seo.refresh.auto_publish_after_clean_runs'`), and `atoms.approval_gate` does
the rest: before pausing, it counts the gate's trailing streak of clean
**human** approvals (`pipeline_gate_history` rows with `event_kind='approved'`,
`actor='human'`, across all tasks; counted per distinct task; any
`rejected`/`dismissed` row — an operator veto or the staleness sweep — resets
the streak). Once the streak reaches the setting's value (default `5`), the
gate stops pausing: it records an `auto_approved` history row
(`actor='graduation'`), emits an `approval_gate_graduated` finding (Discord,
per-gate dedup), and the refresh republishes autonomously. Mechanics worth
knowing:

- **`0` disables graduation** — the gate pauses forever. Every failure path
  (unparseable setting, streak-read error, history-row write failure) also
  falls back to a pause, never to an unrecorded auto-pass.
- Graduated passes are **trust-neutral**: `auto_approved` rows are excluded
  from the streak scan, so graduation sustains itself without inflating the
  count, and non-human approvals never build trust.
- Pre-graduation pauses surface progress in the review artifact
  (`graduation_progress: "3/5 trailing clean approvals toward auto-publish"`)
  in the console gate lane.
- **Revoking autonomy**: set the setting to `0` (pauses resume immediately),
  reject the next bad proposal (resets the streak), then restore the
  threshold — the gate re-graduates only after a fresh streak of clean
  sign-offs.

### Settings (`app_settings`)

| Key                                         | Default     | Meaning                                                                                      |
| ------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------- |
| `seo.refresh.enabled`                       | `false`     | Master switch for auto-enqueueing refreshes.                                                 |
| `seo.refresh.scope`                         | `meta_only` | Refresh aggressiveness.                                                                      |
| `pipeline_gate_seo_refresh_gate`            | `true`      | Approval-first gate.                                                                         |
| `seo.refresh.auto_publish_after_clean_runs` | `5`         | Trailing clean human approvals before the gate auto-approves (Lock 2). `0` = never graduate. |
| `seo.refresh.outcome_measure_after_days`    | `14`        | Delay before measuring the refresh's effect.                                                 |
| `seo.refresh.max_per_run`                   | `3`         | Max refresh tasks auto-enqueued per run.                                                     |

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

## Query-dimension ingestion (#764)

Phase 1 and Phase 2 both work from **page-level** GSC data — `external_metrics`
never carried the `query` dimension, so `seo_refresh`'s `target_query` has
always fallen back to a post's own topic/keyword rather than a real search
term. Closing that gap is two independent, additive pieces:

**Tap-side:** the installed `tap-google-search-console` package ships a
`performance_report_custom` stream whose dimension set is driven by Singer
catalog **field-level** selection (not a fixed per-stream list, unlike every
other stream it offers). `tap_singer_subprocess.py`'s catalog generator
gained a `field_selection` config key (`{tap_stream_id: [field, ...]}`) to
support this — the `gsc_main` `external_taps` row's `performance_report_custom`
stream requests `page` + `query` via `field_selection`, and
`tap_external_metrics_writer.py` needed **no changes**: it already bundles
arbitrary `dimension_fields` into the `dimensions` jsonb and dedups on
`(source, metric_name, date, slug, dimensions)`, so per-(page, query) rows
fall out for free. Activation is a one-off script
(`scripts/enable_gsc_query_dimension.py`) run once against the live tap row,
separate from the code deploy — mirrors how `seo.refresh.enabled` was flipped
only after explicit operator sign-off in Milestone B.

**Topic-side:** `GscQueryGapSource`
([`services/topic_sources/gsc_query_gap.py`](../../src/cofounder_agent/services/topic_sources/gsc_query_gap.py))
is a new `TopicSource` (same protocol as `devto.py`/`hackernews.py`) that
reads the newly-ingested per-query rows and surfaces queries with real
impressions but a poor average position as `DiscoveredTopic`s — a gap in
_new_ content, distinct from `seo_refresh`'s job of re-optimizing _existing_
posts' metadata. It feeds the ordinary topic-proposal pipeline
(`topic_sources/runner.py`), which handles cross-source dedup/ranking the
same way it already does for every other source.

### Settings (`app_settings`)

| Key                           | Default | Meaning                                                                                                                       |
| ----------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `seo.query_ingestion.enabled` | `false` | Master switch — gates both the tap-row activation and the gap source. `GscQueryGapSource.extract()` returns `[]` while false. |

`GscQueryGapSource`'s own tuning (`min_impressions`, `min_position`,
`window_days`, `max_topics`) lives under `plugin.topic_source.gsc_query_gap.config`
per the standard per-source `TopicSource` convention — no seed row required;
Python-side defaults apply when the row is absent, same as every other
topic source.

## Status

- **Shipped (Phase 1 + Phase 2a):** the analyzer; the `seo_refresh` graph (4
  atoms), its entry seam, the approval-first gate, the settings, and full unit
  coverage. Validated live on a real production post.
- **Shipped (Milestone B — Phase 2b/2c, #763):** auto-enqueue from the analyzer
  (`enqueue_seo_refreshes`, gated on `seo.refresh.enabled`, capped by
  `seo.refresh.max_per_run`); the outcome-measurement job
  (`measure_seo_refresh_outcomes`, read-only, gated on
  `seo.refresh.outcome_measure_after_days`); the `refreshed_at` anchor + the
  analyzer status-latch (so refreshed opportunities aren't re-opened and
  re-refreshed); and the Grafana refresh-queue + outcome-delta panels. Ships
  inert — `enqueue_seo_refreshes` no-ops until `seo.refresh.enabled=true`.
- **Shipped (#764 — code, ships inert):** field-level Singer catalog
  selection in `tap_singer_subprocess.py`; the `GscQueryGapSource` topic
  source. **Pending operator activation:** run
  `scripts/enable_gsc_query_dimension.py` against prod, spot-check a tap run,
  then set `seo.query_ingestion.enabled=true`.
- **Shipped (Lock-2 graduation, 2026-07-25):** auto-publish graduation for
  `seo_refresh` — `atoms.approval_gate` grew a generic `graduation_setting`
  config seam, the `seo_refresh` graph wires it to
  `seo.refresh.auto_publish_after_clean_runs`, and the gate auto-approves once
  the trailing clean-human-approval streak reaches the threshold (see
  "Approval (and earning autonomy)" above).
- **Next:** once query data has been live a few weeks, consider feeding real
  per-query `target_query` values into `seo_opportunities` for sharper
  `seo_refresh` targeting.
