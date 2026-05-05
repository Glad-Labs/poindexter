# Model Router

**File:** `src/cofounder_agent/services/model_router.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_model_router.py`
**Last reviewed:** 2026-04-30

> **Status: legacy.** Being replaced by the LiteLLM-backed `LLMProvider`
> plugin (`services/llm_providers/litellm_provider.py`). Migration is
> tracked in poindexter#199 — phase 1 shipped 2026-05-04 (provider
> plugin + smoke-test against local Ollama). Phase 2 (delete
> model_router + cost_guard / usage_tracker hand-rolling) waits on
> production validation: flip
> `plugin.llm_provider.primary.standard='litellm'` and observe a
> typical pipeline run before the deletion lands. **Do not extend
> this module** with new tier-routing or fallback logic — LiteLLM
> already does it as mature OSS.

## What it does

`ModelRouter` picks an Ollama model for a given task based on a
keyword-driven complexity classification (`SIMPLE` → `qwen3:8b`,
`MEDIUM` → `gemma3:27b`, `COMPLEX` → `qwen3.5:35b`,
`CRITICAL` → `qwen3.5:122b`). It also enforces token budgets per
task type via `get_max_tokens()`, tracks per-provider consecutive
failures so an outage gets an audible critical-log alarm, and seeds an
in-memory month-to-date spend counter from `cost_logs` at startup.

The module also exports a separate top-level `get_model_for_phase`
helper used by the content-generation pipeline. It maps `(phase,
quality_preference)` to a model — `research`/`finalize`/`outline` get
the fast 8B model; `draft`/`refine` get the best available; `assess`
deliberately uses a different model family (`gemma3:27b`) so the QA
critic isn't the same DNA as the writer (multi-model adversarial QA).

The class is **Ollama-only by policy** (see
`feedback_no_paid_apis.md`). Cloud providers are handled separately by
`cost_guard` + the LLM-provider plugin layer; `ModelRouter` exists to
keep that local-default policy in one place and to let operators tune
per-task token caps from `app_settings` without code changes.

## Public API

### Module helpers

- `get_model_router() -> ModelRouter | None` — returns the
  process-global instance (set by `initialize_model_router`). May
  return `None` if startup didn't initialize it; callers must
  null-check.
- `initialize_model_router(default_model="ollama/qwen3:8b") -> ModelRouter` —
  constructs and stores the singleton. Called from `main.py` startup.
- `get_model_for_phase(phase, model_selections, quality_preference) -> str` —
  pure function. `model_selections` is the per-phase user override
  dict (e.g. `{"draft": "ollama/qwen3.5:35b"}`); explicit non-`"auto"`
  selections win, otherwise the `quality_preference` tier
  (`fast`/`balanced`/`quality`) picks defaults.

### `ModelRouter` instance methods

- `route_request(task_type, context=None, estimated_tokens=1000) -> tuple[str, float, TaskComplexity]` —
  returns `(model_name, estimated_cost, complexity)`. Cost is `0.0`
  for Ollama models. Honors `context["priority"] == "critical"` and
  `context["force_premium"]` overrides.
- `get_max_tokens(task_type, context=None) -> int` — returns the
  per-task token cap. Honors `context["max_tokens"]` and
  `context["override_tokens"]` if set; otherwise looks up the merged
  defaults (built-in + `app_settings.model_token_limits_by_task`).
- `get_model_cost(model) -> float` — per-1K-token cost from
  `model_constants.MODEL_COSTS` (zero for Ollama, real numbers for
  cloud entries kept for the recommendation helper).
- `await router.seed_spend_from_db(pool)` — sums month-to-date
  `cost_logs.cost_usd` (excluding `electricity` and `ollama`
  providers, which represent home-power tracking) into
  `_session_cloud_spend`. Logs critical if already over cap at
  startup.
- `record_provider_failure(provider)` /
  `record_provider_success(provider)` /
  `get_provider_health() -> dict` — per-provider consecutive-failure
  counter. Crosses `_FAILURE_ALERT_THRESHOLD` (5) and emits a
  `logger.critical` alarm.
- `recommend_model_for_budget(remaining_budget, estimated_tokens) -> str | None` —
  finds the cheapest model that fits a budget. Useful when the model
  router is asked to make a budget-aware choice for a paid provider
  (effectively dead today since policy is Ollama-first).
- `get_metrics() -> dict` / `reset_metrics()` — request counters
  (total / budget / premium / Ollama uses + estimated cost actual).

## Key behaviors / invariants

- **Ollama-only model recommendations.** `MODEL_RECOMMENDATIONS` only
  contains Ollama paths. There's no cloud-provider routing in this
  class — that's `cost_guard` + the provider plugins.
- **Complexity classification cascade.** `_assess_complexity`
  evaluates in this order: `critical` keywords → `requires_reasoning`
  context flag → `max_tokens > 2000` context flag → `simple` →
  `medium` → `complex` keywords. Default is `MEDIUM`.
- **Token-limit overrides merge, don't replace.**
  `_token_limits_by_task` reads
  `app_settings.model_token_limits_by_task` (a JSON object), parses
  it, and overlays it on `_DEFAULT_MAX_TOKENS_BY_TASK`. Missing keys
  keep their default. Invalid JSON or non-object values fall through
  to defaults with a warning log — never throws.
- **Phase model selection is opinionated.** `get_model_for_phase`
  always returns `gemma3:27b` for the `assess` phase under
  `balanced`/`quality` to deliberately mix model families with the
  writer (`qwen3.5:35b`). Don't "fix" this to a single family — it's
  a multi-model QA design choice.
- **Provider failure counter is in-memory.** Resets on process
  restart. The critical-log alarm relies on the log alert pipeline,
  not on a DB row.
- **`backwards-compat alias`:** the constant `MAX_TOKENS_BY_TASK`
  still re-exports `_DEFAULT_MAX_TOKENS_BY_TASK`. New code should
  call `ModelRouter.get_max_tokens()` (or `_token_limits_by_task()`)
  for the live, app_settings-aware value.

## Configuration

- `model_token_limits_by_task` (default: built-in dict) — JSON
  object overlaying per-task token caps. Example:
  `{"draft": 1500, "research": 200}`. Read fresh on every
  `get_max_tokens` call so changes apply without a restart (#198).
- `use_ollama` (default `false`) — read in `__init__`; if `true`,
  routes prefer Ollama. Today the recommendations are
  Ollama-only regardless, so this flag is mostly historical.
- `monthly_spend_limit` (default `100.0`) — read in `__init__` for
  the in-memory budget guard. The authoritative per-call cap lives
  in `cost_guard` (`monthly_spend_limit_usd`), so don't rely on this
  one in isolation.

The default token caps live in `_DEFAULT_MAX_TOKENS_BY_TASK` at the
top of the module — covers ~50 task verbs across simple / medium /
complex / critical tiers, plus a `default: 800` fallback.

## Dependencies

- **Reads from:**
  - `services.site_config.site_config` for `model_token_limits_by_task`,
    `use_ollama`, and `monthly_spend_limit`.
  - `cost_logs` (only `seed_spend_from_db`) — sums month-to-date
    spend, excluding `electricity` and `ollama` provider rows.
  - `services.model_constants.MODEL_COSTS` — per-1K-token cost map.
- **Writes to:** nothing. State is in-memory only.
- **External APIs:** none. Routing is in-process; the actual LLM
  call happens in the provider plugin the caller selects from the
  returned model string.
- **Callers:**
  - `services.multi_model_qa.MultiModelQA` — uses
    `get_model_router()` to look up a model for the cross-model
    review.
  - `agents.blog_quality_agent`, `services.quality_service` — accept
    an optional `model_router` for LLM-based evaluations.
  - `main.py` startup — calls `initialize_model_router()` and
    `seed_spend_from_db()` once.
  - `schemas.model_converter` — uses `get_model_for_phase` for
    per-phase user model selections.

## Failure modes

- **Singleton not initialized** — `get_model_router()` returns
  `None`. `MultiModelQA.__init__` handles this by falling back to a
  config default. Callers MUST null-check.
- **`site_config` read fails on init** — caught, logged as warning,
  `use_ollama` falls back to `False` and `monthly_spend_limit` to
  `100.0`. Router still works.
- **`seed_spend_from_db` query fails** — caught, logged as error,
  `_session_cloud_spend` stays at `0.0`. The budget guard will
  under-report spend at startup until the next `cost_logs` write
  refreshes it (note: writes don't update the in-memory counter
  here; that path is in `cost_guard`).
- **`model_token_limits_by_task` is malformed** — `_token_limits_by_task`
  catches `ValueError`/`TypeError`, logs a warning, returns the
  defaults. Per-key bad values (e.g. `"draft": "lots"`) are skipped
  with a per-key warning, not the whole override.
- **Unknown task type in `route_request`** — `_assess_complexity`
  defaults to `MEDIUM` (`gemma3:27b`). Not an error, just a
  conservative pick.
- **Provider hit-streak past threshold** — emits
  `logger.critical("[llm_provider] Provider %r has failed %d
consecutive times — possible outage or quota exhaustion", ...)`.
  Wired to the alert pipeline via the log-monitoring stack. Resets
  on the first successful call.

## Common ops

- **Override a task's token cap without code changes:**
  ```bash
  poindexter set model_token_limits_by_task '{"draft": 1500, "summary": 200}'
  ```
  Applies on next `get_max_tokens` call — no restart needed.
- **Check current routing metrics:**
  `services["model_router"].get_metrics()` (or hit whatever debug
  endpoint exposes them).
- **Reset failure counters after a known outage:** call
  `record_provider_success(provider)` for each affected provider, or
  restart the app. There's no DB persistence to clear.
- **Verify the singleton was initialized at startup:** look for the
  `"Global model router initialized"` log line from
  `initialize_model_router`. Absence means the lifespan hook didn't
  run.
- **Find which models the multi-model QA is comparing:** check
  `get_model_for_phase(phase="assess", quality_preference=...)` —
  it's the deliberately-different-family pick.

## See also

- `docs/architecture/services/multi_model_qa.md` — the largest
  consumer of `get_model_router()`.
- `docs/architecture/services/cost_guard.md` — the authoritative
  budget enforcer. `ModelRouter._session_cloud_spend` is a
  best-effort UI counter, not a guard.
- `services.model_constants` — the static `MODEL_COSTS` dict.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_paid_apis.md`
  — why this is Ollama-first.
- `~/.claude/projects/C--Users-mattm/memory/project_multi_model_qa.md`
  — why `assess` deliberately uses a different model family from
  `draft` / `refine`.
