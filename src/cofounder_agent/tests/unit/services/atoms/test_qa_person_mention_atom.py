"""qa.person_mention — should this named human be in the post? (poindexter#1009)

The rail exists because a TRUE statement can still be one we must not publish:
a draft reached awaiting_approval at Q94 having named a private individual and
characterised how they do their job from a rating site. Every claim was
verified accurate, which is why no fabrication or fact-check rail could fire.

The structure under test is the one the 2026-09-01 calibration arrived at
(single-call "find and judge" scored 0.50 balanced accuracy and flagged a
passage with no person in it; two-stage with few-shot extraction scored 0.83):
detection split from judgment, "nobody is named" as a code path, and an
unparseable judgment failing CLOSED.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from poindexter.modules.content.atoms import qa_person_mention as atom

pytestmark = pytest.mark.unit


def _sc(**over: str) -> Any:
    values = {
        "qa_person_mention_enabled": "true",
        "qa_person_mention_model": "gemma-4-31b",
        "qa_person_mention_max_people": "8",
        "qa_person_mention_offender_penalty": "25",
    }
    values.update(over)
    return SimpleNamespace(get=lambda key, default="": values.get(key, default))


def _patch_gates(monkeypatch, *, required: bool = False):
    async def _states(_qa):
        return {"person_mention": (True, required)}

    monkeypatch.setattr(atom, "resolve_gate_states", _states)
    monkeypatch.setattr(
        "poindexter.modules.content.multi_model_qa.MultiModelQA.__init__",
        lambda self, **kw: None,
    )


def _ask_stub(monkeypatch, *, people, verdicts=None, raises=False):
    """Stub the single LLM seam. ``verdicts`` maps person -> raw judge text."""
    verdicts = verdicts or {}
    calls: list[str] = []

    async def _ask(key, *, state, site_config, pool, **fields):
        calls.append(key)
        if raises:
            raise RuntimeError("ollama unreachable")
        if key == "qa.person_mention.extract":
            return people
        return verdicts.get(fields.get("person"), '{"status": "public", "confidence": 90}')

    monkeypatch.setattr(atom, "_ask", _ask)
    return calls


# ---------------------------------------------------------------------------
# Deterministic layer — reputation data attached to a named person
# ---------------------------------------------------------------------------


class TestReputationLayer:
    """Fires regardless of public-figure status: scraped reputation data about
    a named human does not belong in commercial content either way."""

    def test_flags_rating_data_attached_to_a_named_person(self):
        content = "Dana Whitfield holds a 4.2 star rating from 87 customer reviews."
        offenders = atom.find_reputation_mentions(content, ["Dana Whitfield"])
        assert len(offenders) == 1
        assert "rating or review-site data" in offenders[0]

    def test_ignores_reputation_vocabulary_with_no_person_in_the_sentence(self):
        content = "The practice holds a 4.2 star rating from 87 customer reviews."
        assert atom.find_reputation_mentions(content, ["Dana Whitfield"]) == []

    def test_person_and_rating_in_DIFFERENT_sentences_do_not_pair(self):
        content = (
            "Dana Whitfield wrote the specification. "
            "Separately, the vendor holds a 4.2 star rating."
        )
        assert atom.find_reputation_mentions(content, ["Dana Whitfield"]) == []

    @pytest.mark.parametrize(
        "text",
        [
            # The false positive that motivated keying off EXTRACTED people
            # rather than capitalised bigrams, and off rating NOUNS rather
            # than the verb "reviews".
            "Our Poindexter is the pipeline that researches, writes, reviews, "
            "and publishes the posts on this site.",
            # Measured: the only reputation-vocabulary hit across 207 published
            # posts before the regex was tightened — a counting idiom.
            "In one batch, 4 out of 5 candidates were system-introspection topics.",
            "We shipped 4 out of 5 planned PRs today.",
        ],
    )
    def test_ordinary_prose_does_not_fire(self, text):
        assert atom._REPUTATION_RE.search(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "She holds a 4.2 star rating from 87 customer reviews.",
            "He is rated 3 out of 5 on Healthgrades.",
            "The firm has a 2.1-star average and a complaints record with the BBB.",
            "Her Yelp page lists eleven online reviews.",
            "Her average is 4.5 out of five across sixty reviews.",
            "Her review score sits at 3.8 out of 5.",
        ],
    )
    def test_real_reputation_phrasing_fires(self, text):
        assert atom._REPUTATION_RE.search(text) is not None


# ---------------------------------------------------------------------------
# Parsing — an unreadable answer is never a verdict
# ---------------------------------------------------------------------------


class TestParsing:
    def test_extraction_parses_names(self):
        assert atom.parse_people('{"people": ["Ray Dalio", "Ada Lovelace"]}') == [
            "Ray Dalio", "Ada Lovelace",
        ]

    def test_empty_extraction_is_an_empty_list_not_none(self):
        """'Nobody is named' is a real answer and must be distinguishable from
        'we could not read the answer'."""
        assert atom.parse_people('{"people": []}') == []

    @pytest.mark.parametrize("raw", ["", "not json", "{}", '{"people": "Ray Dalio"}'])
    def test_unreadable_extraction_is_none(self, raw):
        assert atom.parse_people(raw) is None

    def test_status_parses(self):
        assert atom.parse_status('{"status":"private","confidence":88}') == ("private", 88)

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            '{"status": "maybe", "confidence": 90}',
            '{"status": "private"}',
            # bool is an int subclass — this is not a measurement.
            '{"status": "private", "confidence": true}',
        ],
    )
    def test_unusable_verdicts_are_none(self, raw):
        assert atom.parse_status(raw) is None


# ---------------------------------------------------------------------------
# run() — the two-stage flow
# ---------------------------------------------------------------------------


class TestRun:
    async def test_no_people_named_is_a_code_path_with_no_judge_call(self, monkeypatch):
        """The calibration's core lesson: an empty extraction returns early in
        CODE. The first prompt made '[]' the salient literal and the model then
        answered it for passages full of names."""
        _patch_gates(monkeypatch)
        calls = _ask_stub(monkeypatch, people='{"people": []}')
        out = await atom.run({
            "content": "The retention job prunes checkpoint rows older than thirty days.",
            "site_config": _sc(),
        })
        assert out == {}
        assert calls == ["qa.person_mention.extract"], "no judge call for nobody"

    async def test_public_figure_in_public_capacity_passes(self, monkeypatch):
        """Regression for the issue's acceptance: Ray Dalio and Martin Gardner
        are cited legitimately in the same batch and must keep passing."""
        _patch_gates(monkeypatch)
        _ask_stub(
            monkeypatch,
            people='{"people": ["Ray Dalio"]}',
            verdicts={"Ray Dalio": '{"status":"public","confidence":95}'},
        )
        out = await atom.run({
            "content": "Ray Dalio's Principles argues for radical transparency.",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is True
        assert review["score"] == 100

    async def test_private_individual_is_flagged(self, monkeypatch):
        _patch_gates(monkeypatch, required=True)
        _ask_stub(
            monkeypatch,
            people='{"people": ["Dana Whitfield"]}',
            verdicts={"Dana Whitfield": '{"status":"private","confidence":88}'},
        )
        out = await atom.run({
            "content": "Dana Whitfield runs a small practice two towns over.",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is False
        assert "private individual" in review["feedback"]

    async def test_advisory_by_default_scores_without_vetoing(self, monkeypatch):
        """Seeded qa_gates.person_mention.required_to_pass=false — the rail
        surfaces the offender and drops the score, but graduation to a veto is
        an operator's settings flip, not a deploy."""
        _patch_gates(monkeypatch, required=False)
        _ask_stub(
            monkeypatch,
            people='{"people": ["Dana Whitfield"]}',
            verdicts={"Dana Whitfield": '{"status":"private","confidence":88}'},
        )
        out = await atom.run({
            "content": "Dana Whitfield runs a small practice two towns over.",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["advisory"] is True
        assert review["approved"] is True, "advisory rails must not veto"
        assert review["score"] == 75.0
        assert "private individual" in review["feedback"]

    async def test_reputation_data_flags_even_a_public_figure(self, monkeypatch):
        """The deterministic half is independent of the public-figure question
        on purpose — issue acceptance 3."""
        _patch_gates(monkeypatch, required=True)
        _ask_stub(
            monkeypatch,
            people='{"people": ["Dana Whitfield"]}',
            verdicts={"Dana Whitfield": '{"status":"public","confidence":95}'},
        )
        out = await atom.run({
            "content": "Dana Whitfield holds a 4.2 star rating from 87 customer reviews.",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is False
        assert "review-site data" in review["feedback"]

    async def test_unparseable_verdict_fails_closed(self, monkeypatch):
        """The known-bad case once passed purely because the judge's response
        could not be parsed and 'no verdict' was read as 'no objection'."""
        _patch_gates(monkeypatch)
        emitted: list[dict] = []
        monkeypatch.setattr(
            "poindexter.utils.findings.emit_finding",
            lambda **kw: emitted.append(kw),
        )
        _ask_stub(
            monkeypatch,
            people='{"people": ["Dana Whitfield"]}',
            verdicts={"Dana Whitfield": "I think this person is probably fine."},
        )
        out = await atom.run({
            "content": "Dana Whitfield runs a small practice two towns over.",
            "site_config": _sc(),
        })
        assert out == {}, "an unreadable verdict must never certify a pass"
        assert emitted and emitted[0]["kind"] == "qa_rail_degraded"

    async def test_offenders_are_still_reported_when_another_verdict_is_unreadable(
        self, monkeypatch,
    ):
        """Fail-closed blocks CERTIFYING a pass; it must not discard a finding
        the rail genuinely made."""
        _patch_gates(monkeypatch, required=True)
        _ask_stub(
            monkeypatch,
            people='{"people": ["Dana Whitfield", "Ray Dalio"]}',
            verdicts={
                "Dana Whitfield": '{"status":"private","confidence":88}',
                "Ray Dalio": "unreadable",
            },
        )
        out = await atom.run({
            "content": "Dana Whitfield runs a practice. Ray Dalio wrote Principles.",
            "site_config": _sc(),
        })
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is False
        assert "unreadable verdict" in review["feedback"]

    async def test_unparseable_extraction_degrades(self, monkeypatch):
        _patch_gates(monkeypatch)
        emitted: list[dict] = []
        monkeypatch.setattr(
            "poindexter.utils.findings.emit_finding",
            lambda **kw: emitted.append(kw),
        )
        _ask_stub(monkeypatch, people="sorry, I cannot help with that")
        out = await atom.run({"content": "Some draft.", "site_config": _sc()})
        assert out == {}
        assert emitted and emitted[0]["kind"] == "qa_rail_degraded"

    async def test_judge_call_raising_never_crashes_the_run(self, monkeypatch):
        _patch_gates(monkeypatch)
        monkeypatch.setattr(
            "poindexter.utils.findings.emit_finding", lambda **kw: None,
        )
        _ask_stub(monkeypatch, people='{"people": []}', raises=True)
        assert await atom.run({"content": "Some draft.", "site_config": _sc()}) == {}

    async def test_master_switch_off_is_silent(self, monkeypatch):
        _patch_gates(monkeypatch)
        calls = _ask_stub(monkeypatch, people='{"people": ["Ray Dalio"]}')
        out = await atom.run({
            "content": "Ray Dalio wrote Principles.",
            "site_config": _sc(qa_person_mention_enabled="false"),
        })
        assert out == {}
        assert calls == [], "disabled rail makes no LLM call"

    async def test_max_people_bounds_the_judge_calls(self, monkeypatch):
        _patch_gates(monkeypatch)
        names = [f"Person Number{i}" for i in range(12)]
        import json as _json

        calls = _ask_stub(monkeypatch, people=_json.dumps({"people": names}))
        await atom.run({
            "content": " ".join(f"{n} did a thing." for n in names),
            "site_config": _sc(qa_person_mention_max_people="3"),
        })
        judge_calls = [c for c in calls if c == "qa.person_mention.classify"]
        assert len(judge_calls) == 3

    async def test_model_chain_never_reaches_the_writer_pin(self):
        """A QA rail must not silently bill a metered cloud writer canary."""
        sc = SimpleNamespace(get=lambda k, d="": {
            "pipeline_writer_model": "claude-sonnet-5",
            "pipeline_local_writer_model": "gemma-4-31b",
        }.get(k, d))
        assert atom._resolve_model(sc) == "gemma-4-31b"
