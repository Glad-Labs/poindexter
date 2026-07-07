# Console Time-Series History (Sub-project C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Telemetry tab's Grafana `/d-solo` iframe embeds with native, zero-dependency SVG trend panels driven by a reusable range-query primitive.

**Architecture:** A pure chart-math module (`timeseries.js`) + a guarded Prometheus range adapter (`promRange` in `api.js`) feed a colorblind-safe `<TimeChart>` (SVG). Seven trend wire methods on `api.js` return one canonical series shape — five from Prometheus, two from new `audit_log` worker endpoints. Panels ride sub-project A's `usePolledResource` for freshness/abort. The `cost-analytics` embed is removed; GPU + DB embeds stay for D/E.

**Tech Stack:** No-build React 18 + in-browser Babel (console), `node:test` + `node:vm` (console unit tests), FastAPI + asyncpg (worker), pytest + `db_pool` (backend). Zero new runtime dependencies.

## Global Constraints

- **Zero runtime dependencies** — the console adds no build step and no chart library; charts are hand-rolled SVG.
- **Colorblind-safe** — series are distinguished by stroke dash + direct end-label + opacity, NEVER by color alone (Matt is colorblind).
- **Never throw on telemetry reads** — `promRange` returns `{ series: [] }` on any failure, exactly like `promScalar`/`promVector`.
- **`pick(liveFn, mockFn)` seam** — every network method is gated: live branch hits the wire, mock branch returns honest-empty (`{series:[]}`) — never fabricated series (feedback_no_dummy_data). Matches `services()`/`gpu()`/`budget()`.
- **Idiom** — closure helpers (`promRange`, `pick`, `http`, `rangeOpts`) are bare-name functions in the IIFE; `PX.api` methods call them by bare name, never `this.`.
- **Canonical series shape** everywhere: `{ series: [ { label: string, points: [ [tEpochMs, value|null], … ] } ] }`. `points` ascending by time; gaps are `null` (line break), never zero-filled — except a **count** series (findings), where an empty bucket is a real `0`.
- **No inline SQL in `routes/`** (adapter-purity ADR) — all SQL lives in service functions.
- **Range defaults:** `1h`/`6h`/`24h`/`7d` → `rangeSeconds` `3600`/`21600`/`86400`/`604800`; `stepSeconds = max(15, round(rangeSeconds/240))`; rate window `winFor = max(stepSeconds, 60)` seconds.
- **Every new `api.js` method gets a B-contract manifest row** (request-only — the 7 trend endpoints declare no console-consumed `response_model` schema, matching `posts`/`analyticsViews`).
- **Linear history, TDD, frequent commits.** Branch: `feat/console-timeseries-history` (already created; the spec is committed there).

---

## File Structure

**Frontend (`src/cofounder_agent/console/`):**

- `js/timeseries.js` — NEW. Pure chart-math (dual-mode global + `module.exports`, like `kpis.js`). Owns: `RANGES`, `deriveStep`, `scaleX/scaleY`, `buildPath`, `seriesBounds`, `isEmptySeries`, `dashFor`, `yTicks`, `xTicks`, `matrixToSeries`.
- `js/api.js` — MODIFY. Add closure fns `promRange`/`rangeOpts`/`winFor`/`labelledRange` + 7 trend methods on `PX.api`.
- `js/charts.jsx` — NEW. `<TimeChart>`, `<RangeControl>`, `<HistoryChart>`, `<HistoryPanel>`.
- `js/panels2.jsx` — MODIFY. Drop `cost-analytics` from `GRAFANA_EMBEDS` (line ~1592).
- `js/app.jsx` — MODIFY. Render `<HistoryPanel>` in `#sec-telemetry` (line ~1466).
- `index.html` — MODIFY. Load `timeseries.js` (before `api.js`) + `charts.jsx` (after `panels2.jsx`).
- `js/__tests__/timeseries.test.js` — NEW.
- `js/__tests__/api.range.test.js` — NEW.
- `js/__tests__/contracts/contract-runtime.js` — MODIFY. Extend `assertRequest` for range queries.
- `js/__tests__/contracts/run-contracts.test.js` — MODIFY. Preload `timeseries.js`.
- `js/__tests__/contracts/contracts.manifest.js` — MODIFY. 7 request-only rows.

**Backend (`src/cofounder_agent/`):**

- `services/qa_trend.py` — NEW. `get_qa_pass_trend` + `_clamp`.
- `routes/qa_routes.py` — NEW. `GET /api/qa/trend`.
- `services/findings_read.py` — MODIFY. Add `get_findings_trend`.
- `routes/findings_routes.py` — MODIFY. Add `GET /api/findings/trend`.
- `utils/route_registration.py` — MODIFY. Register `qa_routes` in `_WORKER_ROUTES`.
- `tests/unit/services/test_qa_trend.py`, `test_findings_trend.py` — NEW (mirror `test_findings_read.py`).

---

## Task 1: `timeseries.js` pure chart-math helpers

**Files:**

- Create: `src/cofounder_agent/console/js/timeseries.js`
- Test: `src/cofounder_agent/console/js/__tests__/timeseries.test.js`

**Interfaces:**

- Produces:
  - `RANGES = { '1h':3600, '6h':21600, '24h':86400, '7d':604800 }`
  - `deriveStep(rangeSeconds) -> number` (seconds; `max(15, round(rangeSeconds/240))`)
  - `seriesBounds(series) -> {tMin,tMax,vMin,vMax}` (ignores `null` values; `null` bounds when empty)
  - `isEmptySeries(series) -> boolean` (true when no series or every point null)
  - `scaleX(t, tMin, tMax, box) -> number`, `scaleY(v, vMin, vMax, box) -> number` (`box = {x,y,w,h}`)
  - `buildPath(points, {tMin,tMax,vMin,vMax}, box) -> string` (SVG `d`; `null` value starts a new sub-path — a gap)
  - `dashFor(i) -> string` (stroke-dasharray from a fixed cycle; `i=0` → `''` solid)
  - `yTicks(vMin, vMax, n=4) -> number[]`, `xTicks(tMin, tMax, n=4) -> number[]`
  - `matrixToSeries(promMatrix, fallbackLabel) -> [{label, points}]` (Prometheus `result` array → canonical series; seconds→ms)

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/timeseries.test.js`:

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const T = require('../timeseries.js');

test('deriveStep targets ~240 points and clamps to 15s floor', () => {
  assert.equal(T.deriveStep(3600), 15); // 3600/240=15
  assert.equal(T.deriveStep(604800), Math.round(604800 / 240)); // 2520
  assert.equal(T.deriveStep(600), 15); // 600/240=2.5 -> clamp 15
});

test('seriesBounds ignores nulls', () => {
  const s = [
    {
      label: 'a',
      points: [
        [1000, 5],
        [2000, null],
        [3000, 9],
      ],
    },
  ];
  const b = T.seriesBounds(s);
  assert.equal(b.tMin, 1000);
  assert.equal(b.tMax, 3000);
  assert.equal(b.vMin, 5);
  assert.equal(b.vMax, 9);
});

test('isEmptySeries true when all points null or no series', () => {
  assert.equal(T.isEmptySeries([]), true);
  assert.equal(T.isEmptySeries([{ label: 'a', points: [[1, null]] }]), true);
  assert.equal(T.isEmptySeries([{ label: 'a', points: [[1, 2]] }]), false);
});

test('buildPath breaks the path at a null (gap), not zero-fill', () => {
  const box = { x: 0, y: 0, w: 100, h: 50 };
  const bounds = { tMin: 0, tMax: 3, vMin: 0, vMax: 10 };
  const d = T.buildPath(
    [
      [0, 5],
      [1, null],
      [2, 5],
      [3, 5],
    ],
    bounds,
    box
  );
  // exactly two M commands: one before the gap, one after.
  assert.equal((d.match(/M/g) || []).length, 2);
});

test('dashFor cycles distinctly; index 0 is solid', () => {
  assert.equal(T.dashFor(0), '');
  assert.notEqual(T.dashFor(1), T.dashFor(0));
  assert.notEqual(T.dashFor(2), T.dashFor(1));
});

test('matrixToSeries maps a Prometheus matrix to canonical series (sec->ms)', () => {
  const matrix = [
    {
      metric: { quantile: '0.95' },
      values: [
        [1000, '0.2'],
        [1060, '0.3'],
      ],
    },
  ];
  const out = T.matrixToSeries(matrix, 'p95');
  assert.equal(out.length, 1);
  assert.equal(out[0].points[0][0], 1000 * 1000); // seconds -> ms
  assert.equal(out[0].points[0][1], 0.2);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/timeseries.test.js`
Expected: FAIL — `Cannot find module '../timeseries.js'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/cofounder_agent/console/js/timeseries.js`:

```js
/* Pure chart-math for the console time-series surface. Dual-mode (browser global
   + module.exports) so it unit-tests on node:test with no DOM, exactly like
   js/kpis.js and js/telemetry.js. Loaded by index.html before api.js. */
(function () {
  const RANGES = { '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800 };

  // ~240 samples across the range, 15s floor (Prometheus caps at 11000 points).
  function deriveStep(rangeSeconds) {
    return Math.max(15, Math.round(rangeSeconds / 240));
  }

  // Bounds over every non-null point across all series. Empty -> null bounds.
  function seriesBounds(series) {
    let tMin = Infinity,
      tMax = -Infinity,
      vMin = Infinity,
      vMax = -Infinity;
    (series || []).forEach((s) =>
      (s.points || []).forEach(([t, v]) => {
        if (t < tMin) tMin = t;
        if (t > tMax) tMax = t;
        if (v != null && Number.isFinite(v)) {
          if (v < vMin) vMin = v;
          if (v > vMax) vMax = v;
        }
      })
    );
    if (!Number.isFinite(tMin))
      return { tMin: null, tMax: null, vMin: null, vMax: null };
    if (!Number.isFinite(vMin)) {
      vMin = 0;
      vMax = 1;
    }
    return { tMin, tMax, vMin, vMax };
  }

  function isEmptySeries(series) {
    return !(series || []).some((s) =>
      (s.points || []).some(([, v]) => v != null && Number.isFinite(v))
    );
  }

  function scaleX(t, tMin, tMax, box) {
    const span = tMax - tMin || 1;
    return box.x + ((t - tMin) / span) * box.w;
  }
  function scaleY(v, vMin, vMax, box) {
    const span = vMax - vMin || 1;
    return box.y + box.h - ((v - vMin) / span) * box.h;
  }

  // SVG `d` string. A null value ends the current sub-path; the next finite
  // value starts a fresh `M` — an honest gap, never a line drawn through nothing.
  function buildPath(points, bounds, box) {
    let d = '';
    let pen = false; // is the pen down (mid sub-path)?
    (points || []).forEach(([t, v]) => {
      if (v == null || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      const x = scaleX(t, bounds.tMin, bounds.tMax, box).toFixed(1);
      const y = scaleY(v, bounds.vMin, bounds.vMax, box).toFixed(1);
      d += (pen ? 'L' : 'M') + x + ',' + y + ' ';
      pen = true;
    });
    return d.trim();
  }

  // Colorblind-safe: identity carried by dash pattern (+ a direct label + opacity
  // at the call site), never hue. Index 0 solid; the rest distinct dashes.
  const DASHES = ['', '5,3', '1.5,2.5', '7,3,1.5,3', '3,2'];
  function dashFor(i) {
    return DASHES[i % DASHES.length];
  }

  function _ticks(min, max, n) {
    if (min == null || max == null) return [];
    const span = max - min || 1;
    const out = [];
    for (let i = 0; i <= n; i++) out.push(min + (span * i) / n);
    return out;
  }
  function yTicks(vMin, vMax, n = 4) {
    return _ticks(vMin, vMax, n);
  }
  function xTicks(tMin, tMax, n = 4) {
    return _ticks(tMin, tMax, n);
  }

  // Prometheus matrix result -> canonical series. Seconds -> ms; label from the
  // metric's non-__name__ labels, else fallbackLabel.
  function matrixToSeries(result, fallbackLabel) {
    return (result || []).map((r) => {
      const m = r.metric || {};
      const parts = Object.keys(m)
        .filter((k) => k !== '__name__')
        .map((k) => k + '=' + m[k]);
      const label = parts.length ? parts.join(',') : fallbackLabel || 'value';
      const points = (r.values || []).map(([t, v]) => [
        Math.round(Number(t) * 1000),
        v == null ? null : Number(v),
      ]);
      return { label, points };
    });
  }

  const api = {
    RANGES,
    deriveStep,
    seriesBounds,
    isEmptySeries,
    scaleX,
    scaleY,
    buildPath,
    dashFor,
    yTicks,
    xTicks,
    matrixToSeries,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') (window.PX || (window.PX = {})).ts = api;
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/timeseries.test.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/timeseries.js src/cofounder_agent/console/js/__tests__/timeseries.test.js
git commit -m "feat(console): timeseries.js pure chart-math helpers"
```

---

## Task 2: `promRange` range-query adapter

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (add `promRange` beside `promVector`, ~line 210; export on `PX.api`, ~line 428)
- Test: `src/cofounder_agent/console/js/__tests__/api.range.test.js`

**Interfaces:**

- Consumes: `window.PX.ts.matrixToSeries` (Task 1) — the test harness `preload`s `timeseries.js` so `window.PX.ts` exists; `HTTP_TIMEOUT_MS`/`cfg`/`fetch` are already in api.js's IIFE scope.
- Produces: closure fn `promRange(promql, { rangeSeconds, stepSeconds }) -> Promise<{series:[{label,points}]}>`. Never throws → `{series:[]}` on any failure. `GET {prometheus}/api/v1/query_range?query=&start=&end=&step=` with its own 8s AbortController (`HTTP_TIMEOUT_MS`). Also exported on `PX.api.promRange` for D/E reuse.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/api.range.test.js`:

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadApiWithFetch } = require('./contracts/contract-runtime.js');

// api.js references window.PX.ts — preload timeseries.js into the sandbox first.
const OPTS = { preload: ['timeseries.js'] };

test('promRange maps a query_range matrix to canonical series', async () => {
  let captured = '';
  const fetchImpl = async (url) => {
    captured = String(url);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          resultType: 'matrix',
          result: [
            {
              metric: {},
              values: [
                [1000, '1.5'],
                [1015, '2.5'],
              ],
            },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(
    fetchImpl,
    { px_prom: 'http://prom:9091' },
    OPTS
  );
  const out = await api.promRange('up', {
    rangeSeconds: 3600,
    stepSeconds: 15,
  });
  assert.ok(captured.includes('/api/v1/query_range'));
  assert.ok(captured.includes('step=15'));
  assert.equal(out.series[0].points[0][0], 1000 * 1000);
  assert.equal(out.series[0].points[0][1], 1.5);
});

test('promRange returns {series:[]} when the fetch throws (never throws)', async () => {
  const fetchImpl = async () => {
    throw new Error('prometheus down');
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.promRange('up', {
    rangeSeconds: 3600,
    stepSeconds: 15,
  });
  // Array.isArray is cross-realm-safe; deepEqual is not (out is a vm-realm object).
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});

test('promRange returns {series:[]} on a non-200', async () => {
  const fetchImpl = async () => ({
    ok: false,
    status: 503,
    json: async () => ({}),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.promRange('up', {
    rangeSeconds: 3600,
    stepSeconds: 15,
  });
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: FAIL — `api.promRange is not a function`.

- [ ] **Step 3: Write minimal implementation**

In `src/cofounder_agent/console/js/api.js`, after `promVector` (its closing `}` near line 210), add:

```js
// Prometheus RANGE query → canonical series {series:[{label,points:[[tMs,v|null]]}]}.
// The reusable history primitive (sub-projects C/D/E). Best-effort like its
// instant-query siblings: Prometheus unreachable / non-200 / abort → {series:[]},
// never throws. Own AbortController — a hung Prometheus can't hang a poll.
async function promRange(promql, opts) {
  const o = opts || {};
  const end = Math.floor(Date.now() / 1000);
  const start = end - (o.rangeSeconds || 3600);
  const step = o.stepSeconds || 60;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS);
  try {
    const u =
      cfg.prometheus +
      '/api/v1/query_range?query=' +
      encodeURIComponent(promql) +
      '&start=' +
      start +
      '&end=' +
      end +
      '&step=' +
      step;
    const res = await fetch(u, { signal: ctrl.signal });
    if (!res.ok) return { series: [] };
    const j = await res.json();
    const result = (j && j.data && j.data.result) || [];
    return { series: window.PX.ts.matrixToSeries(result) };
  } catch {
    return { series: [] }; // unreachable / aborted → honest-empty, never throw.
  } finally {
    clearTimeout(timer);
  }
}
```

Then export it on `PX.api` — extend the existing `promScalar, promVector,` lines (~427):

```js
    promScalar,
    promVector,
    promRange,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.range.test.js
git commit -m "feat(console): promRange range-query adapter (guarded, D/E reuse)"
```

---

## Task 3: Five Prometheus trend methods + harness range support + contract rows

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (closure `rangeOpts`/`winFor`/`labelledRange` near `promRange`; 5 methods on `PX.api` after `gpu()`)
- Modify: `src/cofounder_agent/console/js/__tests__/api.range.test.js` (method tests)
- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js` (`assertRequest` range branch)
- Modify: `src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js` (preload `timeseries.js`)
- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (5 rows)

**Interfaces:**

- Consumes: `promRange` (Task 2), `pick` (existing closure), `window.PX.ts.{RANGES,deriveStep}` (Task 1).
- Produces (each `(range) -> Promise<{series}>`, `pick`-wrapped like `gpu()`):
  - `httpRateSeries` · `httpErrorSeries` · `httpLatencySeries` (2 series p95+p99) · `throughputSeries` · `costSeries`
  - closure helpers: `rangeOpts(range) -> {rangeSeconds,stepSeconds}`, `winFor(o) -> '<n>s'`, `labelledRange(promql, opts, label) -> Promise<{series}>`

- [ ] **Step 1: Write the failing test** — append to `api.range.test.js`:

```js
test('httpRateSeries issues a query_range for the request-rate PromQL', async () => {
  let q = '';
  const fetchImpl = async (url) => {
    q = decodeURIComponent(new URL(url).searchParams.get('query') || '');
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { result: [] } }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.httpRateSeries('1h');
  assert.match(q, /rate\(poindexter_http_requests_total\[/);
  assert.ok(Array.isArray(out.series) && out.series.length === 0); // empty → honest-empty
});

test('httpLatencySeries merges p95 + p99 into two labeled series', async () => {
  const fetchImpl = async (url) => {
    const query = decodeURIComponent(
      new URL(url).searchParams.get('query') || ''
    );
    const quant = query.includes('0.95') ? '0.95' : '0.99';
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [{ metric: { quantile: quant }, values: [[1000, '0.2']] }],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.httpLatencySeries('1h');
  assert.equal(out.series.length, 2);
  assert.deepEqual(out.series.map((s) => s.label).sort(), ['p95', 'p99']);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: FAIL — `api.httpRateSeries is not a function`.

- [ ] **Step 3: Implement the closure helpers** — in `api.js`, immediately after `promRange` (Task 2):

```js
// Range key → {rangeSeconds, stepSeconds}: the bucket grid, derived ONCE
// client-side (PX.ts) so the worker trend endpoints never re-derive it.
function rangeOpts(range) {
  const rs = window.PX.ts.RANGES[range] || 3600;
  return { rangeSeconds: rs, stepSeconds: window.PX.ts.deriveStep(rs) };
}
// Rate/quantile window for rate()/…_bucket — never below 60s.
function winFor(o) {
  return Math.max(o.stepSeconds, 60) + 's';
}
// promRange + force one human label on a single-series aggregate result.
async function labelledRange(promql, opts, label) {
  const r = await promRange(promql, opts);
  return { series: r.series.map((s) => ({ ...s, label })) };
}
```

- [ ] **Step 4: Implement the 5 methods** — in `api.js`, inside `PX.api = { … }` right after the `gpu()` method:

```js
    // ── time-series trends (Prometheus, via promRange) ──────────
    // Verified metric names/labels: poindexter_http_requests_total {method,route,
    // status}; poindexter_http_request_duration_seconds_bucket {le};
    // poindexter_posts_total Gauge by status; poindexter_daily_spend_usd.
    // pick-wrapped like gpu(): mock mode shows "no data" (never hits Prometheus).
    httpRateSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () => labelledRange(`sum(rate(poindexter_http_requests_total[${winFor(o)}]))`, o, 'req/s'),
        () => ({ series: [] })
      );
    },
    httpErrorSeries(range) {
      const o = rangeOpts(range);
      const w = winFor(o);
      return pick(
        () =>
          labelledRange(
            `sum(rate(poindexter_http_requests_total{status=~"5.."}[${w}])) ` +
              `/ sum(rate(poindexter_http_requests_total[${w}])) * 100`,
            o, '5xx %'
          ),
        () => ({ series: [] })
      );
    },
    httpLatencySeries(range) {
      const o = rangeOpts(range);
      const ql = (q) =>
        `histogram_quantile(${q}, sum(rate(` +
        `poindexter_http_request_duration_seconds_bucket[${winFor(o)}])) by (le))`;
      return pick(
        async () => {
          const [p95, p99] = await Promise.all([
            labelledRange(ql('0.95'), o, 'p95'),
            labelledRange(ql('0.99'), o, 'p99'),
          ]);
          return { series: [...p95.series, ...p99.series] };
        },
        () => ({ series: [] })
      );
    },
    throughputSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () => labelledRange('poindexter_posts_total{status="published"}', o, 'published'),
        () => ({ series: [] })
      );
    },
    costSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () => labelledRange('poindexter_daily_spend_usd', o, '$/day'),
        () => ({ series: [] })
      );
    },
```

- [ ] **Step 5: Run to verify method tests pass**

Run: `node --test src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: PASS (`loadApiWithFetch` forces `PX_API_LIVE:true`, so `pick` runs the live branch and the fetch fires).

- [ ] **Step 6: Teach `assertRequest` about range queries** — in `contract-runtime.js`, the `host === 'prometheus'` branch hardcodes `/api/v1/query`. Make it accept a `rangeQuery` flag (backward-compatible — existing rows omit it):

```js
  if (expected.host === 'prometheus') {
    const promPath = expected.rangeQuery ? '/api/v1/query_range' : '/api/v1/query';
    assert.equal(url.pathname, promPath, `[${name}] prometheus path`);
    assert.equal(
      url.searchParams.get('query'),
      expected.query,
      `[${name}] promql`
    );
  } else {
```

- [ ] **Step 7: Preload `timeseries.js` in the contract runner** — `api.js`'s trend methods call `window.PX.ts` at invoke time. In `run-contracts.test.js`, pass the preload opt to `loadApiWithRecorder`:

```js
const { api, calls } = loadApiWithRecorder(
  () => ({ status: 200, payload: fixture == null ? {} : fixture }),
  {},
  { preload: ['timeseries.js'] }
);
```

- [ ] **Step 8: Add 5 manifest rows** — in `contracts.manifest.js`, after the `promVector` row (~line 341, before the closing `];`). Request-only (`host:'prometheus'` rows carry no `response_model`, so no fixture/shape). `httpLatencySeries` uses the array form (two calls, order-independent). `1h` → `winFor=60s`:

```js
  // ── C: time-series trends (Prometheus range) — request-only, no OpenAPI ──
  {
    name: 'httpRateSeries',
    invoke: (api) => api.httpRateSeries('1h'),
    request: {
      host: 'prometheus', rangeQuery: true,
      query: 'sum(rate(poindexter_http_requests_total[60s]))',
    },
  },
  {
    name: 'httpErrorSeries',
    invoke: (api) => api.httpErrorSeries('1h'),
    request: {
      host: 'prometheus', rangeQuery: true,
      query:
        'sum(rate(poindexter_http_requests_total{status=~"5.."}[60s])) ' +
        '/ sum(rate(poindexter_http_requests_total[60s])) * 100',
    },
  },
  {
    name: 'httpLatencySeries',
    invoke: (api) => api.httpLatencySeries('1h'),
    request: [
      {
        host: 'prometheus', rangeQuery: true,
        query:
          'histogram_quantile(0.95, sum(rate(poindexter_http_request_duration_seconds_bucket[60s])) by (le))',
      },
      {
        host: 'prometheus', rangeQuery: true,
        query:
          'histogram_quantile(0.99, sum(rate(poindexter_http_request_duration_seconds_bucket[60s])) by (le))',
      },
    ],
  },
  {
    name: 'throughputSeries',
    invoke: (api) => api.throughputSeries('1h'),
    request: {
      host: 'prometheus', rangeQuery: true,
      query: 'poindexter_posts_total{status="published"}',
    },
  },
  {
    name: 'costSeries',
    invoke: (api) => api.costSeries('1h'),
    request: { host: 'prometheus', rangeQuery: true, query: 'poindexter_daily_spend_usd' },
  },
```

- [ ] **Step 9: Run the full console suite**

Run: `npm run test:console`
Expected: PASS — the 5 rows exercise the new `assertRequest` range branch end-to-end. If a row's `query` string mismatches the built PromQL, align the manifest string to the method output (the method is source of truth).

- [ ] **Step 10: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.range.test.js src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js
git commit -m "feat(console): 5 Prometheus trend methods + range-query contract net"
```

---

## Task 4: `<TimeChart>` + `<RangeControl>` (charts.jsx)

**Files:**

- Create: `src/cofounder_agent/console/js/charts.jsx`
- Modify: `src/cofounder_agent/console/index.html` (load `timeseries.js` before `api.js`; `charts.jsx` after `panels2.jsx`)

**Interfaces:**

- Consumes: `window.PX.ts.*` (Task 1), React (global `React`).
- Produces (on `window`): `TimeChart({ series, stale, height, unit })`, `RangeControl({ value, onChange })`.

**Note on testing:** the console has no React test renderer — component correctness rides on Task 1's pure-helper tests (geometry/gaps/dash) plus the browser smoke in Task 9. This task's verification is a load-without-error check.

- [ ] **Step 1: Implement `charts.jsx`**

Create `src/cofounder_agent/console/js/charts.jsx`:

```jsx
/* Native time-series charts for the Telemetry surface (sub-project C). Zero-dep
   SVG, colorblind-safe (dash + end-label + opacity, never hue). Chart math lives
   in js/timeseries.js (PX.ts); this file is the thin React rendering layer.
   Loaded by index.html after panels2.jsx, before app.jsx. */
// useState/useRef are primitives.jsx globals (it loads first). Do NOT re-declare
// them: every text/babel script shares ONE global lexical scope, so a second
// `const { useState } = React` throws "already declared" and aborts this file.
const TS = () => window.PX.ts;

// Colorblind-safe stroke set (Okabe–Ito-ish); identity still carried by dash +
// label so the chart reads in grayscale. Cycles for >5 series.
const STROKES = ['#4fc3f7', '#ffb74d', '#81c784', '#ba68c8', '#e0e0e0'];

function TimeChart({ series, stale, height = 150, unit = '' }) {
  const ref = useRef(null);
  const [hoverX, setHoverX] = useState(null);
  const ts = TS();
  const list = series || [];
  const W = 320,
    H = height,
    PADL = 34,
    PADB = 16,
    PADT = 8,
    PADR = 44;
  const box = { x: PADL, y: PADT, w: W - PADL - PADR, h: H - PADT - PADB };

  if (ts.isEmptySeries(list)) {
    return (
      <div className="tc-empty" style={{ height: H, opacity: stale ? 0.5 : 1 }}>
        no data in range
      </div>
    );
  }
  const b = ts.seriesBounds(list);
  const yt = ts.yTicks(b.vMin, b.vMax, 4);
  const xt = ts.xTicks(b.tMin, b.tMax, 4);
  const fmtV = (v) =>
    Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100;
  const fmtT = (t) => {
    const d = new Date(t);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  // hover: track x within the plot box
  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    setHoverX(px >= box.x && px <= box.x + box.w ? px : null);
  };
  const hoverT =
    hoverX == null
      ? null
      : b.tMin + ((hoverX - box.x) / box.w) * (b.tMax - b.tMin);
  const nearest = (pts) => {
    let best = null,
      bd = Infinity;
    (pts || []).forEach(([t, v]) => {
      if (v == null) return;
      const dd = Math.abs(t - hoverT);
      if (dd < bd) {
        bd = dd;
        best = [t, v];
      }
    });
    return best;
  };

  return (
    <div className="tc" style={{ opacity: stale ? 0.5 : 1 }}>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        style={{ display: 'block' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverX(null)}
      >
        {yt.map((v, i) => {
          const y = ts.scaleY(v, b.vMin, b.vMax, box).toFixed(1);
          return (
            <g key={'y' + i}>
              <line
                x1={box.x}
                y1={y}
                x2={box.x + box.w}
                y2={y}
                stroke="var(--gl-line, rgba(255,255,255,.08))"
                strokeWidth="0.5"
              />
              <text
                x={box.x - 4}
                y={y}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="7"
                fill="var(--gl-dim, #8a94a6)"
              >
                {fmtV(v)}
              </text>
            </g>
          );
        })}
        {xt.map((t, i) => (
          <text
            key={'x' + i}
            x={ts.scaleX(t, b.tMin, b.tMax, box).toFixed(1)}
            y={H - 4}
            textAnchor="middle"
            fontSize="7"
            fill="var(--gl-dim, #8a94a6)"
          >
            {fmtT(t)}
          </text>
        ))}
        {list.map((s, i) => {
          const d = ts.buildPath(s.points, b, box);
          const last = [...(s.points || [])]
            .reverse()
            .find(([, v]) => v != null);
          return (
            <g key={s.label + i}>
              <path
                d={d}
                fill="none"
                stroke={STROKES[i % STROKES.length]}
                strokeWidth="1.5"
                strokeDasharray={ts.dashFor(i)}
              />
              {last && (
                <text
                  x={box.x + box.w + 3}
                  y={ts.scaleY(last[1], b.vMin, b.vMax, box).toFixed(1)}
                  fontSize="7"
                  dominantBaseline="middle"
                  fill={STROKES[i % STROKES.length]}
                >
                  {s.label}
                </text>
              )}
            </g>
          );
        })}
        {hoverX != null && (
          <line
            x1={hoverX}
            y1={box.y}
            x2={hoverX}
            y2={box.y + box.h}
            stroke="var(--gl-dim, #8a94a6)"
            strokeWidth="0.5"
            strokeDasharray="2,2"
          />
        )}
      </svg>
      {hoverX != null && (
        <div className="tc-readout">
          {fmtT(hoverT)}
          {' · '}
          {list.map((s, i) => {
            const n = nearest(s.points);
            return n ? (
              <span key={i} style={{ marginRight: 8 }}>
                {s.label}{' '}
                <b>
                  {fmtV(n[1])}
                  {unit}
                </b>
              </span>
            ) : null;
          })}
        </div>
      )}
    </div>
  );
}

function RangeControl({ value, onChange }) {
  return (
    <div className="rangectl" role="group" aria-label="time range">
      {Object.keys(window.PX.ts.RANGES).map((r) => (
        <button
          key={r}
          type="button"
          className={'rangectl__btn' + (r === value ? ' is-active' : '')}
          aria-pressed={r === value}
          onClick={() => onChange(r)}
        >
          {r}
        </button>
      ))}
    </div>
  );
}

Object.assign(window, { TimeChart, RangeControl });
```

- [ ] **Step 2: Load the scripts** — in `src/cofounder_agent/console/index.html`, add `timeseries.js` immediately BEFORE the `api.js` script tag (so `PX.ts` exists before any consumer):

```html
<!-- Pure chart-math (PX.ts) — dependency of api.js trend methods + charts.jsx. -->
<script src="js/timeseries.js"></script>
<script src="js/api.js"></script>
```

and add `charts.jsx` after the `panels2.jsx` block, before `drawer.jsx`:

```html
<script type="text/babel" data-presets="react" src="js/charts.jsx"></script>
```

- [ ] **Step 3: Verify the pure dep loads**

Run: `node -e "require('./src/cofounder_agent/console/js/timeseries.js'); console.log('timeseries loads')"`
Expected: `timeseries loads` (charts.jsx is JSX — browser-verified in Task 9).

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/js/charts.jsx src/cofounder_agent/console/index.html
git commit -m "feat(console): TimeChart + RangeControl (zero-dep, colorblind-safe SVG)"
```

---

## Task 5: `<HistoryPanel>` — mount the 5 Prometheus charts, remove the cost embed

**Files:**

- Modify: `src/cofounder_agent/console/js/charts.jsx` (add `HistoryChart` + `HistoryPanel`)
- Modify: `src/cofounder_agent/console/js/panels2.jsx` (drop `cost-analytics` from `GRAFANA_EMBEDS`)
- Modify: `src/cofounder_agent/console/js/app.jsx` (render `<HistoryPanel>` in `#sec-telemetry`)

**Interfaces:**

- Consumes: `window.TimeChart`, `window.RangeControl` (Task 4); `PX.api.{httpRateSeries,…,costSeries}` (Task 3); sub-project A's polling hook + freshness badge (exact export names confirmed in Step 1).
- Produces (on `window`): `HistoryPanel()`.

- [ ] **Step 1: Confirm A's export names** — grep before wiring so the `window.*` refs below are exact:

Run: `grep -nE "usePolledResource|Freshness" src/cofounder_agent/console/js/reliability.js src/cofounder_agent/console/js/primitives.jsx`
Expected: shows how `usePolledResource` and `Freshness` are exposed and what `usePolledResource` returns (`data`/`lastUpdatedAt`/`stale`). Align the `HistoryChart` field names in Step 2 to what it shows.

- [ ] **Step 2: Add `HistoryChart` + `HistoryPanel` to `charts.jsx`** (above the final `Object.assign`):

```jsx
// One history chart: its own poll keyed by (title, range) so a range change is a
// fresh resource (A's AbortController prevents a stale response clobbering it).
function HistoryChart({ title, fetchSeries, range, unit }) {
  // A's hook lives at window.PXR.usePolledResource (reliability.js is an IIFE).
  const r = window.PXR.usePolledResource(() => fetchSeries(range), {
    intervalMs: 30000,
    key: title + ':' + range,
  });
  return (
    <div className="panel tc-panel">
      <div className="panel__head">
        <span className="panel__title">{title}</span>
        <span style={{ flex: 1 }} />
        {/* Freshness is a primitives.jsx window global; its prop is lastUpdatedAt. */}
        <Freshness lastUpdatedAt={r.lastUpdatedAt} stale={r.stale} />
      </div>
      <TimeChart
        series={r.data && r.data.series}
        stale={r.stale}
        unit={unit || ''}
      />
    </div>
  );
}

function HistoryPanel() {
  const [range, setRange] = useState('6h');
  const api = window.PX.api;
  const charts = [
    { title: 'API request rate', fn: (x) => api.httpRateSeries(x), unit: '' },
    { title: 'API error rate', fn: (x) => api.httpErrorSeries(x), unit: '%' },
    {
      title: 'API latency (p95/p99)',
      fn: (x) => api.httpLatencySeries(x),
      unit: 's',
    },
    {
      title: 'Pipeline throughput',
      fn: (x) => api.throughputSeries(x),
      unit: '',
    },
    { title: 'LLM spend', fn: (x) => api.costSeries(x), unit: '$' },
  ];
  return (
    <div id="sec-history">
      <div className="panel__head" style={{ marginBottom: 8 }}>
        <span className="panel__title">
          <span className="idx">▤</span>HISTORY
        </span>
        <span style={{ flex: 1 }} />
        <RangeControl value={range} onChange={setRange} />
      </div>
      <div className="tc-grid">
        {charts.map((c) => (
          <HistoryChart
            key={c.title}
            title={c.title}
            fetchSeries={c.fn}
            range={range}
            unit={c.unit}
          />
        ))}
      </div>
    </div>
  );
}
```

Update the final line of `charts.jsx`:

```jsx
Object.assign(window, { TimeChart, RangeControl, HistoryPanel });
```

- [ ] **Step 3: Remove the cost embed** — in `src/cofounder_agent/console/js/panels2.jsx`, delete the `cost-analytics` entry from `GRAFANA_EMBEDS` (~line 1592) so only GPU + DB remain:

```jsx
const GRAFANA_EMBEDS = [
  { uid: 'hardware-power', panelId: 4, label: 'GPU history' },
  { uid: 'database', panelId: 2, label: 'DB connections' },
];
```

- [ ] **Step 4: Render `HistoryPanel`** — in `src/cofounder_agent/console/js/app.jsx`, in `#sec-telemetry` (~line 1466), add `<HistoryPanel />` as the first child (above `<LogsPanel …>`):

```jsx
              <div id="sec-telemetry">
                <HistoryPanel />
                <LogsPanel
```

- [ ] **Step 5: Verify console unit suite still green** (no regression from the panels2 edit):

Run: `npm run test:console`
Expected: PASS (browser render is verified in Task 9).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/charts.jsx src/cofounder_agent/console/js/panels2.jsx src/cofounder_agent/console/js/app.jsx
git commit -m "feat(console): native History panel (5 Prometheus trends); drop cost embed"
```

---

## Task 6: Backend — QA pass-rate trend endpoint

**Files:**

- Create: `src/cofounder_agent/services/qa_trend.py`
- Create: `src/cofounder_agent/routes/qa_routes.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` (register in `_WORKER_ROUTES`)
- Test: `src/cofounder_agent/tests/unit/services/test_qa_trend.py`

**Interfaces:**

- Produces: `services.qa_trend.get_qa_pass_trend(pool, *, range_seconds:int, step_seconds:int) -> dict` → `{"series":[{"label":"pass %","points":[[tMs, pct|None], …]}]}`. Empty bucket → `None` (rate undefined → line break). `_clamp(range_seconds, step_seconds) -> (int,int)`: range ∈ [60, 604800]; step ≥ 15, raised so `range/step ≤ 1000`.
- Route `GET /api/qa/trend?range_seconds=&step_seconds=` (OAuth via `verify_api_token`) delegates to it.

- [ ] **Step 1: Write the failing test** (mirrors `tests/unit/services/test_findings_read.py` — real Postgres via the session-scoped `db_pool` fixture)

Create `src/cofounder_agent/tests/unit/services/test_qa_trend.py`:

```python
"""Roundtrip tests for ``services.qa_trend.get_qa_pass_trend`` against the
Postgres test DB (``db_pool``). Seeds ``audit_log`` qa_pass_completed rows, then
asserts the epoch-bucketed pass-rate series + the clamp. Mirrors
``tests/unit/services/test_findings_read.py``."""

from __future__ import annotations

import json

import pytest

from services.qa_trend import _clamp, get_qa_pass_trend

# db_pool is loop_scope="session"; tests must share that loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_clamp_bounds_bucket_count():
    # tiny step over a huge range must not exceed ~1000 buckets
    rng, step = _clamp(604800, 1)
    assert rng == 604800
    assert step >= 604800 / 1000
    # range floored to 60s minimum; step floored to 15
    assert _clamp(5, 5) == (60, 15)


async def _seed_qa(conn, *, approved, terminal=True):
    await conn.execute(
        "INSERT INTO audit_log (event_type, source, severity, details) "
        "VALUES ('qa_pass_completed', 'qa.aggregate', 'info', $1::jsonb)",
        json.dumps({"approved": approved, "terminal": terminal, "final_score": 80}),
    )


async def _reset(conn):
    await conn.execute("DELETE FROM audit_log WHERE event_type = 'qa_pass_completed'")


async def test_pass_rate_buckets_and_gaps(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        # one bucket (~now): 1 pass + 1 fail → 50%
        await _seed_qa(conn, approved=True)
        await _seed_qa(conn, approved=False)
    try:
        out = await get_qa_pass_trend(db_pool, range_seconds=3600, step_seconds=900)
        assert out["series"][0]["label"] == "pass %"
        pts = out["series"][0]["points"]
        vals = [v for _, v in pts if v is not None]
        assert 50.0 in vals  # populated bucket = 50%
        assert any(v is None for _, v in pts)  # empty buckets → null (honest gap)
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_qa_trend.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.qa_trend'`.

- [ ] **Step 3: Write the service**

Create `src/cofounder_agent/services/qa_trend.py`:

```python
"""QA pass-rate time-series for the operator console (sub-project C).

Buckets ``audit_log`` rows where ``event_type='qa_pass_completed'`` (one row per
terminal QA gate decision; ``details->>'approved'`` is pass/fail,
``details->>'terminal'`` marks the gate decision vs a deferred rescue pass) into
an epoch-floored ``step_seconds`` grid via ``generate_series``. Returns the
console's canonical series shape. An empty bucket (no reviews) is ``None`` — the
rate is undefined, rendered as a line break, never a fabricated 0
(feedback_no_dummy_data). SQL lives here, not in the route (adapter-purity ADR).
"""

from __future__ import annotations

import math
from typing import Any

MAX_RANGE_SECONDS = 7 * 86400
MAX_BUCKETS = 1000


def _clamp(range_seconds: int, step_seconds: int) -> tuple[int, int]:
    r = max(60, min(int(range_seconds), MAX_RANGE_SECONDS))
    s = max(15, int(step_seconds))
    if r / s > MAX_BUCKETS:
        s = math.ceil(r / MAX_BUCKETS)
    return r, s


async def get_qa_pass_trend(pool: Any, *, range_seconds: int, step_seconds: int) -> dict[str, Any]:
    r, s = _clamp(range_seconds, step_seconds)
    rows = await pool.fetch(
        """
        WITH grid AS (
            SELECT gs AS bucket FROM generate_series(
                floor(extract(epoch FROM NOW() - ($1 * INTERVAL '1 second')) / $2) * $2,
                floor(extract(epoch FROM NOW()) / $2) * $2,
                $2::numeric
            ) AS gs  -- EXTRACT(epoch) is numeric (PG14+); step must match the overload
        ),
        agg AS (
            SELECT floor(extract(epoch FROM timestamp) / $2) * $2 AS bucket,
                   COUNT(*) FILTER (WHERE details->>'approved' = 'true') AS pass,
                   COUNT(*) AS total
            FROM audit_log
            WHERE event_type = 'qa_pass_completed'
              AND COALESCE(details->>'terminal', 'true') = 'true'
              AND timestamp > NOW() - ($1 * INTERVAL '1 second')
            GROUP BY 1
        )
        SELECT g.bucket AS bucket,
               CASE WHEN a.total > 0
                    THEN round(a.pass::numeric / a.total * 100, 1) END AS pct
        FROM grid g LEFT JOIN agg a USING (bucket)
        ORDER BY g.bucket
        """,
        r,
        s,
    )
    points = [
        [int(row["bucket"]) * 1000, float(row["pct"]) if row["pct"] is not None else None]
        for row in rows
    ]
    return {"series": [{"label": "pass %", "points": points}]}
```

- [ ] **Step 4: Run to verify the service test passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_qa_trend.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the route** — create `src/cofounder_agent/routes/qa_routes.py` (mirrors `findings_routes.py` imports/auth exactly):

```python
"""Operator-console QA read routes. Thin adapter over services.qa_trend
(adapter-purity ADR: no inline SQL here)."""

from typing import Any

from fastapi import APIRouter, Depends, Query

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.qa_trend import get_qa_pass_trend
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/qa",
    tags=["qa"],
    dependencies=[Depends(verify_api_token)],
)


@router.get("/trend", response_model=dict[str, Any])
async def qa_trend(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    range_seconds: int = Query(21600, ge=60, le=604800),
    step_seconds: int = Query(90, ge=15),
) -> dict[str, Any]:
    return await get_qa_pass_trend(
        db_service.pool, range_seconds=range_seconds, step_seconds=step_seconds
    )
```

- [ ] **Step 6: Register the router** — in `src/cofounder_agent/utils/route_registration.py`, add to the `_WORKER_ROUTES` list immediately after the `findings_routes` tuple (~line 77):

```python
    ("routes.qa_routes", "router", "qa_router", "QA pass-rate trend for the operator console (/api/qa/trend)"),
```

- [ ] **Step 7: Verify route wiring compiles**

Run: `cd src/cofounder_agent && poetry run python -c "import routes.qa_routes as m; print([r.path for r in m.router.routes])"`
Expected: `['/api/qa/trend']`.

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/services/qa_trend.py src/cofounder_agent/routes/qa_routes.py src/cofounder_agent/utils/route_registration.py src/cofounder_agent/tests/unit/services/test_qa_trend.py
git commit -m "feat(qa): GET /api/qa/trend — pass-rate time-series over audit_log"
```

---

## Task 7: Backend — findings-by-severity trend endpoint

**Files:**

- Modify: `src/cofounder_agent/services/findings_read.py` (add `get_findings_trend`)
- Modify: `src/cofounder_agent/routes/findings_routes.py` (add `GET /api/findings/trend`)
- Test: `src/cofounder_agent/tests/unit/services/test_findings_trend.py`

**Interfaces:**

- Consumes: `services.qa_trend._clamp` (import — one clamp source).
- Produces: `services.findings_read.get_findings_trend(pool, *, range_seconds, step_seconds) -> {"series":[{"label":<severity>,"points":[[tMs, count]]}]}`. Counts: an empty bucket is `0` (a real count — zero findings), NOT null. Route `GET /api/findings/trend`.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/tests/unit/services/test_findings_trend.py`:

```python
"""Roundtrip tests for ``services.findings_read.get_findings_trend`` against the
Postgres test DB (``db_pool``). Mirrors ``test_findings_read.py``."""

from __future__ import annotations

import json

import pytest

from services.findings_read import get_findings_trend

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed(conn, *, severity, kind="x"):
    await conn.execute(
        "INSERT INTO audit_log (event_type, source, severity, details) "
        "VALUES ('finding', 'probe', $1, $2::jsonb)",
        severity,
        json.dumps({"kind": kind, "title": "t", "body": "b"}),
    )


async def _reset(conn):
    await conn.execute("DELETE FROM audit_log WHERE event_type = 'finding'")


async def test_one_series_per_severity_counts(db_pool):
    async with db_pool.acquire() as conn:
        await _reset(conn)
        await _seed(conn, severity="warning")
        await _seed(conn, severity="warning")
        await _seed(conn, severity="critical")
    try:
        out = await get_findings_trend(db_pool, range_seconds=3600, step_seconds=900)
        labels = sorted(s["label"] for s in out["series"])
        assert "warning" in labels and "critical" in labels
        warn = next(s for s in out["series"] if s["label"] == "warning")
        counts = [v for _, v in warn["points"]]
        # populated bucket = 2; empty buckets are 0 (a count), never null
        assert 2 in counts and all(v is not None for v in counts)
    finally:
        async with db_pool.acquire() as conn:
            await _reset(conn)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_findings_trend.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_findings_trend'`.

- [ ] **Step 3: Implement** — append to `src/cofounder_agent/services/findings_read.py` (its top already imports `Any`; if not, add `from typing import Any`):

```python
async def get_findings_trend(pool: Any, *, range_seconds: int, step_seconds: int) -> dict[str, Any]:
    """Findings-count time-series, one series per severity, over an epoch-floored
    ``step_seconds`` grid of ``audit_log`` ``event_type='finding'`` rows. An empty
    bucket is 0 (zero findings is a real value, not a gap). SQL lives here."""
    from services.qa_trend import _clamp  # single clamp source

    r, s = _clamp(range_seconds, step_seconds)
    rows = await pool.fetch(
        """
        WITH grid AS (
            SELECT gs AS bucket FROM generate_series(
                floor(extract(epoch FROM NOW() - ($1 * INTERVAL '1 second')) / $2) * $2,
                floor(extract(epoch FROM NOW()) / $2) * $2,
                $2::numeric
            ) AS gs  -- EXTRACT(epoch) is numeric (PG14+); step must match the overload
        ),
        sev AS (
            SELECT DISTINCT LOWER(severity) AS severity FROM audit_log
            WHERE event_type = 'finding'
              AND timestamp > NOW() - ($1 * INTERVAL '1 second')
        ),
        agg AS (
            SELECT floor(extract(epoch FROM timestamp) / $2) * $2 AS bucket,
                   LOWER(severity) AS severity, COUNT(*) AS c
            FROM audit_log
            WHERE event_type = 'finding'
              AND timestamp > NOW() - ($1 * INTERVAL '1 second')
            GROUP BY 1, 2
        )
        SELECT g.bucket AS bucket, sev.severity AS severity,
               COALESCE(a.c, 0) AS c
        FROM grid g CROSS JOIN sev
        LEFT JOIN agg a ON a.bucket = g.bucket AND a.severity = sev.severity
        ORDER BY sev.severity, g.bucket
        """,
        r,
        s,
    )
    by_sev: dict[str, list] = {}
    for row in rows:
        by_sev.setdefault(row["severity"], []).append(
            [int(row["bucket"]) * 1000, int(row["c"])]
        )
    return {"series": [{"label": sev, "points": pts} for sev, pts in by_sev.items()]}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_findings_trend.py -q`
Expected: PASS.

- [ ] **Step 5: Add the route** — in `src/cofounder_agent/routes/findings_routes.py`, extend the `services.findings_read` import and add a sibling route (its router prefix is `/api/findings`; `Query` is already imported):

```python
from services.findings_read import get_findings_trend, read_findings  # was: read_findings
```

```python
@router.get("/trend", response_model=dict[str, Any])
async def findings_trend(
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    range_seconds: int = Query(21600, ge=60, le=604800),
    step_seconds: int = Query(90, ge=15),
) -> dict[str, Any]:
    return await get_findings_trend(
        db_service.pool, range_seconds=range_seconds, step_seconds=step_seconds
    )
```

- [ ] **Step 6: Verify wiring + adapter-purity**

Run: `cd src/cofounder_agent && poetry run python -c "import routes.findings_routes as f; print([r.path for r in f.router.routes])"`
Expected: includes `/api/findings/trend`.
Run: `python scripts/ci/adapter_purity_lint.py`
Expected: PASS (no new inline SQL in routes).

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/findings_read.py src/cofounder_agent/routes/findings_routes.py src/cofounder_agent/tests/unit/services/test_findings_trend.py
git commit -m "feat(findings): GET /api/findings/trend — by-severity time-series"
```

---

## Task 8: Wire `qaTrend` + `findingsTrend` into the console

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (2 `pick`-wrapped methods)
- Modify: `src/cofounder_agent/console/js/charts.jsx` (2 charts in `HistoryPanel`)
- Modify: `src/cofounder_agent/console/js/__tests__/api.range.test.js` (2 tests)
- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (2 request-only rows)

**Interfaces:**

- Consumes: `rangeOpts` (Task 3 closure), `pick` + `http` (existing closures).
- Produces: `PX.api.qaTrend(range) -> {series}`, `PX.api.findingsTrend(range) -> {series}` — `pick`-wrapped worker GETs (live → `http`, mock → honest-empty), passing frontend-derived `range_seconds`+`step_seconds`.

- [ ] **Step 1: Write the failing test** — append to `api.range.test.js`:

```js
const { loadApiWithRecorder } = require('./contracts/contract-runtime.js');

test('qaTrend GETs /api/qa/trend with derived range_seconds+step_seconds', async () => {
  const { api, calls } = loadApiWithRecorder(
    () => ({
      status: 200,
      payload: { series: [{ label: 'pass %', points: [] }] },
    }),
    {},
    { preload: ['timeseries.js'] }
  );
  const out = await api.qaTrend('1h');
  const call = calls.find((c) => c.url.includes('/api/qa/trend'));
  assert.ok(call, 'hit /api/qa/trend');
  assert.match(call.url, /range_seconds=3600/);
  assert.match(call.url, /step_seconds=15/);
  assert.ok(Array.isArray(out.series));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: FAIL — `api.qaTrend is not a function`.

- [ ] **Step 3: Implement** — in `api.js`, inside `PX.api = { … }` right after the 5 Prometheus trend methods (Task 3):

```js
    // ── time-series trends (worker / audit_log) ─────────────────
    // Frontend derives the bucket grid once (rangeOpts) and passes it explicitly,
    // so the grid is never re-derived server-side (no drift). The route clamps.
    qaTrend(range) {
      const o = rangeOpts(range);
      return pick(
        () => http('GET', `/api/qa/trend?range_seconds=${o.rangeSeconds}&step_seconds=${o.stepSeconds}`),
        () => ({ series: [] })
      );
    },
    findingsTrend(range) {
      const o = rangeOpts(range);
      return pick(
        () => http('GET', `/api/findings/trend?range_seconds=${o.rangeSeconds}&step_seconds=${o.stepSeconds}`),
        () => ({ series: [] })
      );
    },
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: PASS.

- [ ] **Step 5: Add the two charts** — in `charts.jsx` `HistoryPanel`, extend the `charts` array:

```jsx
    { title: 'QA pass-rate', fn: (x) => api.qaTrend(x), unit: '%' },
    { title: 'Findings by severity', fn: (x) => api.findingsTrend(x), unit: '' },
```

- [ ] **Step 6: Add B-contract rows** — in `contracts.manifest.js`, 2 request-only rows (the routes declare no console-consumed schema beyond `{series}`, which `api.range.test.js` covers; no fixture needed). `1h` → `range_seconds=3600`, `step_seconds=15`:

```js
  {
    name: 'qaTrend',
    invoke: (api) => api.qaTrend('1h'),
    request: {
      method: 'GET', path: '/api/qa/trend',
      query: { range_seconds: 3600, step_seconds: 15 },
    },
  },
  {
    name: 'findingsTrend',
    invoke: (api) => api.findingsTrend('1h'),
    request: {
      method: 'GET', path: '/api/findings/trend',
      query: { range_seconds: 3600, step_seconds: 15 },
    },
  },
```

- [ ] **Step 7: Run the console suite**

Run: `npm run test:console`
Expected: PASS (all 7 trend rows + unit tests green).

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/charts.jsx src/cofounder_agent/console/js/__tests__/api.range.test.js src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js
git commit -m "feat(console): qaTrend + findingsTrend wire methods + charts + contract rows"
```

---

## Task 9: Integration verification (full suites + browser + lints)

**Files:** none new — verifies end-to-end.

- [ ] **Step 1: Full console unit suite**

Run: `npm run test:console`
Expected: PASS — timeseries + api.range + all 7 contract rows green.

- [ ] **Step 2: Backend unit tests + lints**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_qa_trend.py tests/unit/services/test_findings_trend.py -q`
Run: `python scripts/ci/adapter_purity_lint.py`
Run: `npx prettier --check "src/cofounder_agent/console/js/**/*.{js,jsx}"` then `npm run lint`
Expected: all PASS/clean. (Run `npx prettier --write` on any file it flags, then re-check.)

- [ ] **Step 3: Browser smoke** (per `reference_console_deploy_live` — a ThreadingHTTPServer + Playwright, NOT single-threaded `http.server`; the console must be flipped live with the dedicated console OAuth client)

Serve the console, open the Telemetry tab in live mode, and assert:

- the HISTORY section renders 7 `.tc-panel` charts under one `RangeControl`;
- each chart shows either SVG `<path>` data or an honest "no data in range";
- clicking `24h` re-fetches (network shows `query_range` + `/api/qa/trend` + `/api/findings/trend` with `range_seconds=86400`);
- no uncaught console errors; the `cost-analytics` iframe is gone; GPU + DB iframes remain.

Capture a screenshot as proof.

- [ ] **Step 4: Add chart CSS if needed** — if the charts need `.tc`, `.tc-grid`, `.tc-panel`, `.rangectl`/`.rangectl__btn`, `.tc-empty`, `.tc-readout` styles, add them to the console stylesheet (grep the existing CSS for `.panel` / `GRAFANA_EMBEDS` styles to match conventions), re-run the browser smoke, and commit:

```bash
git add src/cofounder_agent/console/  # any css touched
git commit -m "style(console): history chart panel styles"
```

---

## Self-Review

**Spec coverage:**

- `promRange` guarded adapter → Task 2. ✅
- `<TimeChart>` colorblind-safe zero-dep SVG → Task 4 (geometry/dash from Task 1). ✅
- Range control + `<HistoryPanel>` → Tasks 4/5. ✅
- 7 trend wire methods → Tasks 3 (5 Prometheus) + 8 (2 worker). ✅
- Uniform series shape (Prometheus + audit_log) → `matrixToSeries` (Task 1) + backend fns return it verbatim (Tasks 6/7). ✅
- QA endpoint (pass-rate, terminal-only, null empty buckets) → Task 6. ✅
- Findings endpoint (per-severity, 0 empty buckets) → Task 7. ✅
- Server-side clamp (range ≤ 7d, ≤1000 buckets) → `_clamp` (Task 6), reused (Task 7). ✅
- Adapter-purity (SQL in services) → Tasks 6/7 + lint (Task 9). ✅
- Embed teardown (drop `cost-analytics`, keep GPU/DB) → Task 5. ✅
- B-contract rows for all 7 methods → Tasks 3/8 (request-only). ✅
- A's polling inheritance → Task 5 `usePolledResource`. ✅
- Tests: timeseries, promRange mapping/guard, method PromQL, backend bucket/clamp, contract rows → Tasks 1/2/3/6/7/8. ✅
- No `console-unit` regression + prettier/eslint → Task 9. ✅

**Verified-against-source facts baked in:** `pick(liveFn, mockFn)` seam + closure-helper idiom (api.js:335, gpu()); `HTTP_TIMEOUT_MS`/`cfg.prometheus` closure scope (api.js:81/63); harness `loadApiWithFetch`/`loadApiWithRecorder` + `preload` + forced `PX_API_LIVE:true` (contract-runtime.js:48/75); runner asserts `nonToken[0]` (single) or any-match (array) + requires a fixture only for `shape` rows (run-contracts.test.js:35-71); `assertRequest` prometheus branch hardcodes `/api/v1/query` → extended for `rangeQuery` (Task 3 Step 6); route imports `get_database_dependency` from `utils.route_utils` + `db_service.pool` (findings_routes.py:20/55); registration is the `_WORKER_ROUTES` tuple list (route_registration.py:77); backend test uses session-scoped `db_pool` + `pytestmark = asyncio(loop_scope="session")` + `audit_log … $N::jsonb` seed (test_findings_read.py:20-45).

**Placeholder scan:** one verify-before-copy note remains (Task 5 Step 1: A's exact `usePolledResource`/`Freshness` export names) — an explicit grep-then-align step with the expected return shape stated, not a vague TODO. Acceptable.

**Type consistency:** canonical `{series:[{label, points:[[tMs, value|null]]}]}` is identical across `matrixToSeries`, `promRange`, `labelledRange`, the 5 Prometheus methods, the 2 worker methods, and both backend fns. `rangeOpts`/`winFor`/`labelledRange`/`deriveStep`/`RANGES` names consistent Tasks 1/3/8. `_clamp` signature identical Tasks 6/7 (imported, single source). `HistoryChart` reads `usePolledResource`'s `{data,lastUpdatedAt,stale}` (confirmed Task 5 Step 1).
