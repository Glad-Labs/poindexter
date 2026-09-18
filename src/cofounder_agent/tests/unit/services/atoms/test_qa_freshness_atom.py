"""qa.freshness — the stale news-take veto.

Pinned on the 2026-09-14 queue case: a reaction to OpenAI's September 8 paper
reading "this week" reached approval on the 11th and sat there until the 14th
while every truth rail passed it.
"""

from __future__ import annotations

from datetime import date

import pytest

from poindexter.modules.content.atoms import qa_freshness as mod
from poindexter.modules.content.atoms._qa_rail_common import _NON_TEXT_FIXABLE_PROVIDERS
from poindexter.modules.content.atoms.qa_freshness import (
    assess,
    find_relative_phrases,
    newest_source_date,
    run,
)
from poindexter.services import qa_gates_db_writer

TODAY = date(2026, 9, 14)
RC = (
    "- [On Navier–Stokes](https://simonwillison.net/2026/Sep/8/on-navier-stokes/): "
    "Sep 8, 2026 · OpenAI presented a solution\n"
    "- [OpenAI post](https://openai.com/index/navier-stokes/): 8 September 2026 — announcement\n"
    "- [background](https://example.com/ns): 2026-08-30 · older explainer\n"
)
NEWS = {"rss", "hacker_news"}


class TestRelativePhrases:
    @pytest.mark.parametrize(
        "text",
        [
            "OpenAI put out a paper this week claiming a resolution.",
            "Earlier today the team published the weights.",
            "The model was just announced at the keynote.",
            "It landed a few days ago and the benchmarks are in.",
            "Breaking: the lab has released the checkpoints.",
            "As of this writing the repo has 2k stars.",
        ],
    )
    def test_anchoring_phrases_are_found(self, text: str) -> None:
        assert find_relative_phrases(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Recently the field has moved toward mixture-of-experts.",
            "Now is a good time to revisit your VRAM budget.",
            "Ken Burns pans over stills are fine for evergreen posts.",
            "In September 2026 the scheduler gained a queue.",
        ],
    )
    def test_evergreen_prose_is_not_anchored(self, text: str) -> None:
        assert find_relative_phrases(text) == []

    def test_phrases_are_deduplicated_and_normalised(self) -> None:
        assert find_relative_phrases("This  week. this week! THIS WEEK?") == ["this week"]


class TestNewestSourceDate:
    def test_picks_the_newest_of_three_formats(self) -> None:
        assert newest_source_date(RC, today=TODAY) == date(2026, 9, 8)

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("posted May 3, 2026", date(2026, 5, 3)),
            ("posted Sep 8th, 2026", date(2026, 9, 8)),
            ("posted 12 March 2026", date(2026, 3, 12)),
            ("posted 2026-07-01", date(2026, 7, 1)),
            ("posted Sept. 1, 2026", date(2026, 9, 1)),
        ],
    )
    def test_each_format_parses(self, text: str, expected: date) -> None:
        assert newest_source_date(text, today=TODAY) == expected

    def test_future_dates_are_ignored(self) -> None:
        # A source line quoting a 2027 deadline must not make the piece "fresh".
        assert newest_source_date("due 2027-01-01; published Sep 1, 2026", today=TODAY) == date(
            2026, 9, 1
        )

    def test_no_date_is_none(self) -> None:
        assert newest_source_date("no calendar dates here, just 42 GB", today=TODAY) is None
        assert newest_source_date("", today=TODAY) is None

    def test_invalid_calendar_date_is_skipped(self) -> None:
        assert newest_source_date("Feb 30, 2026 and 2026-13-40", today=TODAY) is None


class TestAssess:
    def test_the_navier_stokes_case_is_stale(self) -> None:
        verdict = assess(
            content="OpenAI put out a paper this week claiming a resolution.",
            research_context=RC,
            discovered_by="rss",
            created_at=None,
            news_sources=NEWS,
            max_age_days=5,
            today=TODAY,
        )
        assert verdict is not None
        assert verdict["stale"] is True
        assert verdict["age_days"] == 6
        assert verdict["event_date"] == "2026-09-08"
        assert verdict["basis"] == "newest dated source"
        assert verdict["phrases"] == ["this week"]

    def test_news_source_alone_makes_it_news_shaped(self) -> None:
        verdict = assess(
            content="A measured look at the claimed proof.",
            research_context=RC,
            discovered_by="hacker_news",
            created_at=None,
            news_sources=NEWS,
            max_age_days=5,
            today=TODAY,
        )
        assert verdict is not None and verdict["stale"] is True
        assert verdict["from_news_source"] is True and verdict["phrases"] == []

    def test_evergreen_draft_gets_no_verdict(self) -> None:
        assert (
            assess(
                content="A measured look at the claimed proof.",
                research_context=RC,
                discovered_by="internal_rag",
                created_at=date(2026, 9, 1),
                news_sources=NEWS,
                max_age_days=5,
                today=TODAY,
            )
            is None
        )

    def test_fresh_within_the_cap_is_approved(self) -> None:
        verdict = assess(
            content="just announced",
            research_context=RC,
            discovered_by="",
            created_at=None,
            news_sources=NEWS,
            max_age_days=5,
            today=date(2026, 9, 10),
        )
        assert verdict is not None and verdict["stale"] is False and verdict["age_days"] == 2

    def test_age_equal_to_the_cap_is_not_stale(self) -> None:
        verdict = assess(
            content="this week",
            research_context=RC,
            discovered_by="",
            created_at=None,
            news_sources=NEWS,
            max_age_days=6,
            today=TODAY,
        )
        assert verdict is not None and verdict["stale"] is False

    def test_falls_back_to_task_created_at(self) -> None:
        verdict = assess(
            content="this week",
            research_context="no dates in the corpus",
            discovered_by="",
            created_at=date(2026, 9, 1),
            news_sources=NEWS,
            max_age_days=5,
            today=TODAY,
        )
        assert verdict is not None
        assert verdict["basis"] == "task created_at" and verdict["age_days"] == 13

    def test_no_age_at_all_means_no_verdict(self) -> None:
        # Never guess: anchored prose with nothing to date it is not judged.
        assert (
            assess(
                content="this week",
                research_context="",
                discovered_by="rss",
                created_at=None,
                news_sources=NEWS,
                max_age_days=5,
                today=TODAY,
            )
            is None
        )


class _Config:
    def __init__(self, **overrides: str) -> None:
        self._d = {"qa_freshness_enabled": "true", "qa_freshness_max_age_days": "5"}
        self._d.update(overrides)

    def get(self, key: str, default=None):
        return self._d.get(key, default)


def _wire(monkeypatch, *, gate=(True, True), task=(None, "")):
    monkeypatch.setattr(mod, "_today", lambda: TODAY)
    monkeypatch.setattr(mod, "resolve_pool", lambda state, atom: None)

    async def _facts(pool, task_id):
        return task

    async def _gates(qa):
        return {"freshness": gate}

    monkeypatch.setattr(mod, "_task_facts", _facts)
    monkeypatch.setattr(mod, "resolve_gate_states", _gates)


def _state(content: str, **extra):
    return {
        "content": content,
        "research_context": RC,
        "site_config": _Config(),
        "task_id": "t-1",
        **extra,
    }


class TestRun:
    @pytest.mark.asyncio
    async def test_stale_news_take_is_vetoed(self, monkeypatch) -> None:
        _wire(monkeypatch, task=(None, "rss"))
        out = await run(_state("OpenAI put out a paper this week."))
        (review,) = out["qa_rail_reviews"]
        assert review["reviewer"] == "freshness" and review["provider"] == "freshness"
        assert review["approved"] is False and review["score"] == 0.0
        assert "6 day(s) behind" in review["feedback"] and "2026-09-08" in review["feedback"]
        assert "rewrite cannot" in review["feedback"]

    @pytest.mark.asyncio
    async def test_fresh_take_is_approved(self, monkeypatch) -> None:
        _wire(monkeypatch)
        monkeypatch.setattr(mod, "_today", lambda: date(2026, 9, 10))
        out = await run(_state("OpenAI put out a paper this week."))
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is True and review["score"] == 100.0

    @pytest.mark.asyncio
    async def test_evergreen_draft_gets_a_scoreless_na_pass(self, monkeypatch) -> None:
        # NOT `== {}` (poindexter#1060). freshness is required_to_pass and is
        # silent on nearly every draft, so returning nothing made
        # missing_required_gates read it as an ABSENT required gate and
        # hard-veto 6 of 6 clean posts at scores of 95.5-97.6.
        _wire(monkeypatch, task=(date(2026, 8, 1), "internal_rag"))
        out = await run(_state("A measured, undated look at the proof."))
        (review,) = out["qa_rail_reviews"]
        assert review["reviewer"] == "freshness"
        assert review["not_applicable"] is True
        assert review["approved"] is True and review["score"] == 0.0
        assert "Evergreen draft" in review["feedback"]

    @pytest.mark.asyncio
    async def test_news_shaped_but_undatable_says_so(self, monkeypatch) -> None:
        # The rail never guesses an age — but "I cannot date this" is a
        # verdict it must SAY, not withhold.
        _wire(monkeypatch, task=(None, "rss"))
        state = _state("OpenAI put out a paper this week.", research_context="")
        out = await run(state)
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True
        assert "no datable source" in review["feedback"]

    @pytest.mark.asyncio
    async def test_demoted_gate_is_advisory(self, monkeypatch) -> None:
        _wire(monkeypatch, gate=(True, False), task=(None, "rss"))  # (enabled, required)
        out = await run(_state("OpenAI put out a paper this week."))
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is True and review.get("advisory") is True

    @pytest.mark.asyncio
    async def test_master_switch_off_is_na_not_silence(self, monkeypatch) -> None:
        # The off switch must not be able to hard-reject every post: it says
        # whether the rail RUNS, required_to_pass says whether it GATES.
        _wire(monkeypatch, task=(None, "rss"))
        state = _state("OpenAI put out a paper this week.")
        state["site_config"] = _Config(qa_freshness_enabled="false")
        out = await run(state)
        (review,) = out["qa_rail_reviews"]
        assert review["not_applicable"] is True and review["approved"] is True
        assert "qa_freshness_enabled=false" in review["feedback"]

    @pytest.mark.asyncio
    async def test_a_rail_that_could_not_run_still_fails_closed(self, monkeypatch) -> None:
        # The N/A contract covers "ran, nothing to judge" ONLY. No draft at
        # all is a broken pipeline, and the missing_required veto is correct.
        _wire(monkeypatch)
        assert await run(_state("")) == {}
        assert await run({"content": "x", "site_config": None}) == {}

    @pytest.mark.asyncio
    async def test_task_lookup_failure_reduces_coverage_not_the_run(self, monkeypatch) -> None:
        _wire(monkeypatch)

        class _BadPool:
            def acquire(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(mod, "resolve_pool", lambda state, atom: _BadPool())
        # Phrasing + corpus dates still judge without the DB layer.
        monkeypatch.setattr(mod, "_task_facts", mod.__dict__["_task_facts"])
        out = await run(_state("OpenAI put out a paper this week."))
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is False


class TestWiring:
    def test_freshness_veto_is_never_a_rescue_candidate(self) -> None:
        assert "freshness" in _NON_TEXT_FIXABLE_PROVIDERS

    def test_reviewer_maps_to_its_gate_row(self) -> None:
        assert qa_gates_db_writer._REVIEWER_TO_GATE.get("freshness") == "freshness"
