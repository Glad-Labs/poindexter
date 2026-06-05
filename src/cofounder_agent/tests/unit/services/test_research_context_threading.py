"""Regression tests for ``research_context`` threading on the graph_def path.

Glad-Labs/poindexter#553 — the grounding QA rails (``qa.ragas`` and
``qa.deepeval``'s faithfulness check) need a non-empty ``research_context``
in the pipeline state to score; with it absent they skip 100% of the time
("research_sources empty — needs a corpus").

The atom-cutover (#355) flipped ``canonical_blog`` to a DB-stored ``graph_def``
compiled by ``build_graph_from_spec``. On that path, each ``stage.*`` node is
wrapped by ``make_stage_node``, which merges ONLY ``StageResult.context_updates``
back into the shared LangGraph state. ``GenerateContentStage`` produced the
research corpus and *mutated* ``context["research_context"]`` directly but did
NOT echo it in ``context_updates`` — so LangGraph dropped it, and every
downstream qa.* rail read ``state.get("research_context")`` → ``None`` → skip.

These tests lock the contract at two levels:

1. Producer: ``GenerateContentStage`` returns ``research_context`` in
   ``StageResult.context_updates`` (the only thing the adapter propagates).
2. Adapter seam: a ``make_stage_node``-wrapped stage threads
   ``research_context`` into the LangGraph final state, exactly where the
   qa.ragas / qa.deepeval atoms read it.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, StateGraph

from plugins.stage import StageResult
from services.template_runner import PipelineState, make_stage_node


class _FakeConn:
    """Minimal asyncpg-connection stand-in for make_stage_node's plumbing.

    ``PluginConfig.load`` does ``fetchval(SELECT value ...)`` → returning None
    yields a default ``enabled=True`` config. ``_mark_stage_column`` does an
    ``execute(UPDATE ...)``. ``_emit_progress`` runs with ``site_config=None``
    (no-op). All return benign values.
    """

    async def fetchval(self, *_a: Any, **_kw: Any) -> Any:
        return None

    async def execute(self, *_a: Any, **_kw: Any) -> str:
        return "UPDATE 0"

    async def fetch(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []


class _FakeAcquire:
    async def __aenter__(self) -> "_FakeConn":
        return _FakeConn()

    async def __aexit__(self, *_e: Any) -> None:
        return None


class _FakePool(_FakeConn):
    """Acts as both a bare connection (fetchval/execute) and a pool with
    ``acquire()`` — covers both call shapes in make_stage_node's helpers."""

    def acquire(self) -> "_FakeAcquire":
        return _FakeAcquire()


class _ResearchProducingStage:
    """Minimal stage that mirrors GenerateContentStage's two writes:

    - a bare ``context`` mutation (invisible to the graph_def adapter), AND
    - the same value echoed into ``StageResult.context_updates`` (the fix).

    The test asserts the adapter only forwards what's in context_updates, so
    the echo is mandatory for the value to survive.
    """

    name = "generate_content"
    description = "test double"
    timeout_seconds = 5
    halts_on_failure = True

    def __init__(self, research: str) -> None:
        self._research = research

    async def execute(
        self, context: dict[str, Any], config: dict[str, Any],
    ) -> StageResult:
        # Bare mutation — what the buggy code relied on. The adapter
        # ignores this; only context_updates is merged back.
        context["research_context"] = self._research
        return StageResult(
            ok=True,
            detail="ok",
            context_updates={
                "content": "draft body",
                "research_context": self._research,
            },
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_content_stage_returns_research_context_in_updates():
    """The real stage must echo research_context into context_updates.

    Direct unit assertion on the production GenerateContentStage — the
    companion to the make_stage_node seam test below. Patches the stage's
    external deps so we exercise its control flow only.
    """
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    class _Conn:
        async def fetch(self, *_a: Any) -> list[dict[str, Any]]:
            return []

    class _Ctx:
        async def __aenter__(self) -> "_Conn":
            return _Conn()

        async def __aexit__(self, *_e: Any) -> None:
            return None

    class _Pool:
        def acquire(self) -> "_Ctx":
            return _Ctx()

    class _Db:
        def __init__(self) -> None:
            self.pool = _Pool()

        async def get_task(self, _tid: str) -> dict[str, Any]:
            return {"research_context": "caller-supplied corpus"}

        async def update_task(self, **_kw: Any) -> None:
            return None

        async def log_cost(self, _c: dict[str, Any]) -> None:
            return None

    @asynccontextmanager
    async def _no_gpu_lock(*_a: Any, **_kw: Any):
        yield None

    from modules.content.stages.generate_content import GenerateContentStage

    patches = [
        patch(
            "modules.content.ai_content_generator.get_content_generator",
            return_value=SimpleNamespace(
                _internal_links_cache=[],
                generate_blog_post=AsyncMock(return_value=(
                    "Body text long enough to count.", "glm-4.7-5090",
                    {"models_used_by_phase": {}, "model_selection_log": {}},
                )),
            ),
        ),
        patch("services.model_preferences.parse_model_preferences",
              return_value=("glm-4.7-5090", "ollama")),
        patch("services.writing_style_context.build_writing_style_context",
              AsyncMock(return_value="style")),
        patch("services.research_context.build_rag_context",
              AsyncMock(return_value="rag corpus")),
        patch("services.research_service.ResearchService",
              return_value=SimpleNamespace(
                  build_context=AsyncMock(return_value="auto corpus"))),
        patch("services.title_generation.generate_canonical_title",
              AsyncMock(return_value="A Title")),
        patch("services.title_generation.check_title_originality",
              AsyncMock(return_value={
                  "is_original": True, "max_similarity": 0.1,
                  "similar_titles": [],
              })),
        patch("services.text_utils.normalize_text", side_effect=lambda x: x),
        patch("services.text_utils.scrub_fabricated_links",
              side_effect=lambda x, **_k: x),
        patch("services.self_review.self_review_and_revise",
              AsyncMock(return_value=("x", {"revised": False}))),
        patch("services.gpu_scheduler.gpu",
              SimpleNamespace(lock=_no_gpu_lock)),
        patch("services.audit_log.audit_log_bg", MagicMock()),
    ]
    ctx: dict[str, Any] = {
        "task_id": "t1", "topic": "AI", "style": "", "tone": "",
        "target_length": 500, "tags": [], "models_by_phase": {},
        "database_service": _Db(),
    }
    for p in patches:
        p.start()
    try:
        result = await GenerateContentStage().execute(ctx, {})
    finally:
        for p in reversed(patches):
            p.stop()

    assert result.ok is True
    # The contract that #553 hinges on: research_context rides in
    # context_updates, where make_stage_node can forward it to the rails.
    assert "research_context" in result.context_updates
    rc = result.context_updates["research_context"]
    assert rc and rc.strip(), "research_context must be non-empty when research ran"
    assert "caller-supplied corpus" in rc
    assert "auto corpus" in rc
    assert "rag corpus" in rc
    # And it matches the bare-mutation copy — the two writes stay in lockstep.
    assert result.context_updates["research_context"] == ctx["research_context"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_make_stage_node_threads_research_context_into_state():
    """End-to-end seam: a make_stage_node-wrapped stage must land
    ``research_context`` in the LangGraph final state — the exact key the
    qa.ragas / qa.deepeval atoms read via ``state.get("research_context")``.

    This is the regression guard for #553: pre-fix the stage only mutated
    its local context dict, which the adapter discards, so the rails saw
    ``None`` and skipped.
    """
    corpus = "RELATED POSTS WE'VE PUBLISHED: ...\n\nfacts to ground against"
    g: StateGraph = StateGraph(PipelineState)
    node = make_stage_node(_ResearchProducingStage(corpus), pool=_FakePool())
    g.add_node("generate_content", node)
    g.set_entry_point("generate_content")
    g.add_edge("generate_content", END)
    compiled = g.compile()

    final_state = await compiled.ainvoke({"task_id": "t1", "topic": "AI"})

    # The rails read this exact key off the merged state. It must be present
    # and non-empty — otherwise both grounding rails skip (#553).
    assert final_state.get("research_context") == corpus
    research_sources = final_state.get("research_context")
    assert research_sources and research_sources.strip(), (
        "research_context dropped on the graph_def path — qa.ragas / "
        "qa.deepeval would skip with 'research_sources empty' (#553)"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_make_stage_node_drops_bare_mutation_without_echo():
    """Documents WHY the echo is required: a stage that ONLY mutates the
    local context dict (no context_updates echo) loses the value on the
    graph_def path. This is the failure mode that caused #553.
    """

    class _BareMutationStage:
        name = "bare"
        description = "only mutates context, never echoes"
        timeout_seconds = 5
        halts_on_failure = True

        async def execute(
            self, context: dict[str, Any], config: dict[str, Any],
        ) -> StageResult:
            context["research_context"] = "lost corpus"
            return StageResult(ok=True, detail="ok", context_updates={"content": "x"})

    g: StateGraph = StateGraph(PipelineState)
    g.add_node("bare", make_stage_node(_BareMutationStage(), pool=_FakePool()))
    g.set_entry_point("bare")
    g.add_edge("bare", END)
    compiled = g.compile()

    final_state = await compiled.ainvoke({"task_id": "t1", "topic": "AI"})

    # The bare mutation never reaches the shared state — proving the
    # adapter only forwards context_updates.
    assert "research_context" not in final_state
