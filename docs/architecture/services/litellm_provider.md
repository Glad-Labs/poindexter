# LiteLLM Provider

**File:** `src/cofounder_agent/services/llm_providers/litellm_provider.py`
**Tested by:** smoke-tested against local Ollama 2026-05-04; unit tests pending
**Last reviewed:** 2026-05-23

## What it does

`LiteLLMProvider` is an `LLMProvider` plugin (under `plugins.llm_provider.LLMProvider`) backed by the LiteLLM SDK. It is the **primary LLM router on prod** for all four cost tiers as of 2026-05-16 (`plugin.llm_provider.primary.{free,budget,standard,premium}='litellm'`). Provider routing, retries-with-backoff, and authoritative cost tracking are delegated to mature OSS. The legacy `services/model_router.py` + `usage_tracker.py` + `model_constants.py` trio was deleted 2026-05-08 in the Phase 2 cleanup.

Three public methods, all async, mirroring the `LLMProvider` Protocol:

- **`complete(messages, model, **kwargs)`** — non-streaming chat completion. Returns a `Completion(text, model, prompt_tokens, completion_tokens, total_tokens, finish_reason, raw)`. The `raw`dict surfaces`response_cost`when LiteLLM's MODEL_COSTS table knows the model, so`cost_logs` can write the authoritative number without re-deriving prices.
- **`stream(messages, model, **kwargs)`** — streaming chat. Yields `Token(text, finish_reason)`per chunk. Same call signature as`complete`; just sets `stream=True` internally.
- **`embed(text, model)`** — embedding generation via `litellm.aembedding`. Returns `list[float]`. Same model namespace as `complete` (`ollama/nomic-embed-text` etc).

## Activation

The plugin sits alongside `OllamaNativeProvider` and `OpenAICompatProvider` in `plugins/registry.py`'s core samples. As of 2026-05-16 all four `plugin.llm_provider.primary.{free,budget,standard,premium}` rows are set to `'litellm'` on prod — this is the canonical state. To revert a tier to the legacy native provider (e.g. for a focused regression test), an operator flips one row back:

```sql
UPDATE app_settings SET value = 'ollama_native'
 WHERE key = 'plugin.llm_provider.primary.standard';
```

The dispatcher's name lookup picks it up on the next call. Per-call config (api_base, timeout_seconds, drop_params, default_prefix) flows through `kwargs['_provider_config']` from the dispatcher.

## Model namespace

LiteLLM prefixes provider on the model string. The provider auto-prefixes bare names so existing callers don't churn:

| Caller passes                           | Provider sends                                   |
| --------------------------------------- | ------------------------------------------------ |
| `gemma3:27b`                            | `ollama/gemma3:27b` (`default_prefix='ollama/'`) |
| `ollama/glm-4.7-5090:latest`            | passthrough                                      |
| `openai/gpt-4o-mini`                    | passthrough                                      |
| `anthropic/claude-haiku-4-5`            | passthrough                                      |
| `vertex_ai/gemini-2.0-flash`            | passthrough                                      |
| `openrouter/anthropic/claude-haiku-4-5` | passthrough                                      |
| `http://host:port/v1`                   | passthrough (custom api_base)                    |

`drop_params=true` (default) strips kwargs the target backend doesn't recognize so a single call signature works across backends. Per-call `timeout_s` overrides the provider default.

## Reads from / writes to

- **Reads:** `_provider_config` kwargs from the dispatcher (`api_base`, `timeout_seconds`, `drop_params`, `default_prefix`); ENV vars only via LiteLLM's own config surface — the plugin doesn't read os.environ directly per `feedback_no_env_vars`.
- **Writes:** nothing back to Postgres. Cost logging is the dispatcher's job; the plugin just exposes `response_cost` on the `Completion.raw` dict for the dispatcher to consume.
- **External APIs:** whatever LiteLLM speaks — Ollama, OpenAI, Anthropic, Gemini, vLLM, llama.cpp, OpenRouter, Bedrock, Vertex. Per `feedback_no_paid_apis`, the OSS distribution defaults to Ollama via `default_prefix='ollama/'`; cloud providers stay opt-in behind `cost_guard`.

## Failure modes

- **`litellm` not installed** — `import litellm` raises ImportError at first call. Operators who explicitly opt out of LiteLLM should flip `plugin.llm_provider.primary.{tier}` back to `ollama_native` per tier; the worker image ships `litellm` by default since it's the prod router.
- **`acompletion` raises** — exception propagates with full traceback after a `logger.exception()`. Caller (the dispatcher) decides whether to fall back to the next provider in the chain.
- **Empty response** — `text=''` and `finish_reason=''`; caller sees a successful Completion with no content. Surfaces as a quality-validator failure downstream rather than a hard error.
- **Cost table miss** — LiteLLM doesn't know the model's price; `response_cost` is absent from `raw` and `cost_logs` records `0.0`. Not a fatal condition; surfaces as missing cost data on the Grafana cost panel.

## Cleanup status

Phase 2 of poindexter#199 landed 2026-05-08: `services/model_router.py` (~580 LOC), `services/usage_tracker.py` (~295 LOC), and `services/model_constants.py` were all deleted. `services/cost_guard.py` was retained as a thin budget-cap + energy-tracking wrapper. LiteLLM owns provider routing, cost tracking, and rate-limiting; the hand-rolled stack is gone.

## See also

- [`model_router.md`](model_router.md) — stub redirect (the file was deleted 2026-05-08).
- `plugins/llm_provider.py` — the `LLMProvider` Protocol this plugin implements.
- `plugins/registry.py` — where `LiteLLMProvider` is registered alongside `OllamaNativeProvider` and `OpenAICompatProvider`.
- [`../cost-tier-routing.md`](../cost-tier-routing.md) — cost-tier API (`resolve_tier_model`) layered over the provider plugins.
- `feedback_no_paid_apis` (operator design note) — local-default policy.
- `feedback_no_wheel_reinvention` (operator design note) — adoption rationale.
