"""Migration: re-seed canonical_blog graph_def with 11 new content.* atoms (#362).

Decomposes 3 coarse stage.* nodes into fine-grained atom chains:

  stage.generate_content  →  content.generate_draft → content.generate_title
                              → content.check_title_originality
                              → content.normalize_draft

  stage.replace_inline_images  →  content.plan_image_markers
                                   → content.generate_images
                                   → content.inject_images

  stage.finalize_task  →  content.compile_meta → content.persist_task
                           → content.record_pipeline_version
                           → content.evaluate_auto_publish

The stage files are preserved (dev_diary uses stage.finalize_task on its
legacy TEMPLATES factory path, which is unaffected).

Rollback: hardcoded prior graph_def (v4 from 20260603_010000). Note that a
rollback to the prior graph_def requires re-running the v4 migration if those
atoms are needed; this migration simply sets the graph_def back to that known
good state.

IMPORTANT: imports only stdlib + the spec dict (no LangGraph / template_runner)
so the migrations-smoke CI step can apply this without a full app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Import only the spec dict — no heavy deps.
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF  # noqa: E402

# ---------------------------------------------------------------------------
# Rollback: the graph_def as it existed before this migration
# (stage.generate_content / stage.replace_inline_images / stage.finalize_task
# as coarse nodes — 21-node spec from 20260603_010000).
# ---------------------------------------------------------------------------
_ROLLBACK_GRAPH_DEF = {
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
        {"id": "caption_images", "atom": "stage.caption_images"},
        {"id": "qa_programmatic", "atom": "qa.programmatic"},
        {"id": "qa_critic", "atom": "qa.critic"},
        {"id": "qa_deepeval", "atom": "qa.deepeval"},
        {"id": "qa_guardrails", "atom": "qa.guardrails"},
        {"id": "qa_ragas", "atom": "qa.ragas"},
        {"id": "qa_vision", "atom": "qa.vision"},
        {"id": "qa_topic_delivery", "atom": "qa.topic_delivery"},
        {"id": "qa_citations", "atom": "qa.citations"},
        {"id": "qa_consistency", "atom": "qa.consistency"},
        {"id": "qa_self_consistency", "atom": "qa.self_consistency"},
        {"id": "qa_web_factcheck", "atom": "qa.web_factcheck"},
        {"id": "qa_aggregate", "atom": "qa.aggregate"},
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


async def up(pool) -> None:
    graph_def_json = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def  = $1::jsonb,
                   updated_at = NOW()
             WHERE name = 'canonical_blog'
            """,
            graph_def_json,
        )
    logger.info(
        "Migration atom_granularity_generate_content up: re-seeded canonical_blog "
        "graph_def with 11 content.* atoms (#362). result=%s",
        result,
    )


async def down(pool) -> None:
    rollback_json = json.dumps(_ROLLBACK_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def  = $1::jsonb,
                   updated_at = NOW()
             WHERE name = 'canonical_blog'
            """,
            rollback_json,
        )
    logger.info(
        "Migration atom_granularity_generate_content down: restored pre-#362 "
        "canonical_blog graph_def (stage.generate_content / replace_inline_images "
        "/ finalize_task coarse nodes). result=%s",
        result,
    )
