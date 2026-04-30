"""Tests for topic_ranking — goal vectors + weighted cosine scoring.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 3)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.topic_ranking import (
    GOAL_DESCRIPTIONS, goal_vector_for, weighted_cosine_score,
)
from services.niche_service import NicheGoal


pytestmark = pytest.mark.asyncio


async def test_all_goal_types_have_descriptions():
    expected = {"TRAFFIC","EDUCATION","BRAND","AUTHORITY","REVENUE","COMMUNITY","NICHE_DEPTH"}
    assert set(GOAL_DESCRIPTIONS.keys()) == expected
    for desc in GOAL_DESCRIPTIONS.values():
        assert isinstance(desc, str) and len(desc) > 20


async def test_goal_vector_caches_embeddings(monkeypatch):
    calls = []
    async def fake_embed(text):
        calls.append(text)
        return [0.1] * 768
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)
    v1 = await goal_vector_for("TRAFFIC")
    v2 = await goal_vector_for("TRAFFIC")
    assert v1 == v2
    # _embed_text_cached itself caches, so calls should be 1
    assert len(calls) == 1


async def test_weighted_cosine_score_combines_per_goal_signals():
    candidate_vec = [1.0, 0.0, 0.0]
    # Two goals; one aligns perfectly with candidate, the other is orthogonal.
    goal_vecs = {"TRAFFIC": [1.0, 0.0, 0.0], "EDUCATION": [0.0, 1.0, 0.0]}
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    score, breakdown = weighted_cosine_score(candidate_vec, goal_vecs, weights)
    # 0.6 * 1.0 (perfect TRAFFIC) + 0.4 * 0.0 (orthogonal EDUCATION) = 0.6
    assert score == pytest.approx(0.6, abs=0.01)
    assert breakdown == {"TRAFFIC": pytest.approx(0.6, abs=0.01),
                         "EDUCATION": pytest.approx(0.0, abs=0.01)}


async def test_llm_final_score_returns_score_per_candidate(monkeypatch):
    from services.topic_ranking import llm_final_score, ScoredCandidate

    async def fake_ollama_chat(prompt: str, *, model: str) -> str:
        # Simulated JSON response from glm-4.7-5090
        return '{"c1": {"score": 87.5, "breakdown": {"TRAFFIC": 0.5, "EDUCATION": 0.375}},'  \
               ' "c2": {"score": 42.0, "breakdown": {"TRAFFIC": 0.2, "EDUCATION": 0.22}}}'
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_ollama_chat)

    candidates = [
        ScoredCandidate(id="c1", title="A", summary="x", embedding_score=0.6),
        ScoredCandidate(id="c2", title="B", summary="y", embedding_score=0.4),
    ]
    weights = [NicheGoal("TRAFFIC", 60), NicheGoal("EDUCATION", 40)]
    scored = await llm_final_score(candidates, weights)
    assert scored["c1"].llm_score == 87.5
    assert scored["c2"].llm_score == 42.0


def test_apply_decay_multiplies_score():
    from services.topic_ranking import apply_decay
    assert apply_decay(score=80, decay_factor=1.0) == 80
    assert apply_decay(score=80, decay_factor=0.7) == pytest.approx(56)
    assert apply_decay(score=80, decay_factor=0.49) == pytest.approx(39.2)
