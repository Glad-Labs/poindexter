# QA feedback truncation fix — design

**Date:** 2026-07-13
**Status:** Draft — pending approval
**Author:** Claude (with Matt)

## Problem

Two dev_diary-style posts (`the-shift-to-a-native-ui-94d257df`, published
2026-07-10, and `the-shift-to-a-custom-operator-console-b1a787c3`, published
2026-07-13) told the same real story. The pipeline's own QA correctly caught
it: `qa.content_originality` flagged the second post as a 0.86-cosine
near-duplicate of the first on all 3 QA passes, and the overall verdict was a
hard reject (`qa_flagged=true`, final_score 55/100). The post was manually
approved and published anyway roughly 16.5 hours later.

Root cause is a truncation bug, not a missing signal. `format_qa_feedback_from_reviews()`
(`modules/content/multi_model_qa.py:235`) builds the human-readable QA
feedback text by joining all reviewers **in pipeline execution order**, then
hard-truncates the joined string at `max_chars=4000` from the tail:

```python
text = "\n".join(lines)
if len(text) > max_chars:
    text = text[: max_chars - 20].rstrip() + "\n...(truncated)"
```

Per the `canonical_blog` rail order (`services/canonical_blog_spec.py`),
`content_originality` runs **2nd-to-last** — `qa.programmatic → qa.critic →
qa.deepeval → qa.ragas → qa.vision → qa.topic_delivery → qa.citations →
qa.unlinked_attribution → qa.consistency → qa.self_consistency →
qa.content_originality → qa.web_factcheck → qa.aggregate` — preceded by
several reviewers with verbose free-text feedback (`ollama_critic`,
`deepeval_faithfulness`, etc., each 200–700+ chars of prose). The cumulative
text blows past 4000 chars before reaching `content_originality` far more
often than not.

This is the single field every consumer reads: `pipeline_versions.qa_feedback`,
`pipeline_tasks_view.qa_feedback` (→ `services/tasks_mcp.get_task_qa_feedback`
→ the `poindexter pipeline qa <task>` CLI, documented as showing "the per-rail
breakdown... the gate never silently buries the reason").

**Verified against the actual incident** (task `b1a787c3-16f2-4969-8798-e4573cc40e13`,
checked via `docker exec poindexter-worker` + asyncpg since the read-only
postgres MCP was briefly unavailable): `qa_flagged=true` (so `list_tasks`
would have shown a "⚑" on this task), `qa_feedback` length = 3995 chars,
text cuts off right after the `topic_delivery` line — it never reaches
`content_originality`. The flag fired correctly; the tool the system itself
recommends for "why was this flagged" structurally cannot show the reason,
regardless of who reads it or how carefully.

## Goals / non-goals

**Goals**

- Guarantee every reviewer that actually ran gets at least a compact line in
  the formatted feedback, regardless of its position in the rail order or how
  verbose the reviewers ahead of it are.
- Put the most concerning findings first, so in any residual truncation
  scenario the important lines are the ones that survive.

**Non-goals (YAGNI)**

- No change to hard-gate vs. advisory semantics, thresholds, or which rails
  block auto-publish — that's the "hard-block near-duplicates" option Matt
  explicitly deferred this round.
- No changes to the CLI/MCP surfaces themselves (`tasks list`, `tasks get`,
  `approve_post`, `poindexter pipeline qa`, etc.) — they all read the one
  formatted field, so fixing the formatter fixes every consumer for free.
- No backfill of historical truncated `qa_feedback` rows (not selected this
  round — a candidate follow-up, not part of this change).
- The overall safety cap stays; only how content is packed into it changes.

## Design

### Current behavior

Reviewers are formatted in list order (= execution order), joined, then the
**whole blob** is truncated from the tail if it exceeds `max_chars`.

### New behavior

The `Final score: N/100 (APPROVED/REJECTED)` header line is unaffected and
still prints first; only the per-reviewer lines below it are re-ordered and
capped.

1. **Sort `qa_reviews` by `score` ascending (stable)** before formatting —
   not by `approved`. Advisory reviewers always report `approved=true`
   regardless of their own verdict; the real pass/fail is embedded as text
   inside `feedback` (e.g. `"(failed, not required_to_pass)"`). `score` is
   the one severity signal that's meaningful across both hard-gate and
   advisory reviewers alike — a 13.6 is a 13.6 whether or not it's advisory.
2. **Cap each reviewer's `feedback` text individually**
   (`per_reviewer_max_chars: int = 200`, new default) before building its
   line. Long elaboration gets a trailing `…`; the reviewer's identity,
   score, and pass/fail status always survive.
3. **Keep the whole-blob `max_chars` (default 4000) as a backstop.** With
   per-reviewer capping, 14 reviewers × ~220 chars/line ≈ 3100 chars — this
   should essentially never fire against the current pipeline, but it stays
   as a hard ceiling for a future template with many more rails.
4. `format_qa_feedback_from_reviews` gains one new optional kwarg
   (`per_reviewer_max_chars`). Both existing callers
   (`modules/content/atoms/content_compile_meta.py`,
   `modules/content/stages/finalize_task.py`) need no changes — they inherit
   the fixed behavior automatically.

### Worked example (this incident, re-run through the new logic)

Sorted ascending by score, `content_originality` (13.6) and `ollama_critic`
(25) land in the first two lines of the output — both guaranteed to survive
any truncation. The bug that let this incident happen (the one line that
explained the reject silently dropped) cannot recur under this ordering.

## Testing

New cases in `tests/unit/services/test_multi_model_qa_helpers.py`
(`TestFormatQAFeedbackFromReviews`):

- **Reproduces the bug shape:** several reviewers with long feedback ahead of
  one short, low-score reviewer → the low-score reviewer's line is present in
  the output (fails today, passes after the fix).
- **Sort order:** reviewers passed out of score order appear in ascending-score
  order in the output text.
- **Per-reviewer cap:** one reviewer with e.g. 2000 chars of feedback → that
  reviewer's own line is capped to `per_reviewer_max_chars` (+ ellipsis),
  while every other reviewer's line still appears in full.
- **Advisory-but-low-score shape:** a review dict with `approved=True` and a
  low `score` (the actual shape every advisory rail uses) sorts by score, not
  stranded near the tail because `approved=True` looks "fine."

Existing test `test_truncation_applied` currently pins the old whole-blob
truncation (single reviewer, 8000 chars, `max_chars=300` → truncated to 300
total). That pins the buggy behavior, not a real invariant — update it to
assert the **per-reviewer** cap applies instead (output length tracks
`per_reviewer_max_chars` + line prefix, not the old blob-level `max_chars`).

All other existing tests (empty list, missing fields, non-dict entries,
final-score header, default reviewer/provider fallback) are unaffected — no
behavior change there.

## Rollout

Single PR: `modules/content/multi_model_qa.py` +
`tests/unit/services/test_multi_model_qa_helpers.py`. No settings, schema,
or migration changes — a pure function-level fix. Ships via the normal PR
flow.

## Open items to confirm during implementation

- Confirm neither existing caller passes a custom `max_chars` that would need
  re-tuning against the new per-reviewer default.
- Spot-check the ~200-char per-reviewer budget against this incident's actual
  14-reviewer payload to make sure it still reads well, not just technically
  fits.
