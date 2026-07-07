# Sub-project D — Native GPU / hardware / power trend panels (design)

**Status:** approved (brainstorm 2026-07-07)
**Program:** console-reliability (A shipped #2141 · B shipped #2165 · C shipped #2192 · **D** · E next)
**Depends on:** sub-project C (merged `260d4d80d`) — reuses its `promRange`, `js/timeseries.js` (`PX.ts`), and `js/charts.jsx` (`TimeChart` / `RangeControl` / `HistoryChart`).

## Goal

Replace the operator console's last embedded Grafana **hardware** panel with native SVG trend charts — GPU utilization, temperature, VRAM, power draw (all per-GPU), plus total system power — reusing sub-project C's chart stack, so day-to-day hardware watching happens in-console instead of an iframe.

## Context & motivation

The console (`src/cofounder_agent/console/`, no-build React) is Matt's primary operating UI; Grafana is the deep-dive fallback. Sub-project C replaced the Telemetry tab's cost-analytics embed with native trend charts and left two Grafana `/d-solo` iframes behind for D and E:

```js
// console/js/panels2.jsx — current GRAFANA_EMBEDS
{ uid: 'hardware-power', panelId: 4, label: 'GPU history' },  // ← D replaces this
{ uid: 'database',       panelId: 2, label: 'DB connections' }, // ← E replaces this
```

Panel `hardware-power/4` is the Grafana board's "VRAM Usage & Power Draw" timeseries — a single embedded panel. D replaces it (and adds more of the GPU/power story) with native charts. The full Grafana **Hardware & Power** board (voltages, fans, PSU rails, electricity cost, per-core RAPL, GPU-history SQL tables — ~18 panels) stays exactly where it is for deep-dives; the console shows the operator-glance subset.

**This is a small sub-project.** C built the entire foundation (the `promRange` adapter, the pure chart-math in `PX.ts`, the `TimeChart` renderer, colorblind-safe encoding, honest-empty handling, the contract-net `rangeQuery` flag). D points that foundation at GPU/power metrics and adds one small, reusable legend seam. There is **no backend** — every metric is already in Prometheus — so D adds no service and no worker route, and therefore trips **neither** census guard that bit C (`regen-services-doc`, `test_worker_manifest_has_expected_routes`).

## Verified metric landscape (live Prometheus, `localhost:9091`, 2026-07-07)

The GPU metrics carry a per-GPU label — this rig is the two-card pool (3090 + 5090):

```
nvidia_gpu_utilization_percent{gpu="0"} = 2      nvidia_gpu_utilization_percent{gpu="1"} = 0
nvidia_gpu_memory_used_mib{gpu="0"}     = 12179   nvidia_gpu_memory_used_mib{gpu="1"}     = 7658
nvidia_gpu_temperature_celsius{gpu="0"} = 34      nvidia_gpu_temperature_celsius{gpu="1"} = 32
nvidia_gpu_power_draw_watts{gpu="0"}    = 27.38   nvidia_gpu_power_draw_watts{gpu="1"}    = 21.66
```

(Each also carries `instance="host.docker.internal:9835"`, `job="nvidia-smi"` — noise for a legend.)

Single-series power headline:

```
system_total_power_estimate_watts = 208.4   (whole-system estimate)
```

## Non-goals (YAGNI — remain on the Grafana board)

Electricity cost ($/EIA rate), CPU package power, per-core RAPL, fan RPM, voltages, GPU clock speeds, PSU-rail breakdowns, the "Gaming Detection" state panel, and the GPU-history SQL tables. All are deep-dive detail, not operator-glance signal. Matt chose the **Core 5** scope (not the Comprehensive 7) and **Replace entirely** for the embed.

## Architecture

Pure front-end, three moving parts:

1. **Two small primitive extensions** (reusable by E):
   - `matrixToSeries` gains optional per-series labelling from a chosen metric label.
   - `promRange` threads that option through.
2. **Five `PX.api` methods** — one `promRange` call each, `pick`-wrapped with honest-empty mock fallbacks (identical shape to C's five Prometheus trend methods).
3. **A `<HardwarePanel>`** rendered into the Telemetry tab, built from a **`<TrendGroup>`** extracted from C's `<HistoryPanel>` (DRY — both are "a titled RangeControl over a grid of `HistoryChart`s"). Drop the `hardware-power` embed.

### The Core 5 charts

All GPU queries use `max by (gpu) (…)` so only the `gpu` label survives (strips `instance`/`job`), giving clean two-series output legended "GPU 0" / "GPU 1". Gauges need no `rate()`/window — `query_range` samples them at each step — so, unlike C's rate-based queries, **these PromQL strings are range-independent**.

| Chart           | PromQL (exact)                                     | Series | Unit  |
| --------------- | -------------------------------------------------- | ------ | ----- |
| GPU utilization | `max by (gpu) (nvidia_gpu_utilization_percent)`    | 2      | `%`   |
| GPU temperature | `max by (gpu) (nvidia_gpu_temperature_celsius)`    | 2      | `°C`  |
| VRAM used       | `max by (gpu) (nvidia_gpu_memory_used_mib) / 1024` | 2      | ` GB` |
| GPU power draw  | `max by (gpu) (nvidia_gpu_power_draw_watts)`       | 2      | ` W`  |
| System power    | `system_total_power_estimate_watts`                | 1      | ` W`  |

VRAM is charted in **GB** (÷1024) for readability (12179 MiB → 11.9 GB). "VRAM used" per card is the freeze-watch signal (oversubscription → WDDM sysmem spill → hang, per the single-GPU-VRAM-budget work) — the operator wants to see it climbing toward the card's ceiling.

## Primitive extensions

### 1. `matrixToSeries` — optional per-series label (`js/timeseries.js`)

Current (line 93): the label is every non-`__name__` metric label joined `k=v` — for `nvidia_gpu_utilization_percent` that's the ugly `gpu=0,instance=host.docker.internal:9835,job=nvidia-smi`. Extend the signature, positionally and backward-compatibly:

```js
// matrixToSeries(result, fallbackLabel, labelBy, labelPrefix)
function matrixToSeries(result, fallbackLabel, labelBy, labelPrefix) {
  return (result || []).map((r) => {
    const m = r.metric || {};
    let label;
    if (labelBy && m[labelBy] != null) {
      label = (labelPrefix || '') + m[labelBy]; // "GPU 0"
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

Existing callers (`matrixToSeries(result)`) are unaffected. E reuses this for per-`datname` / per-`state` Postgres series.

### 2. `promRange` — thread the option through (`js/api.js`)

Line 238 becomes:

```js
return {
  series: window.PX.ts.matrixToSeries(
    result,
    undefined,
    o.labelBy,
    o.labelPrefix
  ),
};
```

`opts` gains optional `labelBy` / `labelPrefix`. Everything else about `promRange` (its own `AbortController`, `→ {series:[]}` on any failure/non-200, `HTTP_TIMEOUT_MS`) is unchanged.

**Why not `labelledRange`?** `labelledRange` (api.js:257) forces one label on _every_ series (`.map(s => ({...s, label}))`). That is correct for single-series aggregates — and the **System power** chart uses it (`labelledRange('system_total_power_estimate_watts', o, 'total')`) — but it would label both GPUs "GPU", collapsing them. The per-GPU charts need the per-series `labelBy` path.

### 3. `<TrendGroup>` — DRY extraction from `<HistoryPanel>` (`js/charts.jsx`)

C's `HistoryPanel` is "a `range` state + a `RangeControl` + a `tc-grid` of `HistoryChart`s built from a `charts` array". Extract that shell so D doesn't duplicate it:

```jsx
// charts.jsx — new reusable group; charts = [{ title, fn, unit }]
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
  // charts array is C's existing seven, moved verbatim — no change to their
  // titles/fns/units; only the enclosing shell is extracted into TrendGroup.
  return (
    <TrendGroup
      id="sec-history"
      heading="HISTORY"
      charts={[
        {
          title: 'API request rate',
          fn: (x) => api.httpRateSeries(x),
          unit: '',
        },
        // …the other six unchanged (error rate, latency, throughput, spend, QA, findings)
      ]}
    />
  );
}

function HardwarePanel() {
  const api = window.PX.api;
  return (
    <TrendGroup
      id="sec-hardware"
      heading="GPU & POWER"
      charts={[
        {
          title: 'GPU utilization',
          fn: (x) => api.gpuUtilSeries(x),
          unit: '%',
        },
        {
          title: 'GPU temperature',
          fn: (x) => api.gpuTempSeries(x),
          unit: '°C',
        },
        { title: 'VRAM used', fn: (x) => api.vramUsedSeries(x), unit: ' GB' },
        {
          title: 'GPU power draw',
          fn: (x) => api.gpuPowerSeries(x),
          unit: ' W',
        },
        {
          title: 'System power',
          fn: (x) => api.systemPowerSeries(x),
          unit: ' W',
        },
      ]}
    />
  );
}

Object.assign(window, {
  TimeChart,
  RangeControl,
  TrendGroup,
  HistoryPanel,
  HardwarePanel,
});
```

`HistoryPanel` keeps its own `range` state (independent from `HardwarePanel`'s), so the two groups range independently.

## `PX.api` methods (five, `js/api.js`)

Placed next to C's Prometheus trend methods (after `costSeries`), same `pick(live, mock)` shape:

```js
gpuUtilSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () => promRange('max by (gpu) (nvidia_gpu_utilization_percent)', { ...o, labelBy: 'gpu', labelPrefix: 'GPU ' }),
    () => ({ series: [] })
  );
},
gpuTempSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () => promRange('max by (gpu) (nvidia_gpu_temperature_celsius)', { ...o, labelBy: 'gpu', labelPrefix: 'GPU ' }),
    () => ({ series: [] })
  );
},
vramUsedSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () => promRange('max by (gpu) (nvidia_gpu_memory_used_mib) / 1024', { ...o, labelBy: 'gpu', labelPrefix: 'GPU ' }),
    () => ({ series: [] })
  );
},
gpuPowerSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () => promRange('max by (gpu) (nvidia_gpu_power_draw_watts)', { ...o, labelBy: 'gpu', labelPrefix: 'GPU ' }),
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

## Telemetry-tab wiring

- **`js/app.jsx`** — render `<HardwarePanel />` into `#sec-telemetry`, directly after `<HistoryPanel />`.
- **`js/panels2.jsx`** — delete the `{ uid: 'hardware-power', panelId: 4, label: 'GPU history' }` row from `GRAFANA_EMBEDS`, leaving only the `database` embed (E's target). Update the adjacent comment.
- **`js/charts.jsx`** — already loaded by `index.html` (C added the `<script>`); `HardwarePanel` + `TrendGroup` live in it. No new file, no new `<script>` tag.
- **`css/console.css`** — no new CSS; `HardwarePanel` reuses C's `.tc-grid` / `.tc-panel` / `.rangectl` classes.

## Contract-net rows (`js/__tests__/contracts/contracts.manifest.js`)

Five request-only rows, reusing the `rangeQuery` flag C added to `assertRequest`. Because the queries are gauges (no rate window), the expected `query` is range-independent:

```js
{ name: 'gpuUtilSeries',   host: 'prometheus', rangeQuery: true, query: 'max by (gpu) (nvidia_gpu_utilization_percent)' },
{ name: 'gpuTempSeries',   host: 'prometheus', rangeQuery: true, query: 'max by (gpu) (nvidia_gpu_temperature_celsius)' },
{ name: 'vramUsedSeries',  host: 'prometheus', rangeQuery: true, query: 'max by (gpu) (nvidia_gpu_memory_used_mib) / 1024' },
{ name: 'gpuPowerSeries',  host: 'prometheus', rangeQuery: true, query: 'max by (gpu) (nvidia_gpu_power_draw_watts)' },
{ name: 'systemPowerSeries', host: 'prometheus', rangeQuery: true, query: 'system_total_power_estimate_watts' },
```

Each row's driver calls `api.<name>('1h')` under `loadApiWithRecorder` (preloading `timeseries.js`, as C's Prometheus rows do).

## Testing (console-only; no backend, no DB)

- **`js/__tests__/timeseries.test.js`** — add one test: `matrixToSeries(result, undefined, 'gpu', 'GPU ')` labels each series `"GPU 0"` / `"GPU 1"` and still maps seconds→ms; and the no-`labelBy` path is unchanged (guards backward-compat).
- **`js/__tests__/api.range.test.js`** — five tests, one per method: assert the `query_range` URL carries the exact PromQL (`decodeURIComponent(searchParams.get('query'))`) and that a thrown fetch yields honest-empty `{series:[]}` (cross-realm-safe `Array.isArray(out.series) && out.series.length === 0`, per C's gotcha). One test asserts `gpuUtilSeries` maps a two-series matrix to labels `['GPU 0','GPU 1']` via `labels.includes(...)`.
- **Contract net** — the five manifest rows run through the existing `run-contracts.test.js` iterator.
- **Real-browser smoke** — serve the console via the vendored no-cache `ThreadingHTTPServer` (`.claude/launch.json`, from C) and confirm: the "GPU & POWER" group mounts five charts in the ~440px Telemetry masonry column (1-up), GPU charts show two dash-distinguished "GPU 0/1" lines, System power shows one, and mock mode renders honest "no data in range". This browser step is non-negotiable — C proved a Babel-per-file global-scope collision passes compile but crashes render.

## Colorblind-safe & honest-empty (inherited from C)

`TimeChart` already encodes identity by **dash pattern + direct end-label + opacity**, never hue (Matt is colorblind). The two GPUs read as "GPU 0" (solid) and "GPU 1" (dashed) with end-labels — distinguishable in grayscale. Empty/failed ranges render "no data in range" at reduced opacity; a stale poll dims to 0.5. No new handling needed.

## Rollout / deploy

Merges to `main`; the console is static and goes live on the next deploy-checkout `git pull` (no restart). No migration, no settings, no service. The Grafana Hardware & Power board is untouched.

## Risks & watch-outs

- **Multi-GPU label assumption.** Charts assume `nvidia_gpu_*` carries a `gpu` label. Verified live (2 series). If a single-GPU consumer rig exposes one series, `max by (gpu)` still yields one clean "GPU 0" line — degrades gracefully.
- **`system_total_power_estimate_watts` availability.** It's a HWiNFO/estimate-derived gauge (present on Matt's operator rig). On a consumer rig without the power sensors it may be absent → `promRange` returns honest-empty → the chart shows "no data in range" (no crash). Acceptable; the console never fabricates.
- **No census guards** (unlike C): D adds no `services/*.py` and no `_WORKER_ROUTES` entry, so neither `regen-services-doc` nor `test_worker_manifest_has_expected_routes` applies. The only cross-file coupling is the `TrendGroup` extraction touching C's `HistoryPanel` — covered by C's existing console tests plus the browser smoke.

## File-change summary

| File                                                   | Change                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------- |
| `console/js/timeseries.js`                             | `matrixToSeries` gains `labelBy`/`labelPrefix` params             |
| `console/js/api.js`                                    | `promRange` threads `labelBy`/`labelPrefix`; +5 GPU/power methods |
| `console/js/charts.jsx`                                | extract `<TrendGroup>`; add `<HardwarePanel>`; export both        |
| `console/js/app.jsx`                                   | render `<HardwarePanel />` after `<HistoryPanel />`               |
| `console/js/panels2.jsx`                               | drop the `hardware-power` embed row                               |
| `console/js/__tests__/timeseries.test.js`              | +1 `labelBy` test                                                 |
| `console/js/__tests__/api.range.test.js`               | +5 method tests                                                   |
| `console/js/__tests__/contracts/contracts.manifest.js` | +5 Prometheus rows                                                |
