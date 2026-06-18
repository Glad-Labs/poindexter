"""Unit tests for the qa_rail_reviews merge reducer (QA rescue cycle).

The reducer behaves like operator.add (list concat) EXCEPT it honors a reset
sentinel {"__reset__": True} emitted by qa.rewrite, which clears stale
first-pass reviews before the second QA pass re-runs."""

from __future__ import annotations

import pytest

from services.template_runner import _merge_rail_reviews


@pytest.mark.unit
class TestMergeRailReviews:
    def test_normal_concat(self):
        existing = [{"reviewer": "a"}]
        incoming = [{"reviewer": "b"}]
        assert _merge_rail_reviews(existing, incoming) == [
            {"reviewer": "a"}, {"reviewer": "b"},
        ]

    def test_empty_incoming_returns_existing(self):
        existing = [{"reviewer": "a"}]
        assert _merge_rail_reviews(existing, []) == [{"reviewer": "a"}]

    def test_reset_sentinel_clears_existing(self):
        existing = [{"reviewer": "a"}, {"reviewer": "b"}]
        incoming = [{"__reset__": True}]
        # Sentinel clears the prior reviews AND is stripped from the result.
        assert _merge_rail_reviews(existing, incoming) == []

    def test_reset_sentinel_strips_only_sentinel_keeps_rest(self):
        existing = [{"reviewer": "old"}]
        incoming = [{"__reset__": True}, {"reviewer": "fresh"}]
        assert _merge_rail_reviews(existing, incoming) == [{"reviewer": "fresh"}]

    def test_append_after_reset_accumulates_fresh(self):
        # Simulates: qa.rewrite resets -> [], then a rail appends one review.
        after_reset = _merge_rail_reviews([{"reviewer": "stale"}], [{"__reset__": True}])
        assert after_reset == []
        assert _merge_rail_reviews(after_reset, [{"reviewer": "fresh"}]) == [
            {"reviewer": "fresh"},
        ]
