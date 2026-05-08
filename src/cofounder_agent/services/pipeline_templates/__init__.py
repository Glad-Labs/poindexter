"""Pipeline templates — hand-coded LangGraph factories (v1 POC).

Each template is a function that returns an uncompiled LangGraph
``StateGraph``. The :class:`services.template_runner.TemplateRunner`
compiles the graph (with a checkpointer) at run time.

Phase 1 has two templates:

- ``canonical_blog`` — the 12-stage canonical content pipeline, identical
  to today's StageRunner-driven flow. Each existing stage wraps as a
  LangGraph node via :func:`services.template_runner.make_stage_node`.
- ``dev_diary`` — minimal narrative post for daily build-in-public
  output. Three nodes: verify_task → generate_content → finalize_task.
  Skips QA, auto-curator, SEO metadata, media scripts — none of which
  fit a status-report artifact.

Phase 4+ adds architect-LLM-composed templates (cached here via the
``pipeline_templates`` table, with python_factory pointing at a runtime
graph builder).

Spec:
``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Implements: Glad-Labs/poindexter#358.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from services.template_runner import (
    PipelineState,
    TemplateRunRecord,
    _services_from_config,
    halt_or_continue,
    make_stage_node,
)

logger = logging.getLogger(__name__)


def _registered_stages() -> dict[str, Any]:
    """Snapshot of registered Stage instances keyed by name.

    Mirrors :class:`plugins.stage_runner.StageRunner.__init__` — uses
    the public ``get_core_samples()`` so the same stages StageRunner
    sees are available to the template factories.
    """
    from plugins.registry import get_core_samples
    return {
        s.name: s for s in get_core_samples().get("stages", [])
    }


# ---------------------------------------------------------------------------
# canonical_blog — identical behavior to legacy StageRunner default order
# ---------------------------------------------------------------------------


_CANONICAL_BLOG_ORDER: tuple[str, ...] = (
    "verify_task",
    "generate_content",
    "writer_self_review",
    "quality_evaluation",
    "url_validation",
    "replace_inline_images",
    "source_featured_image",
    "cross_model_qa",
    "generate_seo_metadata",
    "generate_media_scripts",
    "capture_training_data",
    "finalize_task",
)


def canonical_blog(
    *, pool: Any, record_sink: list[TemplateRunRecord] | None = None,
) -> StateGraph:
    """The canonical 12-stage blog pipeline as a LangGraph.

    Each stage runs as a wrapped node; conditional edges between them
    short-circuit to END when a node sets ``state['_halt']`` (mirroring
    StageRunner's halt-on-failure / continue_workflow=False semantics).

    Behaviour is intended to be identical to ``StageRunner.run_all`` with
    the canonical order — this is the regression baseline for the v1
    lift. Any divergence from StageRunner is a v1 bug.
    """
    stages_by_name = _registered_stages()
    g: StateGraph = StateGraph(PipelineState)

    # Build node list, skipping any stage not registered (mirrors
    # StageRunner's "in order but not registered" log + skip behaviour).
    present_stages: list[str] = []
    for name in _CANONICAL_BLOG_ORDER:
        stage = stages_by_name.get(name)
        if stage is None:
            logger.info(
                "[canonical_blog] %r in order but not registered — skipping",
                name,
            )
            continue
        g.add_node(name, make_stage_node(stage, pool, record_sink=record_sink))
        present_stages.append(name)

    if not present_stages:
        # Nothing registered — graph has no nodes. Add a no-op END so
        # compile() doesn't fail; runner returns immediately.
        return g

    g.set_entry_point(present_stages[0])
    # Wire halt-aware edges between every consecutive pair.
    for src, dst in zip(present_stages, present_stages[1:]):
        g.add_conditional_edges(src, halt_or_continue(dst), {dst: dst, END: END})
    # Last stage → END (with halt check so a halt on the last stage is
    # still recorded properly via the conditional router).
    g.add_conditional_edges(
        present_stages[-1], halt_or_continue(END), {END: END},
    )
    return g


# ---------------------------------------------------------------------------
# dev_diary — minimal narrative post (v1)
# ---------------------------------------------------------------------------


def dev_diary(
    *, pool: Any, record_sink: list[TemplateRunRecord] | None = None,
) -> StateGraph:
    """dev_diary template — narrative post from the daily PR/commit bundle.

    Three nodes:

    1. ``verify_task`` (existing stage) — confirm the task row exists.
    2. ``narrate_bundle`` (new atom under ``services.atoms``) — single
       LLM call that reads the preserved bundle from
       ``pipeline_versions.stage_data._dev_diary_bundle`` and produces
       2-3 paragraphs of narrative prose with inline PR markdown
       links. Replaces the v1 path that ran ``generate_content`` with
       writer_rag_mode=DETERMINISTIC_COMPOSITOR — which produced
       narrative paragraphs PLUS a long deterministic ``## PRs and
       commits`` bullet list, and read like a changelog. The atom
       drops the bullet list entirely; PR refs are inline in the
       prose.
    3. ``finalize_task`` (existing stage) — persists the post at
       awaiting_approval with full task metadata.

    Skipped vs canonical_blog: writer_self_review, quality_evaluation,
    url_validation, replace_inline_images, cross_model_qa,
    generate_seo_metadata, generate_media_scripts, capture_training_data.
    None of these fit a status-report artifact — either they're
    irrelevant (SEO for build-in-public) or they actively harm the
    output (cross_model_qa rewriting clean prose into hallucinated
    tutorials, see 2026-05-04 saga).

    source_featured_image runs between narrate_bundle and finalize_task
    (re-added 2026-05-07): it's purely additive, sets featured_image_url
    + featured_image_data, and doesn't touch the prose. dev_diary posts
    on 2026-05-05/06 had no hero image because of the original skip;
    operator confirmed images are wanted on the daily build-in-public
    posts (Telegram, 2026-05-07).
    """
    from services.atoms import narrate_bundle as _narrate_atom

    stages_by_name = _registered_stages()
    g: StateGraph = StateGraph(PipelineState)

    # Build the wrapped narrate node so it appears in the run record
    # alongside the wrapped stage nodes (uniform observability).
    # ``config`` annotated as ``RunnableConfig`` so LangGraph's
    # KWARGS_CONFIG_KEYS injector recognizes it and threads the
    # configurable dict through. ``RunnableConfig | None`` would NOT
    # match (the union annotation becomes a string at runtime under
    # ``from __future__ import annotations`` and isn't on the allow-
    # list) — config would silently arrive as None and the partition
    # would lose service handles. See template_runner.make_stage_node.
    async def narrate_node(
        state: PipelineState,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        import time as _time
        t0 = _time.time()
        # Merge service handles from RunnableConfig (Glad-Labs/poindexter#382)
        # so the atom sees the same shape it did pre-partition. Without
        # this the atom's ``state.get("database_service")`` would be
        # None and the bundle-fallback DB read would silently no-op.
        atom_input: dict[str, Any] = dict(state)
        for svc_key, svc_value in _services_from_config(config).items():
            atom_input.setdefault(svc_key, svc_value)
        try:
            result = await _narrate_atom.run(atom_input)
            elapsed_ms = int((_time.time() - t0) * 1000)
            if record_sink is not None:
                # Surface the deterministic quality_score atom returns so
                # capability_outcomes captures the per-node score (the
                # auto_publish_gate's training signal). Without this,
                # quality_score lands on pipeline_versions but never on
                # the per-node outcome row.
                record_sink.append(
                    TemplateRunRecord(
                        name="atoms.narrate_bundle", ok=True,
                        detail=f"{len(result.get('content','') or '')} chars",
                        elapsed_ms=elapsed_ms,
                        metrics={
                            "model_used": result.get("model_used", ""),
                            "quality_score": result.get("quality_score"),
                            "word_count": len((result.get("content") or "").split()),
                        },
                    )
                )
            return result
        except Exception as exc:
            elapsed_ms = int((_time.time() - t0) * 1000)
            logger.exception("[dev_diary] narrate_bundle raised: %s", exc)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name="atoms.narrate_bundle", ok=False,
                        detail=f"raised {type(exc).__name__}: {exc}",
                        halted=True, elapsed_ms=elapsed_ms,
                    )
                )
            return {"_halt": True, "_halt_reason": f"narrate_bundle: {exc}"}

    nodes_added: list[str] = []

    # 1. verify_task
    verify_stage = stages_by_name.get("verify_task")
    if verify_stage is None:
        logger.warning("[dev_diary] verify_task not registered — running atom only")
    else:
        g.add_node("verify_task", make_stage_node(
            verify_stage, pool, record_sink=record_sink,
        ))
        nodes_added.append("verify_task")

    # 2. narrate_bundle (atom)
    g.add_node("narrate_bundle", narrate_node)
    nodes_added.append("narrate_bundle")

    # 3. source_featured_image — additive only (writes featured_image_url
    #    + featured_image_data, never modifies prose). Reads
    #    generate_featured_image flag from context (default True).
    featured_stage = stages_by_name.get("source_featured_image")
    if featured_stage is None:
        logger.warning(
            "[dev_diary] source_featured_image not registered — posts "
            "will publish without a hero image"
        )
    else:
        g.add_node("source_featured_image", make_stage_node(
            featured_stage, pool, record_sink=record_sink,
        ))
        nodes_added.append("source_featured_image")

    # 4. finalize_task
    finalize_stage = stages_by_name.get("finalize_task")
    if finalize_stage is None:
        logger.warning(
            "[dev_diary] finalize_task not registered — post will not "
            "transition to awaiting_approval"
        )
    else:
        g.add_node("finalize_task", make_stage_node(
            finalize_stage, pool, record_sink=record_sink,
        ))
        nodes_added.append("finalize_task")

    if not nodes_added:
        return g

    g.set_entry_point(nodes_added[0])
    for src, dst in zip(nodes_added, nodes_added[1:]):
        g.add_conditional_edges(src, halt_or_continue(dst), {dst: dst, END: END})
    g.add_conditional_edges(
        nodes_added[-1], halt_or_continue(END), {END: END},
    )
    return g


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


TEMPLATES: dict[str, Callable[..., StateGraph]] = {
    "canonical_blog": canonical_blog,
    "dev_diary": dev_diary,
}
