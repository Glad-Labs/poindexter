# Grafana dashboard cleanup — 2026-05-06

Audit + cleanup of the 7 self-hosted Grafana dashboards. Driven by Matt's
screenshot complaints: empty tables, ugly chart-type-spam, weird sizing,
and Mission Control being 10/13 stat panels (violates
`feedback_dashboard_variety`).

## Results at a glance

| dashboard            | uid                    | panels (before) | panels (after) |     net |
| -------------------- | ---------------------- | --------------: | -------------: | ------: |
| Mission Control      | `mission-control`      |              13 |             15 |      +2 |
| Pipeline             | `pipeline-merged`      |              43 |             43 |       0 |
| System Health        | `system-health-merged` |              88 |             60 |     -28 |
| Observability        | `observability-merged` |              34 |             28 |      -6 |
| Cost & Analytics     | `cost-analytics`       |              21 |             21 |       0 |
| Auto-Publish Gate    | `auto-publish-gate`    |               7 |              7 |       0 |
| Integrations & Admin | `integrations-admin`   |              23 |             23 |       0 |
| **Total**            |                        |         **229** |        **197** | **-32** |

29 panels deleted, 3 added, 1 panel had its chart type swapped, 1 SQL
query bugfix. The post-cleanup dashboards have been pushed to the
running Grafana via `/api/dashboards/db` and re-pulled to confirm panel
counts match disk.

### Type-mix shift

| dashboard       | before                                    | after                                                     |
| --------------- | ----------------------------------------- | --------------------------------------------------------- |
| Mission Control | stat:10 gauge:1 bargauge:1 text:1         | stat:9 timeseries:2 gauge:1 bargauge:1 table:1 piechart:1 |
| System Health   | stat:41 table:24 gauge:8 timeseries:6 ... | stat:30 table:7 gauge:8 timeseries:6 ...                  |
| Observability   | stat:14 timeseries:11 ...                 | stat:11 timeseries:8 ...                                  |

Mission Control went from "stat-spam" (77% stats) to a balanced mix (60%
stats with 4 different non-stat panel types). System Health lost 17
redundant tables and 11 stats that were duplicating panels elsewhere.

## What changed per dashboard

### Mission Control (`mission-control`)

- **DELETED panel 13 (text, "Drill into")** — Grafana's built-in
  cross-dashboard link bar already covers this.
- **TYPE SWAP panel 7 ("Tasks in 24h")**: stat → timeseries (bars).
  Same query, but operators get a sparkline instead of a single number,
  so a stalled-pipeline pattern is visible without drilling.
- **NEW panel 14 ("Recently published", table)** — last 5 published
  posts with category + view count.
- **NEW panel 15 ("Published mix by category", donut)** — share of
  published posts by category.
- **NEW panel 16 ("LLM spend (last 7 days)", timeseries bars)** — daily
  cost in USD from `cost_logs`. Covers the "is the system burning money?"
  glance Matt wanted on the home dashboard.

### Pipeline (`pipeline-merged`)

- **QUERY FIX panel 49 ("Recent batch decisions (7 days)")** — the SQL
  joined `internal_topic_candidates ic` and selected `ic.title`, but
  that table's column is `distilled_topic`. Replaced
  `ic.title` → `ic.distilled_topic`. Was returning a SQL error before.

### System Health (`system-health-merged`)

Largest cleanup. 28 panels removed across three categories:

**Broken-schema queries (delete loud, not silently fixed):**

- 14 ("Podcast backfill last run"), 15 ("Video backfill last run") —
  query `app_settings.plugin_job_last_run_backfill_*` keys that don't
  exist (real keys are `idle_last_run_podcast_backfill` /
  `idle_last_run_video_backfill`). Not adding back as "fix" — these
  jobs are tracked in the "Scheduled jobs — last run times" table on
  the same dashboard already.
- 80 ("Newsletter Subscribers") — selected `status` column but the
  table has `verified` (bool). 2-row table; not worth a panel.
- 83 ("Prompts" stat) + 88 ("Prompts" table) — query non-existent
  `prompt_templates` table. UnifiedPromptManager stores prompts in
  Langfuse + YAML now, not a SQL table.
- 84 ("Stages" stat) — queries non-existent `pipeline_stages` table.

**Pure data-browser sprawl (pgAdmin's job, not Grafana's):**

- 67 ("Full Audit Log"), 68 ("Errors Only") — duplicate of the
  Observability dashboard's error feed.
- 70 ("Published Posts"), 71 ("Cost Logs"), 72 ("Brain Knowledge"),
  73 ("Brain Decisions"), 74 ("App Settings (non-secret)"),
  76 ("Quality Evaluations"), 77 ("Categories"),
  78 ("Users", which is a 0-row table), 79 ("Task Status History"),
  81 ("Awaiting Approval", duplicates Pipeline panel 6),
  89 ("Permissions"), 90 ("Agents") — 12 tables that just dumped raw
  rows from a table with no aggregation. They're useful in pgAdmin,
  not on a dashboard.

**Stat-row deduplication:**

- 75 ("Page Views") — 0 rows for 7+ days (ViewTracker beacon is
  AdSense-gated; not active yet).
- 56, 58, 59 — gaming-detection stat-spam reduced from 5 panels to 2.
- 82 ("Settings"), 85 ("Agents"), 86 ("Perms"), 87 ("Alerts") — last
  row of stat-counters that duplicated info already visible elsewhere.

### Observability (`observability-merged`)

- **DELETED 6 panels (8, 9, 35, 36, 37, 38)** — the Uptime-Kuma
  monitor section. Queries `monitor_status` / `monitor_response_time` /
  `monitor_cert_days_remaining`, but the Kuma Prometheus exporter is
  not wired up, and there's a direct link to the Kuma UI from Mission
  Control. We can re-add when the exporter ships (poindexter-side
  feature, not done yet).

## Empty-but-pending panels (kept on purpose)

These remained empty post-cleanup but the data is plausibly going to
arrive — deleting them now would just re-create them later.

| dash       | panel                             | why empty                                                                                  |
| ---------- | --------------------------------- | ------------------------------------------------------------------------------------------ |
| mission    | Alerts firing (1h)                | Empty == healthy. Goes hot the second Alertmanager fires.                                  |
| mission    | Hard rejects (24h)                | Empty == calibrated baseline. 5-20/day during normal autonomous operation.                 |
| pipeline   | Posts Awaiting Your Approval      | Empty because nothing currently in `posts.awaiting_gate IS NOT NULL`. Goes hot at gate.    |
| pipeline   | Quality Distribution (Pending)    | Same — depends on the queue having at least one entry.                                     |
| pipeline   | Hallucination Warnings (rate 5m)  | Metric `content_validator_warnings_total` exists; no warnings have fired in last 5m.       |
| observ     | Spans/sec, Active services, etc.  | `traces_spanmetrics_*` only exists when spans flow. Tempo metrics-generator IS configured. |
| sys-health | Validator warnings (15m rate)     | Same metric as pipeline, idle right now.                                                   |
| sys-health | Active Queries (pg_stat_activity) | Empty when the brain DB is idle. Active connections is the non-zero canary.                |

## Cross-dashboard duplication

Same query, multiple dashboards. Kept on purpose unless noted.

- The 6-panel service-status grid (`up{job="..."}`) appears on **Mission
  Control** AND **System Health**. Kept — they serve different audiences
  (operator-glance vs. health-deep-dive).
- Queue-depth + tasks-in-24h appears on Mission Control AND System
  Health. Kept for the same reason.
- "Published Posts" sat on **Pipeline** AND **System Health**. Removed
  the System Health duplicate — it was a raw-row table while Pipeline
  has the curated quality/throughput context.
- "Newsletter Subscribers" data-browser appeared only on System Health
  with a broken column reference. Removed; the live count surfaces in
  the Cost dashboard's audience-size context if needed.

## How to keep this from regressing

The codebase will produce these dashboards correctly going forward IF:

1. **Schema changes that affect dashboard queries get a smoke test.**
   Today the dashboards directly hit Postgres / Prometheus, but there's
   no CI that runs every `rawSql` against a fresh DB. A 50-line CI step
   that loops through `infrastructure/grafana/dashboards/*.json` and
   runs each query against the migrations-smoke DB would have caught
   the `ic.title`, `prompt_templates`, `pipeline_stages`, and
   `newsletter_subscribers.status` regressions before they shipped.
2. **New panels go through `add_panel(...)` helpers in
   `scripts/grafana_cleanup.py`-style transformers**, so we get a
   single SQL definition and consistent visualization defaults rather
   than each operator hand-rolling another stat panel.
3. **`feedback_dashboard_variety` and `feedback_dashboard_layout` get
   linked from `infrastructure/grafana/README.md`** so the next agent
   editing dashboards reads the rules.

## Verification

```
# disk vs live, post-cleanup
auto-publish-gate.json   disk= 7  live= 7  OK
cost-analytics.json      disk=21  live=21  OK
integrations-admin.json  disk=23  live=23  OK
mission-control.json     disk=15  live=15  OK
observability-merged.json disk=28  live=28  OK
pipeline-merged.json     disk=43  live=43  OK
system-health-merged.json disk=60  live=60  OK
```

```
# query-error count, post-cleanup
0 SQL errors (down from 4: ic.title, prompt_templates,
                            pipeline_stages, newsletter_subscribers.status)
```
