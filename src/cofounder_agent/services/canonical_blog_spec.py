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
  qa.vision → qa.topic_delivery → qa.citations → qa.consistency →
  qa.web_factcheck → qa.aggregate). Each rail appends to the operator.add-reduced
  ``qa_rail_reviews`` channel; qa.aggregate combines them into the gate
  decision (and halts on reject). qa.programmatic is the cheap deterministic
  anti-hallucination net (regex/heuristics, NO LLM); it restores the
  ``programmatic_validator`` gate the cutover originally dropped. qa.vision
  (Glad-Labs/poindexter#563) restores the two vision gates the cutover
  dropped — image-relevance + rendered-preview screenshot — both opt-in.
  qa.topic_delivery / qa.citations / qa.consistency / qa.web_factcheck
  (Glad-Labs/poindexter#658/#659/#660/#661) restore four more checks the
  cutover silently dropped — bait-and-switch delivery, dead-link/citation,
  internal-consistency, and web fact-check — all advisory-first (they score
  but do not yet veto). qa.web_factcheck is ordered LAST so qa.aggregate can
  apply its known_wrong_fact rescue (suppressing a stale-regex
  known_wrong_fact veto when the web confirmed the claim, #661).
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
        # qa.vision (#563): image-relevance + rendered-preview screenshot
        # checks. Restores the two vision gates the #355 cutover dropped from
        # the live path (they only lived inside the deleted review() monolith).
        # Reads preview_url, minted early by stage.verify_task. Both opt-in
        # (qa_vision_check_enabled / qa_preview_screenshot_enabled) — a no-op
        # when off, so it's safe in the default chain.
        {"id": "qa_vision", "atom": "qa.vision"},
        # Four more QA checks the #355 cutover silently dropped (advisory-first
        # restoration, #658/#659/#660/#661). Each delegates to a retained
        # MultiModelQA._check_* method, appends to qa_rail_reviews, and is
        # advisory by default (qa_gates.<gate>.required_to_pass=false) — they
        # SCORE but do not yet veto, to be graduated later.
        # qa.topic_delivery (#658): bait-and-switch — body must deliver what the
        # title/topic promised.
        {"id": "qa_topic_delivery", "atom": "qa.topic_delivery"},
        # qa.citations (#659): dead-link / min-citation gate (HTTP HEAD). Distinct
        # from the advisory url_verifier rail.
        {"id": "qa_citations", "atom": "qa.citations"},
        # qa.consistency (#660): cross-section self-contradiction check.
        {"id": "qa_consistency", "atom": "qa.consistency"},
        # qa.self_consistency (#621): HalluCounter-style pairwise-cosine rail.
        # Default-off (self_consistency_enabled=false). Advisory-first.
        {"id": "qa_self_consistency", "atom": "qa.self_consistency"},
        # qa.web_factcheck (#661): DuckDuckGo product/spec verification. Ordered
        # LAST in the qa block, immediately before qa.aggregate, because the
        # known_wrong_fact rescue (qa.aggregate suppressing a stale-regex
        # known_wrong_fact veto when the web confirmed the claim) needs this
        # rail's verdict present when aggregate runs.
        {"id": "qa_web_factcheck", "atom": "qa.web_factcheck"},
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
        {"from": "qa_ragas", "to": "qa_vision"},
        {"from": "qa_vision", "to": "qa_topic_delivery"},
        {"from": "qa_topic_delivery", "to": "qa_citations"},
        {"from": "qa_citations", "to": "qa_consistency"},
        {"from": "qa_consistency", "to": "qa_self_consistency"},
        {"from": "qa_self_consistency", "to": "qa_web_factcheck"},
        {"from": "qa_web_factcheck", "to": "qa_aggregate"},
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
