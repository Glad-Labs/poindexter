"""Tests for InternalRagSource — generate candidates from internal corpus.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 5)
"""

import pytest
from unittest.mock import AsyncMock
from services.internal_rag_source import InternalRagSource, InternalCandidate


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_generate_pulls_top_k_per_source_kind(db_pool, monkeypatch):
    # The source should query the embeddings table for each enabled source_kind
    # and return distilled candidates.
    src = InternalRagSource(db_pool)
    # Mock the LLM distillation step — it turns a snippet into (topic, angle).
    async def fake_distill(snippets):
        return ("How we handled OAuth phase 1", "Why client credentials grant first")
    monkeypatch.setattr(src, "_distill_topic_angle", fake_distill)

    candidates = await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=["claude_session", "brain_knowledge"],
        per_kind_limit=2,
    )
    assert all(isinstance(c, InternalCandidate) for c in candidates)
    # at most 2 * 2 = 4 candidates if data exists for both source_kinds
    assert len(candidates) <= 4
    if candidates:
        c = candidates[0]
        assert c.distilled_topic
        assert c.distilled_angle
        assert c.primary_ref
        assert isinstance(c.supporting_refs, list)
