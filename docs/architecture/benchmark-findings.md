# Benchmark findings — turning our own instrumentation into topics

Every other topic source reads the outside world: HackerNews, Dev.to, RSS, web
search, GSC query gaps. `benchmark_findings` reads **`cost_logs`** — the ~100k
instrumented inference calls the pipeline has generated running itself — and
proposes a post only when those measurements support a claim nobody else can
make.

That is the point of the whole workstream. A post assembled from what is
already on the internet earns nothing however well it is written; uniqueness is
a property of **information**, not of authorship.

## What it can claim that others cannot

Since 2026-08-26 every Ollama-routed call records both wall-clock duration and
Ollama's true decode duration (see
[decode-split-capture.md](decode-split-capture.md)). That makes the gap between
what a model _decodes_ at and what the application actually _receives_
measurable. Live numbers at the time of writing:

| model                | decode tok/s | delivered tok/s | never reaches caller |
| -------------------- | ------------ | --------------- | -------------------- |
| `phi4:14b`           | 124.7        | 25.3            | **79.7%**            |
| `qwen2.5:7b`         | 235.1        | 64.2            | 72.7%                |
| `qwen3-vl:30b`       | 162.8        | 84.8            | 47.9%                |
| `gemma-4-31B-it-qat` | 62.2         | 36.1            | 42.0%                |
| `qwen3.6:27b`        | 124.6        | 99.2            | 20.4%                |
| `glm-4.7-5090`       | 177.0        | 172.2           | **2.7%**             |

Every published benchmark reports the left column. The right one takes months
of real multi-model workload on one contended box with per-call
instrumentation, which is why almost nobody has it.

**The mechanism is residency, not model speed.** A model that stays resident in
VRAM answers immediately; an intermittently-called one is evicted and reloaded
on nearly every call. The rendered fact block carries an explicit `CAUSAL NOTE`
saying so, because "phi4 is slow" is a checkable falsehood and the writer would
otherwise reach for it.

## A finding is not "a number exists"

Three bars, all DB-tunable, because a claim published on thin evidence is worse
than no post:

| Bar              | Default | Why                                                         |
| ---------------- | ------- | ----------------------------------------------------------- |
| `min_calls`      | 30      | below this a median is an anecdote                          |
| `min_models`     | 3       | a comparison needs breadth                                  |
| `min_spread_pct` | 25      | "everything behaves the same" is true, dull, and not a post |

Two finding kinds, chosen for honest self-limiting cadence:

- **`fleet_residency_tax`** — the comparative table. Re-proposable on a
  cooldown as the fleet changes.
- **`new_model_throughput`** — a model that has newly crossed the sample floor.
  Fires once per model, which is exactly when "nobody has these numbers yet" is
  most true.

**Newness is a fact about the model, not about our instrument.** The
first-seen query is deliberately _not_ filtered on `decode_duration_ms`: that
column only exists from 2026-08-26, so filtering on it makes every model look
brand new. `qwen3-vl:30b` — running for months — was proposed as a new-model
finding on the first run for exactly that reason.

`build_findings(new_model_names=None)` means _"the caller did not determine
which models are new"_, and the safe reading of that is **none of them**.
Treating `None` as "all of them" would propose a first-numbers post for every
model on the fleet.

## The measurements travel with the topic

This is the mechanism that closes the loop:

```
BenchmarkFindingsSource.extract()
  └─ DiscoveredTopic.description        (the rendered fact block)
       └─ topic_pool.summary
            └─ create_blog_post_task     ← carries it into…
                 └─ task metadata.research_context
                      ├─ writer_core._collect_research_context (layer 1)  → grounds the writer
                      └─ qa.numeric_fidelity corpus                        → checks the draft
```

So the writer is grounded on the real numbers, and the finished draft is later
checked **against the same block**. A figure the writer invents does not
reconcile.

### The carry-forward was broken for every source

`claim_best_pooled_topic` has always returned the pool row's `summary`, and
`create_blog_post_task` has always thrown it away. **804 of 1,815 `topic_pool`
rows carry one** (internal_rag ~118 chars, rss ~150) and every one was
discarded at the moment of task creation. Fixing that is what makes this source
possible, and it retroactively improves grounding for the other sources too.

The seam is the one the seed-URL path already documents: _"that's how we get it
in front of the LLM without adding new pipeline wiring."_

## Verified end to end

Grounding a draft on a real generated fact block and running the finished text
through `qa.numeric_fidelity`:

- An honest draft — `124.7`, `25.3`, `2.7%` — reconciles: **3 scored, 0
  flagged.**
- A draft with a fabricated `47,000 measured calls` is **flagged**.

## Settings

Seeded as a single JSON row, `plugin.topic_source.benchmark_findings`:

```json
{
  "enabled": false,
  "config": {
    "window_days": 30,
    "min_calls": 30,
    "min_models": 3,
    "min_spread_pct": 25,
    "cooldown_days": 30,
    "new_model_days": 30,
    "max_topics": 2
  }
}
```

**`enabled: false` must be seeded as a row, not defaulted in Python.**
`topic_sources/runner.py` falls back to `enabled=True` for a _missing_ plugin
row, so a Python-side default would silently switch this source on. It proposes
real pipeline work off measured data, so an operator should read one proposal
before it runs unattended.

Freshness is gated by an explicit cooldown query against `topic_pool`, never by
churning titles to slip past the dedup key — gaming the dedup key would make
the source's cadence a lie. If that lookup fails, the source **stands down**
rather than risk duplicate proposals.

## Not yet wired

The fact block is chart-ready, but nothing emits a `[CHART:]` marker into a
draft yet, so [`ChartProvider`](chart-rendering.md) still has no caller on this
path. Wiring it needs marker plumbing in `content.plan_image_markers` and is
deliberately left out rather than half-built.
