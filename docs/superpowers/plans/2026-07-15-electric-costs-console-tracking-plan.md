# Electric Costs — Console Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Do NOT use subagent-driven-development or any subagent/Task-tool dispatch on this repo** — this project's CLAUDE.md disables it (Anthropic bills subagents at metered API rates, separate from the Max subscription); execute every task inline in the same session, sequentially.

**Goal:** Show electricity cost on the operator console (a Telemetry-tab trend chart + a real number on the Cost Control card's dead "Energy" row), and fix the two accuracy bugs and one gap that scoping this surfaced — a Grafana panel with a hardcoded rate, a console chart pointed at a stale wattage source, and a settings key with no bootstrap default.

**Architecture:** Two independent data flows already exist and just need new consumers wired to them. (1) Prometheus `psu_total_power_watts`/`system_total_power_estimate_watts` — live gauges, feeds two console charts. (2) `cost_logs` (a real ledger `brain_daemon.py::log_electricity_cost` has written to every 5 minutes since 2026-04-11) via `cost_ledger.get_spend()` — feeds the Grafana panel and the Cost Control card. No new services, no new HTTP routes, no new UI components; every change is a new method/field/query on existing surfaces.

**Tech Stack:** FastAPI (Python 3.13, poetry), PostgreSQL, Prometheus, Grafana (file-provisioned dashboard JSON), vanilla-React console (no build step, Babel-in-browser), `node --test` for console JS tests, `pytest` for Python tests.

**Spec:** `docs/superpowers/specs/2026-07-15-electric-costs-console-tracking-design.md`
**Tracking issue:** Glad-Labs/glad-labs-stack#2626
**PR:** Glad-Labs/glad-labs-stack#2627 (draft — commits from this plan land on `claude/electric-costs-console-tracking-1c982b`)

## Global Constraints

- No new services, no new HTTP routes, no new UI components — every piece extends an existing surface (spec Architecture intro).
- Console tests run on `node --test` (no Jest, no jsdom) via `npm run test:console`; Python tests via `poetry run pytest`.
- Mock mode must stay honest-empty on failure/absence — never fabricate a number (`feedback_no_dummy_data`). Real mock _seed_ values (in `data.js`) are fine; a _live-mode_ fallback to a fake number is not.
- New `app_settings` keys go in `settings_defaults.py`, never in a migration file (`CLAUDE.md` — Database migrations section).
- Cross-realm console test rule: assert on adapter output (`out`) with `typeof`/`Array.isArray`/`assert.equal` on primitives only — never `assert.deepEqual` on an object crossing the `node:vm` sandbox boundary (`contracts/README.md`).
- Commit style: Conventional Commits (`type(scope): summary`), one task per commit, squash-friendly — this branch squash-merges into one commit on merge.
- Every commit's trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

## File Structure

| File                                                       | Responsibility                                                                                                                                |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/cofounder_agent/services/settings_defaults.py`        | Add bootstrap default for `electricity_rate_kwh`                                                                                              |
| `infrastructure/grafana/dashboards/hardware-power.json`    | Panel 11: read `cost_logs` instead of the iCUE tap, drop the hardcoded rate                                                                   |
| `src/cofounder_agent/console/js/api.js`                    | Fix `systemPowerSeries` fallback; add `electricityRateKwh`, `electricityCostSeries`, `electricitySourceNote`; extend `budget()`'s mock branch |
| `src/cofounder_agent/console/js/charts.jsx`                | Add the "Electricity cost" chart to `HardwarePanel`                                                                                           |
| `src/cofounder_agent/services/cost_aggregation_service.py` | `get_budget_status()` surfaces the electricity axis it already computes                                                                       |
| `src/cofounder_agent/console/js/data.js`                   | Mock cost object: rename the dead `energyKwhMonth`/`electricityRate` pair to the new single-field shape                                       |
| `src/cofounder_agent/console/js/app.jsx`                   | Live cost merge reads the new backend fields                                                                                                  |
| `src/cofounder_agent/console/js/panels.jsx`                | `CostPanel`'s Energy row renders the real figure + source note                                                                                |
| `src/cofounder_agent/console/js/drawer.jsx`                | The Cost Control card's "Detail" drill-down has its own parallel Energy computation — same rename                                             |

Tests live alongside what they cover: `tests/unit/services/test_settings_defaults.py`, `tests/unit/services/test_cost_aggregation_service.py`, `console/js/__tests__/api.range.test.js`, `console/js/__tests__/contracts/contracts.manifest.js` (+ a new `contracts/fixtures/electricityRateKwh.json`).

---

### Task 1: Seed the `electricity_rate_kwh` bootstrap default

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py:220` (insert after `monthly_spend_limit_usd`)
- Test: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Produces: `DEFAULTS["electricity_rate_kwh"] == "0.16"`, consumed by `seed_all_defaults()` at boot (existing machinery, unchanged) and read later by `brain_daemon.py::log_electricity_cost` and `console/js/api.js::electricityRateKwh()` (Task 3).

- [ ] **Step 1: Write the failing test**

Append to `src/cofounder_agent/tests/unit/services/test_settings_defaults.py` (near the other single-key presence tests, e.g. after `test_rag_rerank_device_default_is_cpu`):

```python
def test_electricity_rate_kwh_default_present():
    # UpdateUtilityRatesJob only writes this key after its first successful
    # EIA call; without a seeded default it's unset from boot until then (or
    # forever, if the shared EIA DEMO_KEY is rate-limited). Every other
    # cost_guard-adjacent tunable has a bootstrap default — this one was
    # missing.
    from services.settings_defaults import DEFAULTS
    assert DEFAULTS["electricity_rate_kwh"] == "0.16"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_electricity_rate_kwh_default_present -v`
Expected: FAIL with `KeyError: 'electricity_rate_kwh'`

- [ ] **Step 3: Add the default**

In `src/cofounder_agent/services/settings_defaults.py`, find this exact block (around line 218-220):

```python
    # ----- Cost / billing -----
    'daily_spend_limit_usd': '2.0',
    'monthly_spend_limit_usd': '100.0',
```

Insert immediately after it (before the existing `# Electricity ledger (cost_ledger.get_spend)...` comment that follows):

```python
    # Electricity rate ($/kWh) used by brain_daemon.py::log_electricity_cost
    # (every 5-min cost_logs write) and the console/Grafana cost surfaces.
    # UpdateUtilityRatesJob refreshes this daily from the EIA API once it has
    # run at least once; this bootstraps a real value from first boot on
    # fresh installs instead of leaving the key unset. Approximate recent
    # U.S. national-average residential rate — a bootstrap constant, not a
    # live data point (glad-labs-stack#2626).
    'electricity_rate_kwh': '0.16',
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py -v`
Expected: PASS (all tests in the file, including the new one and `TestRegistryShape::test_defaults_is_dict_of_strings` which will also touch the new key)

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "$(cat <<'EOF'
feat(settings): seed a bootstrap default for electricity_rate_kwh

UpdateUtilityRatesJob only writes this key after its first successful
EIA call, so it was unset from boot on fresh installs (or permanently,
if the shared DEMO_KEY is rate-limited). Every other cost_guard-
adjacent tunable already has a seeded default; this one was missing.

Refs glad-labs-stack#2626

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Grafana panel 11 — read `cost_logs` instead of the iCUE tap

**Files:**

- Modify: `infrastructure/grafana/dashboards/hardware-power.json:855` (the `rawSql` value on panel id 11)

**Interfaces:**

- Consumes: `cost_logs` rows with `cost_type LIKE 'electricity%'` (already written every 5 min by `brain_daemon.py::log_electricity_cost`, unmodified by this plan).
- Produces: nothing consumed by later tasks — this piece is independent of the console work.

No automated test exists for this file today (verified: no test in the repo references `hardware-power.json`). Verification is a direct SQL check plus a Grafana reload.

- [ ] **Step 1: Confirm today's broken query and the target table, live**

Run (via the Postgres MCP, or `docker exec poindexter-postgres-local psql -U <user> -d <db> -c "..."` if the MCP isn't reachable from this session):

```sql
SELECT created_at, cost_usd, duration_ms
FROM cost_logs
WHERE cost_type LIKE 'electricity%'
ORDER BY created_at DESC
LIMIT 3;
```

Expected: 3 rows, `created_at` timestamps roughly 5 minutes apart, `duration_ms = 300000`, `cost_usd` a small positive decimal (e.g. `0.005467`). (Already verified live during the design phase: 25,939 rows since 2026-04-11, ~282/24h, $27.94 MTD.) If this returns 0 rows, STOP — `brain_daemon.py::log_electricity_cost` isn't running; that's a pre-existing operational issue outside this plan's scope, not something to fix here.

- [ ] **Step 2: Edit the panel query**

In `infrastructure/grafana/dashboards/hardware-power.json`, panel `"id": 11` (`"title": "Electricity Cost (EIA rate)"`), find the exact current line:

```json
          "rawSql": "SELECT sampled_at AS time, (metric_value / 1000.0 * 0.14 * 24)::float AS \"Est. Daily Cost\" FROM sensor_samples_unified WHERE source='corsair_csv' AND metric_name='psu_power_in' AND $__timeFilter(sampled_at) ORDER BY sampled_at",
```

Replace with:

```json
          "rawSql": "SELECT created_at AS time, (cost_usd / NULLIF(duration_ms, 0) * 3600000.0 * 24)::float AS \"Est. Daily Cost\" FROM cost_logs WHERE cost_type LIKE 'electricity%' AND $__timeFilter(created_at) ORDER BY created_at",
```

(`cost_usd / duration_ms` = $/ms for that interval; `* 3600000.0 * 24` converts to an equivalent $/day rate — same "Est. Daily Cost" framing, now sourced from what was actually measured that cycle. `NULLIF(duration_ms, 0)` avoids a divide-by-zero on any row where `duration_ms` is unexpectedly 0 rather than trusting the invariant forever.)

Nothing else in the panel changes — its top-level `"datasource"` (lines 811-814) is already `grafana-postgresql-datasource` / `local-brain-db`, which is exactly right since `cost_logs` lives in the same Postgres database; only the `targets[0].rawSql` string changes.

- [ ] **Step 3: Verify the new query directly**

Run the corrected query against the live DB (same MCP/psql access as Step 1):

```sql
SELECT created_at AS time, (cost_usd / NULLIF(duration_ms, 0) * 3600000.0 * 24)::float AS "Est. Daily Cost"
FROM cost_logs
WHERE cost_type LIKE 'electricity%' AND created_at >= NOW() - INTERVAL '1 hour'
ORDER BY created_at
LIMIT 5;
```

Expected: 5 rows (assuming the box has been up for the past hour), "Est. Daily Cost" values in a plausible range for this rig (idle draws logged ~$0.005-0.008 per 5-min interval during the design-phase check, which is ≈ $1.20-$2.30/day extrapolated — order of magnitude matches the `$1.20` "today" total already measured).

- [ ] **Step 4: Reload Grafana and confirm the panel renders**

If Grafana is reachable from this session (`http://localhost:3000` or the tailnet address), hit its reload endpoint or restart the container per the file-provisioning convention (`docker restart poindexter-grafana` if a manual reload is needed — the provisioner normally auto-reloads on file change). Open the Hardware & Power dashboard (`/d/hardware-power`) and confirm panel 11 ("Electricity Cost (EIA rate)") shows a non-empty trend line, not a query error. If Grafana isn't reachable from this session, leave this as a manual step for Matt to confirm on his next deploy-checkout pull (the JSON change alone is complete and self-contained regardless).

- [ ] **Step 5: Commit**

```bash
git add infrastructure/grafana/dashboards/hardware-power.json
git commit -m "$(cat <<'EOF'
fix(grafana): point the Electricity Cost panel at cost_logs

Panel 11 hardcoded $0.14/kWh and read the reboot-fragile iCUE CSV
tap. Both bugs are fixed by reading cost_logs instead: the brain's
5-minute log_electricity_cost writer already uses the live EIA rate
and the Shelly-first select_power_source() chain at write time, so
the panel just needs to read what was actually logged.

Refs glad-labs-stack#2626

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Console Telemetry — fix `systemPowerSeries`, add the "Electricity cost" chart

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js:1437-1443` (fix), `src/cofounder_agent/console/js/api.js` (new methods, placed immediately after `systemPowerSeries`)
- Modify: `src/cofounder_agent/console/js/charts.jsx` (`HardwarePanel`'s chart array)
- Test: `src/cofounder_agent/console/js/__tests__/api.range.test.js`
- Test: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (+1 row)

**Interfaces:**

- Consumes: `pick`, `promRange`, `labelledRange`, `rangeOpts`, `http` (all already defined earlier in `api.js`, unchanged).
- Produces: `PX.api.electricityRateKwh(): Promise<number|null>` (internal — called by `electricityCostSeries` below via `this`, not consumed by any later task), `PX.api.electricityCostSeries(range: string): Promise<{series: Array<{label:string, points:[number,number][]}>}>` (wired into the chart array in this same task's Step 4 — self-contained, nothing downstream depends on it either).

- [ ] **Step 1: Write the failing tests**

Add to `src/cofounder_agent/console/js/__tests__/api.range.test.js` (near the existing `gpu/vram/system methods issue their exact PromQL` and `systemPowerSeries forces a single "total" label` tests):

```js
test('systemPowerSeries prefers psu_total_power_watts over the estimate', async () => {
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
  await api.systemPowerSeries('1h');
  assert.ok(
    seen.includes('psu_total_power_watts or system_total_power_estimate_watts')
  );
});

test('electricityRateKwh parses a numeric settings value', async () => {
  const fetchImpl = async (url) => {
    assert.ok(String(url).includes('/api/settings'));
    assert.ok(String(url).includes('search=electricity_rate_kwh'));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        items: [{ key: 'electricity_rate_kwh', value: '0.1523' }],
        total: 1,
        limit: 10,
        offset: 0,
      }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  const out = await api.electricityRateKwh();
  assert.equal(out, 0.1523);
});

test('electricityRateKwh returns null when the key is missing or malformed', async () => {
  const missing = loadApiWithFetch(
    async () => ({
      ok: true,
      status: 200,
      json: async () => ({ items: [], total: 0, limit: 10, offset: 0 }),
    }),
    {},
    OPTS
  );
  assert.equal(await missing.electricityRateKwh(), null);

  const malformed = loadApiWithFetch(
    async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        items: [{ key: 'electricity_rate_kwh', value: 'not-a-number' }],
        total: 1,
        limit: 10,
        offset: 0,
      }),
    }),
    {},
    OPTS
  );
  assert.equal(await malformed.electricityRateKwh(), null);
});

test('electricityCostSeries interpolates the fetched rate into the PromQL', async () => {
  const seen = [];
  const fetchImpl = async (url) => {
    const u = String(url);
    if (u.includes('/api/settings')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          items: [{ key: 'electricity_rate_kwh', value: '0.2' }],
          total: 1,
          limit: 10,
          offset: 0,
        }),
      };
    }
    seen.push(decodeURIComponent(new URL(u).searchParams.get('query') || ''));
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { result: [] } }),
    };
  };
  const api = loadApiWithFetch(fetchImpl, {}, OPTS);
  await api.electricityCostSeries('1h');
  assert.ok(
    seen.includes(
      '(psu_total_power_watts or system_total_power_estimate_watts) / 1000 * 0.2 * 24'
    )
  );
});

test('electricityCostSeries is honest-empty when the rate is unavailable', async () => {
  const api = loadApiWithFetch(
    async (url) => {
      if (String(url).includes('/api/settings')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ items: [], total: 0, limit: 10, offset: 0 }),
        };
      }
      throw new Error('should not query Prometheus without a rate');
    },
    {},
    OPTS
  );
  const out = await api.electricityCostSeries('1h');
  assert.ok(Array.isArray(out.series) && out.series.length === 0);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test "src/cofounder_agent/console/js/__tests__/api.range.test.js"`
Expected: the `systemPowerSeries prefers psu_total_power_watts...` test FAILs (old query still uses only the estimate); the four `electricityRateKwh`/`electricityCostSeries` tests FAIL with `api.electricityRateKwh is not a function` / `api.electricityCostSeries is not a function`.

- [ ] **Step 3: Fix `systemPowerSeries` and add the two new methods**

In `src/cofounder_agent/console/js/api.js`, find the exact current block:

```js
    systemPowerSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () => labelledRange('system_total_power_estimate_watts', o, 'total'),
        () => ({ series: [] })
      );
    },
```

Replace with:

```js
    systemPowerSeries(range) {
      const o = rangeOpts(range);
      return pick(
        () =>
          labelledRange(
            'psu_total_power_watts or system_total_power_estimate_watts',
            o,
            'total'
          ),
        () => ({ series: [] })
      );
    },
    // Live electricity rate ($/kWh), same settings-read pattern as
    // voiceJoinUrl(). Returns null (never a fabricated rate) when the
    // setting is missing or non-numeric.
    electricityRateKwh() {
      return pick(
        async () => {
          const r = await http(
            'GET',
            '/api/settings?search=electricity_rate_kwh&limit=10'
          );
          const hit = ((r && r.items) || []).find(
            (s) => s.key === 'electricity_rate_kwh'
          );
          const v = hit && Number(hit.value);
          return v && isFinite(v) && v > 0 ? v : null;
        },
        () => null
      );
    },
    // $/day time series, Shelly-first (same fallback as systemPowerSeries),
    // scaled by the live rate. Honest-empty when the rate is unavailable —
    // never assumes $0.
    electricityCostSeries(range) {
      const o = rangeOpts(range);
      return pick(
        async () => {
          const rate = await this.electricityRateKwh();
          if (!rate) return { series: [] };
          return labelledRange(
            `(psu_total_power_watts or system_total_power_estimate_watts) / 1000 * ${rate} * 24`,
            o,
            'total'
          );
        },
        () => ({ series: [] })
      );
    },
```

**Note:** `this.electricityRateKwh()` relies on `electricityCostSeries` being called as `PX.api.electricityCostSeries(...)` (a method call, not a detached function reference) so `this` binds correctly — the same assumption every other `PX.api.*` method already makes (they're all defined as object methods and always invoked via `api.methodName(...)` throughout this file and its tests).

- [ ] **Step 4: Wire the chart into `HardwarePanel`**

In `src/cofounder_agent/console/js/charts.jsx`, find the exact current array:

```js
function HardwarePanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'GPU utilization', fn: (x) => api.gpuUtilSeries(x), unit: '%' },
    { title: 'GPU temperature', fn: (x) => api.gpuTempSeries(x), unit: '°C' },
    { title: 'VRAM used', fn: (x) => api.vramUsedSeries(x), unit: ' GB' },
    { title: 'GPU power draw', fn: (x) => api.gpuPowerSeries(x), unit: ' W' },
    { title: 'System power', fn: (x) => api.systemPowerSeries(x), unit: ' W' },
  ];
```

Replace with:

```js
function HardwarePanel() {
  const api = window.PX.api;
  const charts = [
    { title: 'GPU utilization', fn: (x) => api.gpuUtilSeries(x), unit: '%' },
    { title: 'GPU temperature', fn: (x) => api.gpuTempSeries(x), unit: '°C' },
    { title: 'VRAM used', fn: (x) => api.vramUsedSeries(x), unit: ' GB' },
    { title: 'GPU power draw', fn: (x) => api.gpuPowerSeries(x), unit: ' W' },
    { title: 'System power', fn: (x) => api.systemPowerSeries(x), unit: ' W' },
    {
      title: 'Electricity cost',
      fn: (x) => api.electricityCostSeries(x),
      unit: '$/day',
    },
  ];
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test "src/cofounder_agent/console/js/__tests__/api.range.test.js"`
Expected: PASS, all tests including the 5 new/fixed ones.

- [ ] **Step 6: Add the contract-manifest row for `electricityRateKwh`**

`electricityCostSeries` needs no manifest row — it's a bare `promRange`-style call like `gpuUtilSeries` (Tier 1, request-only). `electricityRateKwh` transforms the response (Tier 3, like `voiceJoinUrl`), so it needs one.

In `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js`, find the `voiceJoinUrl` row:

```js
  {
    name: 'voiceJoinUrl',
    invoke: (api) => api.voiceJoinUrl(),
    request: {
      method: 'GET',
      path: '/api/settings',
      query: { search: 'voice_agent_public_join_url', limit: 10 },
    },
    shape: (out) =>
      assert.equal(typeof out, 'string', 'voiceJoinUrl returns a string'),
    openapi: { path: '/api/settings', method: 'get' },
  },
```

Add immediately after it:

```js
  {
    name: 'electricityRateKwh',
    invoke: (api) => api.electricityRateKwh(),
    request: {
      method: 'GET',
      path: '/api/settings',
      query: { search: 'electricity_rate_kwh', limit: 10 },
    },
    shape: (out) =>
      assert.ok(
        out === null || (typeof out === 'number' && out > 0),
        'electricityRateKwh returns a positive number or null'
      ),
    openapi: { path: '/api/settings', method: 'get' },
  },
```

(The `shape` check tolerates `null` deliberately — per the contracts README's "Honest limits," a recorded fixture may not contain the sought key, and the adapter's job is to degrade honestly, not to guarantee presence.)

- [ ] **Step 7: Run the contract-net test**

Run: `node --test "src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js"`
Expected: PASS if a `contracts/fixtures/electricityRateKwh.json` fixture exists; if this is the first run and no fixture exists yet, the runner will report a missing-fixture failure for this one row (other rows still pass) — see Step 8.

- [ ] **Step 8: Record the fixture (requires a reachable worker)**

If a worker is running and reachable (`http://localhost:8002` on Matt's dev machine, or `http://host.docker.internal:8002` from inside a container):

```bash
cd src/cofounder_agent/console/js/__tests__/contracts
node record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091
```

This re-records only rows whose schema drifted or is new (`electricityRateKwh` has never been recorded, so it will be captured). Re-run Step 7 to confirm the new fixture makes the row pass.

If no worker is reachable from this session, skip this step — it is not required to land this task. The nightly `console-contract-drift.yml` job (self-hosted runner, reaches the live worker) will record it automatically on its next run and open a `chore(console): refresh contract fixtures` PR, auto-merging if green. Note this explicitly when reporting the task as done.

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/charts.jsx src/cofounder_agent/console/js/__tests__/api.range.test.js src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js
# If Step 8 produced a new fixture file, add it too:
git add src/cofounder_agent/console/js/__tests__/contracts/fixtures/electricityRateKwh.json 2>/dev/null || true
git commit -m "$(cat <<'EOF'
feat(console): add Electricity cost Telemetry chart; fix System power source

systemPowerSeries still read only the software wattage estimate,
predating the Shelly plug (live 2026-07-11). Both it and the new
electricityCostSeries now prefer the real Shelly reading with the
estimate as fallback, matching the rest of the hardware-power chart
group's PromQL 'or' convention.

Refs glad-labs-stack#2626

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Backend — surface the electricity axis from `get_budget_status()`

**Files:**

- Modify: `src/cofounder_agent/services/cost_aggregation_service.py:515-527` (the `get_budget_status` return dict)
- Test: `src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py`

**Interfaces:**

- Consumes: `month: SpendBreakdown` (already computed at `cost_aggregation_service.py:431`, unmodified — has `.electricity_usd: float`, `.electricity_source: Literal["measured","estimated","mixed","none"]`, `.electricity_coverage_pct: float`, all already defined on `cost_ledger.SpendBreakdown`).
- Produces: `GET /api/metrics/costs/budget` response gains `electricity_usd: float`, `electricity_source: str`, `electricity_coverage_pct: float` — consumed by Task 6 (`app.jsx`'s live merge). `routes/metrics_routes.py` needs no change (`get_budget_status` at line 57-59 returns the service's dict verbatim).

- [ ] **Step 1: Write the failing test**

In `src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py`, first extend the shared `_patch_month_api` helper to accept the two new axes (backward-compatible — existing 11 call sites are unaffected since both new kwargs default to today's implicit values):

Find the exact current helper:

```python
def _patch_month_api(monkeypatch, api_usd, *, electricity_usd=0.0):
    """Patch the cost_ledger seam so get_budget_status sees a given month spend.

    Since get_budget_status now reads spend from cost_ledger.get_spend (not raw
    pool SQL), spend is injected here. amount_spent reflects the api axis.
    """
    async def _fake(pool, *, window="day", strict=False, site_config=None):
        return SpendBreakdown(
            api_usd=api_usd,
            electricity_usd=electricity_usd,
            total_usd=api_usd + electricity_usd,
            electricity_source="measured",
        )

    monkeypatch.setattr(cost_ledger, "get_spend", _fake)
```

Replace with:

```python
def _patch_month_api(
    monkeypatch,
    api_usd,
    *,
    electricity_usd=0.0,
    electricity_source="measured",
    electricity_coverage_pct=0.0,
):
    """Patch the cost_ledger seam so get_budget_status sees a given month spend.

    Since get_budget_status now reads spend from cost_ledger.get_spend (not raw
    pool SQL), spend is injected here. amount_spent reflects the api axis.
    """
    async def _fake(pool, *, window="day", strict=False, site_config=None):
        return SpendBreakdown(
            api_usd=api_usd,
            electricity_usd=electricity_usd,
            total_usd=api_usd + electricity_usd,
            electricity_source=electricity_source,
            electricity_coverage_pct=electricity_coverage_pct,
        )

    monkeypatch.setattr(cost_ledger, "get_spend", _fake)
```

Then add a new test in the `TestGetBudgetStatus` class (near `test_amount_spent_is_api_axis`):

```python
    @pytest.mark.asyncio
    async def test_electricity_axis_included(self, monkeypatch):
        _patch_month_api(
            monkeypatch,
            4.0,
            electricity_usd=27.94,
            electricity_source="measured",
            electricity_coverage_pct=98.3,
        )
        svc = _make_service(db=_make_db())
        result = await svc.get_budget_status(monthly_budget=150.0)
        assert result["electricity_usd"] == 27.94
        assert result["electricity_source"] == "measured"
        assert result["electricity_coverage_pct"] == 98.3
        # Still excluded from the LLM/API budget-cap figure:
        assert result["amount_spent"] == 4.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_aggregation_service.py::TestGetBudgetStatus::test_electricity_axis_included -v`
Expected: FAIL with `KeyError: 'electricity_usd'`

- [ ] **Step 3: Add the three keys to `get_budget_status`'s return dict**

In `src/cofounder_agent/services/cost_aggregation_service.py`, find the exact current return statement:

```python
            return {
                "monthly_budget": monthly_budget,
                "amount_spent": round(amount_spent, 2),
                "amount_remaining": round(amount_remaining, 2),
                "percent_used": round(percent_used, 2),
                "days_in_month": days_in_month,
                "days_remaining": days_remaining,
                "daily_burn_rate": round(daily_burn_rate, 4),
                "projected_final_cost": round(projected_final_cost, 2),
                "alerts": alerts,
                "status": status,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
```

Replace with:

```python
            return {
                "monthly_budget": monthly_budget,
                "amount_spent": round(amount_spent, 2),
                "amount_remaining": round(amount_remaining, 2),
                "percent_used": round(percent_used, 2),
                "days_in_month": days_in_month,
                "days_remaining": days_remaining,
                "daily_burn_rate": round(daily_burn_rate, 4),
                "projected_final_cost": round(projected_final_cost, 2),
                "alerts": alerts,
                "status": status,
                "electricity_usd": round(month.electricity_usd, 2),
                "electricity_source": month.electricity_source,
                "electricity_coverage_pct": round(month.electricity_coverage_pct, 1),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
```

(`month` is the `SpendBreakdown` already computed at line 431 — no new query.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_cost_aggregation_service.py -v`
Expected: PASS — the new test plus all 11 existing `_patch_month_api`-based tests (unaffected by the new optional kwargs).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/cost_aggregation_service.py src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py
git commit -m "$(cat <<'EOF'
feat(costs): surface the electricity axis from get_budget_status

month (the SpendBreakdown already computed for amount_spent) already
carries electricity_usd/electricity_source/electricity_coverage_pct
— they were computed and discarded. Three new keys on the existing
GET /api/metrics/costs/budget response, no new query, no route
change (the route already returns the service's dict verbatim).

Refs glad-labs-stack#2626

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Console — `electricitySourceNote()` helper

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (new pure method on `PX.api`)
- Test: `src/cofounder_agent/console/js/__tests__/api.range.test.js`

**Interfaces:**

- Produces: `PX.api.electricitySourceNote(source: string|null|undefined, coveragePct: number|null|undefined): string` — consumed by Task 6 (`panels.jsx::CostPanel`).

- [ ] **Step 1: Write the failing test**

Add to `src/cofounder_agent/console/js/__tests__/api.range.test.js`:

```js
test('electricitySourceNote maps each source to an operator-legible note', async () => {
  const api = loadApiWithFetch(
    async () => ({ ok: true, status: 200, json: async () => ({}) }),
    {},
    OPTS
  );
  assert.equal(
    api.electricitySourceNote('measured', 98.3),
    'measured, live wall power'
  );
  assert.equal(
    api.electricitySourceNote('estimated', 42),
    'estimated — 42% sensor coverage this window'
  );
  assert.equal(
    api.electricitySourceNote('mixed', 55.6),
    'estimated — 56% sensor coverage this window'
  );
  assert.equal(api.electricitySourceNote('none', 0), '— pending');
  assert.equal(api.electricitySourceNote(null, null), '— pending');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test "src/cofounder_agent/console/js/__tests__/api.range.test.js"`
Expected: FAIL with `api.electricitySourceNote is not a function`

- [ ] **Step 3: Implement the helper**

In `src/cofounder_agent/console/js/api.js`, immediately after the `electricityCostSeries` method added in Task 3, add:

```js
    // Maps cost_ledger's electricity_source (+ coverage) to an
    // operator-legible note for the Cost Control card's Energy row.
    electricitySourceNote(source, coveragePct) {
      if (source === 'measured') return 'measured, live wall power';
      if (source === 'estimated' || source === 'mixed') {
        const pct = coveragePct != null ? Math.round(coveragePct) : 0;
        return `estimated — ${pct}% sensor coverage this window`;
      }
      return '— pending';
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test "src/cofounder_agent/console/js/__tests__/api.range.test.js"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.range.test.js
git commit -m "$(cat <<'EOF'
feat(console): add electricitySourceNote helper

Small pure mapper from cost_ledger's electricity_source/coverage_pct
to an operator-legible note. Used by the Cost Control card's Energy
row (next task) instead of a synthetic "X kWh x $Y/kWh" line.

Refs glad-labs-stack#2626

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Console — wire the Cost Control card's Energy row

**Files:**

- Modify: `src/cofounder_agent/console/js/data.js:846-848` (mock cost object)
- Modify: `src/cofounder_agent/console/js/app.jsx:101-116` (live `costR` merge)
- Modify: `src/cofounder_agent/console/js/panels.jsx:618-652` (`CostPanel`)
- Modify: `src/cofounder_agent/console/js/drawer.jsx:999-1073` (Cost Control "Detail" drawer — has its own parallel `energyKwhMonth * electricityRate` computation, confirmed by grepping every reference to those two field names before writing this task)

**Interfaces:**

- Consumes: `PX.api.budget()` response gains `electricity_usd`/`electricity_source`/`electricity_coverage_pct` (Task 4); `PX.api.electricitySourceNote()` (Task 5).
- Produces: `cost.electricityUsdMonth: number|null`, `cost.electricitySource: string`, `cost.electricityCoveragePct: number` on the object every consumer reads (`app.jsx`'s `cost` variable, passed to both `CostPanel` and the drawer's `c`) — grepped across all of `console/js` for `energyKwhMonth`/`electricityRate` to confirm these two files are the complete consumer set; no other file references either name.

No automated test exists for `CostPanel`/`drawer.jsx`/`data.js`'s cost object today (verified — no `__tests__` file references any of them). Verification is the real-browser smoke in Task 7 (both the card AND its Detail drawer). This task is still one commit — it's a single coherent "the Energy row shows real data everywhere it's shown" change; splitting the four files further would leave visibly-broken intermediate states (e.g. `app.jsx` alone without the `panels.jsx`/`drawer.jsx` rewrites would pass `electricityUsdMonth` to components still reading the old `energyKwhMonth`/`electricityRate` keys, silently rendering "— pending" everywhere).

- [ ] **Step 1: Update the mock cost object**

In `src/cofounder_agent/console/js/data.js`, find the exact current block:

```js
    // Local energy: the real physical cost of running the box (cost_guard + EIA).
    energyKwhMonth: 9.5,
    electricityRate: 0.142, // $/kWh (EIA residential)
```

Replace with:

```js
    // Local energy: the real physical cost of running the box, from the
    // measured cost_logs electricity ledger (see cost_ledger.get_spend).
    electricityUsdMonth: 1.35, // ~= 9.5 kWh x $0.142/kWh, kept plausible
    electricitySource: 'measured',
    electricityCoveragePct: 98.3,
```

- [ ] **Step 2: Update the live merge in `app.jsx`**

In `src/cofounder_agent/console/js/app.jsx`, find the exact current block:

```js
return PX.api.budget().then((b) => {
  if (!b) throw new Error('budget: empty read');
  // Merge the live spend read onto the PX.cost base: static facts ($0
  // infra, energy rate, notes) come from the base; byModel/daily/energy
  // stay empty until those reads are routed (honest-empty, not mocked).
  return {
    ...PX.cost,
    monthToDate: b.amount_spent ?? PX.cost.monthToDate,
    budget: b.monthly_budget ?? PX.cost.budget,
    projected: b.projected_final_cost ?? PX.cost.projected,
    dailyBurn: b.daily_burn_rate ?? PX.cost.dailyBurn,
    percentUsed: b.percent_used ?? PX.cost.percentUsed,
    status: b.status ?? PX.cost.status,
    alerts: b.alerts ?? [],
    byModel: [],
    daily: [],
    energyKwhMonth: null,
  };
});
```

Replace with:

```js
return PX.api.budget().then((b) => {
  if (!b) throw new Error('budget: empty read');
  // Merge the live spend read onto the PX.cost base: static facts
  // ($0 infra, notes) come from the base; byModel/daily stay empty
  // until those reads are routed (honest-empty, not mocked).
  // electricity_* now rides the same budget() read (cost_aggregation_
  // service surfaces cost_ledger's measured ledger) — real, not
  // Prometheus-estimated.
  return {
    ...PX.cost,
    monthToDate: b.amount_spent ?? PX.cost.monthToDate,
    budget: b.monthly_budget ?? PX.cost.budget,
    projected: b.projected_final_cost ?? PX.cost.projected,
    dailyBurn: b.daily_burn_rate ?? PX.cost.dailyBurn,
    percentUsed: b.percent_used ?? PX.cost.percentUsed,
    status: b.status ?? PX.cost.status,
    alerts: b.alerts ?? [],
    byModel: [],
    daily: [],
    electricityUsdMonth: b.electricity_usd ?? null,
    electricitySource: b.electricity_source ?? 'none',
    electricityCoveragePct: b.electricity_coverage_pct ?? 0,
  };
});
```

- [ ] **Step 3: Rewrite `CostPanel`'s Energy row**

In `src/cofounder_agent/console/js/panels.jsx`, find the exact current block:

```js
function CostPanel({ cost, onOpen, fresh }) {
  const pct =
    cost.percentUsed != null
      ? cost.percentUsed
      : (cost.monthToDate / cost.budget) * 100;
  const warn = pct > 80 || cost.status === 'warning';
  const energyUsd =
    cost.energyKwhMonth != null
      ? cost.energyKwhMonth * cost.electricityRate
      : null;
  // The honest cost rows. Infra is $0 (self-hosted); the real levers are LLM/API
  // spend vs cap (the headline) + local energy. `energyKwhMonth === null` means
  // live mode with no backend read → explicit "backend read pending", never a
  // fabricated number (feedback_no_dummy_data).
  const rows = [
    ['Infra', '$0/mo', cost.infraNote],
    [
      'Energy',
      energyUsd != null ? `~$${energyUsd.toFixed(2)}/mo` : '— pending',
      energyUsd != null
        ? `${cost.energyKwhMonth} kWh × $${cost.electricityRate}/kWh`
        : 'cost_guard energy read not yet routed',
    ],
```

Replace with:

```js
function CostPanel({ cost, onOpen, fresh }) {
  const pct =
    cost.percentUsed != null
      ? cost.percentUsed
      : (cost.monthToDate / cost.budget) * 100;
  const warn = pct > 80 || cost.status === 'warning';
  const energyUsd = cost.electricityUsdMonth;
  // The honest cost rows. Infra is $0 (self-hosted); the real levers are LLM/API
  // spend vs cap (the headline) + measured electricity (cost_ledger's real
  // 5-min ledger, not an estimate). `electricityUsdMonth == null` means live
  // mode with no backend read → explicit "backend read pending", never a
  // fabricated number (feedback_no_dummy_data).
  const rows = [
    ['Infra', '$0/mo', cost.infraNote],
    [
      'Energy',
      energyUsd != null ? `$${energyUsd.toFixed(2)}/mo` : '— pending',
      energyUsd != null
        ? window.PX.api.electricitySourceNote(
            cost.electricitySource,
            cost.electricityCoveragePct
          )
        : 'backend read pending',
    ],
```

The rest of `CostPanel` (the `Daily burn`/`Agent API` rows and everything from the `return (` statement onward) is unchanged.

- [ ] **Step 4: Apply the same rename to the Cost Control "Detail" drawer**

`drawer.jsx` renders its own independent Energy row when the card's "Detail" action opens it — same formula, same dead fields. In `src/cofounder_agent/console/js/drawer.jsx`, find the exact current block:

```js
const energyUsd =
  c.energyKwhMonth != null ? c.energyKwhMonth * c.electricityRate : null;
```

Replace with:

```js
const energyUsd = c.electricityUsdMonth;
```

Then find the exact current block a little further down:

```js
                [
                  'Energy',
                  energyUsd != null
                    ? `~$${energyUsd.toFixed(2)}/mo · ${c.energyKwhMonth} kWh`
                    : '— pending',
                ],
```

Replace with:

```js
                [
                  'Energy',
                  energyUsd != null
                    ? `$${energyUsd.toFixed(2)}/mo · ${window.PX.api.electricitySourceNote(c.electricitySource, c.electricityCoveragePct)}`
                    : '— pending',
                ],
```

- [ ] **Step 5: Manual mock-mode check**

Serve the console (see Task 7's server command) and confirm — in mock mode (the default, no worker required):

- The Cost Control card's Energy row shows `$1.35/mo` and the note `measured, live wall power`, matching the new `data.js` seed values from Step 1.
- Clicking the card's "Detail" action opens the drawer, whose own "Infra & energy" section shows the same `$1.35/mo` figure with the same source note (not `9.5 kWh` / the old format).

This is a fast sanity check before the fuller Task 7 smoke pass.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/data.js src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/panels.jsx src/cofounder_agent/console/js/drawer.jsx
git commit -m "$(cat <<'EOF'
feat(console): wire the Cost Control card's Energy row to real data

The row (and its Detail-drawer twin) has shown "— pending" in live
mode since sub-project D (energyKwhMonth hardcoded null;
electricityRate was a stale mock constant live mode never
overrode). Both now read the electricity_usd/_source/_coverage_pct
fields get_budget_status() gained in the previous task, with
electricitySourceNote() replacing the old synthetic
"X kWh x $Y/kWh" text with the ledger's own honesty signal.

Refs glad-labs-stack#2626

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Full-suite verification + real-browser smoke

**Files:** none (verification only)

**Interfaces:** none — this task consumes everything produced by Tasks 1-6 and produces no new interface.

- [ ] **Step 1: Run the full Python unit suite**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/ -q`
Expected: PASS, 0 failures, 0 collection errors (same green baseline as before this plan, plus the new tests from Tasks 1 and 4).

- [ ] **Step 2: Run the full console test suite**

Run: `npm run test:console`
Expected: PASS, including every test added in Tasks 3 and 5, and the existing contract-net suite (`run-contracts.test.js`) — the `electricityRateKwh` row passes whether or not Task 3 Step 8 recorded a live fixture (the `shape` check tolerates the honest-empty `null` case either way; if literally no fixture file exists yet, `run-contracts.test.js` will report that row missing rather than passing — acceptable per Task 3 Step 8's note, and closed automatically by the nightly job).

- [ ] **Step 3: Serve the console and smoke-test in a real browser**

Start the console's static file server per `.claude/launch.json` (create it first if it doesn't exist, matching `docs/superpowers/specs/2026-07-07-console-hardware-power-design.md`'s precedent — a vendored no-cache `ThreadingHTTPServer`), then open it in the Browser tool.

Confirm, in **mock mode** (default on first load, no worker needed):

- The Telemetry tab's "GPU & POWER" group shows 6 charts (was 5) — "Electricity cost" renders last, unit `$/day`, honest "no data in range" (mock mode's `electricityCostSeries` returns `{series: []}`).
- The Cost Control card's Energy row shows `$1.35/mo` · "measured, live wall power" (the new `data.js` seed), and its Detail drawer shows the matching figure.

If a live worker is reachable, toggle **live mode** and confirm:

- "Electricity cost" renders a real trend line (or honest-empty if `electricity_rate_kwh` genuinely isn't set on that box).
- The Energy row (card and drawer) shows a real `$X.XX/mo` figure and a source note reflecting the live `electricity_source` value.

- [ ] **Step 4: Final review of the branch diff**

```bash
git status
git log --oneline main..HEAD
git diff main..HEAD --stat
```

Confirm the diff only touches the files listed in the File Structure table above (plus the spec/plan docs already committed earlier in this branch's history) — no stray changes.

- [ ] **Step 5: Push and confirm CI**

```bash
git push
gh pr view 2627 --json state,mergeable,statusCheckRollup
```

Once CI is green, mark PR #2627 ready for review (`gh pr ready 2627`) — per this repo's convention, a green CI on a routine fix/feature PR is the merge gate; merge it once green rather than waiting for further confirmation.
