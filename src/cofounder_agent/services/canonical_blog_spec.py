"""The canonical_blog pipeline as a static graph_def spec (atom-cutover #355).

Pure data — NO imports beyond typing — so a migration can import just this
dict without pulling in LangGraph / template_runner. This is the authoritative
spec; the Plan-4 migration seeds ``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` into
``pipeline_templates.graph_def`` and ``TemplateRunner`` compiles it via
``build_graph_from_spec`` when ``pipeline_use_graph_def`` is on.

Three previously-coarse stage nodes have been decomposed into fine-grained
atom chains (#362):

- ``stage.generate_content`` → 4 content.* atoms:
  content.generate_draft → content.generate_title →
  content.check_title_originality → content.normalize_draft

- ``stage.replace_inline_images`` → 3 content.* atoms:
  content.plan_image_markers → content.generate_images →
  content.inject_images

- ``stage.finalize_task`` → 4 content.* atoms:
  content.compile_meta → content.persist_task →
  content.record_pipeline_version → content.evaluate_auto_publish

The dev_diary template (4-node TEMPLATES factory) still uses
``stage.finalize_task`` directly and is unaffected.

The qa.* rail block and seo.* atom chain are unchanged from #355 except:

- ``qa.guardrails`` was removed (#730): guardrails-ai was uninstalled
  2026-05-12 (CVE), and the standalone atom was a dead no-op burning
  execution time on every run. The native re-implementation
  (``guardrails_rails.py``, #996) provides the advisory ``guardrails_brand``
  / ``guardrails_competitor`` rails; the standalone graph node stays removed.

- The three serial SEO atoms were collapsed into one structured call (#734):
  ``seo.generate_title → seo.generate_description → seo.extract_keywords``
  replaced by ``seo.generate_all_metadata``. Saves ~2 min/post by making
  one LLM call returning ``{title, description, keywords}`` as JSON.
  The three individual atom files are retained as standalone importable units.

- ``cross_model_qa`` → qa.programmatic → qa.critic → qa.deepeval →
  qa.ragas → qa.vision → qa.topic_delivery →
  qa.citations → qa.unlinked_attribution → qa.consistency →
  qa.self_consistency → qa.web_factcheck → qa.aggregate

A deterministic citation-repair atom (``content.reconcile_citations``, #765)
runs after the writer block (before quality_evaluation), and an advisory rail
(``qa.unlinked_attribution``, #765) runs after qa.citations — together they
re-link named sources the writer dropped the URL for and flag the residual.
- ``generate_seo_metadata`` → seo.generate_all_metadata (collapsed, #734)

All chains are sequential for cutover robustness.
"""

from __future__ import annotations

from typing import Any

CANONICAL_BLOG_GRAPH_DEF: dict[str, Any] = {
    "name": "canonical_blog",
    "description": (
        "Canonical blog pipeline (atom-composed, #362/#734): 11 content.* atoms "
        "replace 3 coarse stage nodes + qa.* rail block + seo.generate_all_metadata "
        "(single structured call replacing 3 serial seo.* atoms, #734)."
    ),
    "entry": "verify_task",
    "nodes": [
        {"id": "verify_task", "atom": "stage.verify_task"},
        # content.generate_content decomposition (#362):
        {"id": "generate_draft", "atom": "content.generate_draft"},
        {"id": "generate_title", "atom": "content.generate_title"},
        {"id": "check_title_originality", "atom": "content.check_title_originality"},
        {"id": "normalize_draft", "atom": "content.normalize_draft"},
        # draft_gate (#363): true LangGraph interrupt() approval gate after the
        # writer atom chain. NO-OP UNLESS the operator enables it
        # (pipeline_gate_draft_gate=on); seeded DISABLED so prod canonical_blog
        # runs are unaffected. When on, the gate atom checkpoints the graph and
        # pauses for operator approval; resume via `poindexter pipeline resume
        # <task_id>` (LangGraph reloads the checkpoint and continues from here,
        # skipping the upstream writer nodes). gate_name is seeded from the
        # node config so the atom's `requires=('task_id','gate_name')` is
        # satisfied at build/seed validation time.
        {"id": "draft_gate", "atom": "atoms.approval_gate",
         "config": {"gate_name": "draft_gate"}},
        {"id": "writer_self_review", "atom": "stage.writer_self_review"},
        {"id": "resolve_internal_link_placeholders", "atom": "stage.resolve_internal_link_placeholders"},
        # Deterministic citation repair (#765): re-link named sources the writer
        # dropped the URL for, matching them against the research corpus by
        # domain handle. Placed BEFORE quality_evaluation/url_validation/qa.* so
        # the inserted links flow through the dead-link check like any citation.
        {"id": "reconcile_citations", "atom": "content.reconcile_citations"},
        {"id": "quality_evaluation", "atom": "stage.quality_evaluation"},
        {"id": "url_validation", "atom": "stage.url_validation"},
        # content.replace_inline_images decomposition (#362):
        {"id": "plan_image_markers", "atom": "content.plan_image_markers"},
        {"id": "generate_images", "atom": "content.generate_images"},
        {"id": "inject_images", "atom": "content.inject_images"},
        {"id": "source_featured_image", "atom": "stage.source_featured_image"},
        # Re-caption inline + featured images with vision (qwen3-vl) so alt
        # text describes the ACTUAL rendered pixels, not the generation prompt.
        {"id": "caption_images", "atom": "stage.caption_images"},
        # qa.* rail block (replaces the cross_model_qa stage) — linear chain.
        {"id": "qa_programmatic", "atom": "qa.programmatic"},
        {"id": "qa_critic", "atom": "qa.critic"},
        {"id": "qa_deepeval", "atom": "qa.deepeval"},
        {"id": "qa_ragas", "atom": "qa.ragas"},
        {"id": "qa_vision", "atom": "qa.vision"},
        {"id": "qa_topic_delivery", "atom": "qa.topic_delivery"},
        {"id": "qa_citations", "atom": "qa.citations"},
        # Advisory rail (#765): flags named-source attributions left unlinked +
        # unmatched against the corpus AFTER reconcile_citations did its repair.
        {"id": "qa_unlinked_attribution", "atom": "qa.unlinked_attribution"},
        {"id": "qa_consistency", "atom": "qa.consistency"},
        {"id": "qa_self_consistency", "atom": "qa.self_consistency"},
        {"id": "qa_web_factcheck", "atom": "qa.web_factcheck"},
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
        # QA rescue cycle: qa.aggregate emits _goto="qa_rewrite" on a rescuable
        # reject; the branch router (build_graph_from_spec) routes here for one
        # bounded revision pass, then the loop edge re-runs the QA block.
        {"id": "qa_rewrite", "atom": "qa.rewrite"},
        # seo.* collapsed into one structured call (#734) — replaces the
        # three-atom serial chain (seo.generate_title → seo.generate_description
        # → seo.extract_keywords) with a single LLM round-trip that returns
        # {title, description, keywords} as JSON, saving ~2 min/post.
        # The three individual atoms are retained as standalone importable units.
        {"id": "seo_all_metadata", "atom": "seo.generate_all_metadata"},
        {"id": "generate_media_scripts", "atom": "stage.generate_media_scripts"},
        {"id": "generate_video_shot_list", "atom": "stage.generate_video_shot_list"},
        # Director self-critique (#video-quality §3.1): revise the shot list
        # before Gate 1 on the writer-grade video_director_model.
        {"id": "review_video_shot_list", "atom": "stage.review_video_shot_list"},
        {"id": "capture_training_data", "atom": "stage.capture_training_data"},
        # content.finalize_task decomposition (#362):
        {"id": "compile_meta", "atom": "content.compile_meta"},
        {"id": "persist_task", "atom": "content.persist_task"},
        {"id": "record_pipeline_version", "atom": "content.record_pipeline_version"},
        {"id": "evaluate_auto_publish", "atom": "content.evaluate_auto_publish"},
    ],
    "edges": [
        {"from": "verify_task", "to": "generate_draft"},
        # generate_content atom chain → draft_gate → writer_self_review
        {"from": "generate_draft", "to": "generate_title"},
        {"from": "generate_title", "to": "check_title_originality"},
        {"from": "check_title_originality", "to": "normalize_draft"},
        {"from": "normalize_draft", "to": "draft_gate"},
        {"from": "draft_gate", "to": "writer_self_review"},
        {"from": "writer_self_review", "to": "resolve_internal_link_placeholders"},
        {"from": "resolve_internal_link_placeholders", "to": "reconcile_citations"},
        {"from": "reconcile_citations", "to": "quality_evaluation"},
        {"from": "quality_evaluation", "to": "url_validation"},
        # replace_inline_images atom chain
        {"from": "url_validation", "to": "plan_image_markers"},
        {"from": "plan_image_markers", "to": "generate_images"},
        {"from": "generate_images", "to": "inject_images"},
        {"from": "inject_images", "to": "source_featured_image"},
        {"from": "source_featured_image", "to": "caption_images"},
        # qa.* rail block
        {"from": "caption_images", "to": "qa_programmatic"},
        {"from": "qa_programmatic", "to": "qa_critic"},
        {"from": "qa_critic", "to": "qa_deepeval"},
        {"from": "qa_deepeval", "to": "qa_ragas"},
        {"from": "qa_ragas", "to": "qa_vision"},
        {"from": "qa_vision", "to": "qa_topic_delivery"},
        {"from": "qa_topic_delivery", "to": "qa_citations"},
        {"from": "qa_citations", "to": "qa_unlinked_attribution"},
        {"from": "qa_unlinked_attribution", "to": "qa_consistency"},
        {"from": "qa_consistency", "to": "qa_self_consistency"},
        {"from": "qa_self_consistency", "to": "qa_web_factcheck"},
        {"from": "qa_web_factcheck", "to": "qa_aggregate"},
        # seo.* collapsed (#734) — single structured call
        {"from": "qa_aggregate", "to": "seo_all_metadata"},
        # QA rescue cycle (default-on, qa_rewrite_max_attempts=1):
        # qa_aggregate -> qa_rewrite is the conditional branch (taken when
        # qa.aggregate sets _goto="qa_rewrite"); qa_rewrite -> qa_programmatic
        # is the bounded back-edge (loop-flagged so DAG validation permits it).
        {"from": "qa_aggregate", "to": "qa_rewrite", "branch": True},
        {"from": "qa_rewrite", "to": "qa_programmatic", "loop": True},
        {"from": "seo_all_metadata", "to": "generate_media_scripts"},
        {"from": "generate_media_scripts", "to": "generate_video_shot_list"},
        {"from": "generate_video_shot_list", "to": "review_video_shot_list"},
        {"from": "review_video_shot_list", "to": "capture_training_data"},
        # finalize_task atom chain
        {"from": "capture_training_data", "to": "compile_meta"},
        {"from": "compile_meta", "to": "persist_task"},
        {"from": "persist_task", "to": "record_pipeline_version"},
        {"from": "record_pipeline_version", "to": "evaluate_auto_publish"},
        {"from": "evaluate_auto_publish", "to": "END"},
    ],
}

__all__ = ["CANONICAL_BLOG_GRAPH_DEF"]
