# Cost-tier model routing

**Status:** Active. Lane B of `Glad-Labs/poindexter#450` (the OSS migration umbrella). Batch 1 + batch 2 sweep #3 landed 2026-05-09; sweep #4 + end-of-Lane-B vestigial-param cleanup pending.

## Why this exists

Pre-2026-05-09, ~22 production call sites embedded specific LLM model identifiers as Python literals — `"gemma3:27b"`, `"gemma3:27b-it-qat"`, `"llama3:latest"`, `"qwen3-coder:30b"`, etc. Each was hardcoded for the model that worked best at that call site at that time. The cost: every operator who runs a fork of Poindexter had to either accept Matt's specific model selection or fork the code to swap it. There was no DB-tunable seam.

The cost-tier API makes the model selection a configuration concern, not a code concern. Every call site declares **what kind of model** it needs (small/free, mid/budget, default/standard, premium), and the operator's `app_settings` rows decide **which specific model** maps to that tier.

## The four tiers

| Tier       | Default model                | When to use                                                                                                |
| ---------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `free`     | `ollama/qwen3:8b`            | Smallest local, ~zero cost. Use for image-decision, search-query gen, anything where a 7B model is plenty. |
| `budget`   | `ollama/gemma3:27b-it-qat`   | Quantized 27B; fast local. Use for cold-data summarization, retention work, eval judges.                   |
| `standard` | `ollama/gemma3:27b`          | Default writer + critic. Use for blog generation, QA review, post-pipeline rewrites.                       |
| `premium`  | `anthropic/claude-haiku-4-5` | Cloud cross-model QA critic; cost_guard-gated. Use sparingly for high-stakes calls.                        |

The defaults are seeded by `services/migrations/20260509_203928_seed_cost_tier_model_mappings.py`. Operators tune by updating the row:

```sql
-- Pin the standard tier to a heavier local model
UPDATE app_settings
   SET value = 'ollama/qwen2.5:72b'
 WHERE key = 'cost_tier.standard.model';
```

The next call to `resolve_tier_model(pool, "standard")` picks up the new value (no container restart — `app_settings` is read-through, not cached).

## The API

### `services.llm_providers.dispatcher.resolve_tier_model(pool, tier)`

The bridge from `cost_tier="standard"` (what call sites speak) to `"ollama/gemma3:27b"` (what providers consume).

```python
from services.llm_providers.dispatcher import resolve_tier_model

model = await resolve_tier_model(pool, tier="standard")
# -> "ollama/gemma3:27b" (whatever the operator has configured)
```

**Contract:**

- Reads `app_settings.cost_tier.<tier>.model`. Strips whitespace.
- Raises `ValueError` if `tier` isn't one of `free` / `budget` / `standard` / `premium` / `flagship`.
- Raises `RuntimeError` if the row is missing or empty. **Does not silently fall back** — per `feedback_no_silent_defaults.md`, the absence of a tier mapping is a configuration bug, not a quiet fallback.
- Returns the LiteLLM-style identifier (e.g. `"ollama/gemma3:27b"`). Most call sites then `.removeprefix("ollama/")` because `OllamaClient` consumes the bare model name; LiteLLMProvider consumes the full identifier.

### `services.llm_providers.dispatcher.dispatch_complete(pool, messages, model, tier="standard")`

Pre-existing convenience wrapper that combines provider lookup + complete call. **Still expects an explicit `model=` argument** — the model resolution happens at the call site (you call `resolve_tier_model` first, then pass the result). Two-step rather than one-step because (a) some call sites need the model string for cost tracking before the provider call, (b) the LLMProvider interface is stable across the existing 4 implementations and shouldn't change for this migration.

## The fallback chain

Every migrated call site uses this exact pattern (ported from `feature/oss-lane-b-batch-1-qa-surface` and reused unchanged across the sweeps):

```python
from services.integrations.operator_notify import notify_operator
from services.llm_providers.dispatcher import resolve_tier_model

async def _resolve_critic_model(pool, settings) -> str:
    """Bridge cost_tier="standard" → concrete model id, with the
    qa_fallback_critic_model setting as a last-ditch fallback gated by
    notify_operator() per feedback_no_silent_defaults.md.
    """
    try:
        tier_model = await resolve_tier_model(pool, "standard")
        return tier_model.removeprefix("ollama/")
    except (RuntimeError, ValueError):
        # Tier mapping missing — try the per-call-site fallback.
        fallback = await settings.get("qa_fallback_critic_model")
        if not fallback:
            await notify_operator(
                "qa critic: tier='standard' has no model AND "
                "qa_fallback_critic_model is empty — review skipped"
            )
            raise
        return fallback.removeprefix("ollama/")
```

**Why a fallback exists at all:** the cost-tier rows are seeded by migration but operators may transiently misconfigure them (typo, accidentally cleared the row, network hiccup during reload). The per-call-site fallback key is the safety net — but it pages the operator on miss, so a silent, persistent misconfiguration can't hide.

**What the fallback is NOT:** it's not a "default model" path. The fallback row in `app_settings` is also operator-tunable, and most fallbacks already have non-empty defaults seeded. The fallback exists so a tier-row outage doesn't silently degrade output quality without alerting the operator.

## Where the migrated call sites live

Each call site in the codebase that selects a model now uses the resolve-tier-model + fallback pattern. The mapping of tier-to-call-site is intentional, not arbitrary — see the inventory at `.shared-context/migrations/2026-05-09-lane-b-model-inventory.md` for the per-occurrence rationale.

| Call site                                                    | Tier       | Per-call-site fallback key                               |
| ------------------------------------------------------------ | ---------- | -------------------------------------------------------- |
| `multi_model_qa._review_with_ollama` (cross-model QA critic) | `standard` | `qa_fallback_critic_model`                               |
| `multi_model_qa._run_gate_prompt` (gate critics)             | `standard` | `qa_fallback_critic_model`                               |
| `multi_model_qa._review_with_cloud_model` (fallback)         | `standard` | `qa_fallback_critic_model`                               |
| `stages/cross_model_qa._rewrite_draft` (writer rewrite)      | `standard` | `pipeline_writer_model` / `qa_fallback_writer_model`     |
| `self_review.self_review_and_revise`                         | `standard` | `writer_self_review_model`                               |
| `title_generation`                                           | `standard` | `pipeline_writer_model`                                  |
| `podcast_service` (script generation)                        | `standard` | `pipeline_writer_model` (3-stage waterfall)              |
| `image_service` (search-query gen)                           | `standard` | `image_search_query_model`                               |
| `image_decision_agent` (image direction)                     | `budget`   | `model_role_image_decision`                              |
| `jobs.collapse_old_embeddings`                               | `budget`   | `embedding_collapse_summary_model`                       |
| `integrations.handlers.retention_summarize_to_table`         | `budget`   | `memory_compression_summary_model`                       |
| `social_poster` (social copy gen)                            | `standard` | `social_poster_fallback_model` (Lane B batch 2 sweep #4) |
| `video_service` (SDXL prompt gen)                            | `standard` | `video_slideshow_prompt_model` (Lane B batch 2 sweep #4) |
| `ragas_eval` (judge model)                                   | `budget`   | `ragas_judge_model`                                      |

## What didn't migrate (and why)

Per the Lane B inventory, several occurrences are deliberately NOT routed through `resolve_tier_model`:

- **`task_executor.py:1362`** — `qwen3-coder:30b` for retry-writer adjustment. This is intent-based ("switch writers on retry"), not a tier choice. Migrated to a named setting (`task_executor_first_retry_writer_model`) instead.
- **Model-class detection sites** (5 sites doing `if "qwen3" in model.lower()`) — these branch on whether a model is a "thinking" model (produces `<think>...</think>` blocks). NOT a tier migration target. A separate `is_thinking_model` registry is on the deferred backlog.
- **Reference / canonical-default tables** — `cost_guard.py` energy-per-1K-Wh table, `plugins/llm_providers/anthropic.py:_PER_MODEL_RATES`, `plugins/llm_providers/<name>.default_model`, `services/settings_defaults.py` seed defaults. Already overridable per-row via the existing `app_settings` paths; not fallback codepaths.

## How operators tune the system

Day-to-day operator workflows after Lane B:

### "I want the QA critic to use a heavier local model"

```sql
UPDATE app_settings SET value = 'ollama/qwen2.5:72b' WHERE key = 'cost_tier.standard.model';
```

Affects: cross-model QA, writer rewrites, self-review, title generation, podcast script gen, image-search-query gen, social copy. All in one row.

### "I want image-decision to use a heavier model (more accurate decisions)"

```sql
UPDATE app_settings SET value = 'ollama/gemma3:27b' WHERE key = 'cost_tier.budget.model';
```

Affects: image-decision agent, retention summarization, eval judges. The image-decision call alone would need a per-call-site override:

```sql
UPDATE app_settings SET value = 'ollama/gemma3:27b' WHERE key = 'model_role_image_decision';
```

Per-call-site keys win when both are set (the call-site fallback IS the override; tier is the default).

### "I want premium tier to actually use Claude Sonnet for high-stakes critic calls"

```sql
UPDATE app_settings SET value = 'anthropic/claude-sonnet-4-6' WHERE key = 'cost_tier.premium.model';
```

The `cost_guard.py` daily/monthly cost cap fires before the call to a paid cloud model — you can't accidentally blow the budget.

### "I want to see which model fired for which call"

Every call goes through `cost_logs` (the LiteLLM cost-tracking path). The `cost_logs` table records `model`, `prompt_tokens`, `completion_tokens`, `cost_usd`. Per-call-site filtering is via the `purpose` column. The Cost dashboard in Grafana groups by purpose × model.

## Open questions / future work

- ~~**End-of-Lane-B cleanup:** delete the 5 vestigial `model_router=None` ctor params at `quality_service.py:117/887/897`, `firefighter_service.py:268`, `agents/blog_quality_agent.py:31/138`. They accept any duck-typed router-like object now (the deleted `model_router` module is gone), but the param signatures are noise.~~ Landed 2026-05-09. `quality_service.py` + `agents/blog_quality_agent.py` cleaned (4 occurrences across 2 files + 1 test). `firefighter_service.py:268`'s `model_router` param was _not_ vestigial — it accepts a duck-typed `_ModelRouterLike` Protocol injected from `routes/triage_routes.py` (a function reference for testability, unrelated to the deleted `services/model_router.py` module). The original inventory entry conflated the two; corrected.
- **`is_thinking_model` registry:** the 5 model-class detection sites (`if "qwen3" in model.lower()`) hardcode a list of thinking-model identifiers. A small registry table or app_settings JSON would let operators add/remove thinking models without code changes.
- **LiteLLMProvider as the standard provider:** today `cost_tier.standard.model` defaults to `ollama/gemma3:27b` and the dispatcher's `plugin.llm_provider.primary.standard` defaults to `ollama_native`. Once LiteLLMProvider is the default for all tiers, the cost-tracking + retry plumbing inherits for free.

## Related docs

- `.shared-context/migrations/2026-05-09-lane-b-model-inventory.md` — full per-file audit with bucket-A / bucket-B / bucket-C categorization
- `docs/reference/app-settings.md` — all settings keys including the cost_tier rows
- `Glad-Labs/poindexter#450` — the OSS migration umbrella
- `Glad-Labs/poindexter#199` — the original LiteLLM cost/usage layer migration (closed)
