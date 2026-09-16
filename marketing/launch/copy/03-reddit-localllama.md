# r/LocalLLaMA post — goes FIRST (Phase 2.1)

**Title options:**

1. `I run a full autonomous blog pipeline on one RTX 5090 — the writer and critic are different model families on purpose`
2. `A year of running gemma3:27b + phi4:14b as writer/critic pairs in production — what actually works`
3. `Open-sourced my local content pipeline: 16 QA rails, ~80% draft rejection, zero API costs`

---

## Post body

I've been running a local-model content pipeline in production for about a year — it researches, writes, reviews, and publishes long-form posts for a real site ([gladlabs.io](https://www.gladlabs.io), 207 live posts, 2,100+ runs), on one box under Pop!_OS: RTX 5090 + 3090, 64 GB. Upfront so nobody has to catch me on it: it ships writing drafts with a local model, and every rail that judges them is local, but on my own install I've pinned the writer slot to Claude Sonnet — that's exactly the slot I'm here to get recommendations for. Just open-sourced it (Apache 2.0): https://github.com/Glad-Labs/poindexter

Some things a year of unattended local inference taught me, since this sub is the only place that will appreciate them:

**Cross-family QA is the whole game.** The shipped writer is `gemma3:27b`, the critic is `phi4:14b` — deliberately a different family. Same-family critics rubber-stamp their sibling's output; their biases are correlated, so they miss the same hallucinations. With a cross-family pair, about half of all drafts get killed by the QA rails (13 of them: LLM critics, DeepEval/Ragas evals, deterministic anti-hallucination validators, citation checks against the research corpus). The published output is decent _because_ the rejection rate is high, not despite it.

**Reasoning models will leak `<think>` into your structured output eventually.** No matter how nice your prompt is. Composition is now grammar-constrained JSON so the think-spiral physically can't reach the wire. Related: my VRAM estimator was wrong about sliding-window KV cache for months before I caught it, and endpoints you assume stay warm don't — the pipeline now actively warms GPU-pinned Ollama endpoints before dispatch.

**Silent failure is the default failure.** My ingestion taps once pulled in nothing for 17 days. Nothing crashed — that's why I didn't notice. The system now treats staleness itself as an alarm condition, and a watchdog daemon restarts what it can and pages me on Telegram for what it can't.

Current model layout (all swappable at runtime via a DB setting, no restart):

| Role                                 | Model            | VRAM   |
| ------------------------------------ | ---------------- | ------ |
| Writer                               | gemma3:27b       | 16 GB  |
| Adversarial critic                   | phi4:14b         | 9 GB   |
| Fast tasks (SEO, routing, summaries) | qwen3:8b         | 5 GB   |
| Embeddings                           | nomic-embed-text | 274 MB |

Core pipeline runs on 8 GB+ VRAM (slowly on CPU). Image QA uses qwen3-vl:30b if you have the headroom. Cloud models exist as an opt-in plugin behind a spend guard, but the default is your GPU, your data, zero API costs.

It's alpha and the README is honest about the rough edges (schema still moves between releases, self-host only, Windows needs WSL). What I'd love from this sub: writer-model recommendations to test against the current pair — the writer slot is the one most worth upgrading, and you all have opinions I can't get anywhere else. What would you run in a 27B-class writer slot today?

Numbers from my own box, median decode over the last 30 days (5090, Ollama):

| Model              | Role                   | tok/s |
| ------------------ | ---------------------- | ----- |
| qwen2.5:7b         | aux / structured tasks | ~254  |
| qwen3-vl:30b       | vision critic          | ~163  |
| phi4:14b           | aux                    | ~132  |
| gemma-4-31B-it-qat | local writer slot      | ~66   |

Throughput isn't the bottleneck — judgment is. The pipeline averages 3 tasks/day and peaks around 13, and most of a run is QA rails and rewrites rather than first-token generation.

---

_Mechanics: post ~9pm your time, any weeknight. Re-read the sub's self-promo rules the night before. Engage that evening and next morning from your phone. The questions you get here are the dress rehearsal for HN — fold surprises into the crib sheet and the Show HN first comment._
