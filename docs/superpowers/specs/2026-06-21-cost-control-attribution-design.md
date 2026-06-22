# Honest cost attribution + tiered cost controls (gate / throttle / anomaly)

**Date:** 2026-06-21
**Status:** Design approved, pending spec review
**Lineage:** Started from an operator question — "how are we using cost controls?"
— that turned into a live-data audit. The audit found the cost-control _code_
exists but is (a) vacuous (the dollar gate has never fired — `0`
`CostGuardExhausted` rows in 30 days, `$0` genuinely-billable cloud spend) and
(b) running on a polluted meter (local models mispriced against hosted prices —
`llama3.2:3b`, the _free_ tier, logged `$5.60` of phantom "API cost" on
2026-06-21 and tripped `DailySpendOverBudget` on money that was never spent).
This spec fixes the meter, then layers the controls the operator actually
asked for.

## Problem

Every cost consumer reads `cost_logs.cost_usd`, but that column means three
different things depending on the row, and nothing separates them:

1. **Real cloud API spend** (USD billed by a vendor) — currently `$0`; becomes
   real only when a paid model is enabled.
2. **Electricity** (USD-equivalent of locally-burned kWh) — the only real
   recurring cost today (~`$34`/mo at `electricity_rate_kwh=0.2579`), written by
   the brain daemon sampling the PSU (`brain/brain_daemon.py:2221`, `cost_type`
   `electricity_active` / `electricity_idle`).
3. **Phantom** — local inference rows mis-priced because a bare Ollama tag
   (`llama3.2:3b`) collides with a _hosted_ Llama price in `litellm.model_cost`.
   Not a real cost at all.

Live reconciliation for the current month made the mess concrete:

| Number                                 | Value      | Meaning                               |
| -------------------------------------- | ---------- | ------------------------------------- |
| `get_budget` (operator-facing, phone)  | **$42.58** | blended — what the operator sees      |
| `what_the_cap_counts` (cost_guard sum) | **$8.30**  | what the dollar gate enforces against |
| `electricity`                          | **$34.25** | real power bill (uncapped)            |
| `real_cloud_spend`                     | **$0.00**  | money actually owed a vendor          |

So the number on the phone (`$42.58`) is dominated by electricity + phantom and
bears no relationship to either the `$10` monthly cap (which only the
cost_guard-filtered `$8.30` is measured against) or to real money owed (`$0`).

### Two structural faults beneath the numbers

- **Recording vs enforcement disagree on local-vs-paid.** The recording side
  tags phantom local costs `provider='litellm'` (not in the cap's exclusion
  list `('electricity','ollama','ollama_native')`), so they _count toward the
  cap_. The enforcement side (`_is_paid_llm_call`) correctly treats a bare local
  tag as free, so `_enforce_budget_if_paid` _skips the gate_. Result: on
  2026-06-21, `$6.22` of phantom spend counted against a `$2` daily cap (3.1×)
  and fired a false `DailySpendOverBudget`, while 1,997 calls sailed through
  un-gated. The dispatcher comment at `dispatcher.py:458` claims "the cost log
  and the gate must agree on local-vs-paid" — they didn't.
- **Electricity is double-counted.** The brain measures real wall power
  continuously (the bill), AND every local inference row _also_ estimates its
  own GPU-power-×-duration and stuffs it into `cost_usd` (the dispatcher's
  post-2026-06-21 phantom patch sets local `cost_usd = kwh_to_usd(estimate)`).
  Those are the same watts counted twice.

### Three competing half-implementations

| Surface                                          | What it does                                                                                                                                                                | Fault                                                                      |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `cost_guard.py`                                  | daily/monthly cap, sums `cost_logs` excluding `('electricity','ollama','ollama_native')`                                                                                    | gates only "paid"-classified calls; never fires; polluted input            |
| `cost_aggregation_service.py::get_budget_status` | its _own_ budget logic, hardcoded `$150`, summing the blended total; consumed by the **anthropic plugin** (`anthropic.py:571`) + metrics dashboard (`metrics_routes.py:53`) | second, disagreeing budget system on a dirty meter                         |
| `detect_anomalies.py`                            | z-score on `cost_per_day = SUM(cost_usd)`                                                                                                                                   | sums the polluted blend; can't tell an electricity spike from an API spike |

"None of it works" really means: the right pieces exist but **disagree with each
other and run on a dirty meter.**

## Decisions (operator-approved 2026-06-21)

Four forks were put to the operator during brainstorming:

1. **Anchor → "Clean attribution first."** Everything else is unreliable until
   the ledger is honest.
2. **Scope → "Full arc, one design doc."** Design attribution + all three
   controls together; implement in phases.
3. **Enforcement → "Tiered, differs by cost type."** Alert → throttle →
   hard-stop, where **API** can hard-refuse but **electricity/total** only ever
   alerts-then-throttles (you can't refuse electricity mid-inference without
   throwing away in-flight content).
4. **Approach → "Unified ledger (#1)."** Write-time taxonomy + one `cost_ledger`
   read API + history backfill; every consumer routes through it. (Read-time SQL
   view and per-consumer point-fixes were both rejected — see Approaches.)

Two refinements the operator added on the data model:

- Wall power stays authoritative, **but** because the HX1500i PSU sampling has
  been flaky, electricity falls back to per-call estimates for windows the
  measured feed didn't cover (flagged, never double-counted).
- Per-call electricity estimates are **retained as a first-class "local vs
  cloud" view** — counterfactual cloud-$ saved and local-vs-cloud Wh — not just
  attribution. (Phase 5, future.)

## Non-goals

- **Not** enabling any paid cloud model. `allow_paid_base_url` stays `false`;
  this spec makes the gate _honest and tested_, not _active_.
- **Not** changing the GPU-VRAM serialization control
  (`gpu_serialize_llm_dispatch`) — that's a concurrency control, orthogonal to
  dollar/energy cost.
- **Not** a new Prometheus _alert rule_ for electricity (anomaly findings
  already route to Telegram/Discord; revisit only if dashboard + finding prove
  insufficient).
- **Not** graduated/proportional throttling in v1 — the throttle pauses _new_
  task pickup (binary, with hysteresis); proportional slowdown is a noted
  follow-up.
- **No** rename of `cost_type` values or the existing `daily_spend_limit_usd` /
  `monthly_spend_limit_usd` keys (backcompat per `feedback_backcompat_now_required`).

## Approaches considered

- **#1 — Unified ledger (CHOSEN).** Write-time `cost_usd` invariant (local
  inference → `$0`), one `cost_ledger.get_spend()` read seam, history backfill.
  Most call-site churn, but that churn _is_ the fix: it ends the disagreement
  structurally and gives the controls clean per-axis numbers. Aligns with
  "service layer is the contract" and `feedback_calculated_vs_generated`.
- **#2 — Read-time SQL view.** A `v_cost_ledger` classifies on read; writers
  mostly untouched. Auto-covers history, least writer risk — rejected because
  the meter stays dirty underneath, the gate's hot-path sum inherits brittle
  in-SQL locality heuristics (vs the Python `_is_paid_llm_call`), and phantom
  keeps being written.
- **#3 — Point-fixes per consumer.** Patch each query independently. Fastest,
  smallest — rejected because it recreates the original sin (N hand-rolled cost
  queries that drift apart again).

## Detailed design

### 1. The `cost_usd` write invariant (the phantom kill)

`cost_type` stays the axis label. We enforce what `cost_usd` is _allowed to
mean_ per row:

- **any local row** (Ollama inference, ffmpeg compositor — any non-cloud,
  non-electricity row) → `cost_usd` = **API dollars only**, i.e. `0`. Only a
  genuinely-paid cloud call carries a real `cost_usd`.
- **`electricity_kwh` column** → **attribution only**, never summed into a
  dollar total.
- **electricity row** (`cost_type LIKE 'electricity%'`) → `cost_usd` = the
  measured kWh × rate; this is the bill.

The primary write-path change is in `dispatcher.py::_record_dispatch_cost`: for a
local call (`not _is_paid_llm_call(...)`), write `cost_usd = 0.0` (today it
writes `kwh_to_usd(estimate_local_kwh(...))`, which conflates electricity onto
the inference row). Keep populating `electricity_kwh` for attribution. The
compositor writer (`provider='compositor.ffmpeg_local'`) is local too and must
likewise write `cost_usd=0` — its electricity is the brain's to measure — and
the backfill zeroes its historical rows. This is what makes the
`NOT LIKE 'electricity%'` api-axis query equal "real cloud only" without any
in-SQL locality heuristic. This makes
the API axis honest by construction — a local row contributes `0` to any API
sum — and removes the double-count (electricity now comes _only_ from brain
rows).

A **contract test asserts a local call writes `cost_usd=0`** — the durable
canary against phantom regression. If a future change re-prices local calls, CI
goes red.

### 2. `cost_ledger.py` — the single read seam

```python
# services/cost_ledger.py  (substrate; content/finance-agnostic)
from dataclasses import dataclass
from typing import Any, Literal

Window = Literal["day", "month"] | tuple  # ("day"|"month") or (start, end)

@dataclass
class SpendBreakdown:
    api_usd: float            # real cloud spend (local rows are $0 by invariant)
    electricity_usd: float    # measured-primary, estimate-fallback
    total_usd: float          # api + electricity
    electricity_source: Literal["measured", "estimated", "mixed", "none"]
    electricity_coverage_pct: float
    by_type: dict[str, float]

async def get_spend(pool: Any, *, window: Window = "day",
                    strict: bool = False) -> SpendBreakdown: ...
```

Axis SQL (window predicate elided):

```sql
-- api_usd: everything non-electricity. Local rows are $0 by the write invariant,
-- so only genuinely-paid cloud calls contribute. No in-SQL locality heuristic.
SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs
WHERE COALESCE(cost_type, 'inference') NOT LIKE 'electricity%'  -- <window>

-- electricity_usd (measured): the brain's PSU rows = the bill
SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs
WHERE cost_type LIKE 'electricity%'  -- <window>
```

`strict=True` re-raises on DB error (for the fail-closed gate); default swallows
to a zero-ish breakdown (for fail-open callers). Every consumer below calls
`get_spend`; none re-implements a `SUM`.

### 3. Electricity: measured-primary, estimate-fallback

`electricity_usd` prefers the brain's measured rows. **Coverage** = fraction of
the window covered by measured samples, where each sample "covers" up to
`electricity_source_gap_minutes` after it.

- coverage ≥ `electricity_measured_min_coverage_pct` → `measured`.
- coverage below threshold → fall back to the per-call estimate for the window
  (`SUM(electricity_kwh) × electricity_rate_kwh`), `source="estimated"`.
- partial (a refinement noted for the plan): fill only the _uncovered_ sub-
  windows with estimates → `source="mixed"`.

Measured and estimate are **mutually exclusive per time span** — the estimate
only ever fills time the measured feed did _not_ cover — so the double-count
cannot recur. `get_spend` returns `electricity_source` + `electricity_coverage_pct`
so a flaky-PSU day reads "estimated, 60% coverage" instead of silently wrong.

### 4. Backfill migration `YYYYMMDD_HHMMSS_zero_local_inference_cost_usd.py`

A one-time **data** mutation (allowed in a migration; this is not a settings
seed, so it does not violate `feedback_seed_data_in_baseline_not_new_migrations`).
`up()`:

```sql
-- Zero phantom + per-call-electricity dollars on historical LOCAL inference rows.
-- Preserve electricity_kwh / tokens / model (Phase 5 savings view needs them).
-- Precise: only inference rows on a local provider; never electricity, never
-- genuinely-paid cloud rows.
UPDATE cost_logs
   SET cost_usd = 0
 WHERE COALESCE(cost_type, 'inference') NOT LIKE 'electricity%'
   AND cost_usd > 0
   AND provider NOT IN ('anthropic', 'openai', 'gemini', 'openrouter')
   AND (provider IN ('ollama', 'ollama_native', 'litellm')
        OR model !~ '^(anthropic|openai|gemini|openrouter)/');
```

Idempotent (re-run zeroes nothing new), bounded, dry-run `SELECT COUNT(*)` first.
`down()` — documented no-op (forward-only; reverting would re-introduce phantom).
The `litellm`-provider clause is what catches the `$5.60` `llama3.2:3b` pollution
(litellm is the local router here, gated by `allow_paid_base_url=false`).

> Note for the plan: confirm the local-classification clause against a prod
> `cost_logs` sample before running (the `model !~ cloud-prefix` guard must not
> catch a genuinely-paid row). This mirrors
> `feedback_survey_data_shapes_before_regex_backfill`.

### 5. P2 — total-cost gate (API hard, total alert)

`cost_guard.check_budget` / `preflight` replace their inline `_sum_cost` with
`cost_ledger.get_spend(pool, window="day"|"month", strict=True)` and gate on
`api_usd`:

- genuinely-paid call pushing `api_usd` over `daily_spend_limit_usd` /
  `monthly_spend_limit_usd` → `CostGuardExhausted` (unchanged hard-refuse,
  unchanged fail-closed posture — now on clean numbers).
- a soft **alert** at `cost_alert_threshold_pct` fires on **both** axes via
  `total_usd` (log + finding), but never blocks.

The existing exclusion-list `SUM` (`provider NOT IN (...)`) is deleted — the
ledger's invariant supersedes the brittle provider-name filter.

### 6. P3 — spend throttle (total, soft)

A new `services/spend_throttle.py` mirrors `pipeline_throttle.py` exactly (same
module-singleton + `get_state()` + Prometheus observability + DB-error → fail-
open pattern), but keyed on spend instead of approval-queue depth:

```python
async def should_throttle(pool, *, site_config=None) -> ThrottleDecision:
    # Two ceilings on total_usd; throttled if EITHER is crossed:
    #   daily   : get_spend(pool,"day").total_usd   vs cost_throttle_daily_budget_usd
    #   monthly : get_spend(pool,"month").total_usd vs cost_throttle_monthly_budget_usd
    # Each budget<=0 disables that axis (escape-hatch convention from
    # max_approval_queue=0). Returns which ceiling tripped ("daily"|"monthly")
    # for observability + the operator notification.
    #   daily   : rate limit — resets at midnight; hysteresis releases at
    #             budget*(1 - resume_buffer_pct/100).
    #   monthly : cumulative backstop — a month-to-date SUM only ever rises, so
    #             once crossed it stays throttled until month rollover (no
    #             hysteresis). Escape hatch: raise the ceiling or set 0.
```

Consulted at the **new-work** seam — the Prefect `content_generation_flow`
before claiming a `pending` row, and topic-discovery / `create_post` — _not_ in
the per-LLM-call hot path. Over budget → defer claiming new work; in-flight
tasks finish. That is what makes "slow down" safe: never kill a post mid-
generation, just stop starting new ones. The **daily** ceiling is a rate limit
that clears at midnight; the **monthly** ceiling is a cumulative backstop —
crossing it pauses new generation for the rest of the month (in-flight still
finishes, and the operator can raise it or disable it from the phone), which is
why its default sits well above real burn. Fails **open** (DB error → not
throttled): a dead DB must never become a content outage. Aligns with
`feedback_self_heal_not_suppress` (throttle before page).

### 7. P4 — per-axis anomaly (alert)

`detect_anomalies.py::_metric_queries` replaces the single polluted
`cost_per_day` metric with two:

- `api_usd_per_day` → `cost_ledger` api axis, daily.
- `electricity_usd_per_day` → electricity axis, daily.

Both run through the existing z-score machinery (`z_score_threshold`,
`baseline_window_days`, `current_window_hours`) and the existing
`emit_finding` → Telegram/Discord path. An electricity spike now fires
distinctly from an API spike, and neither masks the other. No new knobs — reuses
`plugin.job.detect_anomalies.config.*`.

### 8. P5 — local-vs-cloud savings view (future, observability-only)

A read helper (`cost_ledger.get_savings(pool, window)`) computing, from the
preserved per-call rows:

- **money saved** = Σ counterfactual cloud price for those tokens (local API
  spend is `$0`, so it's pure savings) — via `cost_lookup.get_model_cost_per_1k`
  against a configured "what cloud model would I have used" mapping.
- **energy comparison** = local actual Wh (`electricity_kwh`) vs the same call's
  theoretical cloud datacenter Wh (`cost_guard.estimate_cloud_kwh` +
  `_DEFAULT_CLOUD_ENERGY_WH_PER_1K`).

All primitives already exist; this is a dashboard/read feature, not new capture.
Lowest priority; listed so the data model (and the backfill) deliberately
preserves what it needs.

### 9. Unify the two budget systems

`cost_aggregation_service.get_budget_status` drops the hardcoded `$150`, reads
the ledger + `app_settings` caps, and becomes **advisory/observability only**.
`cost_guard` stays the single enforcement point. The anthropic plugin
(`anthropic.py:571`) keeps calling `get_budget_status` for its pre-flight
_display_, but enforcement is no longer duplicated there (cost_guard already
gates at dispatch) — closing the double-gate. `get_spend_totals` (the MCP
`get_budget` source, `mcp-server/server.py:624`) returns the full breakdown so
the phone shows API vs electricity, not a blended number.

### 10. Config surface

| Key                                     | Default        | Axis / tier      | Status                             |
| --------------------------------------- | -------------- | ---------------- | ---------------------------------- |
| `daily_spend_limit_usd`                 | `2.0`          | API · hard-stop  | exists (now clean `api_usd`)       |
| `monthly_spend_limit_usd`               | `10.0`         | API · hard-stop  | exists                             |
| `cost_alert_threshold_pct`              | `80`           | both · alert     | exists                             |
| `cost_throttle_enabled`                 | `true`         | total · throttle | **new**                            |
| `cost_throttle_daily_budget_usd`        | `3.00`         | total · throttle | **new** (`0` disables)             |
| `cost_throttle_monthly_budget_usd`      | `60.00`        | total · throttle | **new** (cumulative; `0` disables) |
| `cost_throttle_resume_buffer_pct`       | `10`           | total · throttle | **new** (hysteresis, daily only)   |
| `electricity_measured_min_coverage_pct` | `80`           | ledger           | **new**                            |
| `electricity_source_gap_minutes`        | `15`           | ledger           | **new**                            |
| `plugin.job.detect_anomalies.config.*`  | z=2.0, 30d/24h | both · alert     | exists (metrics split)             |

Net new keys: **six** (seeded in `settings_defaults.py` per
`feedback_seed_data_in_baseline_not_new_migrations`). The two operator-tunable
judgment calls: `cost_throttle_daily_budget_usd` (~2× current ~$1.5/day
electricity) and `cost_throttle_monthly_budget_usd` (`$60` ≈ 1.8× the ~$34/mo
real burn, comfortably above the ~$18/mo idle-electricity floor so idle alone
can't lock it); both explicitly "tweak later."

## Error handling & fail-direction

The two enforcement controls fail in **opposite** directions, on purpose:

- **API hard gate** fails **closed** — ledger read error → refuse the paid call
  (budget unverifiable). Preserves cost_guard's existing posture via
  `get_spend(strict=True)`.
- **Spend throttle** fails **open** — ledger read error → don't throttle. A dead
  DB must not become a content outage (mirrors `pipeline_throttle`).

Each direction is the safe one for its job: the gate protects money (safe =
refuse), the throttle protects against runaway but mustn't itself stall the
business (safe = allow).

Other edges: double-count mutual-exclusion invariant (estimate fills only
uncovered time); phantom-regression canary; backfill idempotent + precise +
dry-run-first; throttle hysteresis + budget-above-idle-floor (idle electricity
~$0.6/day, well under the $3 default); no double-gating after the budget-system
unification.

## Tests

Per `feedback_docs_and_tests_default`:

- `test_cost_ledger.py` (**new**) — api/electricity separation; measured-vs-
  estimate fallback; mutual-exclusion (no double-count); `source`/`coverage`
  flags; `strict` raises vs swallows; empty/zero windows.
- **Phantom canary** — a local dispatch writes `cost_usd=0` (fails CI if hosted
  pricing returns). Extend `test_llm_providers_dispatcher.py`.
- `test_cost_guard.py` (**extend**) — gate reads `api_usd`; fail-closed on
  read error; local call skipped; alert at threshold.
- `test_spend_throttle.py` (**new**, mirror `test_pipeline_throttle.py`) — daily
  over budget → throttle (+ hysteresis); monthly-cumulative over budget →
  throttle (no hysteresis, stays tripped); either `budget=0` disables that axis;
  fail-open on DB error; reports which ceiling tripped.
- `test_detect_anomalies_job.py` (**extend**) — api and electricity z-scored
  independently; one spiking doesn't mask the other.
- Backfill — `migrations_smoke.py` (required CI) + a focused `integration_db`
  test (phantom row → `$0`, electricity row untouched, genuinely-paid row
  untouched, `electricity_kwh` preserved).
- `test_cost_aggregation_service.py` (**extend**) — `get_budget_status` reads
  ledger + app_settings caps, no `$150` hardcode.

## Observability

Per `feedback_grafana_everything` + `feedback_visual_verification`:

- **Cost & Analytics** — split the blended spend stat into **API $** and
  **Electricity $** (today/month) + a `source`/`coverage` badge. This panel is
  the visual proof that `$42.58` becomes `~$0 API / $34 electricity`.
- **New throttle gauge** (Pipeline or Cost board) — active state + seconds-
  throttled + day/month `total_usd` vs their budgets + which ceiling tripped,
  mirroring the approval-queue throttle's Prometheus gauge.
- **Findings / Telegram-Discord** — the api-vs-electricity anomaly split surfaces
  on the existing Findings dashboard.
- **P5 savings panel** (future) — cumulative cloud-$ saved + local-vs-cloud Wh on
  Cost / Hardware & Power.
- `get_budget` (phone) returns the breakdown, not a blended number.

## Implementation phasing

1. **P1 — attribution (foundation).** Write invariant + `cost_ledger` +
   measured/fallback electricity + backfill + dashboard split + budget-system
   unification. Ship and verify before anything else.
2. **P2 — total-cost gate** onto the ledger (`api_usd` hard, `total_usd` alert).
3. **P3 — spend throttle** at the new-work seam.
4. **P4 — per-axis anomaly** split.
5. **P5 — savings view** (future / low-priority).

Each phase is independently shippable; 2–4 depend only on P1.

## Risks & mitigations

- **Backfill mis-classifies a genuinely-paid row as local → wrongly zeroed.**
  Mitigated by the explicit cloud-provider exclusion + cloud-prefix regex guard,
  a dry-run `COUNT(*)`, and prod-sample survey before running
  (`feedback_survey_data_shapes_before_regex_backfill`). Today there are `$0`
  paid rows, so live risk is near-nil; the guard is for the future-paid case.
- **Estimate-fallback under-counts** (per-call estimates miss idle draw the PSU
  would have caught). Accepted + labeled (`source=estimated`); measured is always
  preferred when present.
- **Throttle starves new work if total stays over budget** (e.g. high idle
  electricity). The **daily** ceiling self-clears at midnight. The **monthly**
  ceiling is cumulative — crossing it pauses new generation for the rest of the
  month by design; mitigated by setting it above the ~$18/mo idle-electricity
  floor (so idle alone can't trip it) and well above real burn, plus the
  `cost_throttle_enabled=false` / `budget=0` / raise-the-ceiling escape hatch
  reachable from the phone.
- **Reseed drift** — new keys must be in `settings_defaults.py` only, never a
  migration seed.
- **Phantom regression** — the canary test is the durable guard.

## Verification plan — closing the loop

The audit that started this is the acceptance test. After P1:

- Re-run the session's reconciliation query — `api_usd` reads clean (`~$0` today,
  or real cloud once enabled), electricity separate, phantom gone; the `$42.58`
  blended stat splits.
- `get_budget` (phone) shows the split, not a blend.
- A canary that enables a paid model _in a test_ confirms the gate actually
  **refuses** over the cap — the first time that gate is genuinely exercised
  (today: `0` lifetime fires).

After P2–P4: `test` suite green; throttle gauge + per-axis anomaly visible in
Grafana; a synthetic over-budget run throttles new pickup without halting
in-flight work.

## Out of scope / follow-ups

- Graduated/proportional throttle (v1 is binary + hysteresis).
- Per-gap `mixed` electricity fill (v1 may whole-window switch at the coverage
  threshold).
- Retiring the per-call estimate entirely (kept deliberately for P5).
