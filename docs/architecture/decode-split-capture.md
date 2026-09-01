# The decode/prefill split — what it measures, and why it needs a watchdog

`cost_logs.duration_ms` is **wall-clock call time**: it includes GPU-lock wait,
model load/eviction, prompt prefill, and decode. Divide output tokens by it and
you get _effective_ throughput — what the application received — which is not
the number any published benchmark reports.

Ollama reports the breakdown (`eval_duration` = decode ns, `prompt_eval_duration`
= prefill ns), but **LiteLLM's Ollama transformations map only the token counts
into `Usage` and drop the durations**. `services/llm_providers/ollama_timings.py`
recovers them; the dispatcher persists them as `cost_logs.decode_duration_ms`
and `prefill_duration_ms` (migration `20260826_040508`, live since 04:05 UTC on
2026-08-26).

## Two different numbers, and the gap between them is the interesting one

| Column                               | What it measures                                            |
| ------------------------------------ | ----------------------------------------------------------- |
| `output_tokens / duration_ms`        | what the caller actually got — queue, load, prefill and all |
| `output_tokens / decode_duration_ms` | true decode speed — comparable to a published benchmark     |

Measured over production calls, the two diverge enormously and _unevenly_:

| model                | decode tok/s | delivered tok/s | tax     |
| -------------------- | ------------ | --------------- | ------- |
| `qwen2.5:7b`         | 235.1        | 55.6            | 76%     |
| `glm-4.7-5090`       | 177.0        | 172.2           | **3%**  |
| `qwen3-vl:30b`       | 162.8        | 84.8            | 48%     |
| `phi4:14b`           | 124.7        | 25.3            | **80%** |
| `gemma-4-31B-it-qat` | 62.2         | 36.1            | 42%     |

The mechanism is **residency**, not model speed: `glm-4.7` stays pinned and warm
(median overhead 0.6 s), while `phi4` is an intermittently-called critic that is
evicted and reloaded almost every time (median overhead 8.9 s). Read that table
as "what this workload delivers given how often each model is resident" — never
as "phi4 is slow".

## Coverage is not backfillable

Rows written before 2026-08-26 04:05 UTC have `NULL` and always will —
the duration was never returned to us, so there is nothing to recompute from.
`NULL` means **"not reported"**, never `0` (`feedback_no_dummy_data`). Rows that
legitimately carry `NULL`:

- **Cloud models.** `anthropic/claude-sonnet-5` has no Ollama timing split.
- **Failed calls.** A GPU-lock timeout or connection error decodes nothing.
- **Zero-output calls.** No decode phase happened.

Excluding those, coverage on Ollama-routed successful calls has been **100.0%**
every day since the deploy.

## Why it needs a watchdog

The capture works by **monkey-patching `transform_response` on two LiteLLM
config classes**. That seam is deliberately fail-open — an observability stash
must never break a completion — so if LiteLLM moves its internals, the wrapper
logs once and every call afterwards proceeds normally with the columns left
NULL. The dataset simply stops growing, silently.

`tests/unit/services/llm_providers/test_ollama_timings.py` pins the wrapped
signature against the installed litellm, which catches an upgrade **in CI**. It
cannot catch a runtime drift on a box whose litellm moved underneath it.

`ProbeDecodeSplitCoverageJob` (`services/jobs/probe_decode_split_coverage.py`,
every 6h) is that missing watcher. It emits an
`llm_decode_split_coverage_low` finding (severity `warn`) when coverage on a
learned model drops below threshold.

### The self-calibrating denominator

The probe carries **no hardcoded list of local models** — such a list rots on
every pin change, and a rotted list is a gate that silently passes. Instead:

> A model that has reported a split at least once inside the learning window is,
> by construction, Ollama-routed — so its recent rows are _entitled_ to one.

Consequences, all intentional:

- A litellm upgrade that breaks the wrapper drops **every** learned model to 0%
  → one finding naming all of them.
- A single model regressing (a pin moved onto a route the wrapper doesn't cover)
  shows up as that model alone.
- A brand-new model that has never reported is **invisible** until it reports
  once. The probe genuinely cannot distinguish "new local model whose capture is
  broken" from "new cloud model", and inventing a vendor list to try would
  reintroduce exactly the rot this design avoids.

Model names are normalized with the house `^ollama(_chat)?/` strip before
grouping (see [`cost-logs-model-identity.md`](cost-logs-model-identity.md)) —
one engine logs under up to three spellings, and an un-normalized `GROUP BY`
would both split a model into several rows and make a call site that changes its
prefix look brand-new, dropping it out of the learned set.

## Settings

| Key                                 | Default | Meaning                                                                  |
| ----------------------------------- | ------- | ------------------------------------------------------------------------ |
| `llm_decode_split_probe_enabled`    | `true`  | master switch                                                            |
| `llm_decode_split_window_hours`     | `24`    | alert window — wider keeps re-reporting un-backfillable pre-deploy rows  |
| `llm_decode_split_learn_days`       | `30`    | learning window for the denominator                                      |
| `llm_decode_split_min_coverage_pct` | `90`    | below this on a learned model → finding                                  |
| `llm_decode_split_min_sample`       | `50`    | below this many eligible calls, report without a verdict (0/1 is not 0%) |

## Where it surfaces

`GET /api/metrics/llm-throughput/trend?metric=decode` returns true decode tok/s
(`services/llm_throughput.py`); `metric=speed` returns the wall-clock rate. Both
render on the console History panel and the Cost & Analytics board's "Model
Throughput" row.
