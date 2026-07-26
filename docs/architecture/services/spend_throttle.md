# Spend Throttle

**File:** `src/cofounder_agent/services/spend_throttle.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_spend_throttle.py`
**Last reviewed:** 2026-07-07

## What it does

`spend_throttle` defers **new work** when total spend crosses a soft budget.
It is P3 of the [cost-attribution design](../2026-06-21-cost-control-attribution-design.md)
(§6) and a fail-**open** sibling of [`pipeline_throttle`](./pipeline_throttle.md):
instead of gating on approval-queue depth it gates on money — the
[`cost_ledger`](./cost_ledger.md) `total_usd` (paid API + measured electricity)
against two soft ceilings.

It is consulted at the **new-work seam** — the Prefect `content_generation_flow`
just before it claims a `pending` row — **not** in the per-LLM-call hot path.
Over budget → the flow defers claiming this run and returns
`{"claimed": False, "throttled": True, "ceiling": ...}`; in-flight tasks finish
untouched. That is what makes "slow down" safe: it never kills a post
mid-generation, it just stops starting new ones. Operator-triggered runs
(explicit `task_id`/`topic`) bypass the gate.

## The two ceilings

| Ceiling     | Budget key                         | Behavior                                                                                                                                                                                          |
| ----------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **daily**   | `cost_throttle_daily_budget_usd`   | Rate limit. Engages at the budget; **hysteresis** releases only below `budget * (1 - cost_throttle_resume_buffer_pct/100)` so it doesn't chatter. Clears at midnight (the day-window SUM resets). |
| **monthly** | `cost_throttle_monthly_budget_usd` | Cumulative backstop. A month-to-date SUM only rises, so once crossed it stays engaged until month rollover (**no hysteresis**). Default sits well above real burn.                                |

Throttled if **either** ceiling is crossed. When both cross, the **monthly**
ceiling is reported (it's the more binding one — sticky until rollover). A
budget `<= 0` disables that axis (the escape-hatch convention).

## Relationship to `cost_guard`

The two controls fail in **opposite** directions, by design:

- **`cost_guard`** — the per-call **hard** stop. Gates on the api axis, fails
  **closed** (a ledger read error refuses the paid call — budget unverifiable).
- **`spend_throttle`** — the new-work **soft** stop. Gates on total spend, fails
  **open** (a ledger read error / missing pool → _not_ throttled). A dead DB
  must never become a content outage.

`spend_throttle` is the gentle "slow down" that _precedes_ the hard cap, per
`feedback_self_heal_not_suppress` (throttle before page).

`cost_guard` also **reads `cost_throttle_daily_budget_usd`** — its advisory
`cost_budget_alert` finding warns at `cost_alert_threshold_pct` of this daily
budget, so the operator hears "you are approaching the line where new work gets
deferred" before the throttle actually engages. That alert is the early signal
for _this_ mechanism, not for the hard cap; it is the one place cost_guard
measures against the total axis. It does not consult `cost_throttle_enabled` —
turning the throttle off stops the deferral, not the observability; silence the
alert by setting the budget `<= 0`. Keep the two readers of this key in sync:
if the budget moves, the warning line moves with it automatically.

## Public API

- `await should_throttle(pool, *, site_config=None) -> ThrottleDecision` — the
  gate. Reads all knobs from `site_config` (documented defaults when `None`).
  Fails open on any error. Updates module state as a side effect (so
  `get_state()` stays current) and emits a one-shot `spend_throttle_engaged`
  finding on the inactive→active transition.
- `ThrottleDecision(throttled, ceiling, reason, daily_total_usd, daily_budget_usd, monthly_total_usd, monthly_budget_usd)`
  — the result. `ceiling` is `"daily"` | `"monthly"` | `None`.
- `get_state() -> dict` — snapshot for Prometheus / health (mirrors
  `pipeline_throttle.get_state()`): `active`, `active_seconds`, `total_seconds`,
  `ceiling`, `daily_total_usd`, `daily_budget_usd`, `monthly_total_usd`,
  `monthly_budget_usd`.
- `reset_for_tests()` — reset the module singleton (tests call it in fixtures).

## Configuration

All from `app_settings` (DB-first), seeded in `settings_defaults.py`:

- `cost_throttle_enabled` (default `true`) — master switch.
- `cost_throttle_daily_budget_usd` (default `3.00`; `<= 0` disables the daily axis).
- `cost_throttle_monthly_budget_usd` (default `60.00`; `<= 0` disables the monthly axis).
- `cost_throttle_resume_buffer_pct` (default `10`) — daily hysteresis release band.

The two judgment-call defaults are deliberately above real burn (~$1.5/day,
~$34/mo) so idle electricity alone can't lock the throttle — both are
"tweak later" per design §10.

## Observability

- **Discord finding** (primary): `spend_throttle_engaged` (`severity='warn'`),
  emitted once per engage transition. An unlisted finding kind routes loud by
  default (`findings_alert_router._delivery_for` → `route` → dispatcher by
  severity → Discord).
- **Prometheus gauges** (`metrics_exporter`, mirror `spend_throttle.get_state()`):
  `poindexter_spend_throttle_active`, `_seconds_total`,
  `_daily_total_usd`, `_daily_budget_usd`, `_monthly_total_usd`,
  `_monthly_budget_usd`. A Grafana panel (Cost & Analytics board) is the natural
  next step once verified on live Grafana.

## Dependencies

- **Reads from:** `cost_ledger.get_spend` (the single spend seam) + injected
  `site_config`. No direct SQL.
- **Writes to:** `audit_log` (event_type `finding`, kind `spend_throttle_engaged`)
  via `utils.findings.emit_finding`, once per engage.
- **Consulted by:** `services/flows/content_generation.py` (the new-work seam).

## See also

- [`cost_ledger.md`](./cost_ledger.md) — the spend read seam.
- [`cost_guard.md`](./cost_guard.md) — the per-call hard cap (fails closed).
- [`pipeline_throttle.md`](./pipeline_throttle.md) — the approval-queue sibling.
- `feedback_self_heal_not_suppress` — throttle before page.
