"""content.record_pipeline_version — upsert pipeline_versions after finalization.

Extracted from FinalizeTaskStage. Calls PipelineDB.upsert_version to
ensure the pipeline_versions row is written (poindexter#473).

Idempotent — upsert semantics mean re-running is safe.

Produces: stages["5_version_recorded"] = True.

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.record_pipeline_version",
    type="atom",
    version="1.0.0",
    description=(
        "Upsert pipeline_versions row for the finalized task so operators "
        "can read the post in the approval queue (poindexter#473)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="content", type="str", description="finalized body"),
        FieldSpec(name="title", type="str", description="canonical title"),
        FieldSpec(name="database_service", type="object", description="DB service"),
    ),
    outputs=(
        FieldSpec(name="stages", type="dict", description="stages dict with 5_version_recorded=True"),
    ),
    requires=("task_id", "content"),
    produces=("stages",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_write",),
    retry=RetryPolicy(
        max_attempts=3,
        backoff_s=1.0,
        retry_on=("asyncpg.PostgresConnectionError",),
    ),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Upsert pipeline_versions."""
    task_id = state.get("task_id")
    database_service = state.get("database_service")
    if not task_id or database_service is None:
        return {}

    content_text = state.get("content") or ""
    seo_title = state.get("seo_title") or ""
    seo_description = state.get("seo_description") or ""
    seo_keywords_raw = state.get("seo_keywords")
    if isinstance(seo_keywords_raw, list):
        seo_keywords_string = ", ".join(seo_keywords_raw)
    else:
        seo_keywords_string = seo_keywords_raw or ""

    excerpt_text = state.get("excerpt") or ""
    qa_feedback_text = state.get("qa_feedback_formatted") or state.get("qa_feedback") or ""
    final_quality_score = state.get("quality_score") or 0
    final_title = state.get("title") or seo_title or state.get("topic", "")

    try:
        from services.pipeline_db import PipelineDB
        # content.persist_task assembles the full task_metadata and publishes
        # it on this channel one node earlier; we re-assert the same blob.
        task_metadata = state.get("task_metadata") or {}
        version_data = {
            "title": final_title,
            "content": content_text,
            "excerpt": excerpt_text,
            "featured_image_url": state.get("featured_image_url"),
            "seo_title": seo_title,
            "seo_description": seo_description,
            "seo_keywords": seo_keywords_string,
            "quality_score": final_quality_score,
            "qa_feedback": qa_feedback_text,
            "models_used_by_phase": state.get("models_used_by_phase", {}),
            "featured_image_prompt": state.get("featured_image_prompt"),
            "tags": state.get("tags"),
        }
        # Only write the metadata blobs when populated. upsert_version merges
        # via ``stage_data || EXCLUDED.stage_data`` (jsonb shallow-merge, right
        # side wins) and an empty ``{}`` is ``not None`` — so passing an empty
        # dict here would clobber the full metadata persist_task just wrote,
        # wiping preview_token / pre_approve_content off the approval-queue row
        # (Glad-Labs/poindexter#693). Skipping the keys leaves the prior write
        # intact.
        if task_metadata:
            version_data["metadata"] = task_metadata
            version_data["task_metadata"] = task_metadata
        await PipelineDB(database_service.pool).upsert_version(task_id, version_data)
        stages = state.get("stages") or {}
        stages["5_version_recorded"] = True
        logger.info("[content.record_pipeline_version] pipeline_versions upserted for %s", task_id)
        return {"stages": stages}
    except Exception as ver_err:
        logger.warning(
            "[content.record_pipeline_version] pipeline_versions write failed for %s: %s",
            task_id, ver_err,
        )
        return {}


__all__ = ["ATOM_META", "run"]
