# `cost_logs.model` — one engine, three spellings

The same local model has been recorded under up to three different strings,
so every per-model `GROUP BY` split it into phantom series. On 2026-08-27 the
Cost & Analytics "By Model" table read:

| row                                | calls  |
| ---------------------------------- | ------ |
| `gemma-4-31B-it-qat:latest`        | 27,257 |
| `ollama/gemma-4-31B-it-qat:latest` | 637    |

Same engine, same endpoint, two rows. `phi4:14b` and `qwen3-vl:30b` split the
same way.

## Why the spellings diverge

`dispatch_complete` used to log **the string the caller passed**, not the model
that ran. Those differ because two independent things spell the pin differently:

1. **29 call sites across 14 files strip the prefix before dispatching** —
   `ragas_eval`, `multi_model_qa` (×6), `podcast_service`, `social_poster`,
   `self_review`, `title_generation`, `image_decision_agent`, … each do
   `.removeprefix("ollama/")` on their configured `*_model` value. Legacy from
   when they spoke to Ollama directly and needed the bare tag.
   `LiteLLMProvider._resolve_model` → `resolve_model_name` silently re-adds it,
   so **the call is byte-identical** — only the log differed.
2. **A few settings keys are genuinely bare** — `preferred_ollama_model`
   (the `topic_ranking` volume) and `voice_agent_llm_model`, which is
   correctly bare because Pipecat talks to Ollama directly — a prefixed value
   there 404s (`model 'ollama/qwen2.5:7b' not found`).

## Which spelling is correct where

`ollama/` vs bare is **cosmetic at dispatch** — the three places that could
have keyed off the unresolved string all resolve first
(`_is_paid_llm_call` treats a bare name as local by construction;
`_routes_to_pinned_endpoint` → `pinned_api_base_for` and `_api_base_for` both
call `resolve_model_name` before the override lookup).

`ollama_chat/` is **not** cosmetic: it routes to `/api/chat` instead of
`/api/generate`, which is what makes tool-result messages round-trip. Any
agentic pin must use it, and it needs its own `model_api_base_overrides` entry
because that map is keyed on the RESOLVED name.

Cloud prefixes (`anthropic/`, `gemini/`, `openai/`) are real provenance.

## The fix — write side

`services/llm_providers/dispatcher.py::_cost_log_model` records what the
provider **actually dispatched**: prefer `Completion.model`, which every
provider stamps with the identifier it ran (LiteLLM stamps the prefix-resolved
name). On the failure path there is no `Completion`, so it falls back to the
provider's own `_resolve_model` — otherwise the ~4.5% of rows that error, 87%
of them bare, would keep the phantom series alive. Providers that resolve
nothing (`anthropic` / `gemini` / `ollama_native` each talk to one backend with
the name as given) expose no resolver and keep the raw string, so a wrong
namespace can never be synthesized for them.

This is a **reporting label only** — `_is_paid_llm_call`, the budget gate and
the electricity fallback all deliberately keep reading the requested `model`,
so spend behaviour is byte-identical to before.

## The fix — read side

Every per-model rollup normalizes with the same regex:

```sql
regexp_replace(model, '^ollama(_chat)?/', '')
```

Used by `services/llm_throughput.py` and all nine per-model panels on the Cost
& Analytics board. `ollama_chat/` collapses into the engine bucket **on read**
(these are cost/volume rollups) while staying distinct in the stored column.

The read-side fix is not redundant with the write-side one: the write fix only
canonicalizes rows written from here on, and the panels have to read two years
of history that is already split.

`tests/unit/infrastructure/test_grafana_cost_model_grouping.py` walks every
dashboard and fails any new panel that buckets the raw column.

## Rows on this table that are not LLM calls

Reading the "By Model" table, three groups are not inference at all:

| `model`                         | written by                       | what it is                                    |
| ------------------------------- | -------------------------------- | --------------------------------------------- |
| `system`                        | `brain_daemon` electricity cycle | 5-min wall-power estimate — **not API spend** |
| `h264`, `videos.insert`         | `cost_guard.record_usage`        | ffmpeg render, YouTube API                    |
| `Systran/faster-whisper-medium` | `cost_guard.record_usage`        | Whisper captioning                            |

`system` carries a non-zero `cost_usd` (estimated electricity), so summing the
cost column across the whole table mixes power with API spend. Filter on
`provider <> 'electricity'` for a dollars view.

## See also

- [`cost-tier-routing.md`](cost-tier-routing.md) — per-step `*_model` pins.
- [`2026-06-21-gpu-llm-dispatch-serialization.md`](2026-06-21-gpu-llm-dispatch-serialization.md)
  — the other consumer of `resolve_model_name`.
