# Electric costs — console tracking + two accuracy fixes (design)

**Status:** approved (brainstorm 2026-07-15)
**Depends on:** console sub-project D (`260d4d80d`.., PR #2196 — native GPU/hardware/power panels) and the wall-power-metering work (Shelly plug live 2026-07-11, `psu_total_power_watts` in Prometheus).

## Goal

Show electricity cost ($/day) on the operator console's Telemetry tab, wire
the console's existing Cost Control card to the same real numbers, and fix
two accuracy gaps in the electricity-cost machinery discovered while scoping
this: the Grafana panel that already shows a cost figure ignores the live
EIA-refreshed rate, and its wattage source is the reboot-fragile iCUE CSV tap
rather than the reboot-proof Shelly plug reading.

## Context & motivation

The console's Telemetry tab (`console/js/charts.jsx::HardwarePanel`) shows
GPU/system power in **watts** — a deliberate sub-project D scope decision
(`docs/superpowers/specs/2026-07-07-console-hardware-power-design.md` lists
"Electricity cost ($/EIA rate)" as a non-goal, staying Grafana-only). Digging
into the Grafana side to fold this into the console surfaced two live bugs:

1. **Grafana panel 11** ("Electricity Cost (EIA rate)" on the Hardware & Power
   board) hardcodes `0.14` in its SQL, despite `UpdateUtilityRatesJob`
   fetching the real EIA residential rate daily into
   `app_settings.electricity_rate_kwh`. The panel titled "EIA rate" has never
   actually read the EIA-sourced value.
2. That same panel's wattage source is `sensor_samples_unified` /
   `corsair_csv.psu_power_in` — the iCUE CSV tap, which per
   [[project_wall_power_metering]] is reboot-fragile (iCUE's CSV logging is a
   manual toggle that does not auto-resume after a reboot/iCUE restart) and
   was explicitly demoted to best-effort once the Shelly plug
   (`psu_total_power_watts`, Prometheus, reboot-proof) became the source of
   truth for wall power on 2026-07-11 — four days _after_ sub-project D
   shipped its watts-only console chart still pointed at the old
   `system_total_power_estimate_watts` software estimate.
3. **Gap found while fixing #1:** `electricity_rate_kwh` has no seeded
   default in `settings_defaults.py`. `UpdateUtilityRatesJob` only writes it
   after its first successful EIA call — up to 24h after boot, or never on a
   box where the shared `DEMO_KEY` gets rate-limited. Every other tunable in
   this codebase gets a bootstrap default per the documented convention
   ("New app_settings keys belong in settings_defaults.py"); this one was
   missed, likely because the job's predecessor (`IdleWorker.
_update_utility_rates`) assumed it would always run before anyone read
   the value.

Matt confirmed (via the two-fix scoping question) that both the console
addition and a full repoint of the Grafana panel onto Shelly are in scope —
not just the minimal hardcoded-literal fix.

**Extended after spec approval (same day):** Matt asked to also wire the
console's existing **Cost Control card** (`console/js/panels.jsx::CostPanel`,
the "COST CONTROL" panel on the main dashboard). That card already has a
designed-but-dead "Energy" row — `energyKwhMonth * electricityRate` — that
has shown `— pending` in live mode since sub-project D (`app.jsx` hardcodes
`energyKwhMonth: null` on the live path, comment: "energy stays empty until
those reads are routed"). `electricityRate` is also currently a stale static
mock value (`data.js`: `0.142`) that live mode never overrides.

**Course-corrected after Matt's follow-up ("cost log should be where we are
retaining costs right?"):** he was right to check, and the answer changed
the design. `cost_logs` already has a fully-built, currently-live electricity
ledger I'd missed: `brain/brain_daemon.py::log_electricity_cost` inserts a
row every 5 minutes (`cost_type='electricity_active'/'electricity_idle'`,
real `cost_usd`), computed from the _exact same_ `select_power_source()`
priority chain (Shelly → iCUE → estimate → floor) and the live
`electricity_rate_kwh` setting — same inputs I was about to re-derive from
Prometheus. `services/cost_ledger.py::get_spend()` already splits this into
a clean `api_usd` / `electricity_usd` axis, consumed today by the MCP
`get_budget` tool (`get_spend_totals()`) but **not yet by the console's HTTP
route**. Verified live (2026-07-15): 25,939 electricity rows since
**2026-04-11**, ~282 in the last 24h (a clean 5-min cadence), **$27.94**
month-to-date, **$1.20** today. This is a real 3-month measured ledger, not
a 4-day-old Prometheus feed — it supersedes the Prometheus-extrapolation
approach originally planned for both the Grafana panel (piece 1) and the
Cost Control card (piece 5, below), which are now redesigned around it. The
Telemetry chart (piece 4) is unaffected — see its updated note on why.

## Non-goals (YAGNI)

- **Building a new accumulation/integration job.** Superseded by the
  discovery above — `brain_daemon.py::log_electricity_cost` already _is_
  that job, has been since 2026-04-11. Pieces 1 and 5 read its real ledger
  (`cost_logs`) rather than a client-side extrapolation. Only piece 4 (the
  Telemetry chart) still uses an instantaneous Prometheus extrapolation
  ("at the current draw, a day would cost $X") — deliberately, as a live
  monitoring gauge, not an accounting figure; see piece 4's note.
- **Changing what `log_electricity_cost` measures or how often it runs.**
  Its 5-minute cadence, source-priority chain, and idle/active split are
  all working and unrelated to this spec — only its _consumers_ change
  (a Grafana query and a new HTTP field), never the writer.
- **GPU TDP / `gpu_power_watts` fixes**, per-core RAPL, PSU rail breakdowns,
  fan/voltage panels — untouched, stay Grafana-only per sub-project D's
  original scope.
- **Historical backfill** of the corrected Grafana panel — the fix changes
  the query going forward; no attempt to reconstruct what the true cost
  _was_ under the old broken query.

## Architecture

A Grafana fix, a settings gap-fill, and three console changes — all reusing
existing patterns, no new services, no new HTTP routes, no new UI
components.

### 1. Grafana panel fix (`infrastructure/grafana/dashboards/hardware-power.json`)

Revised: point panel 11 at the real `cost_logs` ledger instead of
recomputing from raw wattage. Datasource **stays** `grafana-postgresql-
datasource` (`local-brain-db`) — no template variable, no datasource
switch needed, since the ledger already bakes in the correct rate and
source priority at write time. Just the SQL changes:

```sql
SELECT
  created_at AS time,
  (cost_usd / duration_ms * 3600000.0 * 24)::float AS "Est. Daily Cost"
FROM cost_logs
WHERE cost_type LIKE 'electricity%' AND $__timeFilter(created_at)
ORDER BY created_at
```

(`cost_usd / duration_ms` gives $/ms for that 5-minute interval;
`* 3600000.0 * 24` converts to an equivalent $/day rate, same "Est. Daily
Cost" framing the panel already has — just sourced from what was actually
measured/logged for that interval instead of a fresh recompute against a
possibly-stale wattage reading.)

This single change fixes both original bugs at once: the rate is whatever
`log_electricity_cost` actually used that cycle (the live
`electricity_rate_kwh` setting, not a hardcoded `0.14`), and the wattage
behind it already went through the Shelly-first `select_power_source()`
chain (not the iCUE-only `psu_power_in` this panel used to read). Title,
`axisLabel: "$/day"`, `unit: currencyUSD`, and panel type (`timeseries`)
are unchanged.

### 2. Gap-fill — seed `electricity_rate_kwh` (`src/cofounder_agent/services/settings_defaults.py`)

Add `"electricity_rate_kwh": "0.16"` (approximate recent U.S. national
average residential rate — a bootstrap constant, documented in-line as such,
not a live data point). Applied idempotently via the existing
`seed_all_defaults` → `INSERT ... ON CONFLICT (key) DO NOTHING` path, so it
only fills the gap on installs where the key is still unset — it will
**never** override Matt's current live value once `UpdateUtilityRatesJob`
has written one. Self-corrects to the real EIA rate within a day on any box
with network access.

### 3. Console fix — `systemPowerSeries` (`src/cofounder_agent/console/js/api.js`)

One-line query change, same fallback semantics as the Grafana fix:

```js
// before
() => labelledRange('system_total_power_estimate_watts', o, 'total'),
// after
() => labelledRange('psu_total_power_watts or system_total_power_estimate_watts', o, 'total'),
```

### 4. Console addition — "Electricity cost" chart

`src/cofounder_agent/console/js/api.js` — two new `PX.api` methods, placed
next to the existing power methods:

```js
electricityRateKwh() {
  // mirrors voiceJoinUrl()'s settings-read pattern exactly
  return pick(
    async () => {
      const r = await http('GET', '/api/settings?search=electricity_rate_kwh&limit=10');
      const hit = ((r && r.items) || []).find((s) => s.key === 'electricity_rate_kwh');
      const v = hit && Number(hit.value);
      return v && isFinite(v) && v > 0 ? v : null;
    },
    () => null // mock: no rate known (honest-empty, matches voiceJoinUrl's mock convention)
  );
},
electricityCostSeries(range) {
  const o = rangeOpts(range);
  return pick(
    async () => {
      const rate = await api.electricityRateKwh();
      if (!rate) return { series: [] }; // honest-empty — never assume $0
      return labelledRange(
        `(psu_total_power_watts or system_total_power_estimate_watts) / 1000 * ${rate} * 24`,
        o,
        'total'
      );
    },
    () => ({ series: [] }) // mock: matches every other hardware chart's honest-empty convention
  );
},
```

`src/cofounder_agent/console/js/charts.jsx` — one new entry in
`HardwarePanel`'s chart array, after `System power`:

```js
{ title: 'Electricity cost', fn: (x) => api.electricityCostSeries(x), unit: '$/day' },
```

No new components — reuses `TrendGroup`/`HistoryChart` verbatim, same as
every other chart in this group.

**Why this stays Prometheus-based rather than switching to `cost_logs`
(unlike pieces 1 and 5):** this chart lives in `HardwarePanel` alongside
five other live Prometheus gauge charts (GPU util/temp/VRAM/power, system
power) — it's a monitoring surface ("what's the rate right now"), refreshed
every 30s at Prometheus's native resolution. `cost_logs` is the accounting
ledger ("what did we actually spend"), on a 5-minute cadence, surfaced on
the Cost Control card instead. Same underlying inputs, two different
consumers with different jobs.

### 5. Console addition — wire the Cost Control card's "Energy" row

Revised: read the real ledger instead of estimating from Prometheus.
`CostAggregationService.get_budget_status()` (`services/cost_aggregation_service.py`)
already computes `month = await cost_ledger.get_spend(pool, window="month")`
for the `amount_spent` (api-axis) figure — `month.electricity_usd` /
`.electricity_source` / `.electricity_coverage_pct` are sitting right
there, unused. No new query.

**`services/cost_aggregation_service.py`** — add three keys to
`get_budget_status()`'s returned dict, reading fields already on `month`:

```python
"electricity_usd": round(month.electricity_usd, 2),
"electricity_source": month.electricity_source,  # "measured" | "estimated" | "mixed" | "none"
"electricity_coverage_pct": month.electricity_coverage_pct,
```

`routes/metrics_routes.py`'s `GET /api/metrics/costs/budget` handler
returns this dict verbatim, so no route-file change is needed — the new
fields ride along automatically.

**`console/js/api.js`** — `budget()`'s mock branch gains matching fields
(derived from `data.js`'s existing mock cost object) so mock and live
render the same shape.

**`console/js/app.jsx`** — in the live-mode `costR` merge (the block that
currently hardcodes `energyKwhMonth: null`), read `b.electricity_usd` /
`b.electricity_source` directly — no separate Prometheus call, no
client-side math. Remove the stale "energy stays empty until routed"
comment.

**`console/js/panels.jsx`** — `CostPanel`'s Energy row changes shape: it
already has the real dollar figure, so it stops multiplying two
client-held numbers together (which also removes the `NaN`-on-partial-read
risk from the previous revision of this piece — there's only one field
now, not two):

```js
// before
const energyUsd =
  cost.energyKwhMonth != null
    ? cost.energyKwhMonth * cost.electricityRate
    : null;
// rows: ['Energy', energyUsd != null ? `~$${energyUsd.toFixed(2)}/mo` : '— pending',
//        energyUsd != null ? `${cost.energyKwhMonth} kWh × $${cost.electricityRate}/kWh` : 'cost_guard energy read not yet routed']

// after
const energyUsd = cost.electricityUsdMonth;
// rows: ['Energy', energyUsd != null ? `$${energyUsd.toFixed(2)}/mo` : '— pending',
//        energyUsd != null ? sourceNote(cost.electricitySource) : 'backend read pending']
```

`sourceNote()` is a tiny new helper mapping `electricity_source` (+
`electricity_coverage_pct`, so it's actually used rather than fetched and
discarded) to an operator-legible note: "measured" → "measured, live wall
power", "estimated"/"mixed" → `` `estimated — ${coverage.toFixed(0)}%
sensor coverage this window` ``, "none" → "— pending". Replaces the old
synthetic "X kWh × $Y/kWh" text with the ledger's own honesty signal, which
didn't exist in the original design.

Mock mode: `data.js`'s static cost object renames `energyKwhMonth: 9.5` /
`electricityRate: 0.142` to a single `electricityUsdMonth: 1.35` (≈ 9.5 ×
0.142, kept plausible) + `electricitySource: 'measured'`, continuing to
drive the card exactly as today, just under the new field names.

## Data flow

Two independent flows, sharing the same underlying wattage/rate inputs but
serving different consumers:

```
Live monitoring (pieces 3, 4 — console HardwarePanel):

Shelly plug -> nvidia-smi-exporter.py /metrics -> Prometheus psu_total_power_watts
                                                          |
                                                          v
                                        console promRange (instantaneous, /30s)
                                                          |
                                                          x  electricity_rate_kwh (console-fetched)

Accounting ledger (pieces 1, 5 — Grafana panel + Cost Control card):

select_power_source() [Shelly -> iCUE -> estimate -> floor]
                       |
                       v
brain_daemon.py::log_electricity_cost  (every 5 min, live since 2026-04-11)
                       |  x electricity_rate_kwh (brain-read, own 0.29 fallback)
                       v
                 cost_logs (cost_type LIKE 'electricity%', real cost_usd per row)
                       |
              +--------+--------+
              v                 v
   Grafana SQL panel     cost_ledger.get_spend()
   (piece 1)             -> CostAggregationService.get_budget_status()
                          -> GET /api/metrics/costs/budget (piece 5)

Both flows read the same app_settings.electricity_rate_kwh, kept fresh by
UpdateUtilityRatesJob (daily, EIA) / bootstrapped by settings_defaults.py.
```

## Error handling

- **No Shelly** (consumer rig without the plug) — both flows already
  degrade gracefully through the same chain: pieces 3/4's PromQL `or` falls
  to `system_total_power_estimate_watts`; pieces 1/5's `select_power_source()`
  (used by the brain writer) falls the same way. Never blank, never a
  fabricated Shelly reading.
- **Rate missing or malformed (console fetch, pieces 3/4)** — chart renders
  honest "no data in range," never a silently-assumed $0/day line.
- **Rate read failure inside the brain daemon (pieces 1/5)** — already
  handled today: `log_electricity_cost` logs a warning and uses its own
  compiled `$0.29/kWh` fallback for that cycle only (unchanged by this
  spec). The gap-fill (piece 2) makes the _real_ `app_settings` value
  available from boot on fresh installs, so this fallback is rarely hit.
- **EIA fetch failures** (network down, `DEMO_KEY` rate-limited) —
  `UpdateUtilityRatesJob` already logs a warning and no-ops (unchanged
  behavior); the seeded default keeps every consumer showing a reasonable
  rate in the meantime instead of going dark.
- **Brain daemon down for an extended window (piece 5)** — `cost_logs`
  stops gaining new `electricity_active`/`electricity_idle` rows, so
  `get_spend()`'s measured-coverage check drops below threshold and
  `electricity_source` flips to `"estimated"`. It doesn't go to zero:
  every local LLM call already writes a `electricity_kwh` estimate
  (`CostGuard.estimate_local_kwh`, `dispatcher.py`) as a genuine fallback
  axis — degraded accuracy, not a missing number. `sourceNote()` (piece 5)
  surfaces the degraded state to the operator instead of hiding it.

## Testing

- `console/js/__tests__/api.range.test.js` — new tests for
  `electricityRateKwh` (settings-search shape; missing/non-numeric key →
  `null`) and `electricityCostSeries` (interpolates the fetched rate into
  the expected PromQL string; honest-empty when the rate is unavailable);
  one test for `systemPowerSeries`'s corrected fallback query string.
- `console/js/__tests__/contracts/contracts.manifest.js` — one settings-GET
  row for `electricityRateKwh` (mirroring `voiceJoinUrl`'s existing fixture
  in `contracts/fixtures/`), one `rangeQuery` row for `electricityCostSeries`.
- `console/js/__tests__/kpis.test.js` / existing `CostPanel` coverage —
  extend for the simplified Energy row: `electricityUsdMonth` present →
  renders `$X.XX/mo` + the right `sourceNote()` per `electricitySource`
  value; missing → "— pending".
- Real-browser smoke (per sub-project D precedent) — serve the console,
  confirm "Electricity cost" renders as the 6th chart in the GPU & POWER
  group, mock mode shows honest "no data in range," and the Cost Control
  card's Energy row shows a real figure in live mode instead of "— pending."
- `src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py`
  — extend for `get_budget_status()`'s three new keys, reusing whatever
  `SpendBreakdown` fixture/mock the existing tests already build.
- `src/cofounder_agent/tests/unit/services/test_settings_defaults.py` —
  extend/confirm coverage for the new `electricity_rate_kwh` default.
- `src/cofounder_agent/tests/unit/services/jobs/test_update_utility_rates_job.py`
  — confirm the existing upsert-based test still passes unchanged (the job
  itself isn't modified, only what it's seeded against).
- Grafana dashboard JSON has no existing automated test coverage (verified —
  no test references `hardware-power.json`); panel 11 is verified manually
  via a live Grafana reload after deploy. Sanity-check query (already run
  once for this spec, via the Postgres MCP): `SELECT SUM(cost_usd) FROM
cost_logs WHERE cost_type LIKE 'electricity%' AND created_at >=
date_trunc('month', NOW())` should roughly match the panel's visible
  trend and the Cost Control card's new Energy row.

## Rollout

- Console is static — live on the next deploy-checkout `git pull`, no
  restart.
- Grafana dashboard JSON is file-provisioned — auto-reloads on the next
  deploy-checkout pull, same as every other dashboard edit.
- `settings_defaults.py` seed applies on next worker boot via the existing
  `StartupManager._run_migrations()` → `seed_all_defaults(pool)` path.

## File-change summary

| File                                                                                       | Change                                                                                                                                              |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `infrastructure/grafana/dashboards/hardware-power.json`                                    | Panel 11: repoint SQL from `sensor_samples_unified`/`corsair_csv` to `cost_logs` (`cost_type LIKE 'electricity%'`); datasource unchanged (Postgres) |
| `src/cofounder_agent/services/settings_defaults.py`                                        | Seed `electricity_rate_kwh` default (`"0.16"`)                                                                                                      |
| `src/cofounder_agent/services/cost_aggregation_service.py`                                 | `get_budget_status()`: add `electricity_usd` / `electricity_source` / `electricity_coverage_pct` from the already-computed `month` object           |
| `src/cofounder_agent/console/js/api.js`                                                    | Fix `systemPowerSeries` fallback query; add `electricityRateKwh()`, `electricityCostSeries()`; `budget()` mock gains matching electricity fields    |
| `src/cofounder_agent/console/js/charts.jsx`                                                | Add "Electricity cost" entry to `HardwarePanel`'s chart array                                                                                       |
| `src/cofounder_agent/console/js/app.jsx`                                                   | Live `costR` merge reads `b.electricity_usd`/`b.electricity_source` directly; drop the stale "not yet routed" comment                               |
| `src/cofounder_agent/console/js/panels.jsx`                                                | `CostPanel`: Energy row reads `electricityUsdMonth` directly + new `sourceNote()` helper, replacing the kWh×rate math                               |
| `src/cofounder_agent/console/js/data.js`                                                   | Mock cost object: `energyKwhMonth`/`electricityRate` → `electricityUsdMonth`/`electricitySource`                                                    |
| `src/cofounder_agent/console/js/__tests__/api.range.test.js`                               | Tests for `electricityRateKwh`/`electricityCostSeries` + the `systemPowerSeries` fallback                                                           |
| `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js`                 | +2 rows (settings GET, cost rangeQuery)                                                                                                             |
| `src/cofounder_agent/console/js/__tests__/contracts/fixtures/`                             | New fixture for the electricity-rate settings response                                                                                              |
| `src/cofounder_agent/console/js/__tests__/kpis.test.js` (or existing `CostPanel` coverage) | Cover the simplified Energy row + `sourceNote()`                                                                                                    |
| `src/cofounder_agent/tests/unit/services/test_cost_aggregation_service.py`                 | Cover the three new `get_budget_status()` keys                                                                                                      |
| `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`                        | Cover the new default                                                                                                                               |

## Risks & watch-outs

- **`0.16` is an approximate bootstrap constant**, not sourced from a live
  EIA call at write time — acceptable because it self-corrects within a day
  on any box with network access, and is clearly commented as a fallback
  rather than presented as live data. Lower-stakes than originally scoped:
  Matt's own instance already carries a real value (`$0.2579/kWh`, last
  refreshed 2026-04-30) — this only protects fresh/consumer installs.
- **Consumer rigs without a Shelly plug** will show cost figures derived
  from the software wattage estimate (both flows degrade to it) — same
  accuracy ceiling the estimate already has elsewhere; not a new
  limitation.
- **Piece 1's Grafana query assumes `duration_ms` is always positive** (it
  is — the brain writer always sets it to the cycle length in ms — but
  worth a defensive `NULLIF`/`GREATEST` in the actual SQL rather than
  trusting the invariant forever).
- **No longer a risk, noted for contrast with the originally-approved
  design:** the ledger this now reads (`cost_logs`, since 2026-04-11, ~26k
  rows) has three months of real history, not the Shelly Prometheus feed's
  four days — pieces 1 and 5 are on more solid ground than they would have
  been under the first revision of this spec.
