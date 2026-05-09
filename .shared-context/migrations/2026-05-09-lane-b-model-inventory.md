# Lane B Model-Name Inventory — 2026-05-09

> Read-only audit. Source for the Lane B sweep dispatch plan.
>
> Note: The umbrella plan referenced in the task (`2026-05-09-oss-migration-plan.md`)
> is not present in `.shared-context/migrations/`. This inventory is being delivered
> standalone; the sweep agents downstream can consume it directly.

## Summary

- Files scanned: **40 non-test Python files** (39 under `src/cofounder_agent/`
  - 1 under `brain/`). Audit count of 36 is mildly stale — confirmed 40
    actually carry hardcoded model strings.
- **Bucket A** (runtime call sites that should migrate to `cost_tier=`):
  **22 occurrences** across **11 files**
- **Bucket B** (defaults / fallbacks that already have or need an
  app_settings key): **30 occurrences** across **13 files**
- **Bucket C** (test/util/dev-only references — docstrings, comments,
  CLI help, alert text, schema field descriptions, dev scripts):
  **39 occurrences** across **24 files**
- Files already partially migrated to LiteLLMProvider: **2**
  (`services/llm_providers/litellm_provider.py` itself, and
  `services/llm_providers/dispatcher.py`'s docstring example)
- Vestigial `model_router=None` ctor params still present: **5 sites**:
  - `services/quality_service.py:117` (constructor)
  - `services/quality_service.py:887` (factory `get_quality_service`)
  - `services/quality_service.py:897` (factory `get_unified_quality_service`)
  - `services/firefighter_service.py:268` (function `run_triage`)
  - `agents/blog_quality_agent.py:31` + `:138`

These accept any duck-typed router-like object now (the actual
`model_router.py` module was deleted 2026-05-08 per CLAUDE.md). Deletion
is end-of-Lane-B housekeeping — no behavior change, just remove the dead
parameter.

## Recommended sweep dispatches

Group bucket-A and migration-eligible bucket-B literals into 4 logical
surfaces. Each surface = one sweep agent. All dispatches are scoped to
not exceed Matt's 3-agent ceiling — run as 2 batches of 2.

### Batch 1 (parallelizable)

1. **QA / critic surface** — `services/multi_model_qa.py`,
   `services/stages/cross_model_qa.py`, `services/self_review.py`,
   `services/stages/writer_self_review.py`. Migrates the cross-model
   critic + writer-self-review default literals (`"gemma3:27b"`) to
   `cost_tier="standard"` calls via the existing LiteLLMProvider /
   ollama_native dispatcher. Preserves the `qa_fallback_critic_model`
   and `qa_fallback_writer_model` settings as last-ditch fallbacks
   gated by `notify_operator()` per `feedback_no_silent_defaults.md`.
   Touches ~10 occurrences. Highest-traffic surface.

2. **Writer / content surface** — `services/title_generation.py`,
   `services/podcast_service.py`, `services/image_service.py`,
   `services/image_decision_agent.py`. All read
   `pipeline_writer_model` / `default_ollama_model` /
   `model_role_image_decision` from site_config and `removeprefix`
   the result. Migrate to `cost_tier="standard"` (writer/title/podcast)
   or `cost_tier="budget"` (image-decision is a small qwen3:8b call).

### Batch 2 (parallelizable)

3. **Retention / housekeeping jobs** — `services/jobs/collapse_old_embeddings.py`,
   `services/integrations/handlers/retention_summarize_to_table.py`.
   Both default to `gemma3:27b-it-qat` for offline summary work.
   Migrate to `cost_tier="budget"` (these run on cold data, latency
   doesn't matter; budget tier should be a cheaper / smaller model).

4. **Misc / leaf utilities** — `services/social_poster.py` (default
   `ollama/llama3:latest`), `services/video_service.py` (hardcoded
   `llama3:latest` for SDXL prompt gen), `services/task_executor.py:1362`
   (hardcoded `qwen3-coder:30b` for retry adjustment),
   `services/ai_content_generator.py` (model-class detection for
   thinking models — special-case, see Open Questions),
   `services/ragas_eval.py` (judge model `llama3:8b`).

   Most of these are 1-2 line touches. The model-class detection
   occurrences (`is_thinking_model = any(t in model.lower() for t in
("qwen3", "glm-4", "deepseek-r1"))`) are a special pattern — they
   don't pick a model, they branch on a model identity. Treat as
   "leave in place pending a `is_thinking_model` setting/registry".

### Out-of-scope for the Lane B sweep

- `services/cost_guard.py:82-110` — canonical energy-per-1K-Wh table
  for cloud SKUs. Already DB-overridable per-model via
  `plugin.llm_provider.<provider>.model.<model>.energy_per_1k_wh`.
  Literals are reference defaults, NOT a fallback codepath. Leave
  as-is.
- `services/cost_lookup.py:85` — comment text in a LiteLLM lookup
  helper. Bucket C.
- `plugins/llm_providers/anthropic.py:206-208` — `_PER_MODEL_RATES`
  dict, provider-internal cost reference matching the cost_guard
  pattern. Already supports `app_settings.plugin.llm_provider.anthropic.rate_overrides`.
  Bucket B (canonical-default-with-override). Leave as-is.
- `plugins/llm_providers/anthropic.py:367` + `plugins/llm_providers/gemini.py:135`
  — provider plugin's own `default_model`. The provider IS the abstraction
  layer; its default_model is configured via `plugin.llm_provider.<name>.default_model`.
  Already correct path. Leave as-is.
- `services/settings_defaults.py:82-92` — DB seed defaults. The
  RIGHT place for these literals to live. Leave as-is.

## Tier mapping seeds needed

The existing `qa_fallback_critic_model`, `qa_fallback_writer_model`,
`pipeline_writer_model`, `pipeline_critic_model`, `default_ollama_model`,
`model_role_image_decision`, `writer_self_review_model`,
`podcast_script_model`, `image_search_query_model`,
`embedding_collapse_summary_model`, `social_poster_model`,
`ragas_judge_model` keys all already exist (most seeded in
`settings_defaults.py`).

What does NOT yet exist is the **cost-tier mapping layer** — the bridge
between `cost_tier="standard"` calls and the concrete model-string above.
The Lane B sweep needs these 4 keys seeded BEFORE any call site is
migrated:

| Settings key               | Tier     | Proposed default literal     | Rationale                                                         |
| -------------------------- | -------- | ---------------------------- | ----------------------------------------------------------------- |
| `cost_tier.free.model`     | free     | `ollama/qwen3:8b`            | Smallest local, used for image-decision today                     |
| `cost_tier.budget.model`   | budget   | `ollama/gemma3:27b-it-qat`   | Quantized 27B; fast local; current retention default              |
| `cost_tier.standard.model` | standard | `ollama/gemma3:27b`          | Current `pipeline_writer_model` + `pipeline_critic_model` default |
| `cost_tier.premium.model`  | premium  | `anthropic/claude-haiku-4-5` | Current cross-model QA cloud critic; cost_guard-gated             |

In addition, two **per-call-site fallback keys** are missing and should
be seeded for the no-silent-defaults guarantee:

| Settings key                             | Tier     | Current literal        | Source file:line                 |
| ---------------------------------------- | -------- | ---------------------- | -------------------------------- |
| `social_poster_fallback_model`           | budget   | `ollama/llama3:latest` | `services/social_poster.py:97`   |
| `video_slideshow_prompt_model`           | budget   | `llama3:latest`        | `services/video_service.py:93`   |
| `task_executor_first_retry_writer_model` | standard | `qwen3-coder:30b`      | `services/task_executor.py:1362` |

## Per-file detail

### `services/multi_model_qa.py`

| Line       | Snippet                                                                  | Bucket | Notes                                                                                    |
| ---------- | ------------------------------------------------------------------------ | ------ | ---------------------------------------------------------------------------------------- |
| 294        | docstring example `pipeline_critic_model = "anthropic/claude-haiku-4-5"` | C      | Class docstring                                                                          |
| 777        | `fallback_model = "gemma3:27b"`                                          | B      | Already has `qa_fallback_critic_model` settings key — fine; add `notify_operator` on hit |
| 829        | docstring `Uses gemma3:27b by default`                                   | C      | Method docstring                                                                         |
| 853        | comment `qwen3:30b can legitimately take ~60s`                           | C      | Inline comment                                                                           |
| 893-898    | `default_model = "gemma3:27b"` + comment                                 | A      | Migrate `_review_with_ollama` to `cost_tier="standard"` via dispatcher                   |
| 908        | `is_thinking_model = any(t in ollama_model.lower() ...)`                 | A\*    | Special: model-class detection. Defer pending `is_thinking_model` registry               |
| 1028-1029  | `default_model = "gemma3:27b"` (gate prompt fallback)                    | B      | Same `qa_fallback_critic_model`; same `notify_operator` recommendation                   |
| 1290       | docstring `qwen3-vl:30b by default`                                      | C      |                                                                                          |
| 1298       | docstring `qa_vision_model — default "qwen3-vl:30b"`                     | C      |                                                                                          |
| 1310, 1319 | `model = "qwen3-vl:30b"` + settings read                                 | B      | Has `qa_vision_model` setting key; add `notify_operator` on the literal-fallback branch  |
| 1507, 1518 | same pattern for `qa_preview_vision_model`                               | B      | Same                                                                                     |

### `services/stages/cross_model_qa.py`

| Line     | Snippet                                                     | Bucket | Notes                                                                     |
| -------- | ----------------------------------------------------------- | ------ | ------------------------------------------------------------------------- |
| 20       | docstring `default ``gemma3:27b```                          | C      |                                                                           |
| 583      | `or "gemma3:27b"` — primary writer fallback                 | A      | Migrate to `cost_tier="standard"` writer call                             |
| 604, 611 | log + audit text `gemma3:27b`                               | C      | Just log strings (the audit_log payload should also become tier-aware)    |
| 621-622  | `site_config.get("qa_fallback_writer_model", "gemma3:27b")` | B      | Already has settings key; add `notify_operator` if literal-fallback fires |

### `services/self_review.py`

| Line | Snippet                                                       | Bucket | Notes                                                                                    |
| ---- | ------------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------- |
| 64   | `site_config.get("writer_self_review_model") or "gemma3:27b"` | A      | Migrate to `cost_tier="standard"`; preserve setting fallback path with `notify_operator` |

### `services/stages/writer_self_review.py`

| Line | Snippet                                 | Bucket | Notes            |
| ---- | --------------------------------------- | ------ | ---------------- |
| 5    | docstring `qwen3:30b's tendency to ...` | C      | Module docstring |

### `services/title_generation.py`

| Line | Snippet                                                    | Bucket | Notes                                                 |
| ---- | ---------------------------------------------------------- | ------ | ----------------------------------------------------- |
| 141  | `site_config.get("pipeline_writer_model") or "gemma3:27b"` | A      | Migrate to `cost_tier="standard"` via LiteLLMProvider |

### `services/podcast_service.py`

| Line | Snippet                                                    | Bucket | Notes                                                              |
| ---- | ---------------------------------------------------------- | ------ | ------------------------------------------------------------------ |
| 329  | `or site_config.get("default_ollama_model", "gemma3:27b")` | A      | Migrate to `cost_tier="standard"`; podcast-specific override stays |
| 332  | same fallback in except branch                             | B      | `notify_operator` if literal-fallback fires                        |
| 334  | `model = "gemma3:27b"` (when `model == "auto"`)            | B      | Add `notify_operator`                                              |
| 366  | comment `qwen3:30b/glm-4.7 on a 5090`                      | C      |                                                                    |

### `services/image_service.py`

| Line | Snippet                                             | Bucket | Notes                                                             |
| ---- | --------------------------------------------------- | ------ | ----------------------------------------------------------------- |
| 635  | `_sc.get("image_search_query_model", "gemma3:27b")` | A      | Migrate to `cost_tier="budget"` (3-5 word query gen — small task) |

### `services/image_decision_agent.py`

| Line     | Snippet                                                                      | Bucket | Notes                                                   |
| -------- | ---------------------------------------------------------------------------- | ------ | ------------------------------------------------------- |
| 85       | `site_config.get("model_role_image_decision", "qwen3:8b").removeprefix(...)` | A      | Migrate to `cost_tier="free"` or `"budget"`; small task |
| 167, 211 | comments about qwen3 thinking-model handling                                 | C      |                                                         |

### `services/jobs/collapse_old_embeddings.py`

| Line | Snippet                                                                       | Bucket | Notes                                 |
| ---- | ----------------------------------------------------------------------------- | ------ | ------------------------------------- |
| 436  | `_get_setting(pool, "embedding_collapse_summary_model", "gemma3:27b-it-qat")` | A      | Migrate to `cost_tier="budget"`       |
| 502  | `summary_model: str = "gemma3:27b-it-qat"` (function param default)           | B      | Caller-side default; keep as backstop |

### `services/integrations/handlers/retention_summarize_to_table.py`

| Line | Snippet                                        | Bucket | Notes                                                                                                             |
| ---- | ---------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------- |
| 92   | `_DEFAULT_SUMMARY_MODEL = "gemma3:27b-it-qat"` | B      | Module-level constant; should read from site_config + tier mapping; add `notify_operator` if literal default used |

### `services/social_poster.py`

| Line | Snippet                                                          | Bucket | Notes                                                 |
| ---- | ---------------------------------------------------------------- | ------ | ----------------------------------------------------- |
| 97   | `site_config.get("social_poster_model", "ollama/llama3:latest")` | A      | Migrate to `cost_tier="budget"`; preserve setting key |

### `services/video_service.py`

| Line | Snippet                            | Bucket | Notes                                                                                    |
| ---- | ---------------------------------- | ------ | ---------------------------------------------------------------------------------------- |
| 92   | comment about thinking-model issue | C      |                                                                                          |
| 93   | `model = "llama3:latest"`          | A      | Hardcoded — propose new setting `video_slideshow_prompt_model` + tier mapping (`budget`) |

### `services/task_executor.py`

| Line | Snippet                                                            | Bucket | Notes                                                                                  |
| ---- | ------------------------------------------------------------------ | ------ | -------------------------------------------------------------------------------------- |
| 521  | comment `qwen2.5:72b or llama3.3:70b without a redeploy`           | C      | Inline comment                                                                         |
| 1362 | `adjustments = {"model_selections": {"draft": "qwen3-coder:30b"}}` | B      | Hard-coded retry-1 model. Propose new setting `task_executor_first_retry_writer_model` |

### `services/ai_content_generator.py`

| Line    | Snippet                                                                                         | Bucket | Notes                                                             |
| ------- | ----------------------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------------- |
| 607     | `_is_thinking_refine = any(t in model_name.lower() for t in ("qwen3", "glm-4", "deepseek-r1"))` | A\*    | Model-class detection; defer pending `is_thinking_model` registry |
| 774-776 | comment + `_is_thinking = any(...)` repetition                                                  | A\*    | Same — defer                                                      |
| 999     | docstring `e.g., 'qwen3.5:35b'`                                                                 | C      |                                                                   |

### `services/ollama_client.py`

| Line    | Snippet                                       | Bucket | Notes            |
| ------- | --------------------------------------------- | ------ | ---------------- |
| 14      | docstring `Pull models: ollama pull qwen3:8b` | C      | Module docstring |
| 256-257 | comment about qwen2.5:72b vs gemma3:27b       | C      |                  |
| 512     | comment about qwen3 thinking-model split      | C      |                  |

### `services/ollama_resilience.py`

| Line | Snippet                    | Bucket | Notes           |
| ---- | -------------------------- | ------ | --------------- |
| 145  | docstring `qwen3, glm-4.7` | C      | Class docstring |

### `services/ragas_eval.py`

| Line                                                                                         | Snippet                                         | Bucket                                                                              | Notes |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------- | ----- |
| 66                                                                                           | comment `glm-4.7 / qwen3 variants are stronger` | C                                                                                   |       |
| (68 not in grep) `judge_model = "llama3:8b"` line is the literal default — confirmed in read | B                                               | `ragas_judge_model` setting already covers it; add `notify_operator` on literal hit |

### `services/llm_providers/dispatcher.py`

| Line | Snippet                        | Bucket | Notes                          |
| ---- | ------------------------------ | ------ | ------------------------------ |
| 16   | docstring `model="gemma3:27b"` | C      | Module docstring usage example |

### `services/llm_providers/litellm_provider.py`

| Line    | Snippet                                                                    | Bucket | Notes                                              |
| ------- | -------------------------------------------------------------------------- | ------ | -------------------------------------------------- |
| 230-238 | docstring listing `ollama/glm-4.7-5090:latest`, `openai/gpt-4o-mini`, etc. | C      | Class docstring (LiteLLM model-string conventions) |
| 294-295 | docstring example                                                          | C      |                                                    |

### `services/cost_guard.py`

| Line   | Snippet                               | Bucket | Notes                                                                        |
| ------ | ------------------------------------- | ------ | ---------------------------------------------------------------------------- |
| 62     | comment `gpt-4o input is ~$0.0025/1K` | C      |                                                                              |
| 84-108 | `_DEFAULT_CLOUD_ENERGY_WH_PER_1K`     | B      | Canonical energy table; already DB-overridable per-model; **leave in place** |

### `services/cost_lookup.py`

| Line | Snippet                                      | Bucket | Notes          |
| ---- | -------------------------------------------- | ------ | -------------- |
| 85   | comment `claude-haiku-4-5 not anthropic/...` | C      | Inline comment |

### `services/cost_aggregation_service.py`

| Line | Snippet                              | Bucket | Notes            |
| ---- | ------------------------------------ | ------ | ---------------- |
| 212  | docstring example `"model": "gpt-4"` | C      | Method docstring |

### `services/content_router_service.py`

| Line    | Snippet                                                    | Bucket | Notes |
| ------- | ---------------------------------------------------------- | ------ | ----- |
| 286-287 | comment about pipeline_writer_model vs gemma3:27b mismatch | C      |       |

### `services/decision_service.py`

| Line | Snippet                                   | Bucket | Notes            |
| ---- | ----------------------------------------- | ------ | ---------------- |
| 23   | docstring example `model_used="qwen3:8b"` | C      | Module docstring |

### `services/audit_log.py`

| Line | Snippet                                             | Bucket | Notes            |
| ---- | --------------------------------------------------- | ------ | ---------------- |
| 16   | docstring example `{"model": "ollama/qwen3.5:35b"}` | C      | Module docstring |

### `services/topic_discovery.py`

| Line | Snippet                                                       | Bucket | Notes          |
| ---- | ------------------------------------------------------------- | ------ | -------------- |
| 603  | comment `If gemma3:27b wrote a bad post about Kubernetes ...` | C      | Inline comment |

### `services/pipeline_architect.py`

| Line | Snippet                               | Bucket | Notes            |
| ---- | ------------------------------------- | ------ | ---------------- |
| 10   | docstring `glm-4.7-5090 or qwen3:30b` | C      | Module docstring |

### `services/prometheus_rule_builder.py`

| Line | Snippet                                            | Bucket | Notes                                              |
| ---- | -------------------------------------------------- | ------ | -------------------------------------------------- |
| 208  | alert description `Run \`ollama pull gemma3:27b\`` | C      | Alert message text — could become tier-aware later |

### `services/phases/base_phase.py`

| Line | Snippet                                                       | Bucket | Notes            |
| ---- | ------------------------------------------------------------- | ------ | ---------------- |
| 106  | docstring example `{"content": "...", "model_used": "gpt-4"}` | C      | Method docstring |

### `services/settings_defaults.py`

| Line | Snippet                                    | Bucket | Notes                                              |
| ---- | ------------------------------------------ | ------ | -------------------------------------------------- |
| 82   | `'default_ollama_model': 'gemma3:27b'`     | B      | DB seed default — correct path; **leave in place** |
| 88   | `'model_role_image_decision': 'qwen3:8b'`  | B      | DB seed default — correct path; **leave in place** |
| 90   | `'pipeline_fallback_model': 'gemma3:27b'`  | B      | DB seed default — correct path; **leave in place** |
| 91   | `'pipeline_writer_model': 'gemma3:27b'`    | B      | DB seed default — correct path; **leave in place** |
| 92   | `'qa_fallback_writer_model': 'gemma3:27b'` | B      | DB seed default — correct path; **leave in place** |

### `agents/blog_content_generator_agent.py`

| Line | Snippet                                                                        | Bucket | Notes          |
| ---- | ------------------------------------------------------------------------------ | ------ | -------------- |
| 78   | comment `UI sends "model" as "provider-modelname" (e.g., "ollama-gemma3:12b")` | C      | Inline comment |

### `plugins/llm_provider.py`

| Line | Snippet                                          | Bucket | Notes            |
| ---- | ------------------------------------------------ | ------ | ---------------- |
| 95   | docstring example `"gemma3:27b" for Ollama, ...` | C      | Method docstring |

### `plugins/llm_providers/anthropic.py`

| Line    | Snippet                                       | Bucket | Notes                                                                                                    |
| ------- | --------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------- |
| 18, 57  | docstring `Default model is claude-haiku-4-5` | C      | Module/class docstrings                                                                                  |
| 206-208 | `_PER_MODEL_RATES` dict                       | B      | Provider-internal cost reference; already DB-overridable; **leave in place**                             |
| 222     | comment `claude-haiku-4-5-20260301`           | C      |                                                                                                          |
| 367     | `"default_model": "claude-haiku-4-5"`         | B      | Provider's own default; correct path (`plugin.llm_provider.anthropic.default_model`); **leave in place** |

### `plugins/llm_providers/gemini.py`

| Line   | Snippet                               | Bucket | Notes                                                    |
| ------ | ------------------------------------- | ------ | -------------------------------------------------------- |
| 18, 23 | docstring                             | C      |                                                          |
| 135    | `_DEFAULT_MODEL = "gemini-2.5-flash"` | B      | Provider's own default; correct path; **leave in place** |

### `schemas/database_response_models.py`

| Line | Snippet                                                   | Bucket | Notes                  |
| ---- | --------------------------------------------------------- | ------ | ---------------------- |
| 291  | Pydantic Field description `(gpt-4, claude-3-opus, etc.)` | C      | Schema field docstring |

### `routes/memory_dashboard_routes.py`

| Line | Snippet                                                                   | Bucket | Notes                     |
| ---- | ------------------------------------------------------------------------- | ------ | ------------------------- |
| 324  | HTML placeholder `Natural language query (e.g. "why did we pick gemma3")` | C      | Frontend placeholder text |

### `poindexter/memory/__init__.py`

| Line   | Snippet           | Bucket | Notes            |
| ------ | ----------------- | ------ | ---------------- |
| 16, 24 | docstring example | C      | Module docstring |

### `poindexter/cli/__init__.py`

| Line | Snippet                                                             | Bucket | Notes               |
| ---- | ------------------------------------------------------------------- | ------ | ------------------- |
| 8    | docstring example `python -m poindexter memory search "why gemma3"` | C      | CLI usage docstring |

### `poindexter/cli/memory.py`

| Line     | Snippet                       | Bucket | Notes              |
| -------- | ----------------------------- | ------ | ------------------ |
| 108, 199 | CLI help / docstring examples | C      | Argparse help text |

### `scripts/verify_cost_guard.py`

| Line   | Snippet                                          | Bucket | Notes                                                   |
| ------ | ------------------------------------------------ | ------ | ------------------------------------------------------- |
| 29, 40 | `model="gemma3:27b"`, `model="gemini-2.5-flash"` | C      | One-shot dev script that synthesizes cost-guard records |

### `brain/health_probes.py`

| Line | Snippet                                 | Bucket | Notes                                                                                                                                                                                |
| ---- | --------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 263  | `"model": "qwen3-coder:30b"` in payload | A      | Brain health-probe; should call via dispatcher with `cost_tier="free"` (smallest probe) — but brain runs independent of FastAPI's site_config so dispatcher availability needs check |

## Special cases / open questions

1. **Model-class detection (thinking vs non-thinking).** Five sites
   (`ai_content_generator.py:607,776`, `multi_model_qa.py:908`,
   `image_decision_agent.py:167`) branch on `if "qwen3" in
model.lower() or "glm-4" in model.lower() ...`. They don't pick a
   model — they adapt token budget / prompting based on what was
   chosen. These should NOT migrate to `cost_tier=` because that's
   a parallel concern. **Recommendation:** introduce a separate
   registry `is_thinking_model(model_name) -> bool` keyed on model
   name patterns + an `app_settings.thinking_model_patterns` override
   list. Out of scope for the Lane B sweep — flag for a follow-up
   issue.

2. **`brain/health_probes.py` operates outside FastAPI lifespan.**
   The brain daemon does not have access to the SiteConfig DI seam
   the way services do. Either (a) brain probe stays bucket-A but
   uses its own minimal direct settings query (existing pattern in
   the brain tree), or (b) the probe deliberately stays on a fixed
   small model (`qwen3-coder:30b` is intentional to verify a known
   model is loaded). **Recommendation:** confirm with Matt before
   migrating — could be deliberate.

3. **`task_executor.py:1362` retry-1 writer.** The hardcoded
   `qwen3-coder:30b` is a deliberate "switch writers on retry"
   behavior — not a fallback per se, but a _strategy_. Migrating
   this to a `cost_tier=` doesn't capture intent. Better as
   a new app_setting `task_executor_first_retry_writer_model`
   with a clear name explaining what it's for.

4. **`services/video_service.py:93` `model = "llama3:latest"`** —
   commented `Use llama3 for prompt generation — some models (glm,
qwen thinking mode) return empty`. This is **deliberate**
   anti-thinking-model selection. A `cost_tier="budget"` migration
   could land on a thinking model and break this. **Recommendation:**
   migrate but explicitly add a `prefer_non_thinking=True` flag, or
   add a `non_thinking_budget` synonym tier.

5. **No umbrella plan file exists.** The task references
   `.shared-context/migrations/2026-05-09-oss-migration-plan.md` as
   the parent doc, but the `.shared-context/migrations/` directory
   does not exist on this branch (had to be created). Either the
   plan was committed elsewhere, or the umbrella write-up is still
   forthcoming. This inventory stands alone; suggest the umbrella
   author cross-reference this doc when finalizing the parent plan.

6. **`anthropic.py._PER_MODEL_RATES` and `cost_guard._DEFAULT_CLOUD_ENERGY_WH_PER_1K`
   are not pure bucket-B fallbacks.** They're canonical reference
   tables — the place where a literal SHOULD live for one-by-one
   override. They map model name → numeric rate, not "what model
   should I call?". Lane B should leave them alone. Calling them
   bucket B is a categorical compromise — they're more like
   "infrastructure data, parsable as B."

7. **Vestigial `model_router=None` cleanup is independent of Lane B
   sweep but cheap to bundle.** The 5 sites listed in the summary
   accept any duck-typed object since the underlying module was
   deleted. Removing the parameter is a 5-line PR with zero
   behavior change — could be batched into Dispatch 4 (Misc)
   without raising risk.
