# LiteLLM Provider

**File:** `src/cofounder_agent/services/llm_providers/litellm_provider.py`
**Tested by:** smoke-tested against local Ollama 2026-05-04; unit tests pending
**Last reviewed:** 2026-05-04

## What it does

`LiteLLMProvider` is an `LLMProvider` plugin (under `plugins.llm_provider.LLMProvider`) backed by the LiteLLM SDK. It's the canonical replacement for `services/model_router.py` — phase 1 of poindexter#199 — and gets the worker authoritative cost tracking, retries-with-backoff, and provider-routing for free instead of hand-rolled.

Three public methods, all async, mirroring the `LLMProvider` Protocol:

- **`complete(messages, model, **kwargs)`** — non-streaming chat completion. Returns a `Completion(text, model, prompt_tokens, completion_tokens, total_tokens, finish_reason, raw)`. The `raw`dict surfaces`response_cost`when LiteLLM's MODEL_COSTS table knows the model, so`cost_logs` can write the authoritative number without re-deriving prices.
- **`stream(messages, model, **kwargs)`** — streaming chat. Yields `Token(text, finish_reason)`per chunk. Same call signature as`complete`; just sets `stream=True` internally.
- **`embed(text, model)`** — embedding generation via `litellm.aembedding`. Returns `list[float]`. Same model namespace as `complete` (`ollama/nomic-embed-text` etc).

## Activation

The plugin sits alongside `OllamaNativeProvider` and `OpenAICompatProvider` in `plugins/registry.py`'s core samples. To make it the live provider for a cost tier, an operator flips one app_setting:

```sql
UPDATE app_settings SET value = 'litellm'
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

- **`litellm` not installed** — `import litellm` raises ImportError at first call. Operators on the OSS distribution who don't intend to use LiteLLM should leave `plugin.llm_provider.primary.standard` set to `ollama_native` (the legacy default).
- **`acompletion` raises** — exception propagates with full traceback after a `logger.exception()`. Caller (the dispatcher) decides whether to fall back to the next provider in the chain.
- **Empty response** — `text=''` and `finish_reason=''`; caller sees a successful Completion with no content. Surfaces as a quality-validator failure downstream rather than a hard error.
- **Cost table miss** — LiteLLM doesn't know the model's price; `response_cost` is absent from `raw` and `cost_logs` records `0.0`. Not a fatal condition; surfaces as missing cost data on the Grafana cost panel.

## Phase 2 (pending)

Once the operator has flipped the setting and a typical pipeline run completes successfully on LiteLLM, phase 2 of poindexter#199 deletes `services/model_router.py` (~580 LOC) + `services/cost_guard.py` (~738 LOC, kept thinly as a budget-cap wrapper) + `services/usage_tracker.py` (~295 LOC). LiteLLM owns provider routing, cost tracking, and rate-limiting; the hand-rolled stack is replaced.

## See also

- `services/model_router.md` — legacy router with deprecation banner pointing here.
- `plugins/llm_provider.py` — the `LLMProvider` Protocol this plugin implements.
- `plugins/registry.py` — where `LiteLLMProvider` is registered alongside `OllamaNativeProvider` and `OpenAICompatProvider`.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_paid_apis.md` — local-default policy.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_wheel_reinvention.md` — adoption rationale.
