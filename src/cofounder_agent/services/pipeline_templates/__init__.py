"""Pipeline templates — hand-coded LangGraph factories (v1 POC).

Each template is a function that returns an uncompiled LangGraph
``StateGraph``. The :class:`services.template_runner.TemplateRunner`
compiles the graph (with a checkpointer) at run time.

Phase 1 has two templates:

- ``canonical_blog`` — the 13-stage canonical content pipeline. Each
  existing stage wraps as a LangGraph node via
  :func:`services.template_runner.make_stage_node`. Count updated
  2026-05-15 when ``resolve_internal_link_placeholders`` was added
  to close the ``[posts/<slug>]`` leak path.
- ``dev_diary`` — minimal narrative post for daily build-in-public
  output. Four nodes: verify_task → narrate_bundle (atom) →
  source_featured_image → finalize_task. Skips QA, auto-curator,
  SEO metadata, media scripts — none of which fit a status-report
  artifact.

Phase 4+ adds architect-LLM-composed templates (cached here via the
``pipeline_templates`` table, with python_factory pointing at a runtime
graph builder).

Implements: Glad-Labs/poindexter#358.
"""

from __future__ import annotations

import json
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

    Uses the public ``get_core_samples()`` registry so template factories
    see the same Stage instances the rest of the substrate registers.
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
    # Resolve ``[posts/<slug>]`` placeholders that the writer emits.
    # Must run BEFORE quality_evaluation / cross_model_qa whose
    # programmatic_validator critical-flags any unresolved placeholder
    # (see migration 20260512_213806_seed_unresolved_placeholder_validator_rule.py).
    # Captured 2026-05-15: ~95% canonical_blog rejection rate traced
    # to leaked placeholders. New stage shipped same day.
    "resolve_internal_link_placeholders",
    "quality_evaluation",
    "url_validation",
    "replace_inline_images",
    "source_featured_image",
    "cross_model_qa",
    "generate_seo_metadata",
    "generate_media_scripts",
    # Director — produces shot list for the post's video. Runs after
    # generate_media_scripts so the podcast_script is available (the
    # shot list aligns its narration_offset_s to it). Non-critical
    # (halts_on_failure=False); a director failure leaves
    # context["video_shot_list"] absent and the legacy renderer path
    # keeps running. Lands the shot list in audit_log for operator
    # review (PR 1 of #649 sequenced plan; PR 2 wires the renderer).
    "generate_video_shot_list",
    "capture_training_data",
    "finalize_task",
)


def canonical_blog(
    *, pool: Any, record_sink: list[TemplateRunRecord] | None = None,
) -> StateGraph:
    """The canonical 13-stage blog pipeline as a LangGraph.

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

        # Phase 1 lab harness — pick a variant from the niche's active
        # experiment, if any. Identical hook to the two_pass writer
        # path (see services/stages/generate_content.py). Returns None
        # for the common path (no experiment running for this niche);
        # production behavior unchanged. The runner is internally
        # fail-safe — any DB hiccup logs a warning + returns None.
        variant = None
        nb_niche = atom_input.get("niche_slug")
        nb_task_id = atom_input.get("task_id")
        nb_db = atom_input.get("database_service")
        nb_pool = getattr(nb_db, "pool", None) if nb_db is not None else None
        if nb_niche and nb_pool is not None and nb_task_id:
            try:
                from services import experiment_runner
                variant = await experiment_runner.pick_variant(
                    nb_pool, str(nb_niche), str(nb_task_id),
                )
                experiment_runner.apply_variant_to_state(atom_input, variant)
            except Exception as exc:  # noqa: BLE001 — defense in depth
                logger.warning(
                    "[dev_diary] experiment_runner.pick_variant raised: "
                    "%s — falling back to no variant", exc,
                )
                variant = None

        try:
            result = await _narrate_atom.run(atom_input)
            elapsed_ms = int((_time.time() - t0) * 1000)
            if record_sink is not None:
                # Surface the deterministic quality_score atom returns so
                # capability_outcomes captures the per-node score (the
                # auto_publish_gate's training signal). Without this,
                # quality_score lands on pipeline_versions but never on
                # the per-node outcome row.
                metrics_dict: dict[str, Any] = {
                    "model_used": result.get("model_used", ""),
                    "quality_score": result.get("quality_score"),
                    "word_count": len((result.get("content") or "").split()),
                    # Phase 0 lab observability — propagates the
                    # prompt resolution provenance from the atom
                    # to capability_outcomes.{prompt_template_*}
                    # columns via record_run.
                    "prompt_template_key": result.get("prompt_template_key"),
                    "prompt_template_version": result.get(
                        "prompt_template_version"
                    ),
                    # niche_slug rides on state too, but stamping
                    # it here keeps the per-record metrics dict
                    # self-contained for downstream consumers.
                    "niche_slug": (
                        atom_input.get("niche_slug") or None
                    ),
                }
                # Phase 1 lab harness — when a variant was assigned,
                # stamp its id on the metrics dict so
                # capability_outcomes.record_run picks it up + writes
                # it to the variant_id column. ``apply_variant_to_state``
                # already set it on atom_input above, but stamping
                # here on metrics keeps the per-record dict
                # self-contained.
                if variant is not None:
                    metrics_dict["variant_id"] = variant.variant_id
                    metrics_dict["variant_label"] = variant.variant_label
                    metrics_dict["experiment_id"] = variant.experiment_id
                    metrics_dict["experiment_key"] = variant.experiment_key
                record_sink.append(
                    TemplateRunRecord(
                        name="atoms.narrate_bundle", ok=True,
                        detail=f"{len(result.get('content','') or '')} chars",
                        elapsed_ms=elapsed_ms,
                        metrics=metrics_dict,
                    )
                )
            # Forward variant_id to the returned state so downstream
            # nodes (source_featured_image, finalize_task) — and
            # ultimately record_run via state-level fallback — see the
            # same variant the writer used.
            if variant is not None:
                result["variant_id"] = variant.variant_id
                result["variant_label"] = variant.variant_label
                result["experiment_id"] = variant.experiment_id
                result["experiment_key"] = variant.experiment_key
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
# DB reader: load_active_graph_def
# ---------------------------------------------------------------------------


async def load_active_graph_def(pool: Any, slug: str) -> dict[str, Any] | None:
    """Return the active ``graph_def`` for ``slug`` from ``pipeline_templates``,
    or ``None`` when there's no active row, the row is unreadable, or the
    spec is empty/node-less (the column default ``'{}'``).

    Best-effort: a DB error degrades to ``None`` (the runner falls back to the
    legacy Python factory) rather than failing the run.
    """
    if pool is None or not slug:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph_def FROM pipeline_templates "
                "WHERE slug = $1 AND active = true "
                "ORDER BY version DESC LIMIT 1",
                slug,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pipeline_templates] load_active_graph_def(%r) failed: %s", slug, exc)
        return None
    if not row:
        return None
    raw = row["graph_def"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[pipeline_templates] graph_def for %r is not valid JSON", slug)
            return None
    if not isinstance(raw, dict) or not raw.get("nodes"):
        return None
    return raw


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


TEMPLATES: dict[str, Callable[..., StateGraph]] = {
    "canonical_blog": canonical_blog,
    "dev_diary": dev_diary,
}
