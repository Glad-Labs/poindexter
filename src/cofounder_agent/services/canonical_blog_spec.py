"""The canonical_blog pipeline as a static graph_def spec (atom-cutover #355).

Pure data — NO imports beyond typing — so a migration can import just this
dict without pulling in LangGraph / template_runner. This is the authoritative
spec; the Plan-4 migration seeds ``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` into
``pipeline_templates.graph_def`` and ``TemplateRunner`` compiles it via
``build_graph_from_spec`` when ``pipeline_use_graph_def`` is on.

Structure: the 13 coarse stages that survive the granularity refactor are
``stage.<name>`` nodes (resolved through the surfaced-stage registry);
the monolithic ``cross_model_qa`` stage is replaced by the Plan-3 qa.* rail
atoms wired as a LINEAR chain (qa.critic → qa.deepeval → qa.guardrails →
qa.ragas → qa.aggregate). Each rail appends to the operator.add-reduced
``qa_rail_reviews`` channel; qa.aggregate combines them into the gate
decision (and halts the graph on reject). Sequential (not parallel fan-out)
for cutover robustness — the rails are parallelizable, so a future spec can
fan them out once the graph_def path is proven.
"""

from __future__ import annotations

from typing import Any

CANONICAL_BLOG_GRAPH_DEF: dict[str, Any] = {
    "name": "canonical_blog",
    "description": (
        "Canonical blog pipeline (atom-composed): 13 coarse stages + the "
        "qa.* rail block replacing cross_model_qa."
    ),
    "entry": "verify_task",
    "nodes": [
        {"id": "verify_task", "atom": "stage.verify_task"},
        {"id": "generate_content", "atom": "stage.generate_content"},
        {"id": "writer_self_review", "atom": "stage.writer_self_review"},
        {"id": "resolve_internal_link_placeholders", "atom": "stage.resolve_internal_link_placeholders"},
        {"id": "quality_evaluation", "atom": "stage.quality_evaluation"},
        {"id": "url_validation", "atom": "stage.url_validation"},
        {"id": "replace_inline_images", "atom": "stage.replace_inline_images"},
        {"id": "source_featured_image", "atom": "stage.source_featured_image"},
        # qa.* rail block (replaces the cross_model_qa stage) — linear chain.
        {"id": "qa_critic", "atom": "qa.critic"},
        {"id": "qa_deepeval", "atom": "qa.deepeval"},
        {"id": "qa_guardrails", "atom": "qa.guardrails"},
        {"id": "qa_ragas", "atom": "qa.ragas"},
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
        {"id": "generate_seo_metadata", "atom": "stage.generate_seo_metadata"},
        {"id": "generate_media_scripts", "atom": "stage.generate_media_scripts"},
        {"id": "generate_video_shot_list", "atom": "stage.generate_video_shot_list"},
        {"id": "capture_training_data", "atom": "stage.capture_training_data"},
        {"id": "finalize_task", "atom": "stage.finalize_task"},
    ],
    "edges": [
        {"from": "verify_task", "to": "generate_content"},
        {"from": "generate_content", "to": "writer_self_review"},
        {"from": "writer_self_review", "to": "resolve_internal_link_placeholders"},
        {"from": "resolve_internal_link_placeholders", "to": "quality_evaluation"},
        {"from": "quality_evaluation", "to": "url_validation"},
        {"from": "url_validation", "to": "replace_inline_images"},
        {"from": "replace_inline_images", "to": "source_featured_image"},
        {"from": "source_featured_image", "to": "qa_critic"},
        {"from": "qa_critic", "to": "qa_deepeval"},
        {"from": "qa_deepeval", "to": "qa_guardrails"},
        {"from": "qa_guardrails", "to": "qa_ragas"},
        {"from": "qa_ragas", "to": "qa_aggregate"},
        {"from": "qa_aggregate", "to": "generate_seo_metadata"},
        {"from": "generate_seo_metadata", "to": "generate_media_scripts"},
        {"from": "generate_media_scripts", "to": "generate_video_shot_list"},
        {"from": "generate_video_shot_list", "to": "capture_training_data"},
        {"from": "capture_training_data", "to": "finalize_task"},
        {"from": "finalize_task", "to": "END"},
    ],
}

__all__ = ["CANONICAL_BLOG_GRAPH_DEF"]
