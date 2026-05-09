"""
Pipeline Database Module — writes to the new pipeline_* tables.

Phase 2 of issue #211 (content_tasks split). This module mirrors writes
to the new normalized tables. During dual-write mode, tasks_db.py calls
these functions after each content_tasks write so both schemas stay in sync.

Tables:
    pipeline_tasks         — task queue and lifecycle
    pipeline_versions      — generated content with version history
    pipeline_distributions — per-target publish tracking

Note: approval/rejection writes have moved to ``pipeline_gate_history``
(see Glad-Labs/poindexter#366). The legacy ``add_review`` helper that
wrote to ``pipeline_reviews`` was retired 2026-05-09 once the unification
backfill landed and the ``content_tasks`` view started reading from
``pipeline_gate_history``. Callers now write directly to
``pipeline_gate_history`` (the SQL is a single ``INSERT`` and the table
shape doesn't warrant another helper).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from asyncpg import Pool

logger = logging.getLogger(__name__)


class PipelineDB:
    """Dual-write target for the new pipeline tables."""

    def __init__(self, pool: Pool):
        self.pool = pool

    # ------------------------------------------------------------------
    # pipeline_tasks
    # ------------------------------------------------------------------

    async def upsert_task(self, task_id: str, data: dict[str, Any]) -> None:
        """Insert or update a pipeline_tasks row."""
        try:
            await self.pool.execute(
                """
                INSERT INTO pipeline_tasks (
                    task_id, task_type, topic, status, stage, site_id,
                    style, tone, target_length, category, primary_keyword,
                    target_audience, percentage, message, model_used,
                    error_message, created_at, updated_at, started_at, completed_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                ON CONFLICT (task_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    stage = EXCLUDED.stage,
                    percentage = EXCLUDED.percentage,
                    message = EXCLUDED.message,
                    model_used = EXCLUDED.model_used,
                    error_message = EXCLUDED.error_message,
                    updated_at = EXCLUDED.updated_at,
                    started_at = COALESCE(EXCLUDED.started_at, pipeline_tasks.started_at),
                    completed_at = COALESCE(EXCLUDED.completed_at, pipeline_tasks.completed_at)
                """,
                task_id,
                data.get("task_type", "blog_post"),
                data.get("topic") or data.get("title", "untitled"),
                data.get("status", "pending"),
                data.get("stage", "pending"),
                data.get("site_id"),
                data.get("style", "technical"),
                data.get("tone", "professional"),
                data.get("target_length", 1500),
                data.get("category"),
                data.get("primary_keyword"),
                data.get("target_audience"),
                data.get("percentage", 0),
                data.get("message"),
                data.get("model_used"),
                data.get("error_message"),
                data.get("created_at", datetime.now(timezone.utc)),
                data.get("updated_at", datetime.now(timezone.utc)),
                data.get("started_at"),
                data.get("completed_at"),
            )
        except Exception as e:
            logger.warning("[pipeline_db] upsert_task failed for %s: %s", task_id, e)

    async def update_task_status(
        self, task_id: str, status: str, **kwargs: Any
    ) -> None:
        """Update status and optional fields on pipeline_tasks."""
        try:
            await self.pool.execute(
                """
                UPDATE pipeline_tasks SET
                    status = $2,
                    stage = COALESCE($3, stage),
                    percentage = COALESCE($4, percentage),
                    message = COALESCE($5, message),
                    model_used = COALESCE($6, model_used),
                    error_message = COALESCE($7, error_message),
                    updated_at = NOW(),
                    completed_at = CASE WHEN $2 IN ('published','failed','cancelled','rejected','rejected_final') THEN NOW() ELSE completed_at END
                WHERE task_id = $1
                """,
                task_id,
                status,
                kwargs.get("stage"),
                kwargs.get("percentage"),
                kwargs.get("message"),
                kwargs.get("model_used"),
                kwargs.get("error_message"),
            )
        except Exception as e:
            logger.warning("[pipeline_db] update_task_status failed for %s: %s", task_id, e)

    # ------------------------------------------------------------------
    # pipeline_versions
    # ------------------------------------------------------------------

    async def upsert_version(self, task_id: str, data: dict[str, Any]) -> None:
        """Insert or update version 1 of pipeline_versions (current content state)."""
        try:
            stage_data = {}
            for key in ("metadata", "result", "task_metadata", "model_selections",
                        "model_selection_log", "featured_image_data",
                        "featured_image_prompt", "tags", "progress"):
                val = data.get(key)
                if val is not None:
                    stage_data[key] = val if isinstance(val, (dict, list)) else str(val)

            await self.pool.execute(
                """
                INSERT INTO pipeline_versions (
                    task_id, version, title, content, excerpt,
                    featured_image_url, seo_title, seo_description, seo_keywords,
                    quality_score, qa_feedback, models_used_by_phase, stage_data
                ) VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (task_id, version) DO UPDATE SET
                    title = COALESCE(EXCLUDED.title, pipeline_versions.title),
                    content = COALESCE(EXCLUDED.content, pipeline_versions.content),
                    excerpt = COALESCE(EXCLUDED.excerpt, pipeline_versions.excerpt),
                    featured_image_url = COALESCE(EXCLUDED.featured_image_url, pipeline_versions.featured_image_url),
                    seo_title = COALESCE(EXCLUDED.seo_title, pipeline_versions.seo_title),
                    seo_description = COALESCE(EXCLUDED.seo_description, pipeline_versions.seo_description),
                    seo_keywords = COALESCE(EXCLUDED.seo_keywords, pipeline_versions.seo_keywords),
                    quality_score = COALESCE(EXCLUDED.quality_score, pipeline_versions.quality_score),
                    qa_feedback = COALESCE(EXCLUDED.qa_feedback, pipeline_versions.qa_feedback),
                    models_used_by_phase = COALESCE(EXCLUDED.models_used_by_phase, pipeline_versions.models_used_by_phase),
                    stage_data = pipeline_versions.stage_data || EXCLUDED.stage_data
                """,
                task_id,
                data.get("title"),
                data.get("content"),
                data.get("excerpt"),
                data.get("featured_image_url"),
                data.get("seo_title"),
                data.get("seo_description"),
                data.get("seo_keywords"),
                data.get("quality_score"),
                data.get("qa_feedback"),
                json.dumps(data.get("models_used_by_phase", {})),
                json.dumps(stage_data) if stage_data else "{}",
            )
        except Exception as e:
            logger.warning("[pipeline_db] upsert_version failed for %s: %s", task_id, e)

    # ------------------------------------------------------------------
    # pipeline_distributions
    # ------------------------------------------------------------------

    async def add_distribution(
        self, task_id: str, target: str, post_id: str | None = None,
        post_slug: str | None = None, external_url: str | None = None,
        status: str = "published"
    ) -> None:
        """Record a distribution (publish) event."""
        try:
            from uuid import UUID
            pid = UUID(post_id) if post_id else None
            await self.pool.execute(
                """
                INSERT INTO pipeline_distributions (
                    task_id, target, status, post_id, post_slug, external_url, published_at
                ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (task_id, target) DO UPDATE SET
                    status = EXCLUDED.status,
                    post_id = COALESCE(EXCLUDED.post_id, pipeline_distributions.post_id),
                    post_slug = COALESCE(EXCLUDED.post_slug, pipeline_distributions.post_slug),
                    external_url = COALESCE(EXCLUDED.external_url, pipeline_distributions.external_url),
                    published_at = COALESCE(EXCLUDED.published_at, pipeline_distributions.published_at)
                """,
                task_id, target, status, pid, post_slug, external_url,
            )
        except Exception as e:
            logger.warning("[pipeline_db] add_distribution failed for %s: %s", task_id, e)
