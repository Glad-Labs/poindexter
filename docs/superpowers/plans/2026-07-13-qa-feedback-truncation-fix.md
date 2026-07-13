# QA Feedback Truncation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task (subagent dispatch is disabled for this
> project — see `feedback_no_subagent_delegation` in CLAUDE.md — so
> subagent-driven-development is not an option here). Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Fix `format_qa_feedback_from_reviews()` so a low-scoring QA reviewer
(e.g. `content_originality` flagging a near-duplicate post) can never be
silently dropped by a whole-blob tail-truncation, regardless of its position
in the pipeline's rail order or how verbose the reviewers ahead of it are.

**Architecture:** Pure function-level fix in one shared formatter. Sort
reviewers by ascending score (the one severity signal that works across both
hard-gate and advisory reviewers) before formatting, cap each reviewer's
feedback text individually instead of only capping the whole joined blob, and
keep the existing whole-blob cap as a backstop. Every consumer
(`pipeline_versions.qa_feedback`, `pipeline_tasks_view.qa_feedback`, the
`poindexter pipeline qa <task>` CLI) reads this one field, so no other files
need to change.

**Tech Stack:** Python 3.13, pytest (no new dependencies).

## Global Constraints

- No new `app_settings` keys, schema changes, or migrations — this is a pure
  function-level fix (spec non-goal).
- No changes to CLI/MCP consumer surfaces (`tasks list`, `tasks get`,
  `approve_post`, `poindexter pipeline qa`, etc.) — fixing the shared
  formatter fixes every consumer for free.
- No backfill of historical truncated `qa_feedback` rows — out of scope for
  this change.
- The two existing callers (`modules/content/atoms/content_compile_meta.py`,
  `modules/content/stages/finalize_task.py`) must not require any edits —
  they inherit the fixed behavior via the new parameter's default value.
- Ship via a PR, never push directly to `main` (`feedback_all_changes_via_pr`).
- No subagent/Task-tool dispatch — execute steps inline, sequentially
  (`feedback_no_subagent_delegation`).
- Design doc of record: `docs/superpowers/specs/2026-07-13-qa-feedback-truncation-fix-design.md`.

---

### Task 1: Fix `format_qa_feedback_from_reviews` truncation + regression tests

**Files:**

- Modify: `src/cofounder_agent/modules/content/multi_model_qa.py:235-276`
- Modify: `src/cofounder_agent/tests/unit/services/test_multi_model_qa_helpers.py`

**Interfaces:**

- Produces: `format_qa_feedback_from_reviews(qa_reviews: list[dict], final_score: float | None = None, approved: bool | None = None, max_chars: int = 4000, per_reviewer_max_chars: int = 200) -> str`
  — same name, same first four parameters (unchanged types/defaults), one
  new optional keyword parameter `per_reviewer_max_chars` appended at the
  end so both existing callers (which pass no such argument) are unaffected.
- Consumes: nothing new — same `qa_reviews` dict shape as today
  (`reviewer: str`, `provider: str`, `score: float`, `approved: bool`,
  `feedback: str`, all optional/defensive as in the current code).

- [ ] **Step 1: Write the new regression tests (failing against current code)**

  Open `src/cofounder_agent/tests/unit/services/test_multi_model_qa_helpers.py`.
  Inside `class TestFormatQAFeedbackFromReviews:`, add these five test methods
  (place them after `test_default_reviewer_provider`, before the closing of
  the class):

  ```python
    def test_low_score_reviewer_not_dropped_behind_verbose_ones(self):
        """Regression test for the 2026-07-13 incident: a low-score
        reviewer's line must survive even when several verbose reviewers
        ran before it in the pipeline's execution order."""
        verbose = "v" * 500
        reviews = [
            {"reviewer": "ollama_critic", "provider": "ollama", "score": 25,
             "approved": False, "feedback": verbose},
            {"reviewer": "deepeval_faithfulness", "provider": "deepeval",
             "score": 67, "approved": True, "feedback": verbose},
            {"reviewer": "ragas_eval", "provider": "ragas", "score": 50,
             "approved": True, "feedback": verbose},
            {"reviewer": "content_originality",
             "provider": "content_originality_gate", "score": 13.6,
             "approved": True,
             "feedback": (
                 "content near-duplicate of published post 'x' "
                 "(worst-chunk cosine 0.86 > 0.83)"
             )},
        ]
        out = format_qa_feedback_from_reviews(reviews, max_chars=1000)
        assert "content_originality" in out
        assert "near-duplicate" in out

    def test_reviewers_sorted_by_score_ascending(self):
        reviews = [
            {"reviewer": "high", "provider": "p", "score": 90,
             "approved": True, "feedback": "fine"},
            {"reviewer": "low", "provider": "p", "score": 10,
             "approved": False, "feedback": "bad"},
            {"reviewer": "mid", "provider": "p", "score": 50,
             "approved": True, "feedback": "meh"},
        ]
        out = format_qa_feedback_from_reviews(reviews)
        assert out.index("low") < out.index("mid") < out.index("high")

    def test_per_reviewer_cap_applies_individually(self):
        reviews = [
            {"reviewer": "verbose", "provider": "p", "score": 80,
             "approved": True, "feedback": "a" * 2000},
            {"reviewer": "terse", "provider": "p", "score": 85,
             "approved": True, "feedback": "short"},
        ]
        out = format_qa_feedback_from_reviews(reviews, per_reviewer_max_chars=50)
        assert "a" * 51 not in out
        assert "…" in out
        assert "short" in out
        assert "terse" in out

    def test_advisory_low_score_sorts_by_score_not_approved_flag(self):
        """Advisory rails report approved=True even when their own verdict
        failed (the real verdict is embedded in the feedback text) — the
        sort must use score, not the approved flag, or a failing advisory
        rail sorts as if it were fine."""
        reviews = [
            {"reviewer": "fine_rail", "provider": "p", "score": 95,
             "approved": True, "feedback": "all good"},
            {"reviewer": "content_originality",
             "provider": "content_originality_gate", "score": 13.6,
             "approved": True,
             "feedback": "[advisory] (failed, not required_to_pass) near-duplicate"},
        ]
        out = format_qa_feedback_from_reviews(reviews)
        assert out.index("content_originality") < out.index("fine_rail")

    def test_truncation_backstop_still_fires_with_many_reviewers(self):
        """The whole-blob max_chars backstop still applies when enough
        reviewers, even after per-reviewer capping, exceed it."""
        reviews = [
            {"reviewer": f"r{i}", "provider": "p", "score": 80,
             "approved": True, "feedback": "z" * 100}
            for i in range(10)
        ]
        out = format_qa_feedback_from_reviews(
            reviews, max_chars=300, per_reviewer_max_chars=100,
        )
        assert len(out) <= 300
        assert "...(truncated)" in out
  ```

- [ ] **Step 2: Replace the outdated `test_truncation_applied` test**

  In the same class, find:

  ```python
    def test_truncation_applied(self):
        big = "y" * 8000
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 80,
              "approved": True, "feedback": big}],
            max_chars=300,
        )
        assert len(out) <= 300
        assert "...(truncated)" in out
  ```

  Replace it with (same test name kept, body updated — this test pinned the
  old whole-blob-truncation behavior, which is exactly the bug):

  ```python
    def test_truncation_applied(self):
        """A single reviewer's oversized feedback is capped individually
        (per_reviewer_max_chars), not by the old whole-blob max_chars —
        the whole-blob cap is a backstop for many reviewers, not the
        primary truncation mechanism for one verbose reviewer."""
        big = "y" * 8000
        out = format_qa_feedback_from_reviews(
            [{"reviewer": "x", "provider": "p", "score": 80,
              "approved": True, "feedback": big}],
            per_reviewer_max_chars=50,
        )
        assert "…" in out
        assert "y" * 51 not in out
        assert len(out) < 200
  ```

- [ ] **Step 3: Run the test file to verify the new/updated tests fail**

  Run:

  ```bash
  cd src/cofounder_agent && "C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_multi_model_qa_helpers.py -o addopts="" -q -p no:cacheprovider
  ```

  Expected: 6 FAILURES (the 5 new tests + the rewritten `test_truncation_applied`)
  — everything else in the file still passes. The new tests should fail because
  the current code sorts nothing and truncates the whole blob from the tail
  (so `content_originality` gets cut, `low`/`mid`/`high` stay in insertion
  order, and per-reviewer feedback isn't capped at all). The rewritten
  `test_truncation_applied` fails because current code has no
  `per_reviewer_max_chars` parameter at all (`TypeError: unexpected keyword
argument`).

- [ ] **Step 4: Implement the fix**

  In `src/cofounder_agent/modules/content/multi_model_qa.py`, replace the
  existing `format_qa_feedback_from_reviews` function (lines 235-276) with:

  ```python
  def format_qa_feedback_from_reviews(
      qa_reviews: list[dict],
      final_score: float | None = None,
      approved: bool | None = None,
      max_chars: int = 4000,
      per_reviewer_max_chars: int = 200,
  ) -> str:
      """Format serialized qa_reviews into reviewer-facing text (GH-86).

      Mirrors :meth:`MultiModelResult.format_feedback_text` for callers
      that only hold the serialized dicts (e.g. when finalize reads the
      ``qa_reviews`` list from context without reconstructing the full
      :class:`MultiModelResult`).

      Reviewers are sorted by ascending ``score`` (not ``approved`` —
      advisory reviewers always report ``approved=True`` regardless of
      their own verdict, so score is the only severity signal that works
      across both hard-gate and advisory reviewers) and each reviewer's
      feedback text is capped individually at ``per_reviewer_max_chars``,
      so every reviewer that ran always gets at least a line. A low-scoring
      reviewer can no longer be silently dropped by a handful of verbose
      reviewers ahead of it in the whole-blob ``max_chars`` truncation
      (2026-07-13 incident: ``content_originality`` flagged a near-duplicate
      post but its line was truncated away behind earlier reviewers).
      """
      if not qa_reviews:
          return ""
      dict_reviews = [r for r in qa_reviews if isinstance(r, dict)]

      def _score(r: dict) -> float:
          try:
              return float(r.get("score", 0))
          except (TypeError, ValueError):
              return 0.0

      dict_reviews.sort(key=_score)

      lines: list[str] = []
      if final_score is not None:
          status_str = (
              "APPROVED" if approved
              else "REJECTED" if approved is False
              else ""
          )
          suffix = f" ({status_str})" if status_str else ""
          lines.append(f"Final score: {float(final_score):.0f}/100{suffix}")
      for r in dict_reviews:
          reviewer = r.get("reviewer", "unknown")
          provider = r.get("provider", "?")
          score = _score(r)
          status = "pass" if r.get("approved") else "FAIL"
          fb = (r.get("feedback") or "").strip() or "(no feedback)"
          if len(fb) > per_reviewer_max_chars:
              fb = fb[:per_reviewer_max_chars].rstrip() + "…"
          lines.append(
              f"- {reviewer} [{provider}] {score:.0f}/100 {status}: {fb}"
          )
      text = "\n".join(lines)
      if len(text) > max_chars:
          text = text[: max_chars - 20].rstrip() + "\n...(truncated)"
      return text
  ```

- [ ] **Step 5: Run the test file to verify everything passes**

  Run:

  ```bash
  cd src/cofounder_agent && "C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/test_multi_model_qa_helpers.py -o addopts="" -q -p no:cacheprovider
  ```

  Expected: all tests PASS (0 failures), including every pre-existing test
  in the file that wasn't touched (`test_empty_returns_empty_string`,
  `test_minimal_review_dict`, `test_score_with_no_final_score`,
  `test_final_score_with_approved_status`,
  `test_final_score_with_rejected_status`,
  `test_final_score_with_no_approved_flag`, `test_skips_non_dict_entries`,
  `test_invalid_score_coerced_to_zero`, `test_missing_score_defaults_to_zero`,
  `test_missing_feedback_uses_placeholder`, `test_default_reviewer_provider`,
  and the `TestSummary` / `MultiModelResult.format_feedback_text` classes
  earlier in the file).

- [ ] **Step 6: Run the two existing callers' test suites to confirm no breakage**

  `content_compile_meta.py` and `finalize_task.py` call this function without
  passing `per_reviewer_max_chars`, so they should be unaffected — confirm
  with their own test files:

  ```bash
  cd src/cofounder_agent && "C:\Users\mattm\AppData\Local\pypoetry\Cache\virtualenvs\poindexter-backend-YHugfB---py3.13\Scripts\python.exe" -m pytest tests/unit/services/atoms/test_content_atoms.py tests/unit/services/stages/test_pipeline_versions_persistence_473.py -o addopts="" -q -p no:cacheprovider
  ```

  Expected: all tests PASS (0 failures). If anything fails, read the
  assertion carefully — a test asserting exact `qa_feedback` text content
  (rather than just presence/absence of a substring) may need its fixture
  data updated to match the new sorted-order output; do not change
  `multi_model_qa.py` to work around a test that was asserting incidental
  ordering, since ordering is now a deliberate part of the fix.

- [ ] **Step 7: Commit**

  ```bash
  git add src/cofounder_agent/modules/content/multi_model_qa.py src/cofounder_agent/tests/unit/services/test_multi_model_qa_helpers.py
  git commit -m "fix(qa): stop truncation from silently dropping low-score QA reviewers
  ```

format_qa_feedback_from_reviews joined all reviewers in pipeline execution
order and truncated the whole blob from the tail. content_originality runs
2nd-to-last behind several verbose reviewers, so its line (e.g. a flagged
near-duplicate post) got silently cut far more often than not -- exactly
what let a QA-rejected duplicate post get manually approved on 2026-07-13.

Sort reviewers by ascending score (not approved -- advisory reviewers
always report approved=true regardless of their own verdict) and cap each
reviewer's feedback individually instead of only capping the whole blob,
so every reviewer that ran always gets at least a line."

```

**Verification for this task:** Steps 3 and 5 above are the task's own
red/green cycle. Step 6 is the cross-check that the two real pipeline
callers aren't affected. No manual/UI verification needed — this is a pure
text-formatting change with no schema, settings, or route changes.
```
