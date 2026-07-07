# Sub-project E — Native Postgres-internals panels (design)

**Status:** approved (brainstorm 2026-07-07)
**Program:** console-reliability (A #2141 · B #2165 · C #2192 · D #2196 · **E** — final)
**Depends on:** sub-projects C (`260d4d80d`) + D (`c8429203e`) — reuses `promRange`, `js/timeseries.js` (`PX.ts`), `js/charts.jsx` (`TimeChart` / `RangeControl` / `TrendGroup` / `HistoryChart`) and D's `matrixToSeries` `labelBy` seam. **Adds no new primitive.**

## Goal

Replace the operator console's **last** embedded Grafana panel (the `database` / "DB connections" iframe) with native SVG trend charts of the Postgres internals that matter at a glance — connections, connection states, cache-hit ratio, transaction rate, database size, and dead tuples — making the Telemetry tab **100% native (zero iframes)** and completing the C/D/E program.

## Context & motivation

The console (`src/cofounder_agent/console/`, no-build React) is Matt's primary operating UI; Grafana is the deep-dive fallback. C replaced the cost embed, D replaced the hardware embed, and this leaves one:

```js
// console/js/panels2.jsx — GRAFANA_EMBEDS after D
{ uid: 'database', panelId: 2, label: 'DB connections' },  // ← E replaces this (the last one)
```

E replaces it. Removing the last embed makes `GRAFANA_EMBEDS` empty, so the `GrafanaEmbed` component and `grafanaPanelUrl` helper become dead and are removed — the Telemetry tab renders no iframes at all. The full Grafana **Database** board stays for SQL-backed deep-dives (per-table row counts, active queries, index usage — things served by a Grafana Postgres SQL datasource that the console cannot reach).

**E is the smallest of the three.** C built the foundation (`promRange`, `PX.ts`, `TimeChart`, colorblind-safe encoding, honest-empty, the contract-net `rangeQuery` flag). D added the `labelBy` seam and extracted `TrendGroup`. E is _only_ 6 `PX.api` methods + one `TrendGroup` wrapper + wiring + the embed cleanup. **No new primitive, no backend, no worker route** — so it trips neither the `regen-services-doc` nor the `_WORKER_ROUTES` census guard. E being the third consumer that needed zero new infrastructure is itself the proof the seams were designed right.

## Verified metric landscape (live Prometheus, `localhost:9091`, 2026-07-07)

postgres*exporter is live (`pg_up=1`, job `postgres-exporter:9187`, 353 `pg*\*` metrics):

```
sum(pg_stat_database_numbackends)                 = 31       pg_settings_max_connections = 300
sum by (state) (pg_stat_activity_count)           = idle:29, active:0, idle in transaction:0, + 3 always-0 states
cache hit %                                        = 96.98
sum(pg_stat_user_tables_n_dead_tup)               = 30403
pg_database_size_bytes{datname="poindexter_brain"} = 1.63 GB
```

**Gotcha — ephemeral test DBs.** `pg_database_size_bytes{datname=~"poindexter.*"}` returns **14 series** because unit/e2e runs spawn `poindexter_unit_*` / `poindexter_e2e_*` / `poindexter_test_*` databases. Size queries must match the two real DBs exactly: `datname=~"poindexter|poindexter_brain"`. Connections / cache / transactions / dead-tuples aggregate cluster-wide with `sum(...)`, so they're unaffected.

## Non-goals (YAGNI — remain on the Grafana Database board)

Per-table row counts, table sizes, index usage, active-queries list, dead-tuples-by-table — all Grafana **SQL-datasource** panels (they query Postgres directly, not Prometheus, so the console can't reach them). Also deferred: deadlocks-rate and checkpoint/bgwriter activity (Matt chose Core 6, not Comprehensive 8). These stay one click away in Grafana.

## Architecture

Pure front-end, reusing everything:

1. **Six `PX.api` methods** — each a `promRange`/`labelledRange` call, `pick`-wrapped with honest-empty `{series:[]}` mock fallbacks. Two are 2-series merges (`Promise.all` + concat) following the exact `httpLatencySeries` shape.
2. **A `<DatabasePanel>`** — a `TrendGroup id="sec-database" heading="DATABASE"` wrapper (identical structure to `HistoryPanel` / `HardwarePanel`), rendered into `#sec-telemetry` after `<HardwarePanel>`.
3. **Embed cleanup** — drop the `database` row (→ `GRAFANA_EMBEDS = []`), remove the now-dead `<GrafanaEmbed>` render + component + `grafanaPanelUrl`.

### The Core 6 charts

All aggregate cluster-wide except size (per real DB) and state (per state). Gauges take no rate window; the transaction-rate chart uses `winFor(o)` like C's rate charts.

| Chart                | PromQL (exact, 1h→`winFor`=60s)                                                                             | Series                         | Pattern                     |
| -------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------ | --------------------------- |
| Connections          | `sum(pg_stat_database_numbackends)` + `pg_settings_max_connections`                                         | 2: "in use" / "max"            | 2-query merge               |
| Connections by state | `sum by (state) (pg_stat_activity_count{state=~"active\|idle\|idle in transaction"})`                       | 3                              | `labelBy:'state'`           |
| Cache hit ratio      | `sum(pg_stat_database_blks_hit) / (sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100` | 1 ("hit %")                    | forced label                |
| Transaction rate     | `sum(rate(pg_stat_database_xact_commit[60s]))` + `sum(rate(pg_stat_database_xact_rollback[60s]))`           | 2: "commits/s" / "rollbacks/s" | 2-query merge + rate window |
| Database size        | `pg_database_size_bytes{datname=~"poindexter\|poindexter_brain"} / 1073741824`                              | 2 (GB, per DB)                 | `labelBy:'datname'`         |
| Dead tuples          | `sum(pg_stat_user_tables_n_dead_tup)`                                                                       | 1 ("dead tuples")              | forced label                |

## `PX.api` methods (six, `js/api.js`)

Placed after D's `systemPowerSeries` in the same `pick(live, mock)` style. The two 2-series merges mirror `httpLatencySeries` (api.js:1240) exactly:

```js
dbConnectionsSeries(range) {
  const o = rangeOpts(range);
  return pick(
    async () => {
      const [inUse, max] = await Promise.all([
        labelledRange('sum(pg_stat_database_numbackends)', o, 'in use'),
        labelledRange('pg_settings_max_connections', o, 'max'),
      ]);
      return { series: [...inUse.series, ...max.series] };
    },
    () => ({ series: [] })
  );
},
dbConnStateSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () =>
      promRange(
        'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})',
        { ...o, labelBy: 'state' }
      ),
    () => ({ series: [] })
  );
},
dbCacheHitSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () =>
      labelledRange(
        'sum(pg_stat_database_blks_hit) / ' +
          '(sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100',
        o,
        'hit %'
      ),
    () => ({ series: [] })
  );
},
dbTxnRateSeries(range) {
  const o = rangeOpts(range);
  const w = winFor(o);
  return pick(
    async () => {
      const [c, r] = await Promise.all([
        labelledRange(`sum(rate(pg_stat_database_xact_commit[${w}]))`, o, 'commits/s'),
        labelledRange(`sum(rate(pg_stat_database_xact_rollback[${w}]))`, o, 'rollbacks/s'),
      ]);
      return { series: [...c.series, ...r.series] };
    },
    () => ({ series: [] })
  );
},
dbSizeSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () =>
      promRange(
        'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824',
        { ...o, labelBy: 'datname' }
      ),
    () => ({ series: [] })
  );
},
dbDeadTuplesSeries(range) {
  const o = rangeOpts(range);
  return pick(
    () => labelledRange('sum(pg_stat_user_tables_n_dead_tup)', o, 'dead tuples'),
    () => ({ series: [] })
  );
},
```

## `<DatabasePanel>` + wiring (`js/charts.jsx`, `js/app.jsx`)

```jsx
function DatabasePanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'Connections', fn: (x) => api.dbConnectionsSeries(x), unit: '' },
    {
      title: 'Connections by state',
      fn: (x) => api.dbConnStateSeries(x),
      unit: '',
    },
    { title: 'Cache hit ratio', fn: (x) => api.dbCacheHitSeries(x), unit: '%' },
    {
      title: 'Transaction rate',
      fn: (x) => api.dbTxnRateSeries(x),
      unit: '/s',
    },
    { title: 'Database size', fn: (x) => api.dbSizeSeries(x), unit: ' GB' },
    { title: 'Dead tuples', fn: (x) => api.dbDeadTuplesSeries(x), unit: '' },
  ];
  return <TrendGroup id="sec-database" heading="DATABASE" charts={charts} />;
}
```

Export it in the `Object.assign(window, {...})` alongside `HardwarePanel`, and render `<DatabasePanel />` after `<HardwarePanel />` in `#sec-telemetry` (app.jsx).

## Embed removal (the 100%-native capstone)

- **`js/panels2.jsx`** — delete the `database` row (→ `GRAFANA_EMBEDS = []`), then delete the now-dead `GrafanaEmbed` component, the `GRAFANA_EMBEDS` const, and its entry in the module's `Object.assign` export.
- **`js/app.jsx`** — remove the `<GrafanaEmbed />` render (line ~1479).
- **`js/telemetry.js`** — remove `grafanaPanelUrl` (only `GrafanaEmbed` used it); keep the rest of `PX.telemetry`.
- **`js/api.js`** — `grafanaBase()` + `setGrafanaEmbed()` become dead once `GrafanaEmbed` is gone. The plan's first step verifies no other caller (grep); if none, remove both and their no-op rows from the contract manifest. If a caller is found, leave them and note it. (Conservative: the feature does not depend on this removal — it's keep-codebase-current cleanup.)

## Contract-net rows (`js/__tests__/contracts/contracts.manifest.js`)

Six request-only rows reusing the `rangeQuery` flag. Two (`dbConnectionsSeries`, `dbTxnRateSeries`) use an array of two request objects (like `httpLatencySeries`):

```js
{ name: 'dbConnStateSeries', invoke: (api) => api.dbConnStateSeries('1h'),
  request: { host: 'prometheus', rangeQuery: true,
    query: 'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})' } },
{ name: 'dbCacheHitSeries', invoke: (api) => api.dbCacheHitSeries('1h'),
  request: { host: 'prometheus', rangeQuery: true,
    query: 'sum(pg_stat_database_blks_hit) / (sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100' } },
{ name: 'dbSizeSeries', invoke: (api) => api.dbSizeSeries('1h'),
  request: { host: 'prometheus', rangeQuery: true,
    query: 'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824' } },
{ name: 'dbDeadTuplesSeries', invoke: (api) => api.dbDeadTuplesSeries('1h'),
  request: { host: 'prometheus', rangeQuery: true, query: 'sum(pg_stat_user_tables_n_dead_tup)' } },
{ name: 'dbConnectionsSeries', invoke: (api) => api.dbConnectionsSeries('1h'),
  request: [ { host: 'prometheus', rangeQuery: true, query: 'sum(pg_stat_database_numbackends)' },
             { host: 'prometheus', rangeQuery: true, query: 'pg_settings_max_connections' } ] },
{ name: 'dbTxnRateSeries', invoke: (api) => api.dbTxnRateSeries('1h'),
  request: [ { host: 'prometheus', rangeQuery: true, query: 'sum(rate(pg_stat_database_xact_commit[60s]))' },
             { host: 'prometheus', rangeQuery: true, query: 'sum(rate(pg_stat_database_xact_rollback[60s]))' } ] },
```

## Testing (console-only; no backend, no DB)

- **`js/__tests__/api.range.test.js`** — 6 tests: each method issues its exact PromQL (`decodeURIComponent(searchParams.get('query'))`); `dbConnStateSeries` maps a 3-state matrix to labels `['active','idle','idle in transaction']` via `labelBy`; `dbConnectionsSeries` merges two queries into 2 labeled series (`'in use'` / `'max'`); one honest-empty test (thrown fetch → `{series:[]}`, cross-realm-safe `Array.isArray` + `.length`).
- **Contract net** — the 6 manifest rows run through `run-contracts.test.js`.
- **Real-browser smoke** — no-cache static server: the DATABASE group mounts 6 correctly-titled tiles, zero console errors, honest "no data in range" in mock mode; **and the Telemetry tab has zero `<iframe>` elements** (embed removal verified). If live Prometheus is reachable, the by-state chart shows dash-distinguished "active"/"idle"/"idle in transaction" lines. Mandatory — Babel per-file compile does not catch a global-scope crash (C's lesson).

## Colorblind-safe & honest-empty (inherited)

`TimeChart` encodes identity by dash + end-label + opacity, never hue. Multi-series charts (states, size, connections, txn) read in grayscale. Empty/failed ranges → "no data in range"; stale poll → 0.5 opacity. No new handling.

## Rollout / deploy

Merges to `main`; the console is static and goes live on the next deploy-checkout `git pull` (no restart). No migration, settings, service, or route. The Grafana Database board is untouched.

## Risks & watch-outs

- **Ephemeral test-DB noise** — mitigated by exact `datname=~"poindexter|poindexter_brain"` matching on the size chart (verified: wildcard returns 14 series, exact match returns 2).
- **Consumer-rig portability** — on a rig without postgres_exporter, `promRange` returns honest-empty → "no data in range" (no crash, no fabrication).
- **Dead-code removal blast radius** — removing `grafanaBase`/`setGrafanaEmbed` touches the contract manifest's no-op-method list; gated on the plan's no-other-caller grep. The core feature (charts + iframe removal) is independent of it.
- **No census guards** — E adds no `services/*.py` and no `_WORKER_ROUTES` entry.

## File-change summary

| File                                                   | Change                                                                           |
| ------------------------------------------------------ | -------------------------------------------------------------------------------- |
| `console/js/api.js`                                    | +6 Postgres methods; remove dead `grafanaBase`/`setGrafanaEmbed` (if no caller)  |
| `console/js/charts.jsx`                                | add `<DatabasePanel>`; export it                                                 |
| `console/js/app.jsx`                                   | render `<DatabasePanel/>`; remove `<GrafanaEmbed/>`                              |
| `console/js/panels2.jsx`                               | delete `GRAFANA_EMBEDS` + `GrafanaEmbed` + its export                            |
| `console/js/telemetry.js`                              | remove dead `grafanaPanelUrl`                                                    |
| `console/js/__tests__/api.range.test.js`               | +6 method tests                                                                  |
| `console/js/__tests__/contracts/contracts.manifest.js` | +6 Prometheus rows; drop `grafanaBase`/`setGrafanaEmbed` no-op rows (if removed) |
