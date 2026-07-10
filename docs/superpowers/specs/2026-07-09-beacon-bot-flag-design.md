# Beacon bot-flag — design (PR 2 of 2)

**Date:** 2026-07-09
**Status:** approved (design), pending implementation plan
**Related:** analytics-integrity investigation (2026-07-03); PR #2110 (`fix(taps)` — GA4/GSC tap dedup, PR 1 of 2). This is PR 2 of 2, the actual "604-vs-86 console" fix.

## Problem

The first-party beacon (`CF Analytics Engine → services/jobs/sync_cloudflare_analytics.py → page_views`) powers the console "views" KPI via a raw `COUNT(*)`. It is heavily inflated by stealth scraper bots that present a normal browser `User-Agent` and therefore slip the sync job's narrow UA drop-filter (`bot/|crawler|spider|slurp|facebookexternalhit`, `sync_cloudflare_analytics.py:288-293`).

Confirmed against the DB for 2026-06-11 → 07-08 (28 days):

| Source                                     | Value           |
| ------------------------------------------ | --------------- |
| Beacon raw `page_views` (console KPI)      | **1,708**       |
| … of which one Linux `Chrome/149.0.0.0` UA | **1,532 (90%)** |
| Beacon de-botted (that one UA removed)     | 176             |
| GA4 (ground truth, all pages)              | 178             |

The scraper hammers a handful of posts (645 hits on one path, 316 on another, 280 on a third) from a single browser-looking UA. GA4's own bot filtering excludes it; our beacon does not.

Because the bot inflation is also written into ingest-fed running values (`posts.view_count` incremented per-row at `sync_cloudflare_analytics.py:348-355`; `lab_outcomes_v1.views_*_post_publish` counting `page_views`), a read-time filter alone cannot fix them — the flag must be materialized.

## Goals

- The console "views" KPI reflects **human** reader traffic, not the scraper.
- Ingest-fed reader values (`posts.view_count`, `lab_outcomes_v1`) are de-botted.
- Bot classification is **materialized** (a column), tunable via `app_settings`, and auditable (bot rows retained + labelled, not silently dropped — per `feedback_self_heal_not_suppress` / `feedback_dont_silence_fix_dedup`).
- Pipeline **liveness/anomaly/freshness** signals keep seeing all traffic (bots included), so a bot-only period cannot be misread as a dead pipeline.

## Non-goals (this PR — kept surgical, per approval)

- No new Grafana panels (bot-vs-human split visibility is a follow-up).
- No IP-based detection — the beacon has no IP column; CF bot-score is plan-gated. Near-term signal is the `(user_agent, path)` flood-cap only.
- No burst/velocity heuristic (future enhancement).
- No `page_views` retention policy for aged bot rows (future; note below).

## Core principle — two questions, two surfaces

| Question                                                    | Reads                  | Consumers                                                                                                                                                                                                                                                                   |
| ----------------------------------------------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **"Is the pipe flowing?"** (liveness / anomaly / freshness) | **raw `page_views`**   | Grafana traffic-anomaly + capture-dead alerts (`alert-rules.yml:331,406`), Mission-Control "Page-views received" stat (`mission-control.json:416`), pipeline funnel `is_viewed` (`pipeline-merged.json:4280,4339`), DB freshness panel (`database.json:1112`), DB row-count |
| **"How many readers?"** (reader KPI)                        | **`page_views_human`** | console `/api/analytics/views` (`cms_routes.py:698,705,713`), `posts.view_count`, `lab_outcomes_v1.views_*_post_publish`                                                                                                                                                    |

A bot view still proves the URL is live and the ingest is fresh — so liveness stays on raw. Only reader-facing counts move to the human view.

## Why a sweep job, not ingest-time flagging

Flood detection is inherently **windowed**: a single 5-minute sync batch (≤5000 rows) cannot tell that one `(UA, path)` has accumulated 645 hits over 28 days. So the classification runs as a periodic sweep over accumulated rows, not inside the per-batch insert. The sync job stays a pure ingest.

## Components

### 1. Schema migration (pure DDL — `YYYYMMDD_HHMMSS_page_views_is_bot.py`)

- `ALTER TABLE page_views ADD COLUMN is_bot boolean NOT NULL DEFAULT false`, `ADD COLUMN bot_reason text`, `ADD COLUMN flagged_at timestamptz`.
- Partial index for the human read path: `CREATE INDEX idx_page_views_human_created ON page_views (created_at) WHERE is_bot = false` and `CREATE INDEX idx_page_views_human_slug ON page_views (slug) WHERE is_bot = false`.
- `CREATE VIEW page_views_human AS SELECT id, path, slug, referrer, user_agent, created_at FROM page_views WHERE is_bot = false` (explicit column list, not `SELECT *`, so a future `page_views` column doesn't silently alter the view).
- `CREATE OR REPLACE VIEW lab_outcomes_v1 AS …` — reproduce the baseline body verbatim (`0000_baseline.schema.sql:1524-1565`) with the single change `FROM public.page_views pv` → `FROM public.page_views_human pv`. Column list/order unchanged, so the dependent view `experiment_variant_scorecard_v1` is unaffected.
- Update `0000_baseline.schema.sql` (add the three columns to the `page_views` `CREATE TABLE`, add the `page_views_human` view, update the `lab_outcomes_v1` body) and add `("page_views", "is_bot")` to the parity test `_REQUIRED_COLUMNS` (`tests/integration_db/test_baseline_flatten_parity.py`). Data mutation (backfill) is NOT in the migration — it lives in the job.
- Add the three columns to the job's inline `CREATE TABLE IF NOT EXISTS page_views` (`sync_cloudflare_analytics.py:184-192`) for fresh-install parity.

### 2. `FlagBotPageViewsJob` (new — `services/jobs/flag_bot_page_views.py`, registered in `plugins/registry.py` `_SAMPLES`, default cadence 15 min)

Three passes per run, gated by `beacon_bot_flag_enabled`:

1. **Windowed flood pass.** Over the last `beacon_flood_window_hours`, group by `(user_agent, path)`; any group with `COUNT(*) > beacon_flood_cap_per_window` → set `is_bot = true, bot_reason = 'flood:ua_path', flagged_at = now()` for **all rows in that group** (Matt's choice: flag whole group, not just the excess). Flagging is **monotonic** — the job only ever sets `is_bot` true, never resets it to false, to avoid flapping. (Un-flagging after a cap change is a manual `UPDATE … SET is_bot=false WHERE bot_reason='flood:ua_path'` + re-sweep.)
2. **One-time backfill.** Guarded by an `app_settings` sentinel (`beacon_bot_flag_backfilled`, set to `'true'` after first success). Same `(UA, path)` group logic over **all history** with cap `beacon_flood_backfill_cap`, to catch the existing 1,532-row bot. Runs once, then no-ops.
3. **Recompute `posts.view_count`** authoritatively from `page_views_human`: `UPDATE posts SET view_count = COALESCE((SELECT COUNT(*) FROM page_views_human pv WHERE pv.slug = posts.slug), 0)`. This makes the flag job the **single writer** of `view_count` (bot-only posts reset to 0).

**Isolation:** the flood classification is a pure helper — `classify_flood_groups(counts: Mapping[tuple[str,str], int], cap: int) -> set[tuple[str,str]]` — unit-tested independently of the DB wrapper.

### 3. Sync job edit (`sync_cloudflare_analytics.py`)

Remove the incremental `posts.view_count` bump block (lines 344-355) so the flag job is the sole `view_count` writer (no double-count/drift). The obvious-UA drop-filter stays as-is (those rows are not the problem and needn't be stored).

**Assumption to verify in the plan:** `page_views` is the _only_ source of `posts.view_count` (historically the deleted `/api/track/view` handler, now the sync bump). Grep-confirm no other writer before making the recompute authoritative.

### 4. Console repoint (`cms_routes.py`)

Change `FROM page_views` → `FROM page_views_human` in the three queries at lines 698, 705, 713 (daily views, top posts, top referrers).

## Tunables (`services/settings_defaults.py`, all DB-configurable)

| Key                                               | Default | Meaning                                                       |
| ------------------------------------------------- | ------- | ------------------------------------------------------------- |
| `beacon_bot_flag_enabled`                         | `true`  | Master switch for the flag job                                |
| `beacon_flood_window_hours`                       | `24`    | Rolling window for the flood pass                             |
| `beacon_flood_cap_per_window`                     | `20`    | Max `(UA,path)` hits/window before the whole group is flagged |
| `beacon_flood_backfill_cap`                       | `30`    | All-history `(UA,path)` cap for the one-time backfill         |
| `plugin.job.flag_bot_page_views.interval_seconds` | `900`   | Job cadence                                                   |
| `plugin.job.flag_bot_page_views.enabled`          | `true`  | Job registration enable                                       |

Cap rationale: at current traffic no legitimate `(UA, path)` pair reaches 20 same-UA hits/day; the bot pairs are in the hundreds. Both caps are generous headroom and tunable up as traffic grows.

## Data flow

```
CF beacon → CF Analytics Engine
   → sync_cloudflare_analytics (5m): INSERT page_views (is_bot defaults false); obvious-UA rows still dropped
   → FlagBotPageViews (15m): flood-pass sets is_bot=true on (UA,path)-over-cap groups; recompute posts.view_count from page_views_human
Reads:
   raw page_views      → liveness/anomaly/freshness (Grafana alerts, received-stat, funnel is_viewed, DB freshness)
   page_views_human    → console /api/analytics/views, lab_outcomes_v1, posts.view_count
```

## Testing

- **Unit** (`tests/unit/services/jobs/test_flag_bot_page_views_job.py`): `classify_flood_groups` flags an over-cap pair, spares sparse real traffic, respects the cap boundary (exactly-cap not flagged, cap+1 flagged); whole-group semantics (all rows of the pair flagged, not just excess); disabled master switch is a no-op; backfill sentinel makes pass 2 run once then no-op.
- **integration_db** (`tests/integration_db/test_page_views_human_view.py`): seed human + flagged rows; assert `page_views_human` excludes flagged; assert `lab_outcomes_v1.views_*_post_publish` excludes flagged; assert `posts.view_count` recompute equals human count and resets a bot-only post to 0.
- **Route** (`tests/unit/routes/test_cms_routes.py` extension): `/api/analytics/views` counts only human rows.
- **Parity**: `test_baseline_flatten_parity.py` gains `("page_views","is_bot")`.

## Rollout / verification

1. Land migration → `is_bot` column + `page_views_human` + repointed `lab_outcomes_v1`.
2. First `FlagBotPageViews` run backfills history → the 1,532-row bot flagged; `posts.view_count` recomputed.
3. Verify: `SELECT COUNT(*) FILTER (WHERE is_bot), COUNT(*) FROM page_views` ≈ 1,532 / 1,708 for the window; console `/api/analytics/views?days=28` daily sum ≈ 176 (≈ GA 178); a bot-only post shows `view_count = 0`.
4. Confirm liveness alerts + freshness panel unchanged (still on raw).

## Risks & mitigations

- **False positives** (a genuinely popular page exceeding the cap, all same UA): whole-group flagging drops those views. Mitigated by a generous, tunable cap and `bot_reason` audit; monotonic-only flagging plus a documented manual reset path. Bias is deliberately toward under-count over inflation.
- **`view_count` recompute clobbers a non-beacon source**: verified none exists before making it authoritative (plan step).
- **`CREATE OR REPLACE VIEW` on `lab_outcomes_v1`**: safe only if the column list/order is byte-identical to the baseline; the migration reproduces the body verbatim with the single FROM swap and is validated by the existing `lab_outcomes_v1` integration test.
- **Bot rows accumulate** (retained for audit): acceptable now; a `retention_policies` prune for `page_views WHERE is_bot` past N days is a noted follow-up, not this PR.

## Out of scope / follow-ups

- Grafana bot-vs-human split panels (Mission Control).
- Burst/velocity heuristic; optional IP column on the beacon if CF plan changes.
- `page_views` bot-row retention policy.
