# Model Evaluation Loop

Open-source models advance quickly, so any model we pin into a pipeline slot
drifts from "best available" over time — silently, because nothing re-checks it.
The model-eval loop is the self-updating answer to _"is there a better
open-source model than the one we run today?"_ per slot: discover candidates,
score them against a golden set with a capability-appropriate metric, and
propose the winner for promotion.

The full design (all four stages, all three waves) is in
[`../architecture/2026-06-29-model-eval-loop-design.md`](../architecture/2026-06-29-model-eval-loop-design.md).
This runbook covers **what is shipped today** and how to drive it.

## What is shipped (Plan 1 — reranker vertical slice)

Plan 1 proves the loop end-to-end on **one** deterministic slot —
`rag_rerank_model` (the cross-encoder that reranks RAG retrievals) — because
its metric (nDCG) is ground truth, so a regression is unarguable. It wires the
**Evaluate → Promote** half of the loop:

- A **bakeoff**: score the current champion against one or more challenger
  models on a golden set mined from published posts, using **nDCG@10**.
- A **promotion proposal**: if a challenger wins by the configured margin,
  render a PR-ready report and decide the promotion shape (PR vs auto-swap).
- A **CLI** (`poindexter model-eval run` / `status`) as the operator surface.

Not yet shipped (the rest of v1 — see the design doc §14): automated model
**discovery** (HF scan) and **gating** (license / VRAM / servability), the
`EmbeddingScorer` and `STTScorer`, the scheduled `RunModelEvalJob`, and
auto-_execution_ of a promotion (today the proposal is **surfaced**, not
auto-applied — see [Promotion](#promotion)).

## Running a bakeoff

```bash
# Score the current reranker champion against one or more challengers.
poindexter model-eval run --challenger cross-encoder/ms-marco-MiniLM-L-12-v2

# Multiple challengers in one run (--challenger is repeatable):
poindexter model-eval run \
  --challenger cross-encoder/ms-marco-MiniLM-L-12-v2 \
  --challenger BAAI/bge-reranker-base

# Machine-readable output for scripting / a scheduled wrapper:
poindexter model-eval run --challenger BAAI/bge-reranker-base --json
```

The champion is read from `app_settings.rag_rerank_model`; if it is unset the
command **fails loud** rather than guessing (per `feedback_no_silent_defaults`).

Text output reports the champion score, the best challenger score, the relative
margin, the winner, and — when a challenger wins — the rendered promotion
proposal. `--json` emits the same fields as a single object (slot, metric,
champion, scores, winner, margin, `beats_margin`, `proposal_kind`).

### What happens under the hood

1. **Golden set** — `build_reranker_golden_set` queries published posts
   (`status='published'`) and builds (query → relevant-doc) cases: each post's
   title is the query, a chunk of its own body is the one relevant document, and
   chunks from other posts are distractors. The set is **versioned by a hash of
   the post ids** so metric deltas are comparable across runs, and the distractor
   sampling is seeded from that version (deterministic). It **fails loud** if
   there are fewer published posts than the configured candidates-per-case.
2. **Scorer** — `RerankerScorer` loads each model as a `sentence_transformers`
   `CrossEncoder` (on `rag_rerank_device`), reranks each case's candidates, and
   computes **nDCG@10**.
3. **Compare** — the runner picks the best challenger and computes the relative
   margin `(best − champion) / champion`; `beats_margin` is true only when that
   margin clears `model_eval_promotion_margin`.
4. **Record** — every model's result is written to the eval harness (Langfuse by
   default — see [Where results go](#where-results-go)).
5. **Propose** — a winning challenger yields a `PromotionProposal`.

## Checking recorded results

```bash
poindexter model-eval status                 # latest nDCG@10 per model, best first
poindexter model-eval status --json          # same, as JSON
poindexter model-eval status --slot rag_rerank_model   # explicit slot (only value today)
```

`status` reads the latest recorded metric per model for the slot from the
harness. Empty output (`(no recorded eval runs)`) just means no bakeoff has
recorded results yet.

## Promotion

When a challenger wins, the proposal's **kind** is decided as follows:

| Kind        | When                                                                                                                  | Meaning                                                              |
| ----------- | --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `pr`        | default                                                                                                               | Change the slot default in `settings_defaults.py` via a reviewed PR. |
| `auto_swap` | slot is **stateless** (reranker — no re-embed migration) **and** the operator opted in via `<slot>_auto_promote=true` | A pure setting flip is safe to apply directly.                       |

For the reranker that means setting `rag_rerank_model_auto_promote=true` makes a
winning challenger eligible for a direct swap; otherwise (and for every stateful
slot, e.g. embeddings, which would need a re-embed migration) promotion is
PR-only.

**Today the proposal is _surfaced_, not auto-applied.** `poindexter model-eval
run` prints the PR-ready body and the decided kind; the operator acts on it
(opens the PR, or — for an opted-in `auto_swap` — flips the setting). Wiring
auto-execution (auto-open the PR / auto-write the setting) is the deferred
follow-up.

## Settings

All knobs live in `app_settings` (defaults in
`services/settings_defaults.py`), tunable without a code change:

| Key                                       | Default  | Meaning                                                                        |
| ----------------------------------------- | -------- | ------------------------------------------------------------------------------ |
| `model_eval_promotion_margin`             | `0.02`   | Relative margin a challenger must beat the champion by to win (2%).            |
| `model_eval_reranker_golden_size`         | `50`     | Number of (query → relevant-doc) cases in the reranker golden set.             |
| `model_eval_reranker_candidates_per_case` | `20`     | Candidate documents (1 relevant + distractors) ranked per case.                |
| `rag_rerank_model_auto_promote`           | unset    | Set to `true` to make a winning reranker challenger eligible for `auto_swap`.  |
| `rag_rerank_device`                       | (reused) | Device the cross-encoder loads on (`cpu` / `cuda`); reused from the RAG stack. |

## Where results go

The loop separates **computing** the metric (the `Scorer`) from
**storing/comparing** results (the `EvalHarness`) behind an interface, so the
storage backend is a rentable implementation ("own the interface, rent the
implementation"):

- **Production:** `LangfuseEvalHarness` records each model's run as a Langfuse
  dataset item + a custom **Score**, reusing the same SDK primitives as
  `services/langfuse_experiments.py`. It needs Langfuse configured; missing creds
  **fail loud**. (Langfuse runs locally at http://localhost:3010.)
- **Tests / offline:** `InMemoryEvalHarness` is a drop-in double — the
  integration test drives the whole loop through it with no DB, no Langfuse, and
  no model download.

If the self-hosted Langfuse install ever proves unreliable, the harness can be
swapped to Postgres tables without touching scorers, discovery, gating, or
promotion.

## Tests

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/services/model_eval/ tests/unit/cli/test_model_eval_cli.py -q
```

The integration test (`test_loop_integration.py`) drives the full vertical
slice — golden-set build → scorer → runner → promotion — with a fake pool and a
marker-based fake encoder, asserting a stronger challenger is promoted and a tie
holds the champion.

## Related

- Design: [`../architecture/2026-06-29-model-eval-loop-design.md`](../architecture/2026-06-29-model-eval-loop-design.md)
- Plan 1: [`../architecture/2026-06-29-model-eval-loop-wave1-plan.md`](../architecture/2026-06-29-model-eval-loop-wave1-plan.md)
- RAG stack (the reranker's production consumer): [`../architecture/rag-retrieval-stack.md`](../architecture/rag-retrieval-stack.md)
