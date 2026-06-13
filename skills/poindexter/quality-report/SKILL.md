---
name: quality-report
description: Show content quality scores and QA results for tasks that have cleared the pipeline. Use when the user says "quality report", "show quality scores", "how's the content quality", or "QA results".
---

# Quality Report

Shows recent tasks that have cleared the multi-model QA pipeline, grouped by status.
The pipeline ends in one of two terminal states (not "completed" — that status was
removed in the 2026-04 refactor):

- **awaiting_approval** — passed QA, waiting on human review at `/pipeline`
- **published** — approved and pushed to the site via Vercel

Each task carries a `quality_score` populated by `multi_model_qa.py` after the
anti-hallucination QA rails finish. Six OSS rails run (all advisory): DeepEval ×3
(`deepeval_brand_fabrication`, `deepeval_g_eval`, `deepeval_faithfulness`),
guardrails ×2 (`guardrails_brand`, `guardrails_competitor`, native/dep-free since
#996), and Ragas ×1 (`ragas_eval`, averaging faithfulness + answer-relevancy +
context-precision).

## Usage

```bash
scripts/run.sh                    # Both awaiting_approval + published, last 10 each
scripts/run.sh awaiting [limit]   # Just the human-review queue
scripts/run.sh published [limit]  # Just posts that went live
```

## Parameters

- `mode` (optional): `awaiting` | `published` | `all` (default)
- `limit` (optional): number of tasks per group. Defaults to 10.

## Output

For each task: `id`, `title`, `topic`, `quality_score`, `updated_at`.
Score range is 0–100 from the multi-model QA aggregator. 80+ is typical for
approved posts; anything that got below the `qa_final_score_threshold` setting
(currently 80) never reaches these states — it gets rejected.
