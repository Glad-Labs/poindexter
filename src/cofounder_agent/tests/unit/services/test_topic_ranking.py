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
