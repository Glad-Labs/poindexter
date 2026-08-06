"""Tests for the critic-judge scorer + runner ascore path (poindexter#985)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.model_eval.harness import InMemoryEvalHarness
from services.model_eval.runner import run_slot_eval
from services.model_eval.scorers.critic import CriticScorer
from services.model_eval.types import GoldenCase, GoldenSet
from services.site_config import SiteConfig


def _case(kind: str, expected: str, i: int = 0) -> GoldenCase:
    return GoldenCase(
        query=f"Post {i}",
        candidates=[],
        payload={
            "title": f"Post {i}",
            "topic": f"Post {i}",
            # Distinct per (kind, i) — tests key judge behavior on content.
            "content": f"Body {kind} {i}.",
            "expected": expected,
            "kind": kind,
        },
    )


def _golden() -> GoldenSet:
    cases = []
    for i in range(4):
        cases.append(_case("good", "approve", i))
        cases.append(_case("truncated", "veto", i))
    return GoldenSet(name="model_eval_critic", version=1, cases=cases)


def _review_fn(verdict_for):
    async def _review(*, title, content, topic, model):  # type: ignore[no-untyped-def]
        return verdict_for(title, content)

    return _review


def _verdict(approved: bool, score: float):
    return SimpleNamespace(approved=approved, score=score)


@pytest.mark.unit
class TestCriticScorer:
    async def test_sycophant_judge_scores_half(self):
        # A judge that approves EVERYTHING lands at exactly 0.5 balanced
        # accuracy: good=1.0, bad=0.0 — high approve-rate alone proves nothing.
        scorer = CriticScorer(pool=None, platform=None, review_fn=_review_fn(
            lambda t, c: _verdict(True, 90.0)
        ))
        r = await scorer.ascore(model="m", golden_set=_golden(), site_config=SiteConfig())
        assert r.value == 0.5
        assert r.detail["good_approve_rate"] == 1.0
        assert r.detail["bad_veto_rate"] == 0.0

    async def test_discriminating_judge_scores_high(self):
        golden = _golden()
        expected_by_content = {
            c.payload["content"]: c.payload["expected"] for c in golden.cases
        }

        def judge(_t, content):
            if expected_by_content[content] == "approve":
                return _verdict(True, 88.0)
            return _verdict(False, 22.0)

        scorer = CriticScorer(pool=None, platform=None, review_fn=_review_fn(judge))
        r = await scorer.ascore(model="m", golden_set=golden, site_config=SiteConfig())
        assert r.value == 1.0
        assert r.detail["good_approve_rate"] == 1.0
        assert r.detail["bad_veto_rate"] == 1.0
        assert r.detail["veto_rate_by_kind"] == {"truncated": 1.0}
        assert r.detail["scores_good"]["p50"] == 88.0
        assert r.detail["scores_bad"]["p50"] == 22.0

    async def test_unusable_reviews_count_against_the_judge(self):
        scorer = CriticScorer(pool=None, platform=None, review_fn=_review_fn(
            lambda t, c: None  # unreachable model / unparseable output
        ))
        r = await scorer.ascore(model="m", golden_set=_golden(), site_config=SiteConfig())
        # Nothing approved (good_rate 0), everything "vetoed"? No — an
        # unusable review is neither an approve nor a veto: it scores 0 on
        # good AND 0 on bad (result-None short-circuits both counts).
        assert r.detail["unusable_reviews"] == 8
        assert r.detail["good_approve_rate"] == 0.0
        assert r.detail["bad_veto_rate"] == 0.0
        assert r.value == 0.0

    async def test_review_exception_is_one_case_not_the_run(self):
        calls = {"n": 0}

        async def flaky(*, title, content, topic, model):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return _verdict(True, 90.0)

        scorer = CriticScorer(pool=None, platform=None, review_fn=flaky)
        r = await scorer.ascore(model="m", golden_set=_golden(), site_config=SiteConfig())
        assert r.n_cases == 8
        assert r.detail["unusable_reviews"] == 1


@pytest.mark.unit
class TestRunnerAscorePath:
    async def test_runner_awaits_async_scorer(self):
        golden = _golden()
        expected_by_content = {
            c.payload["content"]: c.payload["expected"] for c in golden.cases
        }

        def judge(_t, content):
            return _verdict(expected_by_content[content] == "approve", 80.0)

        scorer = CriticScorer(pool=None, platform=None, review_fn=_review_fn(judge))
        report = await run_slot_eval(
            slot="pipeline_critic_model",
            champion="champ-model",
            challengers=["challenger-a"],
            scorer=scorer,
            golden_set=golden,
            harness=InMemoryEvalHarness(),
            site_config=SiteConfig(),
            promotion_margin=0.02,
        )
        assert report.champion == "champ-model"
        assert report.champion_score == 1.0
        assert report.best_challenger == "challenger-a"
        assert report.winner == "champ-model"  # tie → champion holds
