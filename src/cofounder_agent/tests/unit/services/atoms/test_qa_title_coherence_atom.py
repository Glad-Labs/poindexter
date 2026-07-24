"""Unit tests for the qa.title_coherence atom (2026-07-24).

Pins the contract: an LLM verdict on whether the display title represents
the article; appends a ReviewerResult (reviewer='title_coherence',
provider='title_coherence_gate') to qa_rail_reviews; advisory-first
(DB-gated via qa_gates.title_coherence); fail-open per the qa_rail_degraded
convention (no review + finding when the judge could not measure).
"""

from __future__ import annotations

import json

import pytest

from modules.content.atoms import qa_title_coherence
from modules.content.multi_model_qa import MultiModelQA

# Advisory-first seed: required_to_pass=false.
_ADVISORY_STATES = {"title_coherence": (True, False)}
# Operator-graduated: required_to_pass=true → hard veto allowed.
_HARD_GATE_STATES = {"title_coherence": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


def _patch_judge(monkeypatch, payload):
    if isinstance(payload, Exception):
        async def judge(**kwargs):
            raise payload
    else:
        async def judge(**kwargs):
            return payload
    monkeypatch.setattr(qa_title_coherence, "_judge", judge)


def _capture_findings(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _Cfg:
    def __init__(self, enabled: bool = True, **values):
        self._enabled = enabled
        self._values = values

    def get(self, key, default=None):
        if key == "qa_title_coherence_enabled":
            return "true" if self._enabled else "false"
        return self._values.get(key, default)

    def get_int(self, key, default=0):
        return int(self._values.get(key, default))


def _state(**over):
    base = {
        "content": "An essay about migrating the operator console dashboard.\n\n## The Cutover\n\nDetails.",
        "title": "Every Console Generation Dies Twice. So Did Our Dashboard.",
        "site_config": _Cfg(),
        "task_id": "t-1",
    }
    base.update(over)
    return base


def _verdict(matches: bool, confidence: int = 95, reason: str = "because") -> str:
    return json.dumps({
        "title_represents_article": matches,
        "confidence": confidence,
        "reason": reason,
    })


@pytest.mark.unit
class TestQaTitleCoherenceAtom:
    def test_meta(self):
        m = qa_title_coherence.ATOM_META
        assert m.name == "qa.title_coherence"
        assert "content" in m.requires
        assert "title" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        assert await qa_title_coherence.run({"content": " ", "title": "T", "site_config": _Cfg()}) == {}

    async def test_empty_title_noops(self):
        assert await qa_title_coherence.run({"content": "body", "title": "", "site_config": _Cfg()}) == {}

    async def test_missing_site_config_noops(self):
        assert await qa_title_coherence.run({"content": "body", "title": "T"}) == {}

    async def test_disabled_noops(self, monkeypatch):
        _patch_judge(monkeypatch, _verdict(True))
        out = await qa_title_coherence.run(_state(site_config=_Cfg(enabled=False)))
        assert out == {}

    async def test_pass_verdict_appends_advisory_review(self, monkeypatch):
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        _patch_judge(monkeypatch, _verdict(True, confidence=95))
        out = await qa_title_coherence.run(_state())
        (review,) = out["qa_rail_reviews"]
        assert review["reviewer"] == "title_coherence"
        assert review["provider"] == "title_coherence_gate"
        assert review["approved"] is True
        assert review["score"] == 95.0
        assert review["advisory"] is True

    async def test_fail_verdict_scores_inverse_confidence(self, monkeypatch):
        """Advisory-first: a failing verdict is recorded as advisory —
        ``_mark_advisory_if_configured`` rewrites ``approved=True`` (so the
        bool never vetoes) and the failure signal lives in the score (0.0)
        plus the ``[advisory]``-prefixed feedback."""
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        _patch_judge(monkeypatch, _verdict(False, confidence=100, reason="gaming title on a software essay"))
        out = await qa_title_coherence.run(_state(
            title="Mastering the Leap to Next-Gen Gaming Hardware",
        ))
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is True  # advisory transform, not a real pass
        assert review["score"] == 0.0
        assert review["advisory"] is True
        assert "[advisory]" in review["feedback"]
        assert "gaming" in review["feedback"]

    async def test_graduated_gate_is_not_advisory(self, monkeypatch):
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        _patch_judge(monkeypatch, _verdict(False, confidence=90))
        out = await qa_title_coherence.run(_state())
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is False
        assert review["advisory"] is False

    async def test_string_boolean_verdicts_tolerated(self, monkeypatch):
        # Hard-gate states so the raw approved bool is preserved (the
        # advisory transform would rewrite it — covered separately above).
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        _patch_judge(monkeypatch, json.dumps({
            "title_represents_article": "false", "confidence": 80, "reason": "r",
        }))
        out = await qa_title_coherence.run(_state())
        (review,) = out["qa_rail_reviews"]
        assert review["approved"] is False
        assert review["score"] == 20.0

    async def test_unparseable_response_degrades_with_finding(self, monkeypatch):
        findings = _capture_findings(monkeypatch)
        _patch_judge(monkeypatch, "I think the title is fine, no JSON for you")
        out = await qa_title_coherence.run(_state())
        assert out == {}
        (finding,) = findings
        assert finding["kind"] == "qa_rail_degraded"
        assert finding["dedup_key"] == "qa_rail_degraded:title_coherence"

    async def test_missing_confidence_degrades(self, monkeypatch):
        findings = _capture_findings(monkeypatch)
        _patch_judge(monkeypatch, json.dumps({"title_represents_article": True, "reason": "r"}))
        out = await qa_title_coherence.run(_state())
        assert out == {}
        assert findings and findings[0]["kind"] == "qa_rail_degraded"

    async def test_judge_exception_degrades_with_finding(self, monkeypatch):
        findings = _capture_findings(monkeypatch)
        _patch_judge(monkeypatch, RuntimeError("ollama unreachable"))
        out = await qa_title_coherence.run(_state())
        assert out == {}
        (finding,) = findings
        assert finding["kind"] == "qa_rail_degraded"
        assert "ollama unreachable" in finding["body"]

    async def test_confidence_clamped_to_0_100(self, monkeypatch):
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        _patch_judge(monkeypatch, _verdict(True, confidence=250))
        out = await qa_title_coherence.run(_state())
        (review,) = out["qa_rail_reviews"]
        assert review["score"] == 100.0


@pytest.mark.unit
class TestModelResolution:
    def test_own_pin_wins(self):
        cfg = _Cfg(**{"qa_title_coherence_model": "ollama/judge-model:1b",
                      "pipeline_seo_model": "seo-model"})
        assert qa_title_coherence._resolve_model(cfg) == "judge-model:1b"

    def test_falls_back_to_seo_then_local_writer(self):
        cfg = _Cfg(**{"pipeline_seo_model": "ollama/gemma-4-31B-it-qat:latest"})
        assert qa_title_coherence._resolve_model(cfg) == "gemma-4-31B-it-qat:latest"
        cfg = _Cfg(**{"pipeline_local_writer_model": "local-writer"})
        assert qa_title_coherence._resolve_model(cfg) == "local-writer"

    def test_never_reads_the_cloud_writer_pin(self):
        cfg = _Cfg(**{"pipeline_writer_model": "anthropic/claude-sonnet-5"})
        assert qa_title_coherence._resolve_model(cfg) is None

    def test_all_empty_returns_none(self):
        assert qa_title_coherence._resolve_model(_Cfg()) is None


@pytest.mark.unit
class TestParseVerdict:
    def test_valid(self):
        assert qa_title_coherence._parse_verdict(_verdict(True, 80)) == (True, 80)

    def test_invalid_verdict_string(self):
        raw = json.dumps({"title_represents_article": "maybe", "confidence": 50})
        assert qa_title_coherence._parse_verdict(raw) is None

    def test_non_numeric_confidence(self):
        raw = json.dumps({"title_represents_article": True, "confidence": "high"})
        assert qa_title_coherence._parse_verdict(raw) is None

    def test_negative_confidence_clamped(self):
        raw = json.dumps({"title_represents_article": False, "confidence": -5})
        assert qa_title_coherence._parse_verdict(raw) == (False, 0)
