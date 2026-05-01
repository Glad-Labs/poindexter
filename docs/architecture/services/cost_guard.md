# Cost Guard

**File:** `src/cofounder_agent/services/cost_guard.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_cost_guard.py`
**Last reviewed:** 2026-04-30

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
  current spend in USD, excluding `electricity` / `ollama` /
  `ollama_native` rows.
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

- `daily_spend_limit_usd` (default `2.00`).
- `monthly_spend_limit_usd` (default `100.00`).
- `cost_alert_threshold_pct` (default `80.0`) — soft alert (log only,
  doesn't block) when daily spend crosses this percentage of the cap.
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
  - `cost_logs` for daily/monthly spend sums (filters out
    `electricity` / `ollama` / `ollama_native` provider rows so
    home-power tracking doesn't trip the cap).
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
    going dark (gitea#322 finding 3).
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

- **Inspect today's spend:**
  `SELECT provider, model, SUM(cost_usd) FROM cost_logs WHERE created_at >= date_trunc('day', NOW()) AND provider NOT IN ('electricity','ollama','ollama_native') GROUP BY 1,2 ORDER BY 3 DESC;`
- **Raise the daily cap temporarily:**
  `poindexter set daily_spend_limit_usd 10` (then unset after the burst).
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

- `~/.claude/projects/C--Users-mattm/memory/feedback_cost_controls.md`
  — Matt's $300 Gemini-in-one-night origin story for why this guard
  exists.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_paid_apis.md`
  — local-default + opt-in cloud LLM policy.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_silent_defaults.md`
  — why `CostGuardExhausted` MUST surface and not silently fall back.
- `docs/architecture/services/site_config.md` — how the guard reads
  its limits and rate overrides.
