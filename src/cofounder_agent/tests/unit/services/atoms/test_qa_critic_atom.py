"""Unit tests for the qa.critic atom (atom-cutover Plan 3, #355).
Advisory is now DB-driven via qa_gates.llm_critic.required_to_pass
(poindexter#454). Critic reviewer field is 'ollama_critic'; gate name is
'llm_critic' — these differ by design (the gate maps the role, not the
provider)."""

from __future__ import annotations

import pytest

from services.atoms import qa_critic
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {"content": "a sufficiently long blog body", "topic": "t",
            "seo_title": "A Title", "research_context": None, "site_config": _Cfg()}


# Prod baseline: llm_critic is a hard gate (required_to_pass=True).
_HARD_GATE_STATES = {"llm_critic": (True, True)}

# Operator-demoted: llm_critic made advisory (required_to_pass=False).
_ADVISORY_STATES = {"llm_critic": (True, False)}


@pytest.mark.unit
class TestQaCriticAtom:
    def test_meta(self):
        m = qa_critic.ATOM_META
        assert m.name == "qa.critic"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_hard_gate_when_required_to_pass_true(self, monkeypatch):
        """Prod baseline: qa_gates.llm_critic.required_to_pass=True → advisory=False."""
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return ReviewerResult("ollama_critic", True, 84.0, "looks good", "ollama"), {"cost": 0.0}

        async def fake_gate_states(self):
            return _HARD_GATE_STATES

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_critic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "ollama_critic"
        assert rev["advisory"] is False  # hard gate, required_to_pass=True

    async def test_advisory_when_operator_demotes_critic(self, monkeypatch):
        """Operator flips required_to_pass=False → advisory=True (poindexter#454 lever)."""
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return ReviewerResult("ollama_critic", False, 45.0, "bad", "ollama"), {"cost": 0.0}

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_critic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True  # operator made it advisory

    async def test_no_gate_states_leaves_review_required(self, monkeypatch):
        """Empty gate_states (no DB) → _mark_advisory_if_configured is a no-op →
        review stays required (advisory=False), matching legacy behavior."""
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return ReviewerResult("ollama_critic", True, 84.0, "looks good", "ollama"), {"cost": 0.0}

        async def fake_gate_states(self):
            return {}

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_critic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False  # no gate row → stays required

    async def test_none_result_yields_no_key(self, monkeypatch):
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return None

        async def fake_gate_states(self):
            return _HARD_GATE_STATES

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        assert await qa_critic.run(_state()) == {}

    async def test_empty_content(self):
        assert await qa_critic.run({"content": "", "site_config": _Cfg()}) == {}
