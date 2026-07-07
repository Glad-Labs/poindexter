# Native Postgres-internals Panels (Sub-project E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the console Telemetry tab's last embedded Grafana panel with six native SVG trend charts of Postgres internals (connections, states, cache-hit, transactions, size, dead tuples), making the tab 100% iframe-free.

**Architecture:** Pure front-end, zero new primitives. Add six `PX.api` methods calling `promRange`/`labelledRange` against postgres_exporter metrics, a `<DatabasePanel>` (a `TrendGroup` wrapper), wire it into the Telemetry tab, and remove the now-dead Grafana-embed rendering path (`GrafanaEmbed` + `GRAFANA_EMBEDS` + `telemetry.js`).

**Tech Stack:** No-build React 18 + in-browser Babel, `node:test`, client-side Prometheus `query_range`. Every primitive (`promRange`, `labelledRange`, `winFor`, `rangeOpts`, `PX.ts`, `TimeChart`, `RangeControl`, `TrendGroup`, `matrixToSeries` `labelBy`, the contract-net `rangeQuery` flag) is merged to `main` from C + D.

## Global Constraints

- **Colorblind-safe:** identity by dash + end-label + opacity, never hue. `TimeChart` already does this.
- **Honest-empty, no dummy data:** guarded fetches return real values or `{series:[]}` → "no data in range". Never fabricate.
- **Pure front-end:** E adds NO `services/*.py`, NO `_WORKER_ROUTES` entry, and NO new primitive — it trips neither census guard.
- **One global lexical scope:** every `text/babel` script shares ONE scope. `primitives.jsx:5` owns `const { useState, useEffect, useRef, useCallback } = React;`. Do NOT re-declare them in `charts.jsx`.
- **Cross-realm-safe assertions:** assert with `Array.isArray()` + `.length` + `.includes()`, never `assert.deepEqual` on a sandbox-returned value.
- **Run console tests FROM the worktree root:**
  ```bash
  node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"
  ```
- **Ephemeral-test-DB gotcha:** `pg_database_size_bytes{datname=~"poindexter.*"}` returns 14 series (unit/e2e DBs). Match the two real DBs exactly: `datname=~"poindexter|poindexter_brain"`.
- **All changes via PR; linear history.** Branch `feat/console-database-panels` is created off post-D `main`; the spec is committed.

## File Structure

| File                                                                       | Change                                                      |
| -------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `src/cofounder_agent/console/js/api.js`                                    | +6 Postgres `PX.api` methods (after `systemPowerSeries`)    |
| `src/cofounder_agent/console/js/charts.jsx`                                | add `<DatabasePanel>`; export it                            |
| `src/cofounder_agent/console/js/app.jsx`                                   | render `<DatabasePanel/>`; remove `<GrafanaEmbed/>`         |
| `src/cofounder_agent/console/js/panels2.jsx`                               | delete `GRAFANA_EMBEDS` + `GrafanaEmbed` + its export entry |
| `src/cofounder_agent/console/js/telemetry.js`                              | delete file (only held the now-dead `grafanaPanelUrl`)      |
| `src/cofounder_agent/console/index.html`                                   | remove the `telemetry.js` `<script>`                        |
| `src/cofounder_agent/console/js/__tests__/telemetry.test.js`               | delete file (tested the deleted `grafanaPanelUrl`)          |
| `src/cofounder_agent/console/js/__tests__/api.range.test.js`               | +5 tests                                                    |
| `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` | +6 Prometheus rows                                          |

**Retained (not removed):** `grafanaBase()` / `setGrafanaEmbed()` in `api.js` — generic Grafana-base config accessors with no current caller, kept for a plausible future "open in Grafana ↗" deeplink; removing them would churn `README.md` + the contract-manifest comment for marginal gain.

---

## Task 1: Six Postgres `PX.api` methods + contract rows

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (insert after the `systemPowerSeries` method, before the `// ── time-series trends (worker / audit_log) ──` comment)
- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (insert after the `systemPowerSeries` row, before the `// ── C: time-series trends (worker / audit_log) ──` comment)
- Test: `src/cofounder_agent/console/js/__tests__/api.range.test.js`

**Interfaces:**

- Consumes: `promRange(promql, {rangeSeconds, stepSeconds, labelBy, labelPrefix})`, `labelledRange(promql, opts, label)`, `rangeOpts(range)`, `winFor(o)`, `pick(live, mock)` — all existing.
- Produces: `dbConnectionsSeries`, `dbConnStateSeries`, `dbCacheHitSeries`, `dbTxnRateSeries`, `dbSizeSeries`, `dbDeadTuplesSeries` — each `Promise<{series:[{label, points}]}>`, honest-empty `{series:[]}` on failure/mock. Consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/console/js/__tests__/api.range.test.js`:

```js
test('dbConnStateSeries issues the by-state query and labels each state', async () => {
  const fetchImpl = async (url) => {
    const q = decodeURIComponent(new URL(url).searchParams.get('query') || '');
    assert.equal(
      q,
      'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})'
    );
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [
            { metric: { state: 'active' }, values: [[1000, '1']] },
            { metric: { state: 'idle' }, values: [[1000, '29']] },
            { metric: { state: 'idle in transaction' }, values: [[1000, '0']] },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbConnStateSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 3);
  assert.ok(
    labels.includes('active') &&
      labels.includes('idle') &&
      labels.includes('idle in transaction')
  );
});

test('cache-hit / size / dead-tuples issue their exact PromQL', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          result: [
            { metric: { datname: 'poindexter' }, values: [[1000, '1']] },
          ],
        },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  await api.dbCacheHitSeries('1h');
  await api.dbSizeSeries('1h');
  await api.dbDeadTuplesSeries('1h');
  assert.ok(
    seen.includes(
      'sum(pg_stat_database_blks_hit) / (sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100'
    )
  );
  assert.ok(
    seen.includes(
      'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824'
    )
  );
  assert.ok(seen.includes('sum(pg_stat_user_tables_n_dead_tup)'));
});

test('dbSizeSeries labels series by datname', async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        result: [
          { metric: { datname: 'poindexter' }, values: [[1000, '2']] },
          { metric: { datname: 'poindexter_brain' }, values: [[1000, '1.5']] },
        ],
      },
    }),
  });
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbSizeSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.ok(
    labels.includes('poindexter') && labels.includes('poindexter_brain')
  );
});

test('dbConnectionsSeries merges in-use + max into two labeled series', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: { result: [{ metric: {}, values: [[1000, '1']] }] },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbConnectionsSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('in use') && labels.includes('max'));
  assert.ok(seen.includes('sum(pg_stat_database_numbackends)'));
  assert.ok(seen.includes('pg_settings_max_connections'));
});

test('dbTxnRateSeries merges commits + rollbacks with a rate window', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(decodeURIComponent(new URL(url).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: { result: [{ metric: {}, values: [[1000, '0']] }] },
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.dbTxnRateSeries('1h');
  const labels = out.series.map((s) => s.label);
  assert.equal(out.series.length, 2);
  assert.ok(labels.includes('commits/s') && labels.includes('rollbacks/s'));
  assert.ok(seen.includes('sum(rate(pg_stat_database_xact_commit[60s]))'));
  assert.ok(seen.includes('sum(rate(pg_stat_database_xact_rollback[60s]))'));
});

test('dbCacheHitSeries returns honest-empty when Prometheus throws', async () => {
  const api = loadApiWithFetch(
    async () => {
      throw new Error('prometheus down');
    },
    {},
    OPTS
  );
  const out = await api.dbCacheHitSeries('6h');
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test --test-name-pattern="dbConnStateSeries|exact PromQL|dbSizeSeries|dbConnectionsSeries|dbTxnRateSeries|dbCacheHitSeries returns" src/cofounder_agent/console/js/__tests__/api.range.test.js`
Expected: FAIL — `api.dbConnStateSeries is not a function` (methods not defined yet).

- [ ] **Step 3: Implement the six methods**

In `src/cofounder_agent/console/js/api.js`, insert after the `systemPowerSeries` method's closing `},` and before the blank line + `// ── time-series trends (worker / audit_log) ──` comment:

```js

    // ── time-series trends (Postgres internals — postgres_exporter) ─
    // Cluster-wide sums except size (per real DB) + state (per state). The
    // two 2-series merges mirror httpLatencySeries. datname is matched exactly
    // to skip ephemeral unit/e2e test DBs.
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

- [ ] **Step 4: Add the six contract-net rows**

In `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js`, insert after the `systemPowerSeries` row's closing `},` and before the `// ── C: time-series trends (worker / audit_log) ──` comment:

```js

  // ── E: Postgres internals (Prometheus range) — request-only ──
  {
    name: 'dbConnStateSeries',
    invoke: (api) => api.dbConnStateSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'sum by (state) (pg_stat_activity_count{state=~"active|idle|idle in transaction"})',
    },
  },
  {
    name: 'dbCacheHitSeries',
    invoke: (api) => api.dbCacheHitSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'sum(pg_stat_database_blks_hit) / (sum(pg_stat_database_blks_hit) + sum(pg_stat_database_blks_read)) * 100',
    },
  },
  {
    name: 'dbSizeSeries',
    invoke: (api) => api.dbSizeSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query:
        'pg_database_size_bytes{datname=~"poindexter|poindexter_brain"} / 1073741824',
    },
  },
  {
    name: 'dbDeadTuplesSeries',
    invoke: (api) => api.dbDeadTuplesSeries('1h'),
    request: {
      host: 'prometheus',
      rangeQuery: true,
      query: 'sum(pg_stat_user_tables_n_dead_tup)',
    },
  },
  {
    name: 'dbConnectionsSeries',
    invoke: (api) => api.dbConnectionsSeries('1h'),
    request: [
      { host: 'prometheus', rangeQuery: true, query: 'sum(pg_stat_database_numbackends)' },
      { host: 'prometheus', rangeQuery: true, query: 'pg_settings_max_connections' },
    ],
  },
  {
    name: 'dbTxnRateSeries',
    invoke: (api) => api.dbTxnRateSeries('1h'),
    request: [
      { host: 'prometheus', rangeQuery: true, query: 'sum(rate(pg_stat_database_xact_commit[60s]))' },
      { host: 'prometheus', rangeQuery: true, query: 'sum(rate(pg_stat_database_xact_rollback[60s]))' },
    ],
  },
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`
Expected: PASS — the 6 new api.range tests pass and `run-contracts.test.js` picks up the 6 new rows.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.range.test.js src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js
git commit -m "feat(console): 6 Postgres-internals promRange methods + contract rows"
```

---

## Task 2: `<DatabasePanel>` in charts.jsx

**Files:**

- Modify: `src/cofounder_agent/console/js/charts.jsx` (add `DatabasePanel` after `HardwarePanel`; add to the `Object.assign` export)

**Interfaces:**

- Consumes: `TrendGroup` (charts.jsx), `window.PX.api.db*Series` (Task 1).
- Produces: `window.DatabasePanel`. Consumed by Task 3.

**Note:** JSX rendered by in-browser Babel — no node unit test; the guard is the console suite still passing + Task 3's browser smoke. Do NOT add `const { useState } = React` anywhere in this file.

- [ ] **Step 1: Add the `DatabasePanel` component**

In `src/cofounder_agent/console/js/charts.jsx`, insert after the `HardwarePanel` function (just before the final `Object.assign(window, {...})`):

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

- [ ] **Step 2: Export it**

Update the final `Object.assign` in `charts.jsx`:

```jsx
Object.assign(window, {
  TimeChart,
  RangeControl,
  TrendGroup,
  HistoryPanel,
  HardwarePanel,
  DatabasePanel,
});
```

- [ ] **Step 3: Verify the console suite + JSX transform**

Run:

```bash
node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"
node -e "const fs=require('fs');const d='src/cofounder_agent/console/js';const B=require('./'+d+'/vendor/babel.min.js');B.transform(fs.readFileSync(d+'/charts.jsx','utf8'),{presets:['react']});console.log('charts.jsx JSX OK')"
```

Expected: tests PASS (same count as Task 1 end); `charts.jsx JSX OK`.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/js/charts.jsx
git commit -m "feat(console): add DatabasePanel (DATABASE TrendGroup)"
```

---

## Task 3: Wire it in, remove the last embed, browser-smoke

**Files:**

- Modify: `src/cofounder_agent/console/js/app.jsx` (add `<DatabasePanel/>`; remove `<GrafanaEmbed/>`)
- Modify: `src/cofounder_agent/console/js/panels2.jsx` (delete `GRAFANA_EMBEDS` + `GrafanaEmbed` + its export entry)
- Delete: `src/cofounder_agent/console/js/telemetry.js`
- Delete: `src/cofounder_agent/console/js/__tests__/telemetry.test.js`
- Modify: `src/cofounder_agent/console/index.html` (remove the `telemetry.js` `<script>`)

**Interfaces:**

- Consumes: `window.DatabasePanel` (Task 2).

- [ ] **Step 1: Render `<DatabasePanel/>` after `<HardwarePanel/>`**

In `src/cofounder_agent/console/js/app.jsx` (line ~1468):

```jsx
                <HistoryPanel />
                <HardwarePanel />
                <DatabasePanel />
```

- [ ] **Step 2: Remove the `<GrafanaEmbed/>` render**

In `src/cofounder_agent/console/js/app.jsx`, delete the `<GrafanaEmbed />` line (leaving `<TracesPanel .../>` as the last child before `</div>`):

```jsx
                <TracesPanel traces={traces} fresh={tracesR} />
              </div>
```

- [ ] **Step 3: Delete the `GrafanaEmbed` component + `GRAFANA_EMBEDS`**

In `src/cofounder_agent/console/js/panels2.jsx`, delete the `GRAFANA_EMBEDS` const + its leading comment (lines ~1589-1596) and the entire `GrafanaEmbed` function (lines ~1596-1635), and remove the `GrafanaEmbed,` line from the module's `Object.assign(window, {...})` export (the last entry).

- [ ] **Step 4: Delete the dead `telemetry.js` module + its test + script tag**

```bash
git rm src/cofounder_agent/console/js/telemetry.js src/cofounder_agent/console/js/__tests__/telemetry.test.js
```

In `src/cofounder_agent/console/index.html`, delete the two lines (the comment + the script):

```html
<!-- Pure Grafana-embed URL builder (PX.telemetry) — must load before panels2.jsx. -->
<script src="js/telemetry.js"></script>
```

- [ ] **Step 5: Run the console suite + JSX transforms**

Run:

```bash
node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"
node -e "const fs=require('fs');const d='src/cofounder_agent/console/js';const B=require('./'+d+'/vendor/babel.min.js');for(const f of ['app.jsx','panels2.jsx','charts.jsx'])B.transform(fs.readFileSync(d+'/'+f,'utf8'),{presets:['react']});console.log('JSX OK')"
```

Expected: PASS — count is (Task 1 end) minus 5 (the deleted `telemetry.test.js`); `JSX OK`. There must be no `grafanaPanelUrl`/`GrafanaEmbed` reference left: `grep -rn "GrafanaEmbed\|grafanaPanelUrl\|GRAFANA_EMBEDS\|PX.telemetry" src/cofounder_agent/console/js src/cofounder_agent/console/index.html` returns nothing.

- [ ] **Step 6: Browser smoke (mandatory)**

Recreate the no-cache server if needed (`.claude/launch.json` `console` config → `nocache_server.py` in the current scratchpad, port 8900, root `src/cofounder_agent/console`), `preview_start` the `console` server, navigate to `/console/`, switch to the **Telemetry** tab, and verify:

- `window.DatabasePanel` is a function; no console errors (`preview_console_logs` level error empty).
- `#sec-database` renders **6** `.tc-panel` tiles titled Connections / Connections by state / Cache hit ratio / Transaction rate / Database size / Dead tuples, under a "DATABASE" heading + a RangeControl.
- In mock mode every tile shows "no data in range" (honest-empty).
- **Zero iframes in the Telemetry tab:** `document.querySelectorAll('#sec-telemetry iframe').length === 0` and `#sec-grafana` is gone.
- (If live Prometheus is reachable) inject a 3-state sample into `dbConnStateSeries` and force a re-poll (click a range button) — confirm three dash-distinguished "active"/"idle"/"idle in transaction" lines with end-labels.

If any check fails, read source, fix, re-run Step 5 + this step.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/panels2.jsx src/cofounder_agent/console/index.html
git commit -m "feat(console): mount DatabasePanel; remove last Grafana embed (100% native telemetry)"
```

---

## Completion

After Task 3, announce and use **superpowers:finishing-a-development-branch**:

- Verify the full console suite green from the worktree root.
- Rebase onto latest `main`, push `feat/console-database-panels`, open a PR against `Glad-Labs/glad-labs-stack` (squash, linear history). Only `console-unit` is E-relevant; `test-backend`/`integration-db` run but pass unchanged (no Python).
- Enable squash auto-merge; watch CI to the terminal outcome with a Monitor.
- On merge: update memory (`project_console_primary_ui.md` E bullet → SHIPPED, program complete; `MEMORY.md` pointer) and report the whole C/D/E program done — the console Telemetry tab is 100% native.

```

```
