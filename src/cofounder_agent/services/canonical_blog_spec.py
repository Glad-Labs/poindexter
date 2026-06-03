"""The canonical_blog pipeline as a static graph_def spec (atom-cutover #355).

Pure data — NO imports beyond typing — so a migration can import just this
dict without pulling in LangGraph / template_runner. This is the authoritative
spec; the Plan-4 migration seeds ``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` into
``pipeline_templates.graph_def`` and ``TemplateRunner`` compiles it via
``build_graph_from_spec`` when ``pipeline_use_graph_def`` is on.

Structure: the coarse stages still awaiting granularity decomposition are
``stage.<name>`` nodes (resolved through the surfaced-stage registry). Two
stages have already been decomposed into atom chains (#362):

- ``cross_model_qa`` → the Plan-3 qa.* rail atoms wired as a LINEAR chain
  (qa.programmatic → qa.critic → qa.deepeval → qa.guardrails → qa.ragas →
  qa.aggregate). Each rail appends to the operator.add-reduced
  ``qa_rail_reviews`` channel; qa.aggregate combines them into the gate
  decision (and halts on reject). qa.programmatic is the cheap deterministic
  anti-hallucination net (regex/heuristics, NO LLM); it restores the
  ``programmatic_validator`` gate the cutover originally dropped.
- ``generate_seo_metadata`` → the seo.* atom chain (seo.generate_title →
  seo.generate_description → seo.extract_keywords). Linear so description +
  keywords read the freshly-generated ``seo_title``; each is LLM-driven with
  a programmatic fallback on failure.

Both chains are sequential (not parallel fan-out) for cutover robustness —
the rails are parallelizable, so a future spec can fan them out once the
graph_def path is proven.
"""

from __future__ import annotations

from typing import Any

CANONICAL_BLOG_GRAPH_DEF: dict[str, Any] = {
    "name": "canonical_blog",
    "description": (
        "Canonical blog pipeline (atom-composed): coarse stages + the qa.* "
        "rail block (replacing cross_model_qa) + the seo.* atom chain "
        "(replacing generate_seo_metadata)."
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
        # Re-caption inline + featured images with vision (qwen3-vl) so alt
        # text describes the ACTUAL rendered pixels, not the generation prompt.
        {"id": "caption_images", "atom": "stage.caption_images"},
        # qa.* rail block (replaces the cross_model_qa stage) — linear chain.
        # qa.programmatic FIRST: the cheap deterministic anti-hallucination net
        # (regex/heuristics, NO LLM). Restores the programmatic_validator gate
        # the #355 cutover dropped — a critical fabrication vetoes in
        # qa.aggregate when qa_gates.programmatic_validator.required_to_pass=true.
        {"id": "qa_programmatic", "atom": "qa.programmatic"},
        {"id": "qa_critic", "atom": "qa.critic"},
        {"id": "qa_deepeval", "atom": "qa.deepeval"},
        {"id": "qa_guardrails", "atom": "qa.guardrails"},
        {"id": "qa_ragas", "atom": "qa.ragas"},
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
        # seo.* atom chain (replaces the generate_seo_metadata stage, #362) —
        # linear so description + keywords read the freshly-generated seo_title.
        {"id": "seo_title", "atom": "seo.generate_title"},
        {"id": "seo_description", "atom": "seo.generate_description"},
        {"id": "seo_keywords", "atom": "seo.extract_keywords"},
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
        {"from": "source_featured_image", "to": "caption_images"},
        {"from": "caption_images", "to": "qa_programmatic"},
        {"from": "qa_programmatic", "to": "qa_critic"},
        {"from": "qa_critic", "to": "qa_deepeval"},
        {"from": "qa_deepeval", "to": "qa_guardrails"},
        {"from": "qa_guardrails", "to": "qa_ragas"},
        {"from": "qa_ragas", "to": "qa_aggregate"},
        {"from": "qa_aggregate", "to": "seo_title"},
        {"from": "seo_title", "to": "seo_description"},
        {"from": "seo_description", "to": "seo_keywords"},
        {"from": "seo_keywords", "to": "generate_media_scripts"},
        {"from": "generate_media_scripts", "to": "generate_video_shot_list"},
        {"from": "generate_video_shot_list", "to": "capture_training_data"},
        {"from": "capture_training_data", "to": "finalize_task"},
        {"from": "finalize_task", "to": "END"},
    ],
}

__all__ = ["CANONICAL_BLOG_GRAPH_DEF"]
