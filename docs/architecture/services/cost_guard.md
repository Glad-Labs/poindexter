# Cost Guard

**File:** `src/cofounder_agent/services/cost_guard.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_cost_guard.py`
**Last reviewed:** 2026-07-26

## What it does

`CostGuard` is the pre-call budget enforcer for any LLM provider that
might cost real dollars. Every cloud call (OpenAI, Anthropic, Gemini,
OpenRouter, OpenAI-compat) routes through `check_budget()` (or the
older `preflight()`) BEFORE the SDK fires, and `record_usage()` (or
`record()`) AFTER. If a hypothetical call would push daily or monthly
spend over the configured cap, the guard raises `CostGuardExhausted`.

Local backends (`localhost`, `127.0.0.1`, `host.docker.internal`,
`0.0.0.0`) short-circuit to "$0, allowed" so the guard contract is
uniform across providers without ever blocking self-hosted vLLM /
llama.cpp / Ollama. State lives in `cost_logs` — every preflight does
a fresh DB read so multiple workers can't race past the cap by holding
stale local counters.

The guard also tracks energy. `record_usage()` fills in
`electricity_kwh` automatically — local providers from GPU power ×
duration, cloud providers from per-model Wh/1K-token defaults — so
the cost dashboard can compare "Ollama on the 5090 vs Anthropic in
us-east-1" on energy as well as dollars.

## Public API

- `CostGuard(*, site_config=None, pool=None)` — constructor. Stateless;
  every method that needs state hits the DB.
- `await guard.check_budget(*, provider, model, estimated_cost_usd)` —
  raises `CostGuardExhausted` if the call would breach the cap.
  Three-stage check: `daily_estimate` (single call > daily cap) →
  `daily` (current spend + estimate > daily cap) → `monthly` (same for
  monthly).
- `await guard.estimate_cost(*, provider, model, prompt_tokens, completion_tokens) -> float` —
  pure function over `_get_rate`. Sourced from app_settings rate keys
  with built-in fallbacks for known cloud providers.
- `await guard.record_usage(*, provider, model, prompt_tokens=0, completion_tokens=0, cost_usd=None, phase="llm_call", task_id=None, success=True, duration_ms=None, electricity_kwh=None, is_local=False) -> float` —
  persists the call. Auto-fills `cost_usd` and `electricity_kwh` if
  caller passed `None`. Returns the dollar cost actually written.
- `await guard.get_daily_spend() -> float` / `get_monthly_spend()` —
  current genuinely-paid cloud spend in USD (the ledger's **api axis**).
  Both delegate to `cost_ledger.get_spend(window=...).api_usd` — the single
  spend-read seam the cap and the operator dashboards share, so the cap can
  never drift from the ledger. Local inference/media rows are `$0` by the
  write invariant, so they contribute nothing.
- `await guard.estimate_cloud_kwh(...) / guard.estimate_local_kwh(*, duration_ms)` —
  energy estimators in kWh.
- `guard.kwh_to_usd(kwh) -> float` — convert energy at the configured
  electricity rate.
- `CostGuardExhausted(reason, *, scope, spent_usd, limit_usd, provider, model, estimated_cost_usd)` —
  raised on cap breach. Catch this; do NOT silently retry against a
  different paid provider (see "fail loud" principle).

Lower-level legacy triplet (still exported, used by `OpenAICompatProvider`):

- `guard.estimate(*, provider, model, base_url, prompt_tokens, completion_tokens, rate_table) -> CostEstimate`
- `await guard.preflight(estimate)` — older call-site shape that
  takes a `base_url` and a caller-supplied rate table. New code
  should prefer `check_budget` + `record_usage`.
- `await guard.record(...)` — lower-level cost_logs writer.
- `is_local_base_url(url) -> bool` — module helper for routing.

## Configuration

All from `app_settings` via `site_config`:

- `daily_spend_limit_usd` (default `2.00`) — **API axis**, hard cap.
- `monthly_spend_limit_usd` (default `100.00`) — **API axis**, hard cap.
- `cost_throttle_daily_budget_usd` (default `3.00`) — **total axis** (paid
  API + measured electricity). Owned by `services/spend_throttle.py`; the
  soft alert rents it as the ceiling for its total-axis check so both
  consumers of the total axis measure against the same budget. `<= 0`
  disables the alert, matching the throttle's own escape-hatch convention.
- `cost_alert_threshold_pct` (default `80.0`) — soft alert when projected
  daily **total** spend (both axes: paid API + measured electricity)
  crosses this percentage of the **total-axis** budget above. Advisory
  only: it logs AND emits a `cost_budget_alert` finding
  (`severity='warn'` → Discord), never blocks. The hard cap gates on the
  api axis separately.

  The trip ceiling is deliberately NOT `daily_spend_limit_usd`. Measured
  electricity alone runs ~$1.5-1.9/day against that $2 API cap, so keying
  the total against it left the 80% threshold already consumed before any
  paid call — the alert fired on essentially every day with cloud spend
  and claimed the daily cap was nearly blown while the enforced axis sat
  around 20% (Glad-Labs/poindexter#912). Each axis is now measured against
  its own ceiling, and the finding names both figures so a reader can tell
  which one moved.

- `electricity_rate_kwh` (default `0.16` USD/kWh — EIA 2024 US
  residential avg). Refreshed daily by `UpdateUtilityRatesJob` from
  the EIA API.
- `gpu_power_watts` (default `450.0` — RTX 5090 conservative under
  load). Auto-refreshed daily from nvidia-smi.

Per-provider/per-model rate overrides (precedence: per-model >
provider default > built-in fallback):

- `plugin.llm_provider.<provider>.cost_per_1k_input_usd`
- `plugin.llm_provider.<provider>.cost_per_1k_output_usd`
- `plugin.llm_provider.<provider>.model.<model>.cost_per_1k_input_usd`
- `plugin.llm_provider.<provider>.model.<model>.cost_per_1k_output_usd`

Built-in fallback for unknown cloud models:
`_FALLBACK_RATE_PER_1K = {"input": $0.0005, "output": $0.0015}` —
pinned to GPT-4o-mini-equivalent so an unrecognized OpenRouter /
Together model can't slip through under-budgeted.

Per-provider/per-model energy overrides:

- `plugin.llm_provider.<provider>.energy_per_1k_wh`
- `plugin.llm_provider.<provider>.model.<model>.energy_per_1k_wh`

Built-in defaults live in `_DEFAULT_CLOUD_ENERGY_WH_PER_1K` keyed by
provider + model (e.g. `gemini-2.5-flash`: `0.3 Wh/1K`,
`claude-opus-4-7`: `4.0 Wh/1K`). Fallback for unknown known-cloud
models: `_FALLBACK_ENERGY_WH_PER_1K = 1.0 Wh/1K`. Unknown providers
return `0.0` (treated as local — won't trip the budget on a
misclassified call).

Known cloud providers: `gemini`, `openai`, `anthropic`, `openrouter`.

## Dependencies

- **Reads from:**
  - `cost_logs` **only through `cost_ledger.get_spend`** (P2) — the single
    read seam that owns the api-vs-electricity split. The hard cap reads
    `api_usd` (genuinely-paid cloud); the soft alert reads `total_usd` (both
    axes). Renting the seam means the cap and the operator dashboards read the
    same meter and the api-axis predicate lives in exactly one place — no
    re-derived provider-name denylist that can drift. Local rows are `$0` by the
    write invariant so they self-exclude; home-power `electricity` rows are
    separated by `cost_type`.
  - Injected `site_config` for limit + rate + energy settings.
  - `services.cost_lookup` (post-#199, LiteLLM-backed with 2,600+
    provider/model combos) — referenced in the module docstring;
    actual lookup happens in callers that wire it into the
    `rate_table` arg of `estimate()`.
- **Writes to:**
  - `cost_logs` — every recorded call.
  - `audit_log` (event_type `cost_log_write_failed`, severity
    `error`) — best-effort, fired only when the cost_logs INSERT
    itself fails so the alert pipeline catches a budget tracker
    going dark (Glad-Labs/poindexter#322 finding 3).
  - `audit_log` (event_type `finding`, kind `cost_budget_alert`,
    severity `warn`) — the soft total-axis budget alert, emitted via
    `utils.findings.emit_finding` when projected daily total spend
    crosses `cost_alert_threshold_pct` of `cost_throttle_daily_budget_usd`.
    Advisory; routes to Discord (unlisted kind → `route` by severity);
    never blocks. It is the early warning for the mechanism that acts on
    that ceiling — `spend_throttle` deferring new pipeline work — not for
    the hard cap.
- **External APIs:** none directly. Provider plugins call out; the
  guard only watches their wallets.

## Failure modes

- **Estimate alone exceeds daily cap** — `scope="daily_estimate"`.
  A single call this expensive can't ever fit. Either lower the
  estimate (smaller model, shorter prompt) or raise the cap.
- **Daily cap breach** — `scope="daily"`. Resets at UTC midnight
  (uses `date_trunc('day', NOW())`).
- **Monthly cap breach** — `scope="monthly"`. Resets at UTC start of
  month.
- **`cost_logs` INSERT fails** — caught, logged as warning, mirrored
  into `audit_log` as `cost_log_write_failed`. The original LLM call
  is NOT retried or refunded — the spend happened, just couldn't be
  recorded. Alert on this event.
- **Unknown provider** — `_get_rate` returns `0.0`, `is_local_base_url`
  returns `False`. The call is allowed (since estimate is $0) but
  energy tracking falls back to `_FALLBACK_ENERGY_WH_PER_1K`.
  Misclassification fails open by design — better than tripping the
  budget on a mistakenly-classified Ollama call.
- **Malformed app_settings rate** — `_get_rate` and `_limit` both
  catch `ValueError`/`TypeError`, log a warning, and use the default.
  No crash propagation.

## Common ops

- **Inspect today's paid cloud spend** (the paid-API axis — local rows are `$0`
  by the write invariant, so this sums genuinely-billable cloud calls only):
  `SELECT provider, model, SUM(cost_usd) FROM cost_logs WHERE created_at >= date_trunc('day', NOW()) AND COALESCE(cost_type,'inference') NOT LIKE 'electricity%' GROUP BY 1,2 ORDER BY 3 DESC;`
- **Raise the daily cap temporarily:**
  `poindexter settings set daily_spend_limit_usd 10` (then unset after the burst).
- **Check whether a model is known to LiteLLM:** see
  `services.cost_lookup` lookup; or query
  `SELECT key, value FROM app_settings WHERE key LIKE 'plugin.llm_provider.%.cost_per_1k_%';`
- **Audit cost-log write failures:**
  `SELECT created_at, payload FROM audit_log WHERE event_type = 'cost_log_write_failed' ORDER BY created_at DESC;`
- **Disable a provider entirely** — drop its rate keys to 0 (the call
  will pass budget but you shouldn't have anything calling it; the
  router selection is upstream).
- **Override an OpenRouter routed model rate:** set
  `plugin.llm_provider.openrouter.model.<model>.cost_per_1k_input_usd`
  - the matching `_output_usd`.

## See also

- `feedback_cost_controls` (operator design note)
  — Matt's $300 Gemini-in-one-night origin story for why this guard
  exists.
- `feedback_no_paid_apis` (operator design note)
  — local-default + opt-in cloud LLM policy.
- `feedback_no_silent_defaults` (operator design note)
  — why `CostGuardExhausted` MUST surface and not silently fall back.
- `docs/architecture/services/site_config.md` — how the guard reads
  its limits and rate overrides.
