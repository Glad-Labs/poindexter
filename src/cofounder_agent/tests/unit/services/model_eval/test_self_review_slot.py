"""Guards the self-review detector eval slot (poindexter#1031).

`writer_self_review` caught 1 of 4 injected contradictions when measured by
hand, and production could not have told anyone: a missed detection emits
nothing, so a false PASS and a genuinely clean draft are the same observation.
This slot turns that silence into a number.

These tests pin the judgements that make the number trustworthy — chiefly that
it cannot be gamed from either end, and that an errored call is never credited
as "no contradiction found".
"""

from __future__ import annotations

import pytest

from services.model_eval.golden_sets.self_review import (
    build_self_review_golden_set,
    inject_contradiction,
)
from services.model_eval.scorers.self_review import SelfReviewScorer
from services.model_eval.types import GoldenCase, GoldenSet


class _SC:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args):
        return self._rows


class _Pool:
    def __init__(self, rows):
        self._c = _Conn(rows)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._c

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _posts(n, body="word " * 400):
    return [{"id": f"p{i}", "title": f"Post {i}", "content": body} for i in range(n)]


def _golden(n=2):
    cases = []
    for i in range(n):
        cases.append(GoldenCase(query="t", candidates=[], payload={
            "title": "t", "topic": "t", "draft": "clean body",
            "expected": "pass", "kind": "clean", "post_id": f"p{i}"}))
        cases.append(GoldenCase(query="t", candidates=[], payload={
            "title": "t", "topic": "t", "draft": "body + contradiction",
            "expected": "detect", "kind": "contradiction", "post_id": f"p{i}"}))
    return GoldenSet(name="model_eval_self_review", version=1, cases=cases)


@pytest.mark.unit
class TestGoldenSet:
    def test_injection_is_deterministic_and_appends(self) -> None:
        """Ground truth must come from construction, not from a label."""
        a, b = inject_contradiction("Body."), inject_contradiction("Body.")
        assert a == b
        assert a.startswith("Body.")
        assert len(a) > len("Body.")

    def test_injection_actually_contradicts(self) -> None:
        """A corruption that doesn't negate anything measures nothing."""
        out = inject_contradiction("Body.").lower()
        assert "incorrect" in out
        assert "should not be adopted" in out

    @pytest.mark.asyncio
    async def test_builds_one_clean_and_one_corrupt_case_per_post(self) -> None:
        g = await build_self_review_golden_set(
            pool=_Pool(_posts(3)), site_config=_SC({"model_eval_self_review_posts": "3"}))
        kinds = [c.payload["kind"] for c in g.cases]
        assert kinds.count("clean") == 3
        assert kinds.count("contradiction") == 3

    @pytest.mark.asyncio
    async def test_fails_loud_when_the_corpus_is_too_small(self) -> None:
        """A silently smaller eval would flatter a weak detector — which is
        the exact failure this set exists to catch."""
        with pytest.raises(RuntimeError, match="self-review golden set needs"):
            await build_self_review_golden_set(
                pool=_Pool(_posts(2)), site_config=_SC({"model_eval_self_review_posts": "8"}))

    @pytest.mark.asyncio
    async def test_version_is_stable_for_the_same_posts(self) -> None:
        sc = _SC({"model_eval_self_review_posts": "3"})
        a = await build_self_review_golden_set(pool=_Pool(_posts(3)), site_config=sc)
        b = await build_self_review_golden_set(pool=_Pool(_posts(3)), site_config=sc)
        assert a.version == b.version


@pytest.mark.unit
@pytest.mark.asyncio
class TestScorerCannotBeGamed:
    """Balanced accuracy over BOTH classes, so neither extreme wins."""

    async def test_a_perfect_detector_scores_one(self) -> None:
        async def detect(*, draft, title, topic, model):
            return "1. contradiction" if "contradiction" in draft else None

        r = await SelfReviewScorer(detect_fn=detect).ascore(
            model="m", golden_set=_golden(), site_config=_SC())
        assert r.value == 1.0
        assert r.detail["detection_rate"] == 1.0
        assert r.detail["false_positive_rate"] == 0.0

    async def test_a_detector_that_flags_everything_scores_half(self) -> None:
        """Detection rate alone would call this perfect."""
        async def detect(*, draft, title, topic, model):
            return "1. something"

        r = await SelfReviewScorer(detect_fn=detect).ascore(
            model="m", golden_set=_golden(), site_config=_SC())
        assert r.detail["detection_rate"] == 1.0
        assert r.detail["false_positive_rate"] == 1.0
        assert r.value == 0.5

    async def test_a_detector_that_never_flags_scores_half(self) -> None:
        """The measured production failure — a false-positive rate of 0 would
        otherwise make the near-no-op stage look healthy."""
        async def detect(*, draft, title, topic, model):
            return None

        r = await SelfReviewScorer(detect_fn=detect).ascore(
            model="m", golden_set=_golden(), site_config=_SC())
        assert r.detail["detection_rate"] == 0.0
        assert r.value == 0.5

    async def test_the_measured_one_in_four_reproduces(self) -> None:
        """gemma detected 1/4 by hand; the scorer must report exactly that."""
        seen = {"n": 0}

        async def detect(*, draft, title, topic, model):
            if "contradiction" not in draft:
                return None
            seen["n"] += 1
            return "1. found" if seen["n"] == 1 else None

        r = await SelfReviewScorer(detect_fn=detect).ascore(
            model="m", golden_set=_golden(4), site_config=_SC())
        assert r.detail["detected"] == 1
        assert r.detail["contradiction_n"] == 4
        assert r.detail["detection_rate"] == 0.25


@pytest.mark.unit
@pytest.mark.asyncio
class TestErrorsAreNotCreditedAsClean:
    async def test_a_raising_detector_is_unusable_not_a_pass(self) -> None:
        """Scoring an error as "no contradiction found" would credit a broken
        model with the behaviour of a cautious one and inflate the very number
        this measures."""
        async def detect(*, draft, title, topic, model):
            raise RuntimeError("model unreachable")

        r = await SelfReviewScorer(detect_fn=detect).ascore(
            model="m", golden_set=_golden(), site_config=_SC())
        assert r.detail["unusable"] == 4
        assert r.detail["detection_rate"] == 0.0
        # Crucially NOT a perfect clean-pass rate.
        assert r.detail["false_positive_rate"] == 1.0
        assert r.value == 0.0

    async def test_slot_and_metric_name_are_the_detect_pin(self) -> None:
        """Evaluating the detector must be recorded against the DETECT pin —
        the halves were split precisely so they can differ."""
        async def detect(*, draft, title, topic, model):
            return None

        r = await SelfReviewScorer(detect_fn=detect).ascore(
            model="m", golden_set=_golden(), site_config=_SC())
        assert r.slot == "writer_self_review_review_model"
        assert r.metric_name == "self_review_balanced_accuracy"
