# Model selection: per-step pins

**Status:** Active. Each pipeline step reads its own `*_model` `app_settings`
pin and fails loud when it's unset. The earlier `cost_tier.<tier>.model`
indirection (a five-tier `free`/`budget`/`standard`/`premium`/`flagship` ladder
resolved by `resolve_tier_model()`) was **removed in PR #1907** in favour of
granular per-step control. The filename is kept for URL stability; the doc now
describes the per-step-pin model that replaced it.

> **Migrating from the tier API?** Replace `resolve_tier_model(pool, "standard")`
> at a call site with a direct read of that step's `*_model` pin (see the
> inventory below), and seed the pin in `settings_defaults.py` /
> `0000_baseline.seeds.sql`. The `cost_tier.*.model` rows are deleted by
> migration `20260623_210500_drop_cost_tier_model_seeds.py`.

## Why this exists

Pre-2026-05, ~22 production call sites embedded specific LLM model identifiers as
Python literals — `"gemma3:27b"`, `"llama3:latest"`, `"qwen3-coder:30b"`, etc.
Each was hardcoded for the model that worked best at that call site at that time.
The cost: every operator running a fork had to either accept Matt's specific
model selection or fork the code. There was no DB-tunable seam.

Per-step pins make model selection a configuration concern, not a code concern.
Each call site reads a **named `*_model` setting scoped to that step**, and the
operator's `app_settings` row decides which concrete model fires there. Tuning
one step never moves another.

The tier API was an intermediate step toward this (it replaced the literals with
a five-tier ladder), but the ladder coupled unrelated steps: changing
`cost_tier.standard.model` moved the writer, the critic, title-gen, podcast
scripts, social copy, and image-search-query gen all at once. Per-step pins
decouple them — the granularity the ladder gave up.

## Two decoupled axes — only one was removed

Model selection and provider selection are **separate** namespaces. Removing the
tier→model bridge did not touch the tier→provider one:

| Axis                | Key shape                            | Status                                                                                     |
| ------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------ |
| tier → **model**    | `cost_tier.<tier>.model`             | **Removed** (PR #1907). Each step reads its own `*_model` pin instead.                     |
| tier → **provider** | `plugin.llm_provider.primary.<tier>` | **Kept.** `dispatch_complete(..., tier="standard")` still selects the provider for a call. |

`dispatch_complete` still takes a `tier=` kwarg — it routes to the provider
(`ollama_native` / `litellm` / …) configured for that tier. The model passed to
it now comes from the call site's own pin, not from a tier→model lookup.

## The contract: fail loud, no silent default

Every step resolves its pin and **does not fall back to a hardcoded literal**.
Per [`feedback_no_silent_defaults.md`](../../CLAUDE.md), an unset pin is a
configuration bug, not a quiet default. The reaction depends on whether the step
is on the critical content path:

- **Critical steps** (writer, critic, the LLM-judge QA rails, hygiene-summary
  jobs) → `notify_operator(critical=True)` + raise. The pipeline halts loudly
  rather than degrade output quality silently.
- **Non-critical enhancers** (title regen, the media/video director stages, the
  image-gen prompt synthesiser) → log + soft-skip or return `None`, so a missing
  optional pin degrades that one enhancement without killing the post. Several
  still `notify_operator(critical=False)` so the gap is visible.

```python
# Critical-step shape (e.g. the QA critic):
model = (site_config.get("pipeline_critic_model") or "").strip()
if not model:
    await notify_operator(
        "multi_model_qa: pipeline_critic_model is unset — QA critic cannot run",
        critical=True, site_config=site_config,
    )
    raise RuntimeError("no critic model configured — set pipeline_critic_model")
return model.removeprefix("ollama/")  # OllamaClient wants the bare name
```

## Per-step pin inventory

Each model-selecting call site and the pin it reads. To move a step to a
different model, set its pin — nothing else is affected.

| Call site                                                | Pin (`app_settings` key)                             | On unset                                  |
| -------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------------- |
| `ai_content_generator` (writer)                          | `pipeline_writer_model` → `pipeline_fallback_model`  | critic rejects everything → check the pin |
| `multi_model_qa` critic + gate critics                   | `pipeline_critic_model` → `qa_fallback_critic_model` | notify(critical) + raise                  |
| `self_review.self_review_and_revise`                     | `writer_self_review_model`                           | notify + raise                            |
| `title_generation` (SEO title regen)                     | `pipeline_writer_model`                              | notify(advisory) + return None            |
| `podcast_service` (script generation)                    | `podcast_script_model` → `default_ollama_model`      | notify + regex-script fallback            |
| `image_service` (search-query gen)                       | `image_search_query_model`                           | notify + raise                            |
| `image_decision_agent` (image direction)                 | `model_role_image_decision`                          | notify + page                             |
| `image_providers.ai_generation` (image-gen prompt synth) | `image_prompt_model`                                 | soft fallback prompt                      |
| `video_service` (image-gen slideshow prompt)             | `video_slideshow_prompt_model`                       | notify(critical) + raise                  |
| `stages.generate_media_scripts`                          | `video_scene_model` → `default_ollama_model`         | skip stage (non-critical)                 |
| `stages.generate_video_shot_list` / `review_…`           | `video_director_model` → `video_scene_model`         | skip stage (non-critical)                 |
| `jobs.collapse_old_embeddings`                           | `embedding_collapse_summary_model`                   | notify(critical) + raise                  |
| `handlers.retention_summarize_to_table`                  | `memory_compression_summary_model`                   | notify(critical) + raise                  |
| `social_poster` (social copy gen)                        | `social_poster_fallback_model`                       | notify(critical) + raise                  |
| `ragas_eval` (judge model)                               | `ragas_judge_model`                                  | notify(critical) + raise                  |
| `deepeval_rails` (judge model)                           | `deepeval_judge_model`                               | notify(critical) + raise                  |

The two LLM-judge advisory rails (`ragas_eval`, `deepeval_rails`) and the two
hygiene-summary jobs (`collapse_old_embeddings`, `retention_summarize_to_table`)
should point at a **sub-writer-size** model (e.g. `ollama/phi4:14b`, ~8 GB) so
background/advisory work doesn't load the ~17 GB writer into VRAM. That contract
is pinned by `tests/unit/services/migrations/test_hygiene_summary_model_rightsize.py`.

## What didn't migrate (and why)

Some model references are deliberately **not** per-step pins:

- **Model-class detection** — sites doing `is_thinking_model(model)` branch on
  whether a model emits `<think>…</think>` blocks (a capability test), not a
  model choice. Routed through `services.llm_providers.thinking_models`, which
  reads `app_settings.thinking_model_substrings` (a JSON array).
- **Reference / canonical-default tables** — `cost_guard.py`'s energy-per-1K-Wh
  table, each provider plugin's `default_model`, and `settings_defaults.py`
  seed defaults. Already overridable per-row; not model-selection codepaths.

## How operators tune the system

### "I want the QA critic on a heavier local model"

```sql
UPDATE app_settings SET value = 'ollama/qwen2.5:72b' WHERE key = 'pipeline_critic_model';
```

Affects only the critic — the writer, title-gen, podcast, etc. are untouched
(each has its own pin). Under the old tier ladder this single change moved all of
them; that coupling is what per-step pins removed.

### "I want image-decision on a more accurate model"

```sql
UPDATE app_settings SET value = 'ollama/gemma3:27b' WHERE key = 'model_role_image_decision';
```

### "I want premium cloud QA for high-stakes critic calls"

Point the critic pin at a paid model; `cost_guard.py`'s daily/monthly cap fires
before the call, so you can't accidentally blow the budget:

```sql
UPDATE app_settings SET value = 'anthropic/claude-sonnet-4-6' WHERE key = 'pipeline_critic_model';
```

The provider for that call is selected separately via
`plugin.llm_provider.primary.<tier>` (the tier→provider axis, still in place).

### "I want to see which model fired for which call"

Every call goes through `cost_logs` (the LiteLLM cost-tracking path), which
records `model`, `prompt_tokens`, `completion_tokens`, `cost_usd`, and a
`purpose` column. The Cost dashboard in Grafana groups by purpose × model.

## Reasoning models and structured extraction

Independent of model selection, two guards handle reasoning models (e.g.
`glm-4.7-5090`) that can emit all their tokens into a thinking channel and return
an **empty `content`** field under JSON mode — which used to crash structured
extraction (`json.loads("")`):

1. **Separate model for structured extraction.** `resolve_structured_model()`
   reads `structured_extraction_model` (default `gemma3:27b`, a JSON-reliable
   instruct model) instead of the writer pin, so a reasoning writer doesn't break
   extraction:

   ```sql
   UPDATE app_settings SET value = 'ollama/gemma3:27b' WHERE key = 'structured_extraction_model';
   ```

2. **Reasoning-content fallback in the provider.** When `LiteLLMProvider` gets an
   empty `content` it recovers the payload from the response's `reasoning_content`
   (stripping any `<think>` wrapper). Toggle via
   `plugin.llm_provider.litellm.config.reasoning_content_fallback` (default
   `true`). It triggers on the observed empty-content symptom, so it catches new
   reasoning models even before they're added to `thinking_model_substrings`.

## Related docs

- [`docs/reference/app-settings.md`](../reference/app-settings.md) — every settings key, including the per-step `*_model` pins
- [`docs/architecture/services/litellm_provider.md`](services/litellm_provider.md) — the provider that consumes the resolved model + tracks cost
- [`docs/architecture/anti-hallucination.md`](anti-hallucination.md) — the QA rails whose judge models are per-step pins
