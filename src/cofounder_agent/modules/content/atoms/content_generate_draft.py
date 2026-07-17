"""content.generate_draft — generate the blog post body via the configured LLM.

Thin atom over the writer orchestrator (``GenerateContentStage``), which lives
in the module-root ``modules.content.writer_core`` library. Importing a
module-root lib (not a stage) keeps this graph node atom-independent — an
architect LLM can place ``content.generate_draft`` on a graph and the writer
implementation stays a library detail. (Relocated 2026-07; the old
``stages/generate_content.py`` path is now a back-compat re-export shim.)

Produces: content, research_context, model_used, models_used_by_phase,
          generate_metrics, niche_slug.

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.generate_draft",
    type="atom",
    version="1.0.0",
    description=(
        "Generate the blog-post body via the configured LLM. Routes niche tasks "
        "through atoms.two_pass_writer; non-niche tasks through the legacy "
        "content_generator. Logs cost to DB."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="topic", type="str", description="article topic"),
        FieldSpec(name="style", type="str", description="writing style", required=False),
        FieldSpec(name="tone", type="str", description="writing tone", required=False),
        FieldSpec(name="tags", type="list", description="tags", required=False),
        FieldSpec(name="models_by_phase", type="dict", description="model overrides per phase", required=False),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="site_config", type="object", description="SiteConfig DI instance", required=False),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="raw draft body"),
        FieldSpec(name="research_context", type="str", description="grounding corpus the writer used"),
        FieldSpec(name="model_used", type="str", description="model that wrote the draft"),
        FieldSpec(name="models_used_by_phase", type="dict", description="per-phase model log"),
        FieldSpec(name="generate_metrics", type="dict", description="raw generation metrics dict"),
        FieldSpec(name="niche_slug", type="str", description="niche slug if set"),
    ),
    requires=("task_id",),
    produces=("content", "research_context", "model_used", "models_used_by_phase", "generate_metrics"),
    capability_tier="writer",
    cost_class="api",
    idempotent=False,
    side_effects=("llm_call", "db_write"),
    retry=RetryPolicy(max_attempts=1, backoff_s=0.0),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Delegate to the writer orchestrator in modules.content.writer_core."""
    from modules.content.writer_core import GenerateContentStage
    from plugins.stage import StageResult

    stage = GenerateContentStage()
    result: StageResult = await stage.execute(context=state, config={})
    if not result.ok:
        raise RuntimeError(f"content.generate_draft failed: {result.detail}")
    updates = result.context_updates or {}
    out = {k: updates[k] for k in (
        "content", "research_context", "model_used",
        "models_used_by_phase", "generate_metrics",
    ) if k in updates} | (
        {"niche_slug": updates["niche_slug"]} if "niche_slug" in updates else {}
    ) | (
        {"model_selection_log": updates["model_selection_log"]}
        if "model_selection_log" in updates else {}
    ) | (
        {"stages": updates["stages"]} if "stages" in updates else {}
    )
    # Observability seam (poindexter#873): hand StageResult.metrics to
    # _wrap_atom via the reserved key so content_length / model_used /
    # prompt_template_key / variant_id and the writer_prompt_* prompt-size
    # fields (poindexter#868) reach atom_runs.metrics. _wrap_atom pops it
    # before LangGraph sees the state, so it never becomes durable state.
    if result.metrics:
        out["_atom_metrics"] = dict(result.metrics)
    return out


__all__ = ["ATOM_META", "run"]
