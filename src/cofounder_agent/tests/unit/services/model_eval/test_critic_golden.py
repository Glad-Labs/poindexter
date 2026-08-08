"""Tests for the critic-judge golden-set bootstrap (poindexter#985)."""

from __future__ import annotations

import pytest

from services.model_eval.golden_sets.critic import build_critic_golden_set
from services.site_config import SiteConfig


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self._rows


class _FakePool:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def acquire(self):  # type: ignore[no-untyped-def]
        rows = self._rows

        class _Acq:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return _FakeConn(rows)

            async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
                return False

        return _Acq()


def _posts(n: int) -> list[dict]:
    # Long enough (>1500 chars) that the 55% truncation cut is mid-article,
    # ending with finished prose so 'good' cases read complete.
    body = ("Sentence about the topic goes here with detail. " * 60).strip()
    return [
        {"id": f"p{i}", "title": f"Post {i}", "content": body + f" Unique closer {i}."}
        for i in range(n)
    ]


def _sc(good_n: int = 2) -> SiteConfig:
    return SiteConfig(initial_config={"model_eval_critic_good_posts": str(good_n)})


@pytest.mark.unit
class TestCriticGoldenSet:
    async def test_four_cases_per_post_with_expected_labels(self):
        """One known-good + one corruption per rubric reject class the judge
        must catch: truncation (#984), planning scaffold, and the
        reviewer-role deliberation dump (#1000)."""
        golden = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        assert len(golden.cases) == 8
        kinds = [c.payload["kind"] for c in golden.cases]
        assert kinds.count("good") == 2
        assert kinds.count("truncated") == 2
        assert kinds.count("scaffold") == 2
        assert kinds.count("deliberation") == 2
        for c in golden.cases:
            expected = c.payload["expected"]
            assert expected == ("approve" if c.payload["kind"] == "good" else "veto")

    async def test_truncated_corruption_trips_the_984_detector(self):
        """The truncation corruption must be a REAL truncation by the
        pipeline's own definition — cross-checked against
        content_validator.detect_truncated_content so the two gates can
        never drift apart on what 'truncated' means."""
        from modules.content.content_validator import detect_truncated_content

        golden = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        for c in golden.cases:
            reasons = detect_truncated_content(c.payload["content"])
            if c.payload["kind"] == "truncated":
                assert reasons, "truncated corruption must trip the detector"
            elif c.payload["kind"] == "good":
                assert reasons == [], "good case must read as finished prose"

    async def test_scaffold_corruption_prepends_planning_dump(self):
        golden = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        scaffold = next(c for c in golden.cases if c.payload["kind"] == "scaffold")
        assert scaffold.payload["content"].startswith("*   Topic: Post ")
        assert "Check word count" in scaffold.payload["content"]
        # The original article rides underneath the dump.
        assert "Unique closer" in scaffold.payload["content"]

    async def test_version_stable_for_same_posts(self):
        g1 = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        g2 = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        assert g1.version == g2.version
        rows = _posts(2)
        rows[0]["id"] = "different"
        g3 = await build_critic_golden_set(pool=_FakePool(rows), site_config=_sc(2))
        assert g3.version != g1.version

    async def test_too_few_posts_fails_loud(self):
        with pytest.raises(RuntimeError, match="critic golden set"):
            await build_critic_golden_set(pool=_FakePool(_posts(1)), site_config=_sc(3))


@pytest.mark.unit
class TestDeliberationCorruption:
    """The #1000 escape shape must be a real corruption by the pipeline's own
    detector — otherwise the golden set would grade judges on a case the
    validator itself considers clean."""

    async def test_deliberation_case_trips_the_planning_dump_detector(self):
        from modules.content.content_validator import (
            _strip_code_spans,
            detect_planning_dump_preamble,
        )

        golden = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        for c in golden.cases:
            evidence = detect_planning_dump_preamble(
                _strip_code_spans(c.payload["content"])
            )
            if c.payload["kind"] == "deliberation":
                assert evidence, "deliberation corruption must trip the detector"
            elif c.payload["kind"] == "good":
                assert evidence == [], "good case must read as finished prose"

    async def test_deliberation_case_fuses_article_onto_narration(self):
        golden = await build_critic_golden_set(pool=_FakePool(_posts(2)), site_config=_sc(2))
        case = next(c for c in golden.cases if c.payload["kind"] == "deliberation")
        content = case.payload["content"]
        assert content.startswith("*   Role: Reviewer")
        # No blank line between the narration and the article — the exact
        # 1bdf0360 shape.
        assert "I'll provide the original text.Sentence about" in content
