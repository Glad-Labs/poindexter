# Topic/angle leaked-reasoning guard — design

**Date:** 2026-07-13
**Status:** Approved (brainstorm) → pending implementation plan
**Author:** Claude (with Matt)

## Problem

`InternalRagSource._distill_topic_angle()` (`services/internal_rag_source.py`,
~line 331-413) runs a small LLM call to extract a `(topic, angle)` pair from
raw internal snippets. It already skips an empty/unparseable response and a
`{"storyworthy": false}` verdict — but it does not defend against a distiller
that leaks its own reasoning/meta-commentary into the `topic` or `angle`
string instead of (or mixed into) real content.

A read-only DB scan of `internal_topic_candidates` while diagnosing an
earlier, narrower bug (stray `<|...|>` control tokens) found a worse variant
that pure token-stripping cannot fix. Note on provenance: the diagnosis that
prompted this scan assumed `strip_reasoning_artifacts()` had already been
wired into `_distill_topic_angle()` to handle the control-token case. That
turned out not to be true — grepping the file, its git history across all
branches, and open/merged PRs all confirm no such wiring exists anywhere in
the repo today. `strip_reasoning_artifacts()` itself is real and already used
elsewhere (`llm_text.py`, `litellm_provider.py`,
`modules/content/atoms/content_normalize_draft.py`) — it just was never
called from this function. The design below calls it directly (Signal 1,
below), so this spec closes that gap as a side effect rather than assuming
it's already closed. Row
`id=5b662b41-66c0-403f-945a-b750e922340f` has `distilled_angle`:

```
How conflicting own pull requests can silently stop workflows from dispatching, creating a//trap where updates are<|channel>thought: I need to extract the proposed blog post topic and unique angle from the provided same snippets. 1. Topic: The silent failure/trap of conflicting PRs affecting workflo
```

The model's task-instruction narration ("I need to extract the proposed blog
post topic and unique angle from the provided snippets. 1. Topic: ...")
leaked as if it _were_ the angle, mixed in after real content, and the string
is truncated mid-word. A literal `<|channel>` fragment happens to sit in this
particular row, but the failure mode — the model narrating its own
extraction task instead of producing the extraction's output — is not
inherently tied to a control token surviving. Regex token-stripping is the
wrong tool for the surrounding prose; this needs a guard that recognizes
_leaked meta-commentary as a shape_, not just a literal artifact.

**Compounding gap found while investigating:** `distilled_angle` is not
sanity-checked anywhere in the codebase today. Every call site of
`topic_sanity.evaluate_topic_sanity()` — `tap_builtin_topic_source.py`,
`topic_pool.py`, `topic_batch_service.py` (×2 — sweep intake and batch
write), `topic_proposal_service.py` — validates only `distilled_topic`/title.
The angle field rides into `internal_topic_candidates` completely
unvalidated, which is consistent with how this slipped through: the topic
field was fine, so nothing ever looked at the angle.

## Approaches considered

Three were brainstormed with Matt; **C is the direction approved**:

- **A — Task-vocabulary phrase fingerprint (semantic).** Regex/keyword match
  for phrases that only make sense if the model is narrating the extraction
  task itself. Catches leaks with no surviving control token. Rejected alone
  because it needs upkeep as models phrase things differently (accepted
  trade-off elsewhere in this file — see `_FAILURE_SENTINELS` below — but
  incomplete on its own).
- **B — Token-strip-then-suspect.** Reuse `strip_reasoning_artifacts()`; if
  cleaning changes the string at all, don't salvage the remainder — treat the
  whole candidate as suspect and skip. Near-zero false positives, minimal new
  code. Rejected alone because it's blind to a leak with no literal
  `<|...|>`-shaped token at all (plausible on some backends/prompts).
- **C — Layered (A + B, either signal rejects). Approved.** Closes both
  failure modes for a handful more lines than A alone, no material downside.

For Approach A's phrase fingerprint, a second question was resolved: dev_diary
content is intentionally founder-voice/first-person
(`feedback_content_voice`), so a bare match on generic phrases like "I need
to" would risk false-rejecting a genuine angle ("Why I need to rethink our
flaky CI"). **Resolved: require a compound signal** — a first-person/modal
opener co-occurring with extraction-task vocabulary in the same string. A
real angle essentially never talks about "extracting a topic from snippets,"
so the compound requirement keeps false-positive risk negligible while still
catching the leak shape.

## Design

### Placement

New pure function `detect_leaked_reasoning(text: str) -> str | None` in
`services/topic_sanity.py` — returns a reason string when it fires, `None`
when the text is clean. It lives in `topic_sanity.py` rather than as a
private helper inside `internal_rag_source.py` because that module is
already the established home for "is this LLM-produced topic/angle-shaped
string garbage" logic (`_FAILURE_SENTINELS`, the truncated-title rule, both
from poindexter#2059). Keeping it there means the other `evaluate_topic_sanity`
call sites could opt in later without duplicating anything — an explicit
**non-goal** for this change (see below), left as a possible fast-follow.

`_distill_topic_angle()` calls `detect_leaked_reasoning()` on both `topic`
and `angle`, immediately after the existing empty-topic check (~line 406-412),
using the same skip idiom already used for the empty/unparseable/
not-storyworthy cases: log a warning, `return None`.

### Detection logic — two independent signals, either rejects

1. **Token-strip-then-suspect** (Approach B):

   ```python
   from services.llm_providers.thinking_models import strip_reasoning_artifacts

   if strip_reasoning_artifacts(text) != text:
       return "control_token_artifact"
   ```

   Catches the literal `<|channel>` fragment in the example row, and any
   other Harmony/chat-template leak `strip_reasoning_artifacts` already
   recognizes.

2. **Compound task-vocabulary fingerprint** (Approach A, tight variant):

   ```python
   _META_OPENER_RE = re.compile(
       r"\b(I need to|I will|I'll|let me|I should|I'm going to)\b",
       re.IGNORECASE,
   )
   _META_TASK_VOCAB_RE = re.compile(
       r"\b(extract|distill|the provided|the following)\b.{0,80}"
       r"\b(topic|angle|snippet)s?\b"
       r"|\b(topic|angle)\b.{0,80}\b(extract|distill|snippet)s?\b",
       re.IGNORECASE,
   )
   _NUMBERED_FIELD_ECHO_RE = re.compile(
       r"\b\d+\.\s*(Topic|Angle)\s*:", re.IGNORECASE,
   )

   if _NUMBERED_FIELD_ECHO_RE.search(text):
       return "meta_commentary"
   if _META_OPENER_RE.search(text) and _META_TASK_VOCAB_RE.search(text):
       return "meta_commentary"
   ```

   The numbered-field echo ("1. Topic:", "2. Angle:") is distinctive enough
   on its own — a real angle never contains a literal enumerated field label
   matching the extraction prompt's own schema — so it doesn't need the
   compound-AND treatment. The opener+vocabulary pair does, per the
   false-positive discussion above.

Exact regex literals above are illustrative; final patterns are pinned down
during implementation against the test cases in Testing, below (regex
authoring is exactly the kind of thing TDD should drive, not a spec).

### Skip behavior

Matches the sibling skips already in `_distill_topic_angle`:
`logger.warning("[internal_rag] ...")` naming the matched reason plus a
truncated repr of the offending text, then `return None`. Existing callers
already treat `None` as "skip this candidate" (see
`test_generate_skips_candidates_when_distill_returns_none`).

**No new `emit_finding`.** The sibling skips in this function (empty
response, invalid JSON, empty topic, not-storyworthy) are log-only with no
finding — matching that local convention beats introducing asymmetric
visibility for just this one skip path. Can be revisited if these turn out
to be frequent enough to want on a dashboard.

## Goals / non-goals

**Goals**

- Reject the exact corrupted row (and its failure _shape_, not just its
  literal text) inside `_distill_topic_angle()`.
- Cover both the token-artifact leak and the pure-prose meta-commentary leak.
- Keep the compound-signal requirement tight enough that a genuine
  first-person dev_diary angle is never false-rejected.

**Non-goals (this change)**

- Not wiring `detect_leaked_reasoning()` into the other four
  `evaluate_topic_sanity()` call sites, and not extending `evaluate_topic_sanity`
  itself to validate `angle` the way it already validates `topic`. Both are
  real gaps (see Problem) but are separate, broader changes — flagged for a
  possible follow-up, not bundled here.
- Not adding an `emit_finding` for this skip path (see Skip behavior).
- Not making the phrase list operator-tunable via `app_settings`. This is a
  bug-signature list in the same spirit as `_FAILURE_SENTINELS` (hardcoded,
  updated in code as new leak shapes are found), not an operator policy knob.

## Testing (`feedback_docs_and_tests_default`)

In `tests/unit/services/test_internal_rag_source.py`, following the existing
pattern (mock the raw LLM JSON, assert on `_distill_topic_angle(...)`):

- The exact corrupted row (`id=5b662b41-...`'s `distilled_angle` text) →
  `None`.
- A clean, legitimate `(topic, angle)` pair → passes through unchanged.
- A first-person **dev_diary-style** angle ("Why I need to rethink our flaky
  CI") → passes, proving the compound-signal requirement doesn't
  false-positive on genuine founder-voice content.
- Numbered-field echo alone (no first-person opener) → rejects.
- A literal control-token artifact with no semantic phrase match → rejects
  (Signal 1 fires independently).
- Plain-prose meta-commentary with no control token at all → rejects
  (Signal 2 fires independently).

`detect_leaked_reasoning()` itself gets direct unit coverage in
`tests/unit/services/test_topic_sanity.py` alongside `evaluate_topic_sanity`'s
existing tests, exercising the same cases as pure-function calls (no LLM
mocking needed).

## Rollout

Single PR: `detect_leaked_reasoning()` in `topic_sanity.py` + its call from
`_distill_topic_angle()` + both test files above, via
`feedback_all_changes_via_pr`.
