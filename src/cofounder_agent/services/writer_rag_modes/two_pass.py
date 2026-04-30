"""TWO_PASS — internal-first draft, then conditional external fact-augmentation
loop. Implemented as a LangGraph state machine because:

- Multi-pass with conditional re-entry (revise can surface new
  [EXTERNAL_NEEDED] markers that need another research pass)
- Bounded loop (_MAX_REVISION_LOOPS=3 prevents runaway)
- Future-friendly: when we add an auto-researcher agent or a draft-critic
  loop, they slot in as new nodes/edges rather than refactoring orchestration

Spec §"OSS leverage decisions" — TWO_PASS is the only writer mode using
LangGraph; the simpler modes (TOPIC_ONLY, CITATION_BUDGET, STORY_SPINE)
stay plain Python because they don't have branching.

State flow:

    embed_and_fetch → draft → detect_needs ┐
                                            │ if needs found and loops < max:
                                            ↓
                                  research_each → revise ─┐
                                                           │
                                                           ↓
                                                  detect_needs (loop)
                                                           │
                                                           ↓ if no needs OR loops capped
                                                          END

Deviations from plan:

- Imports `embed_text` from `services.topic_ranking` instead of
  `services.embedding_service` because the embedding_service module has
  no module-level `embed_text` helper — that helper lives in topic_ranking.
- Stores the asyncpg pool in a module-level `_POOL_REGISTRY` keyed by
  thread_id rather than directly in graph state. The MemorySaver
  checkpointer msgpack-serializes state on every step; an asyncpg.Pool
  (or a MagicMock thereof) is not serializable and recurses through
  attribute access. Holding the pool out-of-band keeps state msgpack-clean
  while preserving the same per-thread isolation the plan called for.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from services.logger_config import get_logger

# Module-level keyed-by-thread store for non-serializable state (the asyncpg
# pool). The MemorySaver checkpointer serializes state via msgpack, and an
# asyncpg.Pool is not msgpack-able (and a MagicMock pool in tests recurses
# forever during attribute introspection). Holding the pool out-of-band lets
# checkpointing serialize only the data it needs (snippets, drafts, needs).
_POOL_REGISTRY: dict[str, Any] = {}

logger = get_logger(__name__)


_MAX_REVISION_LOOPS = 3
_NEED_PATTERN = re.compile(r"\[EXTERNAL_NEEDED:\s*([^\]]+)\]")


class _State(TypedDict, total=False):
    topic: str
    angle: str
    snippets: list[dict[str, Any]]
    # NOTE: `pool` is NOT stored in state — it's a non-serializable
    # asyncpg.Pool (or test MagicMock) and would crash msgpack in the
    # checkpointer. See _POOL_REGISTRY above. We keep the field in the
    # TypedDict for type-shape compatibility but never write it.
    pool_thread: str  # lookup key into _POOL_REGISTRY
    draft: str
    needs: list[str]
    research_results: list[dict[str, Any]]
    external_lookups: list[dict[str, Any]]
    revision_loops: int
    loop_capped: bool


# -- nodes --

async def _embed_and_fetch_snippets(state: _State) -> _State:
    from services.topic_ranking import embed_text
    qvec = await embed_text(f"{state['topic']} — {state['angle']}")
    pool = _POOL_REGISTRY[state["pool_thread"]]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT 20
            """,
            qvec,
        )
    snippets = [{"source": r["source_table"], "ref": str(r["source_id"]),
                 "snippet": r["text_preview"]} for r in rows]
    return {**state, "snippets": snippets, "revision_loops": 0,
            "external_lookups": [], "loop_capped": False}


async def _draft_node(state: _State) -> _State:
    from services.ai_content_generator import generate_with_context
    instruction = (
        "Write a first-draft blog post drawing ONLY from the provided internal "
        "snippets. Do NOT make up external facts, statistics, or quotes you cannot "
        "ground in a snippet. If you need an outside fact you don't have, mark it "
        "[EXTERNAL_NEEDED: <description>] in the draft so a follow-up pass can fill it in."
    )
    draft = await generate_with_context(
        topic=state["topic"], angle=state["angle"],
        snippets=state["snippets"], extra_instructions=instruction,
    )
    return {**state, "draft": draft}


def _detect_needs(state: _State) -> _State:
    needs = _NEED_PATTERN.findall(state["draft"])
    return {**state, "needs": [n.strip() for n in needs]}


async def _research_each(state: _State) -> _State:
    from services.research_service import research_topic
    results = []
    for need in state["needs"]:
        aug = await research_topic(query=need, max_sources=2)
        results.append({"need": need, "research": aug})
    cumulative = list(state.get("external_lookups") or []) + results
    return {**state, "research_results": results, "external_lookups": cumulative}


async def _revise_node(state: _State) -> _State:
    from services.topic_ranking import _ollama_chat_json
    aug_block = "\n\n".join(
        f"[EXTERNAL_NEEDED: {r['need']}] → {r['research']}"
        for r in state["research_results"]
    )
    revise_prompt = f"""Revise the following draft. For each [EXTERNAL_NEEDED: ...] marker,
substitute the corresponding external fact provided below. Keep everything else unchanged.
If revision exposes a new claim that needs outside support, mark it [EXTERNAL_NEEDED: ...]
again so the next pass can fill it.

Original draft:
{state['draft']}

External facts:
{aug_block}
"""
    new_draft = await _ollama_chat_json(revise_prompt, model="glm-4.7-5090:latest")
    return {**state, "draft": new_draft, "revision_loops": state.get("revision_loops", 0) + 1}


def _mark_capped(state: _State) -> _State:
    return {**state, "loop_capped": True}


# -- conditional edges --

def _needs_or_done(state: _State) -> str:
    """After detect_needs: route to research_each if needs found AND we haven't
    hit the loop cap, else END (or _done_capped if we're capping)."""
    if not state.get("needs"):
        return END
    if state.get("revision_loops", 0) >= _MAX_REVISION_LOOPS:
        return "_done_capped"
    return "research_each"


def _build_graph():
    g = StateGraph(_State)
    g.add_node("embed_and_fetch", _embed_and_fetch_snippets)
    g.add_node("draft", _draft_node)
    g.add_node("detect_needs", _detect_needs)
    g.add_node("research_each", _research_each)
    g.add_node("revise", _revise_node)
    g.add_node("_done_capped", _mark_capped)

    g.set_entry_point("embed_and_fetch")
    g.add_edge("embed_and_fetch", "draft")
    g.add_edge("draft", "detect_needs")
    g.add_conditional_edges("detect_needs", _needs_or_done, {
        "research_each": "research_each",
        "_done_capped": "_done_capped",
        END: END,
    })
    g.add_edge("research_each", "revise")
    g.add_edge("revise", "detect_needs")
    g.add_edge("_done_capped", END)

    # MemorySaver checkpointer means the graph CAN be paused mid-run and
    # resumed — useful when an operator interrupts mid-revision in v2.
    # v1 uses in-memory; v2 swaps to a postgres checkpointer for
    # cross-process durability.
    return g.compile(checkpointer=MemorySaver())


_GRAPH = _build_graph()


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    thread_id = f"two_pass-{niche_id}-{topic[:32]}"
    _POOL_REGISTRY[thread_id] = pool
    try:
        initial: _State = {"topic": topic, "angle": angle, "pool_thread": thread_id}
        config = {"configurable": {"thread_id": thread_id}}
        final = await _GRAPH.ainvoke(initial, config=config)
        return {
            "draft": final["draft"],
            "snippets_used": final.get("snippets", []),
            "external_lookups": final.get("external_lookups", []),
            "revision_loops": final.get("revision_loops", 0),
            "loop_capped": final.get("loop_capped", False),
            "mode": "TWO_PASS",
        }
    finally:
        _POOL_REGISTRY.pop(thread_id, None)
