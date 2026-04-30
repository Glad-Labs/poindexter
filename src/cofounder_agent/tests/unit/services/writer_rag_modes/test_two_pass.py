"""Tests for the TWO_PASS writer mode (LangGraph state machine).

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 13)

Deviations from plan:
- Monkeypatches `services.topic_ranking.embed_text` instead of
  `services.embedding_service.embed_text` because embedding_service has
  no module-level `embed_text` helper — that helper lives in topic_ranking.
- Uses `raising=False` for the `generate_with_context` and `research_topic`
  monkeypatches because neither symbol currently exists in production
  (`ai_content_generator` and `research_service` modules). Production
  wire-up of those callables is tracked separately (Task 14 wires the
  writer; `research_topic` needs a follow-up to expose a module-level
  helper around `ResearchService`).
- The plan's `_fake_pool_with_no_snippets` helper used `AsyncMock()` for
  the pool, which made `pool.acquire()` return a coroutine and broke the
  `async with` protocol. Switched to `MagicMock` for the sync `acquire`
  method that returns an async-context-manager mock, matching the
  established pool-mocking pattern elsewhere in this codebase.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.writer_rag_modes import two_pass

pytestmark = pytest.mark.asyncio


def _fake_pool_with_no_snippets():
    """Fake asyncpg pool whose acquire() context manager yields a conn with
    fetch() → []. Note: pool.acquire is a SYNC method that returns an
    object supporting `async with`, so we use MagicMock for acquire (not
    AsyncMock) to avoid the call returning a coroutine."""
    pool = MagicMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


async def test_no_external_needed_returns_pass1_draft(monkeypatch):
    """First draft has no [EXTERNAL_NEEDED] markers → graph short-circuits, no revise."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None):
        return "A clean first draft with no markers."
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_embed(text): return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(topic="t", angle="a", niche_id="n", pool=_fake_pool_with_no_snippets())
    assert result["draft"] == "A clean first draft with no markers."
    assert result["external_lookups"] == []
    assert result["revision_loops"] == 0


async def test_external_needed_triggers_research_and_revise(monkeypatch):
    """One marker → research → revise → done in 1 loop."""
    drafts = iter([
        "First draft with [EXTERNAL_NEEDED: a fact] inside.",
        "Revised draft with the actual fact inside.",
    ])
    async def fake_pass1(topic, angle, snippets, extra_instructions=None):
        return next(drafts)
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_revise(prompt, *, model):
        return next(drafts)
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_revise)
    async def fake_research(query, max_sources=2):
        return f"External research result for: {query}"
    monkeypatch.setattr("services.research_service.research_topic", fake_research, raising=False)
    async def fake_embed(text): return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(topic="t", angle="a", niche_id="n", pool=_fake_pool_with_no_snippets())
    assert "Revised draft" in result["draft"]
    assert len(result["external_lookups"]) == 1
    assert result["revision_loops"] == 1


async def test_loop_caps_at_max_revisions(monkeypatch):
    """Pathological: every revision adds new markers. Loop must terminate at _MAX_REVISION_LOOPS=3."""
    counter = {"n": 0}
    async def always_needs_more(topic, angle, snippets, extra_instructions=None):
        counter["n"] += 1
        return f"Draft with [EXTERNAL_NEEDED: thing {counter['n']}] inside."
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", always_needs_more, raising=False)
    async def fake_revise(prompt, *, model):
        counter["n"] += 1
        return f"Revised with [EXTERNAL_NEEDED: another thing {counter['n']}]."
    monkeypatch.setattr("services.topic_ranking._ollama_chat_json", fake_revise)
    async def fake_research(query, max_sources=2):
        return "fact"
    monkeypatch.setattr("services.research_service.research_topic", fake_research, raising=False)
    async def fake_embed(text): return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(topic="t", angle="a", niche_id="n", pool=_fake_pool_with_no_snippets())
    assert result["revision_loops"] == 3
    assert result["loop_capped"] is True
