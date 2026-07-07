# Native GPU / Hardware / Power Panels (Sub-project D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the console Telemetry tab's embedded Grafana hardware panel with five native SVG trend charts (GPU utilization, temperature, VRAM, power draw — all per-GPU — plus total system power), reusing sub-project C's merged chart stack.

**Architecture:** Pure front-end. Add a small per-series label seam to C's `matrixToSeries`/`promRange`, add five `PX.api` methods that call `promRange` against Prometheus gauges, extract a reusable `<TrendGroup>` from C's `<HistoryPanel>`, and render a new `<HardwarePanel>` into the Telemetry tab while dropping the `hardware-power` iframe. No backend, no service, no worker route.

**Tech Stack:** No-build React 18 + in-browser Babel (`text/babel` scripts), `node:test` unit tests, client-side Prometheus `query_range`. All primitives (`promRange`, `js/timeseries.js` `PX.ts`, `js/charts.jsx` `TimeChart`/`RangeControl`/`HistoryChart`, the contract-net `rangeQuery` flag) are already merged to `main` from sub-project C.

## Global Constraints

- **Colorblind-safe:** series identity is carried by dash pattern + direct end-label + opacity, NEVER by hue. (Matt is colorblind.) `TimeChart` already does this; do not add hue-only distinctions.
- **Honest-empty, no dummy data:** every guarded fetch returns real values or `{series:[]}` → `TimeChart` renders "no data in range". Never fabricate or zero-fill.
- **Pure front-end:** D adds NO `services/*.py` and NO `_WORKER_ROUTES` entry, so it trips neither the `regen-services-doc` CI script nor the `test_worker_manifest_has_expected_routes` count guard. If you find yourself adding a Python file or a route, stop — the design is wrong.
- **One global lexical scope:** every `text/babel` script (`primitives.jsx`, `charts.jsx`, `app.jsx`, …) shares ONE global scope. `primitives.jsx:5` already owns `const { useState, useEffect, useRef, useCallback } = React;`. Do NOT re-declare them in `charts.jsx` — a second `const { useState } = React` throws "already declared" and silently aborts the whole file (this crashed C's first browser run; Babel per-file compile does NOT catch it).
- **Cross-realm-safe assertions:** test sandboxes are vm realms with different prototypes. Assert with `Array.isArray()` + `.length` + `.includes()`, never `assert.deepEqual` on a value returned from the sandboxed api.
- **Run console tests FROM the worktree root**, not via `npm run test:console` (that runs from the main repo checkout and silently excludes worktree changes):
  ```bash
  node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"
  ```
- **Reuse, don't reinvent:** `promRange`, `TimeChart`, `RangeControl`, `HistoryChart`, `matrixToSeries`, `rangeOpts`, `labelledRange`, `pick`, `http` all exist and are merged. Call them; do not re-implement.
- **All changes via PR; linear history.** Branch `feat/console-hardware-power` is already created off post-C `main`; the spec is committed (`afdd36241`).

## File Structure

| File                                                                       | Responsibility               | Change                                                   |
| -------------------------------------------------------------------------- | ---------------------------- | -------------------------------------------------------- |
| `src/cofounder_agent/console/js/timeseries.js`                             | pure chart math (`PX.ts`)    | `matrixToSeries` gains `labelBy`/`labelPrefix`           |
| `src/cofounder_agent/console/js/api.js`                                    | sole data adapter (`PX.api`) | `promRange` threads the label opts; +5 GPU/power methods |
| `src/cofounder_agent/console/js/charts.jsx`                                | React chart layer            | extract `<TrendGroup>`; add `<HardwarePanel>`            |
| `src/cofounder_agent/console/js/app.jsx`                                   | app shell                    | render `<HardwarePanel/>` in `#sec-telemetry`            |
| `src/cofounder_agent/console/js/panels2.jsx`                               | Grafana-embed list           | drop the `hardware-power` row                            |
| `src/cofounder_agent/console/js/__tests__/timeseries.test.js`              | `PX.ts` tests                | +1 `labelBy` test, +1 backward-compat                    |
| `src/cofounder_agent/console/js/__tests__/api.range.test.js`               | range-method tests           | +6 tests (label seam + 5 methods)                        |
| `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` | wire-contract net            | +5 Prometheus rows                                       |

---

## Task 1: `matrixToSeries` per-series label seam

**Files:**

- Modify: `src/cofounder_agent/console/js/timeseries.js:93-106`
- Test: `src/cofounder_agent/console/js/__tests__/timeseries.test.js`

**Interfaces:**

- Produces: `matrixToSeries(result, fallbackLabel, labelBy, labelPrefix)` — when `labelBy` is set and a result's `metric[labelBy]` exists, that series' label becomes `(labelPrefix || '') + metric[labelBy]`; otherwise unchanged (join of non-`__name__` labels, else `fallbackLabel`, else `'value'`). Consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/console/js/__tests__/timeseries.test.js`:

```js
test('matrixToSeries labels each series from labelBy with a prefix', () => {
  const matrix = [
    {
      metric: { gpu: '0', instance: 'x', job: 'nvidia-smi' },
      values: [[1000, '2']],
    },
    {
      metric: { gpu: '1', instance: 'x', job: 'nvidia-smi' },
      values: [[1000, '0']],
    },
  ];
  const out = T.matrixToSeries(matrix, undefined, 'gpu', 'GPU ');
  const labels = out.map((s) => s.label);
  assert.equal(out.length, 2);
  assert.ok(labels.includes('GPU 0') && labels.includes('GPU 1'));
  assert.equal(out[0].points[0][0], 1000 * 1000); // still seconds -> ms
});

test('matrixToSeries without labelBy keeps the joined-label behavior', () => {
  const out = T.matrixToSeries([
    { metric: { quantile: '0.95' }, values: [[1000, '0.2']] },
  ]);
  assert.equal(out[0].label, 'quantile=0.95');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test --test-name-pattern="labelBy|joined-label" src/cofounder_agent/console/js/__tests__/timeseries.test.js`
Expected: the `labelBy` test FAILS (labels come back as `gpu=0,instance=x,job=nvidia-smi`, not `GPU 0`). The joined-label test passes (guards backward-compat).

- [ ] **Step 3: Implement the extension**

Replace `matrixToSeries` in `src/cofounder_agent/console/js/timeseries.js` (currently lines 91-106) with:

```js
// Prometheus matrix result -> canonical series. Seconds -> ms. Label: when
// labelBy is set and present on a series' metric, use labelPrefix+that value
// (e.g. "GPU 0"); else the join of non-__name__ labels, else fallbackLabel.
function matrixToSeries(result, fallbackLabel, labelBy, labelPrefix) {
  return (result || []).map((r) => {
    const m = r.metric || {};
    let label;
    if (labelBy && m[labelBy] != null) {
      label = (labelPrefix || '') + m[labelBy];
    } else {
      const parts = Object.keys(m)
        .filter((k) => k !== '__name__')
        .map((k) => k + '=' + m[k]);
      label = parts.length ? parts.join(',') : fallbackLabel || 'value';
    }
    const points = (r.values || []).map(([t, v]) => [
      Math.round(Number(t) * 1000),
      v == null ? null : Number(v),
    ]);
    return { label, points };
  });
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`
Expected: PASS — all prior tests still green (115) plus the 2 new ones (117).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/timeseries.js src/cofounder_agent/console/js/__tests__/timeseries.test.js
git commit -m "feat(console): matrixToSeries per-series labelBy for multi-series legends"
```

---

## Task 2: `promRange` threads the label options

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js:216-244`
- Test: `src/cofounder_agent/console/js/__tests__/api.range.test.js`

**Interfaces:**

- Consumes: `matrixToSeries(result, fallbackLabel, labelBy, labelPrefix)` (Task 1).
- Produces: `promRange(promql, opts)` where `opts` gains optional `labelBy` / `labelPrefix`, forwarded to `matrixToSeries`. Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Append to `src/cofounder_agent/console/js/__tests__/api.range.test.js`:

```js
test('promRange labels multi-series output via labelBy/labelPrefix', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        resultType: 'matrix',
        result: [
          { metric: { gpu: '0', job: 'nvidia-smi' }, values: [[1000, '2']] },
          { metric: { gpu: '1', job: 'nvidia-smi' }, values: [[1000, '0']] },
        ],
      },
    }),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.promRange(
    'max by (gpu) (nvidia_gpu_utilization_percent)',
    {
      rangeSeconds: 3600,
      stepSeconds: 15,
      labelBy: 'gpu',
      labelPrefix: 'GPU ',
    }
  );
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('GPU 0') && labels.includes('GPU 1'));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test --test-name-pattern="promRange labels multi-series" src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: FAIL — labels come back as `gpu=0,job=nvidia-smi` because `promRange` calls `matrixToSeries(result)` without the label args.

- [ ] **Step 3: Implement the passthrough**

In `src/cofounder_agent/console/js/api.js`, change the return inside `promRange` (currently line 238):

```js
const result = (j && j.data && j.data.result) || [];
return {
  series: window.PX.ts.matrixToSeries(
    result,
    undefined,
    o.labelBy,
    o.labelPrefix
  ),
};
```

(`o` is already `opts || {}` at the top of `promRange`, so `o.labelBy`/`o.labelPrefix` are `undefined` when omitted — existing callers are unaffected.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`
Expected: PASS — 118 tests (prior 117 + this 1).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.range.test.js
git commit -m "feat(console): promRange forwards labelBy/labelPrefix to matrixToSeries"
```

---

## Task 3: Five GPU/power `PX.api` methods + contract rows

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js:1267` (insert after `costSeries`, before the `// ── time-series trends (worker / audit_log) ──` comment)
- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js:399` (insert after the `costSeries` row, before the `// ── C: time-series trends (worker / audit_log) ──` comment)
- Test: `src/cofounder_agent/console/js/__tests__/api.range.test.js`

**Interfaces:**

- Consumes: `promRange(promql, {rangeSeconds, stepSeconds, labelBy, labelPrefix})` (Task 2), `rangeOpts(range)`, `labelledRange(promql, opts, label)`, `pick(live, mock)` — all existing in api.js.
- Produces: `gpuUtilSeries(range)`, `gpuTempSeries(range)`, `vramUsedSeries(range)`, `gpuPowerSeries(range)`, `systemPowerSeries(range)` — each returns `Promise<{series:[{label, points:[[tMs, v|null]]}]}>`; honest-empty `{series:[]}` on any failure/mock. Consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/console/js/__tests__/api.range.test.js`:

```js
test('gpuUtilSeries issues the max-by-gpu utilization query with GPU 0/1 labels', async () => {
  const fetchImpl = async (url) => {
    const q = decodeURIComponent(new URL(url).searchParams.get('query') || '');
    assert.equal(q, 'max by (gpu) (nvidia_gpu_utilization_percent)');
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [
            { metric: { gpu: '0' }, values: [[1000, '2']] },
            { metric: { gpu: '1' }, values: [[1000, '0']] },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.gpuUtilSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('GPU 0') && labels.includes('GPU 1'));
});

test('gpu/vram/system methods issue their exact PromQL', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { result: [] } }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  await api.gpuTempSeries('1h');
  await api.gpuPowerSeries('1h');
  await api.vramUsedSeries('1h');
  await api.systemPowerSeries('1h');
  assert.ok(seen.includes('max by (gpu) (nvidia_gpu_temperature_celsius)'));
  assert.ok(seen.includes('max by (gpu) (nvidia_gpu_power_draw_watts)'));
  assert.ok(seen.includes('max by (gpu) (nvidia_gpu_memory_used_mib) / 1024'));
  assert.ok(seen.includes('system_total_power_estimate_watts'));
});

test('systemPowerSeries forces a single "total" label', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        result: [
          { metric: { instance: 'x', job: 'y' }, values: [[1000, '208']] },
        ],
      },
    }),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.systemPowerSeries('1h');
  assert.equal(out.series.length, 1);
  assert.equal(out.series[0].label, 'total');
});

test('vramUsedSeries returns honest-empty when Prometheus throws', async () => {
  const api = loadApiWithFetch(
    async () => {
      throw new Error('prometheus down');
    },
    {},
    OPTS
  );
  const out = await api.vramUsedSeries('6h');
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test --test-name-pattern="gpuUtilSeries|exact PromQL|systemPowerSeries|vramUsedSeries" src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: FAIL — `api.gpuUtilSeries is not a function` (methods not defined yet).

- [ ] **Step 3: Implement the five methods**

In `src/cofounder_agent/console/js/api.js`, insert immediately after the `costSeries` method's closing `},` (line 1267) and before the blank line + `// ── time-series trends (worker / audit_log) ──` comment:

```js

    // ── time-series trends (GPU / hardware / power — Prometheus) ─
    // Gauges: no rate() window, so query_range samples them at each step and
    // the PromQL is range-independent. max by (gpu) strips instance/job so the
    // per-GPU label seam legends them "GPU 0" / "GPU 1" (colorblind-safe via
    // TimeChart's dash + end-label).
    gpuUtilSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_utilization_percent)', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    gpuTempSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_temperature_celsius)', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    vramUsedSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_memory_used_mib) / 1024', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    gpuPowerSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          promRange('max by (gpu) (nvidia_gpu_power_draw_watts)', {
            ...o,
            labelBy: 'gpu',
            labelPrefix: 'GPU ',
          }),
        () => ({ series: [] })
      );
    },
    systemPowerSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () => labelledRange('system_total_power_estimate_watts', o, 'total'),
        () => ({ series: [] })
      );
    },
```

- [ ] **Step 4: Add the five contract-net rows**

In `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js`, insert after the `costSeries` row's closing `},` (line 399) and before the `// ── C: time-series trends (worker / audit_log) ──` comment:

```js

  // ── D: GPU / hardware / power trends (Prometheus range) — request-only ──
  {
    name: 'gpuUtilSeries',
    invoke: (api) => api.gpuUtilSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_utilization_percent)',
    },
  },
  {
    name: 'gpuTempSeries',
    invoke: (api) => api.gpuTempSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_temperature_celsius)',
    },
  },
  {
    name: 'vramUsedSeries',
    invoke: (api) => api.vramUsedSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_memory_used_mib) / 1024',
    },
  },
  {
    name: 'gpuPowerSeries',
    invoke: (api) => api.gpuPowerSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'max by (gpu) (nvidia_gpu_power_draw_watts)',
    },
  },
  {
    name: 'systemPowerSeries',
    invoke: (api) => api.systemPowerSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'system_total_power_estimate_watts',
    },
  },
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`
Expected: PASS — the 4 new api.range tests pass and `run-contracts.test.js` picks up the 5 new rows (each `invoke`d against the recorder, matched to its `query_range` PromQL). Total ~127 tests.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.range.test.js src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js
git commit -m "feat(console): 5 GPU/power promRange methods + contract rows"
```

---

## Task 4: `<TrendGroup>` extraction + `<HardwarePanel>`

**Files:**

- Modify: `src/cofounder_agent/console/js/charts.jsx:219-267` (the `HistoryPanel` function + the `Object.assign` export)

**Interfaces:**

- Consumes: `RangeControl`, `HistoryChart` (already in charts.jsx); `window.PX.api.{httpRateSeries,…,gpuUtilSeries,…,systemPowerSeries}` (Task 3).
- Produces: `window.TrendGroup`, `window.HardwarePanel` (plus the existing `window.HistoryPanel`, now built on `TrendGroup`). Consumed by Task 5.

**Note:** This is JSX rendered by in-browser Babel — there is no node unit test for it. The regression guard is (a) the full console suite still passing (the shared-scope files still parse/export) and (b) the browser smoke in Task 5. Do NOT add a `const { useState } = React` at the top of any new function that isn't already scoped — `useState` is a primitives.jsx global (see Global Constraints).

- [ ] **Step 1: Replace `HistoryPanel` with `TrendGroup` + `HistoryPanel` + `HardwarePanel`**

In `src/cofounder_agent/console/js/charts.jsx`, replace the entire `HistoryPanel` function (currently lines 219-265) with:

```jsx
// A titled RangeControl over a responsive grid of HistoryCharts. Each group
// owns its own range state, so HISTORY and GPU & POWER range independently.
function TrendGroup({ id, heading, charts }) {
  const [range, setRange] = useState('6h');
  return (
    <div id={id}>
      <div className="panel__head" style={{ marginBottom: 8 }}>
        <span className="panel__title">
          <span className="idx">▤</span>
          {heading}
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

function HistoryPanel() {
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
    { title: 'QA pass-rate', fn: (x) => api.qaTrend(x), unit: '%' },
    {
      title: 'Findings by severity',
      fn: (x) => api.findingsTrend(x),
      unit: '',
    },
  ];
  return <TrendGroup id="sec-history" heading="HISTORY" charts={charts} />;
}

function HardwarePanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'GPU utilization', fn: (x) => api.gpuUtilSeries(x), unit: '%' },
    { title: 'GPU temperature', fn: (x) => api.gpuTempSeries(x), unit: '°C' },
    { title: 'VRAM used', fn: (x) => api.vramUsedSeries(x), unit: ' GB' },
    { title: 'GPU power draw', fn: (x) => api.gpuPowerSeries(x), unit: ' W' },
    { title: 'System power', fn: (x) => api.systemPowerSeries(x), unit: ' W' },
  ];
  return <TrendGroup id="sec-hardware" heading="GPU & POWER" charts={charts} />;
}
```

(The `HistoryPanel` chart list is C's existing seven verbatim — only the enclosing RangeControl+grid shell moved into `TrendGroup`.)

- [ ] **Step 2: Export the new components**

In `src/cofounder_agent/console/js/charts.jsx`, update the final `Object.assign` (currently line 267):

```jsx
Object.assign(window, {
  TimeChart,
  RangeControl,
  TrendGroup,
  HistoryPanel,
  HardwarePanel,
});
```

- [ ] **Step 3: Verify the console suite still passes (no shared-scope break)**

Run: `node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`
Expected: PASS — same count as end of Task 3 (~127). The JSX isn't unit-tested, but a parse/scope error in charts.jsx would not affect node tests; the real render check is Task 5's browser smoke.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/js/charts.jsx
git commit -m "feat(console): extract TrendGroup; add HardwarePanel (GPU & POWER group)"
```

---

## Task 5: Wire into Telemetry tab, drop the embed, browser-smoke

**Files:**

- Modify: `src/cofounder_agent/console/js/app.jsx:1466-1467`
- Modify: `src/cofounder_agent/console/js/panels2.jsx:1591-1596`

**Interfaces:**

- Consumes: `window.HardwarePanel` (Task 4).

- [ ] **Step 1: Render `<HardwarePanel/>` in the Telemetry tab**

In `src/cofounder_agent/console/js/app.jsx`, change (line 1466-1467):

```jsx
              <div id="sec-telemetry">
                <HistoryPanel />
```

to:

```jsx
              <div id="sec-telemetry">
                <HistoryPanel />
                <HardwarePanel />
```

- [ ] **Step 2: Drop the `hardware-power` embed**

In `src/cofounder_agent/console/js/panels2.jsx`, change the `GRAFANA_EMBEDS` block (lines 1591-1596):

```js
const GRAFANA_EMBEDS = [
  // Spend-over-time is now the native History panel (sub-project C). GPU + DB
  // stay embedded until sub-projects D/E replace them with native panels.
  { uid: 'hardware-power', panelId: 4, label: 'GPU history' },
  { uid: 'database', panelId: 2, label: 'DB connections' },
];
```

to:

```js
const GRAFANA_EMBEDS = [
  // Spend-over-time (C) and GPU/power (D) are now native History/Hardware
  // panels. Only the DB embed remains, until sub-project E replaces it.
  { uid: 'database', panelId: 2, label: 'DB connections' },
];
```

- [ ] **Step 3: Run the full console suite**

Run: `node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`
Expected: PASS — unchanged count (~127); this task only touches JSX wiring.

- [ ] **Step 4: Browser smoke (mandatory — Babel per-file compile does not catch a global-scope crash)**

Ensure a no-cache static server config exists, then render the console and verify.

First, (re)create the no-cache server in the current scratchpad (the committed `.claude/launch.json` "console" entry may point at a stale path). Write `nocache_server.py`:

```python
import sys, http.server, socketserver

port, root = int(sys.argv[1]), sys.argv[2]

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=root, **k)
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

S(('127.0.0.1', port), H).serve_forever()
```

Point `.claude/launch.json`'s `console` config `runtimeArgs[0]` at this file's absolute path (port `8900`, root `src/cofounder_agent/console`). Then `preview_start` the `console` server and, in the preview page:

- Navigate to `/console/` and switch to the **Telemetry** tab.
- Confirm a **GPU & POWER** group renders **5** chart tiles in the ~440px masonry column (1-up), below the HISTORY group, each with its own RangeControl.
- In mock mode (no live Prometheus), every tile shows "no data in range" (honest-empty), NOT a crash or blank.
- Confirm no `HardwarePanel is not defined` / `Identifier 'useState' has already been declared` errors via `preview_console_logs` (level `error`).
- If a live Prometheus is reachable, confirm the GPU charts show **two** dash-distinguished lines end-labelled "GPU 0" / "GPU 1", and System power shows one line labelled "total".

If any check fails, read the source, fix, re-run Step 3 + this step. (This is exactly the step that caught C's silent global-scope abort.)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/panels2.jsx
git commit -m "feat(console): mount HardwarePanel in Telemetry; drop hardware-power embed"
```

---

## Completion

After Task 5, all five tasks are done and verified. Announce and use **superpowers:finishing-a-development-branch**:

- Verify the full console suite is green from the worktree root.
- Push `feat/console-hardware-power` and open a PR against `Glad-Labs/glad-labs-stack` (squash, linear history). CI will run `console-unit` (the only relevant required check — no backend/route/service means no `test-backend`/`regen-services-doc`/route-guard exposure, though the full suite still runs).
- Enable squash auto-merge; watch CI to the terminal outcome (merged, or a check fails) with a Monitor.
- On merge: update memory (`project_console_primary_ui.md` D bullet → SHIPPED; `MEMORY.md` pointer) and report. E is the last sub-project.

```

```
