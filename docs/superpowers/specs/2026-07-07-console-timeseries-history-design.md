# Console Time-Series History — Design (Sub-project C)

**Date:** 2026-07-07
**Status:** Approved design, pending implementation plan
**Scope:** Operator console (`src/cofounder_agent/console/`) + two small worker
read endpoints.

## Context

The operator console is Matt's primary operating UI; Grafana is the fallback for
deep digging. The "console as primary UI" effort was decomposed into sub-projects
(see `2026-07-04-console-reliability-design.md`): **A** (reliability foundation)
and **B** (live-contract test net) shipped. This spec covers **C — native
time-series history**: the console's own trend charts, replacing the Grafana
`/d-solo` iframe embeds on the Telemetry tab.

C is also **foundational for D and E**. Its range-query primitive (`promRange`)
and chart component (`<TimeChart>`) are what D (GPU / hardware / power) and E
(Postgres internals) compose. C ships first for that reason.

The current Telemetry surface embeds three Grafana panels (`panels2.jsx`,
`GRAFANA_EMBEDS`) that map 1:1 onto C / D / E:

| iframe                      | belongs to |
| --------------------------- | ---------- |
| `cost-analytics` · Spend    | **C**      |
| `hardware-power` · GPU      | D          |
| `database` · DB connections | E          |

**Decided (this pass): replace the embeds entirely** with native panels. C
removes the `cost-analytics` iframe and stands up the native History section; the
GPU + DB iframes remain until D / E land, then `GrafanaEmbed` is deleted. The
teardown is incremental (3 → 2 → 1 → 0).

**Decided (this pass): the broad chart set** — API HTTP RED (request rate, 5xx
error-%, p95/p99 latency), pipeline throughput, LLM spend, QA pass-rate, and
findings-by-severity.

## Goals

- A reusable **range-query adapter** (`promRange`) guarded exactly like the
  existing `promScalar` / `promVector` (never throws → honest-empty), that D and
  E inherit.
- A reusable, **zero-dependency SVG `<TimeChart>`** — the console has no chart
  library and adds no build step. **Colorblind-safe by construction** (Matt is
  colorblind): series are distinguished by stroke dash + direct end-label +
  opacity, never by color alone.
- Native trend panels for the broad set above, each on **A's polling layer**
  (`usePolledResource`) so they inherit the 8 s abort, per-panel freshness/stale
  badge, and retain-last-good behavior for free.
- **One uniform series shape** for every chart, whether the data came from
  Prometheus (`promRange`) or Postgres (the two new worker endpoints), so
  `<TimeChart>` never knows the difference.
- The Telemetry tab's Grafana history embed is **removed**, replaced by native
  panels; the surface no longer depends on Grafana for history.
- Every new `api.js` method is covered by a **B-contract manifest row**, so C
  extends the net that guards it.

## Non-goals (out of scope for C)

- GPU / hardware / power panels → **sub-project D** (composes `promRange` +
  `<TimeChart>`).
- Postgres-internals panels → **sub-project E**.
- Removing in-browser Babel / adding a build step.
- A generic "run arbitrary SQL/PromQL" console surface — the trend endpoints are
  purpose-built, not a query language.
- Real-time streaming (SSE/WebSocket) — polling at the existing cadence is the
  update mechanism, as everywhere else in the console.

## Design

### Data-source architecture

Five of the seven trends are Prometheus metrics the worker already exports;
`promRange` reaches them browser-direct (`cfg.prometheus`, `:9091`,
`/api/v1/query_range`) with no backend work. Two — QA pass-rate and findings —
live only in `audit_log` and get **purpose-built worker endpoints** whose
response is the same uniform series shape `promRange` produces.

**Canonical series shape** (the contract `<TimeChart>` consumes):

```
{ series: [ { label: string, points: [ [tEpochMs, value|null], … ] }, … ] }
```

- `points` are ascending by time; a gap (Prometheus staleness, or a SQL bucket
  with no rows) is `null`, rendered as a break in the line — never faked as 0.
- `promRange` maps a Prometheus matrix result → this shape (one `series` entry
  per result series, `metric` labels joined into `label`).
- The worker endpoints return this shape directly.

### New units

Each unit has one purpose, a defined interface, and (for non-React logic) a pure
form testable under the existing `node:test` + `vm` harness.

1. **`promRange(promql, { rangeSeconds, stepSeconds })`** — in `api.js`, beside
   `promScalar` / `promVector`. Issues `GET {prometheus}/api/v1/query_range?
query=&start=&end=&step=`. Returns `{ series: [...] }` in the canonical shape.
   **Never throws** — Prometheus unreachable or a non-200 → `{ series: [] }`
   (honest-empty), matching its siblings. Not routed through `http()` (Prometheus
   is a different origin with no OAuth), but wraps its own `AbortController` +
   timeout so a hung Prometheus can't leave a poll pending.

2. **`timeseries.js`** — new dual-mode pure-helper module (browser global +
   `module.exports`, like `kpis.js` / `telemetry.js`), holding all chart math so
   it unit-tests without a DOM:
   - `niceStep`, `deriveStep(rangeSeconds)` → `stepSeconds` targeting ~240 points
     (`clamp(round(rangeSeconds/240), 15, …)`).
   - `scaleX`/`scaleY`, `buildPath(points, box)` → SVG `d` string with **gap
     breaks** at `null`.
   - `yTicks(min,max)`, `xTicks(t0,t1,range)` → axis tick arrays.
   - `seriesBounds(series)` → `{tMin,tMax,vMin,vMax}` (ignoring nulls).
   - `isEmptySeries(series)` → true when every series is absent/all-null (drives
     the honest-empty state).
   - `dashFor(i)` → a stroke-dasharray from a fixed colorblind-safe cycle.

3. **`charts.jsx`** — new component file:
   - **`<TimeChart series stale height unit>`** — zero-dep SVG. Renders each
     series as a `<path>` (via `buildPath`) with `dashFor(i)`, a direct
     end-of-line `<text>` label, axes + ticks, and a **hover crosshair** that
     reads every series' value at the nearest sample into a small readout.
     Honest-empty → a centered "no data in range" note. `stale` → reduced opacity
     - the same stale treatment A's panels use. **Colorblind-safe**: dash +
       label + opacity carry all series identity; hue is decorative only.
   - **`<RangeControl value onChange>`** — a 4-button segmented control
     (`1h` / `6h` / `24h` / `7d`).
   - **`<HistoryPanel>`** — the composed native section: one `<RangeControl>` at
     the top whose value is local React state, and the chart grid below. Each
     chart is a child that polls its own series.

4. **Trend wire methods on `api.js`** (all return the canonical series shape;
   each becomes a B-contract manifest row). Prometheus-backed (via `promRange`):
   - `httpRateSeries(range)` — `sum(rate(poindexter_http_requests_total[<win>]))`
   - `httpErrorSeries(range)` — `sum(rate(poindexter_http_requests_total{status=~"5.."}[<win>])) / sum(rate(poindexter_http_requests_total[<win>])) * 100`
   - `httpLatencySeries(range)` — two series, p95 & p99:
     `histogram_quantile(0.9x, sum(rate(poindexter_http_request_duration_seconds_bucket[<win>])) by (le))`
   - `throughputSeries(range)` — `poindexter_posts_total{status="published"}`
   - `costSeries(range)` — `poindexter_daily_spend_usd`

   Worker-backed (audit_log). Both take **`range_seconds` + `step_seconds`** as
   query params (the frontend computes them once via `deriveStep`, so the bucket
   grid is never derived twice / drift-risked; the server does not know the
   `1h`/`6h` vocabulary):
   - `qaTrend(range)` — `GET /api/qa/trend?range_seconds=&step_seconds=` → one
     `pass %` series.
   - `findingsTrend(range)` — `GET /api/findings/trend?range_seconds=&step_seconds=`
     → one series per severity.

   `<win>` is the rate window (`deriveStep`-aligned, min `1m`). Exact label
   selectors (`status`, `route`, `le`) are verified against the live
   `/api/v1/query` at build; the metric names + labels above are confirmed
   (`metrics_exporter.py`: `HTTP_REQUESTS_TOTAL` labels `method|route|status`;
   `POSTS_TOTAL` `Gauge` by `status`).

5. **Worker time-series endpoints** (thin routes; SQL in service functions, per
   the adapter-purity ADR — no inline SQL in `routes/`):
   - **`services/qa_trend.py::get_qa_pass_trend(pool, *, range_seconds, step_seconds)`**
     → canonical series `[{label:'pass %', points:[[tMs, pct|null]]}]`. Buckets
     `audit_log` rows where `event_type='qa_pass_completed'` by an epoch-floored
     `step_seconds` window; `pct = pass / (pass+fail) * 100` per bucket, `null`
     when a bucket has no reviews. Route: `GET /api/qa/trend` in
     `routes/qa_routes.py` (new; OAuth-protected `api:read`, mirrors the existing
     read routes).
   - **`services/findings_read.py::get_findings_trend(pool, *, range_seconds, step_seconds)`**
     → one series per distinct `severity`, counts per bucket over `audit_log`
     `event_type='finding'`. Route: `GET /api/findings/trend` in
     `routes/findings_routes.py` (extends the existing findings router).
   - Bucketing mirrors the `analyticsViews` daily precedent
     (`cms_routes.py:697`) but with an epoch-floored `step_seconds` bucket so the
     SQL series aligns to the same grid as `promRange` (not `date_trunc`'s fixed
     granularities). Both service fns **clamp** their inputs (`range_seconds` ≤ 7d,
     `step_seconds` floored so bucket count ≤ ~1000) before querying — a
     client-supplied tiny step over a huge range must not become an unbounded
     scan. The route validates + clamps; the service fn is the single SQL home.

6. **Telemetry-tab restructure** (`panels2.jsx`, `app.jsx`):
   - Remove the `cost-analytics` entry from `GRAFANA_EMBEDS`.
   - Render `<HistoryPanel>` above the (now 2-iframe) `<GrafanaEmbed>` on the
     Telemetry tab. `GrafanaEmbed` is left in place for D/E's iframes and deleted
     when E lands.
   - `index.html` loads `timeseries.js` (before the `.jsx`) and `charts.jsx`.

### The panel set

`<HistoryPanel>` renders, under one shared `<RangeControl>`:

| Panel                | Series                  | Source                      |
| -------------------- | ----------------------- | --------------------------- |
| API request rate     | req/s                   | `httpRateSeries`            |
| API error rate       | 5xx %                   | `httpErrorSeries`           |
| API latency          | p95, p99 (2 series)     | `httpLatencySeries`         |
| Pipeline throughput  | published posts         | `throughputSeries`          |
| LLM spend            | daily $                 | `costSeries`                |
| QA pass-rate         | pass %                  | `qaTrend` (audit_log)       |
| Findings by severity | one series per severity | `findingsTrend` (audit_log) |

### Data flow

- Each chart: `const r = usePolledResource(() => PX.api.xSeries(range), { intervalMs, key: 'x:' + range })`. The `range` in the key means a range change is a new resource → clean refetch; A's abort guarantees a stale response can't clobber a fresher one.
- `<TimeChart series={r.data?.series} stale={r.stale} />`, with A's `<Freshness ago={…} stale={r.stale} />` in the panel header.
- `<HistoryPanel>` owns `range` state; `<RangeControl>` sets it; all charts re-key together.

### Error handling

- Prometheus unreachable → `promRange` returns `{series:[]}` → `<TimeChart>`
  renders honest-empty. Never throws (matches `promScalar`/`promVector`; A's
  Prometheus-guard acceptance criterion extends to the range helper).
- A worker trend endpoint fails → that panel flips `stale` (last-good retained),
  others stay fresh — A's per-panel isolation.
- A SQL bucket with no rows, or a Prometheus gap → `null` point → a line break,
  never a fabricated 0 (`feedback_no_dummy_data`).

### Testing

- **`timeseries.test.js`** (node:test): `deriveStep` targets ~240 points and
  clamps; `buildPath` breaks the path at `null`; `scaleX/Y` map endpoints to the
  box; `seriesBounds` ignores nulls; `isEmptySeries` true for all-null;
  `dashFor` cycles distinctly (colorblind encoding present).
- **`api.range.test.js`** (node:test + vm): `promRange` maps a matrix fixture →
  canonical shape; returns `{series:[]}` when the underlying fetch throws or
  returns non-200; builds the `query_range` URL with `start/end/step`.
- **Contract net (B):** manifest rows for all 7 trend methods — the 5
  Prometheus ones as `host:'prometheus'` range rows, `qaTrend`/`findingsTrend` as
  tier-3 read rows with recorded fixtures + (where a `response_model` exists) an
  openapi anchor.
- **Backend:** `test_qa_trend.py` / `test_findings_trend.py` — seed `audit_log`
  with `qa_pass_completed` / `finding` rows across buckets, assert bucket counts,
  `pass %` math, null-empty-bucket, and **input clamping** (oversized
  `range_seconds` / undersized `step_seconds` → bounded bucket count). Adapter-
  purity lint stays green (no inline SQL in the new routes).
- No `console-unit` regression; `prettier` + `eslint` clean.

## Acceptance criteria

1. `promRange` returns the canonical shape from a live range query and
   `{series:[]}` when Prometheus is unreachable (never throws).
2. `<TimeChart>` renders 1–N series with per-series dash + end-label; a multi-
   series chart is legible in grayscale (colorblind check); empty and stale
   states are visible and distinct.
3. The Telemetry tab shows the seven native panels under one working range
   control; changing the range refetches all charts; the `cost-analytics` Grafana
   iframe is gone.
4. `qaTrend` / `findingsTrend` return real bucketed history from `audit_log`
   (full back-history, not counters-from-now); empty buckets are `null`.
5. A simulated Prometheus outage leaves the Prometheus panels honest-empty while
   the audit_log panels still render (and vice-versa) — no whole-surface failure.
6. All new `api.js` methods have contract-net rows; backend endpoints have
   service-fn unit tests; adapter-purity lint green; `console-unit` green.

## Decided defaults

- Ranges: **1h / 6h / 24h / 7d**; `stepSeconds = clamp(round(rangeSeconds/240),
15, ∞)`; rate window `<win> = max(stepSeconds, 60) s`.
- Target ~**240 points** per series (well under Prometheus' 11 000-point cap).
- Poll interval for history panels: **30 s** (trends move slowly; cheaper than
  the 5 s health cadence).
- `null` for missing/gap points, always (no zero-fill).
- Colorblind encoding: **dash + direct label + opacity**; hue never load-bearing.
- Series shape is **canonical and identical** across Prometheus and worker
  sources.

## File map

- `console/js/api.js` — MODIFY: `promRange` + 7 trend methods.
- `console/js/timeseries.js` — NEW: pure chart-math helpers (dual-mode).
- `console/js/charts.jsx` — NEW: `TimeChart`, `RangeControl`, `HistoryPanel`.
- `console/js/panels2.jsx` — MODIFY: drop `cost-analytics` from `GRAFANA_EMBEDS`.
- `console/js/app.jsx` — MODIFY: Telemetry tab renders `<HistoryPanel>`.
- `console/index.html` — MODIFY: load `timeseries.js` + `charts.jsx`.
- `console/js/__tests__/timeseries.test.js`, `api.range.test.js` — NEW.
- `console/js/__tests__/contracts/contracts.manifest.js` — MODIFY: 7 rows (+ fixtures).
- `services/qa_trend.py` — NEW; `routes/qa_routes.py` — NEW (`GET /api/qa/trend`).
- `services/findings_read.py` — MODIFY: `get_findings_trend`;
  `routes/findings_routes.py` — MODIFY (`GET /api/findings/trend`).
- `tests/unit/services/test_qa_trend.py`, `test_findings_trend.py` — NEW.
