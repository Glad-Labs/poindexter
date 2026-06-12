# Design: The SEO Harvest Loop (Lock 1, v1)

- **Date:** 2026-06-11
- **Status:** Approved design — ready for implementation plan
- **Author:** Matt + Claude (brainstorming session)
- **Scope:** v1 of "SEO maturation" — Lock 1 of the hands-off-income goal
- **Related:** `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/project_competitive_position.md`

---

## 1. Context & motivation

A competitive assessment (2026-06-11) against the autoblog autopilot tier
(RankYak, Byword, Koala, Surfer) confirmed via code audit that Poindexter is
miles ahead on engineering but behind on the features that _constitute_ the
product. The #1 gap: **no SEO/keyword capability driving topic selection** —
topics are LLM-ideated + trend-scraped, scored on embedding similarity, with
no search data. Google Search Console data _is_ collected but flows one-way
into Grafana and never influences what gets written next.

The business goal is **B-primary**: rank Matt's own sites for autonomous
income, with the objective function of **minimizing Matt's ongoing
involvement**. This feature is "Lock 1" (income exists). "Lock 2" (Matt
removed from the loop, via auto-publish graduation) is tracked separately but
is referenced here because the refresh path ties into it.

### The decisive finding: harvest, don't hunt

Live GSC data (prod, 2026-04-01 → 2026-06-09, ~10 weeks, **page-level**):

| Signal                                       | Value         | Meaning                                  |
| -------------------------------------------- | ------------- | ---------------------------------------- |
| Impressions                                  | 607,342       | Google shows the content a lot           |
| Clicks                                       | 644           | Almost nobody clicks                     |
| **CTR**                                      | **~0.10%**    | 20–30× under a normal page-1 CTR (~2–3%) |
| Posts in striking distance (pos 5–20)        | **72 of 103** | 70% are one shove from real traffic      |
| Page-1-push candidates (pos 3–10, >100 impr) | **24**        | One optimization pass from page 1        |

**Conclusion:** the highest-ROI, fastest-payback SEO move is not generating
new posts — it is harvesting the impressions already earned. New content
compounds over months; re-optimizing the 24 push-candidates could multiply
clicks in weeks with zero new content. The design leads with **harvesting**
(optimize existing posts) and treats **hunting** (new topics) as the lowest
priority.

### The gating data gap

Current GSC `external_metrics.dimensions` contains only
`{site_url, search_type}` — **no `query` dimension**. So a page can be seen as
"almost ranking" but not _for what keyword_, which is exactly what's needed to
optimize a title/meta. The Singer writer already supports a `query` dimension
(`tap_external_metrics_writer.py` docstring lists it); the GSC `external_taps`
row was simply configured without it. **Adding query-level ingestion is a
config change, not new fetch code.**

---

## 2. Goals & non-goals

### Goals (v1)

1. Ingest GSC **query-level** data (page × query) so optimization can be targeted.
2. **Surface** the harvest opportunity automatically — a "fix these N posts" list, day one, read-only.
3. **Refresh** existing striking-distance posts (conservative title/meta-first optimization), gated on approval, graduating to auto-publish via edit-distance trust.
4. **Prove the loop works** — measure GSC position/CTR delta after each refresh.
5. **Close the feedback loop** — performance data drives what gets refreshed/written next (not embedding-similarity).
6. Everything tunable via `app_settings` (SaaS-ready); no paid APIs on by default.

### Non-goals (explicitly out of v1 — YAGNI)

- Paid keyword APIs (DataForSEO et al.) for net-new keyword discovery / volume / difficulty.
- SERP scraping / competitor content-gap analysis.
- Editorial-calendar UI.
- Multi-tenancy.
- Full-content auto-rewrites beyond the conservative refresh scope.

All non-goals are deferrable until the harvest loop proves out.

### Natural follow-ups (post-v1, deferred by design — not skipped)

- **Improve canonical_blog generation-time SEO** (the `seo.*` atoms that write
  title/description/keywords for brand-new posts — currently the source of the
  0.10% CTR). Deferred _after_ harvest, not during, because: (a) it should
  **reuse the Phase 2 `SeoMetadataOptimizer`** rather than be a parallel build;
  (b) it should be **informed by harvest-outcome data** (§3 Phase 2c) so it's
  evidence-based, not the same guesswork that produced the 0.10% CTR; and (c)
  generation has no query data of its own, so it depends on **Phase 3's
  query-targeting** to be genuinely targeted. Net: a cheap, low-risk follow-up
  once v1's optimizer + outcome data exist. Touching the primary prod pipeline's
  SEO atoms _now_ would widen blast radius in service of guesswork.

---

## 3. Architecture — three phases, each ships value independently

### Phase 1 — Visibility (read-only; touches no content)

**1a. Query-dimension GSC ingestion.** Add `query` (and `page`) to the GSC
`external_taps` row: the tap's `tap_config` so the GSC API requests the query
dimension, and `metrics_mapping.<stream>.dimension_fields` so the writer lands
it into `external_metrics.dimensions.query`. No code change to
`tap_external_metrics_writer.py` (it already maps arbitrary `dimension_fields`).

- **Risk to handle:** the query dimension multiplies row volume (page×query×day
  instead of page×day). Needs a retention policy on these rows and/or a cap.
- **Note:** GSC anonymizes low-volume queries (privacy threshold) — some
  impressions will have no query attached. Expected; not a bug.

**1b. Striking-distance analyzer.** A read-only analysis pass that reads the
latest GSC snapshot per post (from `external_metrics` + `post_performance`) and
classifies every post into opportunity tiers:

- **page-1-push** (pos 3–10, high impressions)
- **striking-distance** (pos 5–20)
- **low-CTR** (high impressions, weak CTR — a title/meta problem, not a ranking problem)

For each opportunity it records the post, the target query (from 1a), current
position, impressions, CTR, and the inferred gap. Output lands in a new
`seo_opportunities` table + emits findings + powers a Grafana panel.

**Day-one payoff:** a ranked "fix these 24 posts" list before any content
machinery exists. Matt can act on it by hand immediately.

### Phase 2 — Harvest (the refresh path — where the money is)

**2a. Refresh runs as a dedicated `seo_refresh` graph_def.** The refresh
pipeline is a new template — a small `graph_def` seeded into `pipeline_templates`
(`template_slug='seo_refresh'`, active), composed from atoms and run by
`TemplateRunner` exactly like `canonical_blog` / `dev_diary`. It is **NOT** a
`task_type` branch on the existing pipeline: it rides the blessed
`template_slug` → graph_def → `TemplateRunner` seam (the `pipeline_architect`
composition model from #355), composes exactly the gates a meta edit needs, and
leaves canonical_blog untouched. The analyzer enqueues an ordinary
`pipeline_tasks` row with `template_slug='seo_refresh'` + the target `post_id`.

Graph shape (~6 nodes, linear) — mostly reused atoms:

| Node                    | Atom                                                   | New?    | Role                                                                                                                                                           |
| ----------------------- | ------------------------------------------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| load_post               | `content.load_existing_post`                           | **new** | Entry — hydrate pipeline state from `post_id` instead of generating a draft (the one genuinely novel seam)                                                     |
| optimize_meta           | `seo.optimize_metadata`                                | **new** | The shared `SeoMetadataOptimizer` (also reused by the generation-time follow-up, §2)                                                                           |
| qa_programmatic         | `qa.programmatic`                                      | reuse   | Meta/title sanity — no full 12-rail block needed for a meta-only edit                                                                                          |
| check_title_originality | `content.check_title_originality`                      | reuse   | Guard against duplicate titles                                                                                                                                 |
| refresh_gate            | `atoms.approval_gate` (`gate_name='seo_refresh_gate'`) | reuse   | **Same atom as `draft_gate`** — sign-off via LangGraph interrupt/checkpoint, resumed by `poindexter pipeline resume`. This IS the Lock 2 graduation mechanism. |
| republish               | `content.republish_post`                               | **new** | Terminal — update the existing post + R2 export + ISR revalidate                                                                                               |

- **Default scope `meta_only`:** title + meta description only (where CTR lives;
  the safest edit to live content). Deeper scopes (`meta_and_intro`, `full`) are
  opt-in per niche and **add atoms to the graph**, never branch existing ones.
- Preserves the content that is already ranking — optimization, not regeneration.
- **The optimizer is a standalone, reusable component** (`SeoMetadataOptimizer`),
  not logic buried in an atom. The `seo_refresh` graph and the canonical_blog
  `seo.*` generation atoms do the same job (produce a high-CTR title + meta) at
  different times, which is what makes the generation-time follow-up (§2) cheap —
  wire the same optimizer in, rather than rebuild it.

**2b. Approval & autonomy (Lock 2 tie-in).** Re-publish of an existing post is
**gated on Matt's sign-off initially** and **graduates to auto-publish** once
the trailing edit-distance trust threshold is met
(`seo.refresh.auto_publish_after_clean_runs`), reusing the same edit-distance
mechanism as the main auto-publish gate. Sign-off first, autonomy earned.

**2c. Refresh-outcome tracking.** N days after a refresh, re-read GSC
position/CTR for the post and record the delta against the pre-refresh
baseline. This measures whether the loop actually works (closes the
empirical loop, not just the data loop).

### Phase 3 — Hunt (new topics from search; lowest priority)

**3a. `GscQueryGapSource`** — a new `TopicSource` conforming to
`plugins.topic_source.TopicSource` (`name` + `async extract(pool, config) ->
list[DiscoveredTopic]`). It drops into `services/topic_sources/runner.py` with
**zero orchestration change** (auto-discovered via entry_points, configured via
`plugin.topic_source.gsc_query_gap`). It surfaces queries the site gets
impressions for but has no well-matched page (or ranks > 20) and emits
`DiscoveredTopic`s with a performance-derived `relevance_score`. Rides the
existing generate pipeline untouched.

### Feedback loop — closed

Every phase derives priority from real GSC/performance data, so what gets
refreshed or written next is performance-driven, replacing the current
embedding-similarity-to-a-goal-vector basis in `topic_ranking.py`. This is the
one-way → two-way fix.

---

## 4. Component boundaries & placement

| Unit                                                                                                                                       | Lives in                                                             | Depends on                                                                    | Mutates content? |
| ------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ---------------- |
| Query-dim ingestion                                                                                                                        | `external_taps` row config (DB)                                      | GSC Singer tap, `tap_external_metrics_writer`                                 | No               |
| Striking-distance analyzer                                                                                                                 | `modules/content/` (e.g. `modules/content/seo/striking_distance.py`) | `external_metrics`, `post_performance`, `seo_opportunities`                   | No (read-only)   |
| `seo_refresh` graph_def + 3 new atoms (`content.load_existing_post`, `seo.optimize_metadata`, `content.republish_post`) + outcome tracking | `pipeline_templates` row + `modules/content/atoms/`                  | TemplateRunner, `atoms.approval_gate`, `qa.programmatic`, `seo_opportunities` | Yes (gated)      |
| `GscQueryGapSource`                                                                                                                        | `services/topic_sources/`                                            | `external_metrics`, `plugins.topic_source`                                    | No               |
| Grafana panel(s)                                                                                                                           | `infrastructure/grafana/...` (repo JSON = SoT)                       | `seo_opportunities`, `external_metrics`                                       | No               |

Each unit is testable in isolation against synthetic `external_metrics` /
`post_performance` fixtures. The analyzer is pure read → easy to test;
the refresh atom is the only content-mutating unit and is the one to guard
most carefully.

> **Placement rationale:** topic _sources_ are substrate plugins
> (`services/topic_sources/`, the established pattern). Content _business logic_
> (analyzer, refresh) belongs in `modules/content/` per the content-module
> boundary. Exact module paths are a plan-time detail.

---

## 5. Data model changes

1. **`seo_opportunities`** (new table). One row per (post, target_query,
   opportunity_tier) with: `post_id`, `slug`, `target_query`, `tier`
   (`page1_push` | `striking_distance` | `low_ctr`), `current_position`,
   `impressions`, `ctr`, `gap_score`, `status` (`open` | `queued` | `refreshed`
   | `dismissed`), `detected_at`, plus baseline + post-refresh outcome columns
   (`baseline_position`, `baseline_ctr`, `outcome_position`, `outcome_ctr`,
   `outcome_measured_at`).
2. **`external_metrics`** — no schema change; query-dimension rows just carry
   `dimensions.query`. Retention policy row added for the higher-volume
   query-grain rows.
3. **No `pipeline_tasks` schema change.** Refresh tasks are ordinary
   `pipeline_tasks` rows with `template_slug='seo_refresh'`, carrying the target
   `post_id` + `seo_opportunities` reference in the existing metadata jsonb
   (mirroring `metadata->>'pipeline_task_id'`).
4. **`pipeline_templates`** — the `seo_refresh` graph_def is seeded (active) by
   a migration, the same way `canonical_blog`'s graph_def is.

All new settings go in `settings_defaults.py` (seeded every boot), not
migration files. Schema DDL (`seo_opportunities`, retention row) goes in a
timestamped migration.

---

## 6. Configuration (DB-first `app_settings`, all tunable)

- `seo.harvest.enabled` (master switch, default off until validated)
- `seo.striking_distance.position_min` / `position_max` (default 5 / 20)
- `seo.push_candidate.position_max` (default 10) / `min_impressions` (default 100)
- `seo.low_ctr.min_impressions` / `max_ctr`
- `seo.query_ingestion.enabled`, lookback window, row cap
- `seo.refresh.enabled`
- `seo.refresh.scope` (`meta_only` | `meta_and_intro` | `full`, default `meta_only`)
- `seo.refresh.auto_publish_after_clean_runs` (ties to Lock 2 edit-distance trust)
- `seo.refresh.outcome_measure_after_days` (default e.g. 14)
- Paid keyword enrichment: master switch **default-off**, cost_guard-capped (v2 seam only)

Background algorithm windows (lookback, outcome-measure delay) are settings
with sensible defaults — not hardcoded.

---

## 7. Observability

New Grafana panel set (repo JSON, file-provisioned):

- Striking-distance / push-candidate counts over time
- A live "top optimization opportunities" table (from `seo_opportunities`)
- CTR trend (the headline wound)
- **Refresh outcomes** — position/CTR delta after refresh (proves the loop)

Findings emitted on new high-value opportunities so they surface in the
existing Findings dashboard / routing.

---

## 8. Testing strategy

- **Analyzer classification** — synthetic `external_metrics` / `post_performance`
  fixtures → assert correct opportunity tiering and gap scoring.
- **`seo_refresh` graph atoms** — `content.load_existing_post` hydrates state
  from a `post_id`; `seo.optimize_metadata` (`SeoMetadataOptimizer`) optimizes
  title/meta toward the target query with ranking-signal content preserved (no
  destructive rewrite under `meta_only`); `content.republish_post` updates +
  triggers R2/ISR. Plus a graph-validation test that the `seo_refresh` graph_def
  passes `build_graph_from_spec` requires/produces reachability.
- **Config tunability** — thresholds read from `app_settings`, not constants.
- **`TopicSource` contract** — `GscQueryGapSource` conforms to the interface
  and isolates failures (per the runner's per-source isolation).
- **Outcome tracking** — position/CTR delta computed correctly against baseline.

Per project convention: every change ships contract tests + doc updates.

---

## 9. Open questions & risks (resolve during implementation plan)

1. **Graph entry-from-existing-post (highest risk).** The blessed graph*def
   path always starts by \_generating* (canonical_blog's `verify_task` →
   `generate_draft`). Confirm `content.load_existing_post` can seed the
   `PipelineState` channels (content, title, meta, slug, post_id) from a `posts`
   row so the rest of the `seo_refresh` graph runs unmodified, and that
   `TemplateRunner` accepts a task whose payload references a `post_id` rather
   than a topic. This is the one novel seam — verify it FIRST in the plan, before
   building the rest of the graph.
2. **GSC query-row volume & retention.** Adding the query dimension multiplies
   rows; needs a retention policy and possibly a cap so the analytics table
   doesn't balloon.
3. **Refresh aggressiveness.** `meta_only` is the chosen default (CTR is the
   bleeding wound at 0.1%). Revisit whether `meta_and_intro` should be the
   default per-niche once outcomes are measured.
4. **Re-publish propagation.** Re-publishing an existing post must trigger the
   R2 export + ISR revalidation path (per `prod-content-propagation-r2-isr`),
   not just a DB update.

---

## 10. Success metrics

The loop is working if, over a measurement window:

- Aggregate **CTR rises** off the ~0.10% floor.
- **Refreshed posts show positive position/CTR deltas** (outcome tracking).
- Clicks/impressions trend up **without proportional new-content volume**
  (i.e. harvesting, not just publishing more).

---

## 11. Decision log

- **Harvest before hunt.** Driven by live data: 600K impressions at 0.1% CTR,
  72/103 posts in striking distance. Optimization > new generation for ROI now.
- **v1 may modify & re-publish existing posts** (Matt approved).
- **Re-publish gated on sign-off, graduating to auto-publish** via the Lock 2
  edit-distance mechanism (Matt approved both).
- **Refresh default scope = `meta_only`** — safest edit, targets the CTR wound.
- **Query ingestion is config, not code** — writer already supports the dimension.
- **No paid keyword APIs in v1** — mine owned GSC data first; paid enrichment is
  a v2 seam, default-off, cost_guard-gated.
- **Refresh is a dedicated `seo_refresh` graph_def, not a `task_type` branch**
  (Matt's call). Rides the blessed `template_slug` → graph_def → `TemplateRunner`
  seam, composes only the gates a meta edit needs, reuses `atoms.approval_gate`
  (= the Lock 2 interrupt/resume mechanism), and never touches canonical_blog.
- **Rides existing seams** — TopicSource plugin interface + `pipeline_tasks`
  queue + graph_def composition + `atoms.approval_gate`, minimizing new machinery
  (3 new atoms; everything else reused).
