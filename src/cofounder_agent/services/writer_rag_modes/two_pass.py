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
# DI seam (glad-labs-stack#330) — same pattern as _POOL_REGISTRY: the
# dispatcher seeds the SiteConfig keyed by thread_id, nodes look it up
# from state["pool_thread"]. Site_config can't ride in the LangGraph
# state because it's not msgpack-serializable for the checkpointer.
_SITE_CONFIG_REGISTRY: dict[str, Any] = {}

logger = get_logger(__name__)


# Prompt key in UnifiedPromptManager + YAML registry. YAML default at
# prompts/writer_rag_modes.yaml; Langfuse overrides take effect on the
# next get_prompt call. Per feedback_prompts_must_be_db_configurable.
_REVISE_PROMPT_KEY = "writer_rag_modes.two_pass.revise_prompt"


def _resolve_revise_prompt(*, draft: str, aug_block: str) -> str:
    """Pull the TWO_PASS revise prompt via UnifiedPromptManager.

    Langfuse > YAML defaults > inline fallback. The inline constant only
    fires when the prompt registry hasn't been initialized (bootstrap /
    test paths). Production reads from YAML at minimum.
    """
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _REVISE_PROMPT_KEY, draft=draft, aug_block=aug_block,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[writer_rag_modes.two_pass] prompt_manager lookup for %r "
            "failed (%s) — using inline fallback",
            _REVISE_PROMPT_KEY, exc,
        )
        return _REVISE_PROMPT_FALLBACK.format(draft=draft, aug_block=aug_block)


# Inline fallback — last-resort for bootstrap / test / registry-unreachable
# paths. Canonical prompt lives in prompts/writer_rag_modes.yaml.
_REVISE_PROMPT_FALLBACK = """\
Revise the following draft. For each [EXTERNAL_NEEDED: ...] marker,
substitute the corresponding external fact provided below. Keep everything else unchanged.
If revision exposes a new claim that needs outside support, mark it [EXTERNAL_NEEDED: ...]
again so the next pass can fill it.

Original draft:
{draft}

External facts:
{aug_block}
"""


_NEED_PATTERN = re.compile(r"\[EXTERNAL_NEEDED:\s*([^\]]+)\]")

# Hard fallback used when ``writer_rag_two_pass_max_revision_loops`` is
# unset (test fixtures that don't seed site_config). Operators tune the
# real value via migration 0119. ``_MAX_REVISION_LOOPS`` is kept as a
# module constant so existing imports / tests that reference the symbol
# keep importing cleanly, but the live cap is resolved via
# ``_resolve_max_revision_loops()`` so a runtime app_settings change
# takes effect on the next graph invocation without a restart.
_MAX_REVISION_LOOPS = 3


def _resolve_max_revision_loops(site_config: Any = None) -> int:
    if site_config is None:
        return _MAX_REVISION_LOOPS
    try:
        return site_config.get_int(
            "writer_rag_two_pass_max_revision_loops", _MAX_REVISION_LOOPS,
        )
    except Exception:
        return _MAX_REVISION_LOOPS


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
    # niches.writer_prompt_override flowed down from generate_content.py;
    # prepended to _draft_node's instruction. Empty string when no override
    # is set (the historical generic-instruction-only behaviour).
    writer_prompt_override: str
    # task_metadata.context_bundle flowed down from generate_content.py
    # (set by services/jobs/run_dev_diary_post.py for dev_diary tasks).
    # When non-empty, _draft_node inserts a GROUND TRUTH section in the
    # prompt with the actual PR titles + commits + decisions, so the
    # writer stops riffing on the topic title alone. Closes #353. The
    # dict shape matches DevDiaryContext.to_dict() — merged_prs,
    # notable_commits, brain_decisions, audit_resolved, recent_posts,
    # cost_summary, date.
    context_bundle: dict[str, Any]


# -- nodes --

async def _embed_and_fetch_snippets(state: _State) -> _State:
    from services.topic_ranking import embed_text

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    snippet_limit = (
        site_config.get_int("writer_rag_two_pass_snippet_limit", 20)
        if site_config is not None else 20
    )
    qvec = await embed_text(f"{state['topic']} — {state['angle']}")
    # Convert to pgvector text format. asyncpg has no built-in codec for
    # Python list → pgvector; passing the raw list crashes with
    # "expected str, got list" before the ::vector cast can run.
    # Pattern matches services/embeddings_db.py:151 (the established way
    # this codebase passes vectors to pgvector queries).
    qvec_str = "[" + ",".join(str(v) for v in qvec) + "]"
    pool = _POOL_REGISTRY[state["pool_thread"]]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT $2
            """,
            qvec_str,
            snippet_limit,
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
    # Prepend the niche-level writer prompt override (when present) so
    # niche-specific anti-hallucination rules / brand voice / scope
    # restrictions arrive before the mode-specific TWO_PASS instruction.
    # Wired in by migration 0141 + this PR; empty string when the niche
    # has no override set (preserves historical behaviour).
    override = (state.get("writer_prompt_override") or "").strip()
    if override:
        instruction = f"{override}\n\n---\n\n{instruction}"
    # Inject the context_bundle (set by the dev_diary job for dev_diary
    # tasks) as a GROUND TRUTH section. The writer must base claims on
    # these entries; when present, this is the authoritative source —
    # not the topic string, not the snippets. Closes #353. For niche-
    # batch / ad-hoc tasks this is empty and the section is skipped.
    bundle = state.get("context_bundle") or {}
    if bundle:
        ground_truth = _format_bundle_for_prompt(bundle)
        if ground_truth:
            instruction = (
                f"{instruction}\n\n---\n\n"
                f"GROUND TRUTH (today's actual activity — base every "
                f"claim on these entries, do NOT infer or invent details "
                f"the bundle doesn't contain. When you reference a PR or "
                f"commit, use the exact title and link to the URL given):\n\n"
                f"{ground_truth}"
            )
    draft = await generate_with_context(
        topic=state["topic"], angle=state["angle"],
        snippets=state["snippets"], extra_instructions=instruction,
    )
    return {**state, "draft": draft}


def _format_bundle_for_prompt(bundle: dict[str, Any]) -> str:
    """Render the dev_diary context bundle as plain text for the writer.

    Caps each section (top 8 PRs, top 8 commits, top 5 decisions, top 5
    audit, top 5 posts) to keep the prompt within token budget on large
    multi-day windows. Cost-summary is one line. Truncates individual
    titles at 200 chars. Order matches DevDiaryContext.to_dict().
    """
    lines: list[str] = []
    prs = (bundle.get("merged_prs") or [])[:8]
    if prs:
        lines.append("Merged PRs (use these — every PR claim must match a row here):")
        for p in prs:
            num = p.get("number")
            title = (p.get("title") or "")[:200]
            url = p.get("url") or ""
            author = p.get("author") or ""
            body = (p.get("body") or "").strip()
            tag = f"PR #{num}" if num else "PR"
            lines.append(f"- [{tag}] {title}")
            if url:
                lines.append(f"  {url}")
            if author:
                lines.append(f"  author: {author}")
            # PR body is the writer's primary grounding signal — without it
            # the writer guesses meaning from the title alone (see #353
            # follow-up). Cap to first 800 chars per PR to keep the prompt
            # within budget on multi-day windows; with 8 PRs × 800 chars
            # the bodies fit in ~6.5K chars total. Strip blank lines and
            # indent so the structure stays scannable.
            if body:
                # Compact: collapse runs of blank lines + first 800 chars.
                compact_body = "\n".join(
                    ln for ln in body.splitlines() if ln.strip()
                )[:800]
                lines.append("  description:")
                for body_line in compact_body.splitlines():
                    lines.append(f"    {body_line}")
        lines.append("")
    commits = (bundle.get("notable_commits") or [])[:8]
    if commits:
        lines.append("Notable commits:")
        for c in commits:
            sha = (c.get("sha") or "")[:7]
            subject = (c.get("subject") or "")[:200]
            lines.append(f"- [{sha}] {subject}" if sha else f"- {subject}")
        lines.append("")
    decisions = (bundle.get("brain_decisions") or [])[:5]
    if decisions:
        lines.append("Brain decisions (high-confidence calls):")
        for d in decisions:
            summary = (d.get("summary") or d.get("description") or "")[:200]
            if summary:
                lines.append(f"- {summary}")
        lines.append("")
    audit = (bundle.get("audit_resolved") or [])[:5]
    if audit:
        lines.append("Resolved audit events:")
        for a in audit:
            summary = (a.get("summary") or a.get("event") or "")[:200]
            if summary:
                lines.append(f"- {summary}")
        lines.append("")
    posts = (bundle.get("recent_posts") or [])[:5]
    if posts:
        lines.append("Recently published posts:")
        for p in posts:
            title = (p.get("title") or "")[:200]
            url = p.get("url") or ""
            lines.append(f"- {title}" + (f" — {url}" if url else ""))
        lines.append("")
    cost = bundle.get("cost_summary") or {}
    if cost:
        total = cost.get("total_usd")
        if total is not None:
            lines.append(f"Cost summary: ${total:.2f} across LLM calls today.")
            lines.append("")
    return "\n".join(lines).rstrip()


def _detect_needs(state: _State) -> _State:
    needs = _NEED_PATTERN.findall(state["draft"])
    return {**state, "needs": [n.strip() for n in needs]}


async def _research_each(state: _State) -> _State:
    from services.research_service import research_topic

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    max_sources = (
        site_config.get_int("writer_rag_two_pass_research_max_sources", 2)
        if site_config is not None else 2
    )
    results = []
    for need in state["needs"]:
        aug = await research_topic(query=need, max_sources=max_sources)
        results.append({"need": need, "research": aug})
    cumulative = list(state.get("external_lookups") or []) + results
    return {**state, "research_results": results, "external_lookups": cumulative}


async def _revise_node(state: _State) -> _State:
    from services.topic_ranking import _ollama_chat_json

    site_config = _SITE_CONFIG_REGISTRY.get(state["pool_thread"])
    # 2026-05-12 (poindexter#485): replaced 3 hardcoded glm-4.7-5090
    # fallbacks with the shared resolver. See batch 6 (PR #392).
    from services.llm_text import resolve_local_model
    model = resolve_local_model(site_config=site_config)
    aug_block = "\n\n".join(
        f"[EXTERNAL_NEEDED: {r['need']}] → {r['research']}"
        for r in state["research_results"]
    )
    # 2026-05-12 (poindexter#485): migrated the inline f-string revise
    # prompt to UnifiedPromptManager. YAML default at
    # prompts/writer_rag_modes.yaml; Langfuse overrides take effect on
    # the next call.
    revise_prompt = _resolve_revise_prompt(
        draft=state["draft"], aug_block=aug_block,
    )
    new_draft = await _ollama_chat_json(revise_prompt, model=model)
    return {**state, "draft": new_draft, "revision_loops": state.get("revision_loops", 0) + 1}


def _mark_capped(state: _State) -> _State:
    return {**state, "loop_capped": True}


# -- conditional edges --

def _needs_or_done(state: _State) -> str:
    """After detect_needs: route to research_each if needs found AND we haven't
    hit the loop cap, else END (or _done_capped if we're capping)."""
    if not state.get("needs"):
        return END
    site_config = _SITE_CONFIG_REGISTRY.get(state.get("pool_thread", ""))
    if state.get("revision_loops", 0) >= _resolve_max_revision_loops(site_config):
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
    _SITE_CONFIG_REGISTRY[thread_id] = kw.get("site_config")
    try:
        cb_kw = kw.get("context_bundle") or {}
        initial: _State = {
            "topic": topic,
            "angle": angle,
            "pool_thread": thread_id,
            "writer_prompt_override": str(kw.get("writer_prompt_override") or ""),
            "context_bundle": cb_kw if isinstance(cb_kw, dict) else {},
        }
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
        _SITE_CONFIG_REGISTRY.pop(thread_id, None)
