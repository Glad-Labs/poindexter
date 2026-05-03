"""
Tasks Database Module

Handles all task-related database operations including:
- Task CRUD operations (create, read, update, delete)
- Task status management and filtering
- Task pagination and counting
- Task queries by date range and status
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from asyncpg import Pool

from schemas.database_response_models import TaskCountsResponse, TaskResponse
from schemas.model_converter import ModelConverter
from services.logger_config import get_logger
from utils.json_encoder import safe_json_load
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin
from .decorators import log_query_performance

logger = get_logger(__name__)


def serialize_value_for_postgres(value: Any) -> Any:
    """Serialize Python value for PostgreSQL."""
    if value is None:
        return None
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        # Try to parse ISO format datetime strings
        if "T" in value and len(value) > 18:  # Basic check for ISO datetime format
            try:
                # Handle ISO format with or without microseconds and timezone
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                # Try parsing with fromisoformat
                return datetime.fromisoformat(value)
            except (ValueError, AttributeError):
                # Not a datetime string, return as-is
                return value
        return value
    if hasattr(value, "isoformat"):
        return value
    return str(value)


class TasksDatabase(DatabaseServiceMixin):
    """Task-related database operations."""

    def __init__(self, pool: Pool):
        """
        Initialize tasks database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool
        # Pipeline tables are the primary store (#211 Phase 4).
        # content_tasks is a VIEW over pipeline_tasks + pipeline_versions.
        # Reads go through the view (so consumers see one flat row
        # shape); writes go DIRECTLY to the underlying base tables —
        # the INSTEAD OF triggers that previously redirected view-INSERTs
        # are not reliably present in production (#188), so app-side
        # routing is the source of truth. See add_task() for details.

    @log_query_performance(operation="get_pending_tasks", category="task_retrieval")
    async def get_pending_tasks(self, limit: int = 10) -> list[dict]:
        """
        Get pending tasks from content_tasks.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of pending tasks as dicts
        """
        QUERY_TIMEOUT = 5  # 5-second timeout for fetching pending tasks

        try:
            if not self.pool:
                return []
            builder = ParameterizedQueryBuilder()
            sql, params = builder.select(
                columns=["*"],
                table="content_tasks",
                where_clauses=[("status", SQLOperator.EQ, "pending")],
                order_by=[("created_at", "DESC")],
                limit=limit,
            )
            try:
                async with self.pool.acquire() as conn:
                    # Add query timeout to prevent blocking
                    rows = await asyncio.wait_for(conn.fetch(sql, *params), timeout=QUERY_TIMEOUT)
                    # Convert to dicts for backward compatibility with task_executor
                    result = []
                    for row in rows:
                        task_response = ModelConverter.to_task_response(row)
                        result.append(ModelConverter.to_dict(task_response))
                    return result
            except asyncio.TimeoutError:
                logger.error(
                    "[get_pending_tasks] DB query timeout after %ss — executor will skip this poll cycle",
                    QUERY_TIMEOUT,
                    exc_info=True,
                )
                return []
        except Exception as e:
            if "content_tasks" in str(e) or "does not exist" in str(e) or "relation" in str(e):
                # Table not yet created (migration pending) — silent skip is correct.
                return []
            logger.warning(
                "[get_pending_tasks] Unexpected error fetching pending tasks: %s",
                e,
                exc_info=True,
            )
            return []

    async def get_all_tasks(self, limit: int = 100) -> list[TaskResponse]:
        """
        Get all tasks from content_tasks.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of all TaskResponse models
        """
        try:
            builder = ParameterizedQueryBuilder()
            sql, params = builder.select(
                columns=["*"], table="content_tasks", order_by=[("created_at", "DESC")], limit=limit
            )
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [ModelConverter.to_task_response(row) for row in rows]
        except Exception as e:
            logger.error("Error fetching all tasks: %s", e, exc_info=True)
            return []

    @log_query_performance(operation="add_task", category="task_write")
    async def add_task(self, task_data: dict[str, Any]) -> str:
        """
        Add a new task to the database via direct INSERT into the
        underlying ``pipeline_tasks`` + ``pipeline_versions`` tables.

        Consolidates both manual and automated task creation pipelines.

        Background — #188
        -----------------
        ``content_tasks`` is a VIEW (since migration 0125) and the
        INSTEAD OF INSERT trigger that previously redirected writes to
        the base tables is not reliably present in production, so any
        INSERT into the view raises ``ObjectNotInPrerequisiteStateError:
        cannot insert into view "content_tasks"``. We now mirror what
        the trigger *would* have done, in application code:

        - Core scalar columns go straight into ``pipeline_tasks``.
        - ``title`` + content/SEO + ``stage_data`` JSONB (which carries
          ``metadata`` / ``result`` / ``task_metadata``) go into
          ``pipeline_versions`` at version=1.
        - View-only computed columns (``content_type``, ``approval_status``,
          ``post_id`` …) are simply dropped — they are projected from
          other tables on read and have no underlying storage here.

        Reads of ``content_tasks`` (the view) continue to return these
        fields as before.

        Args:
            task_data: Task data dict with task_name, topic, task_type, status, agent_id, etc.

        Returns:
            Task ID (string)
        """
        task_id = task_data.get("id", task_data.get("task_id", str(uuid4())))
        if isinstance(task_id, UUID):
            task_id = str(task_id)

        # Extract metadata for normalization
        metadata = task_data.get("task_metadata") or task_data.get("metadata", {})
        metadata = safe_json_load(metadata, fallback={})
        if not isinstance(metadata, dict):
            metadata = {}

        # Ensure task_name is preserved in metadata since there is no column for it
        if "task_name" in task_data and "task_name" not in metadata:
            metadata["task_name"] = task_data["task_name"]

        try:
            # Use timezone-aware UTC datetime — pipeline_tasks columns are
            # ``timestamptz`` so asyncpg accepts the aware value directly.
            now = datetime.now(timezone.utc)

            # #231: callers historically passed columns the view never
            # exposed (request_type, agent_id, writing_style_id, …). Most
            # are dead — drop them. The few with live readers are
            # preserved by stashing them inside the task_metadata JSONB
            # blob (which lives in pipeline_versions.stage_data and is
            # projected back out by the view).
            meta_extras: dict[str, Any] = dict(metadata or {})
            for k in (
                "tags", "model_selections", "quality_preference",
                "featured_image_data", "featured_image_prompt",
                "cost_breakdown", "model_selection_log",
            ):
                if task_data.get(k) is not None and k not in meta_extras:
                    meta_extras[k] = task_data.get(k)
            if task_data.get("estimated_cost") is not None:
                meta_extras["estimated_cost"] = float(task_data.get("estimated_cost", 0.0))

            # Build the stage_data JSONB blob. The view (see migration
            # 0125 _CONTENT_TASKS_VIEW_DDL) projects:
            #   metadata      = stage_data -> 'metadata'
            #   result        = stage_data -> 'result'
            #   task_metadata = stage_data -> 'task_metadata'
            # Mirror that exact shape so callers can still read these
            # columns back through content_tasks.
            stage_data: dict[str, Any] = {}
            if meta_extras:
                stage_data["task_metadata"] = meta_extras
            raw_metadata = task_data.get("metadata")
            if raw_metadata is not None:
                _md = safe_json_load(raw_metadata, fallback={}) or {}
                if _md:
                    stage_data["metadata"] = _md
            elif metadata:
                # If the caller passed `task_metadata` only (no separate
                # `metadata`), still expose it under `metadata` so older
                # readers that look at row["metadata"] keep working.
                stage_data.setdefault("metadata", metadata)

            # Title / content / SEO live on pipeline_versions.
            title = task_data.get("title") or task_data.get("task_name")
            content = metadata.get("content") or task_data.get("content")
            excerpt = metadata.get("excerpt") or task_data.get("excerpt")
            featured_image_url = (
                metadata.get("featured_image_url") or task_data.get("featured_image_url")
            )
            qa_feedback = metadata.get("qa_feedback")
            if isinstance(qa_feedback, list):
                qa_feedback = json.dumps(qa_feedback) if qa_feedback else None
            quality_score = metadata.get("quality_score") or task_data.get("quality_score")
            seo_title = metadata.get("seo_title")
            seo_description = metadata.get("seo_description")
            seo_keywords = metadata.get("seo_keywords")
            models_used_by_phase = task_data.get("models_used_by_phase") or {}

            task_type = task_data.get("task_type", "blog_post")

            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO pipeline_tasks (
                            task_id, task_type, topic, status, stage,
                            site_id, style, tone, target_length,
                            category, primary_keyword, target_audience,
                            percentage, message, model_used,
                            error_message, created_at, updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, $11, $12,
                            $13, $14, $15,
                            $16, $17, $17
                        )
                        """,
                        task_id,
                        task_type,
                        task_data.get("topic", ""),
                        task_data.get("status", "pending"),
                        metadata.get("stage", "pending"),
                        task_data.get("site_id"),
                        task_data.get("style", "technical"),
                        task_data.get("tone", "professional"),
                        task_data.get("target_length", 1500),
                        task_data.get("category"),
                        task_data.get("primary_keyword"),
                        task_data.get("target_audience"),
                        metadata.get("percentage", 0),
                        metadata.get("message"),
                        task_data.get("model_used"),
                        task_data.get("error_message"),
                        now,
                    )
                    await conn.execute(
                        """
                        INSERT INTO pipeline_versions (
                            task_id, version, title, content, excerpt,
                            featured_image_url, seo_title, seo_description,
                            seo_keywords, quality_score, qa_feedback,
                            models_used_by_phase, stage_data, created_at
                        ) VALUES (
                            $1, 1, $2, $3, $4,
                            $5, $6, $7,
                            $8, $9, $10,
                            $11::jsonb, $12::jsonb, $13
                        )
                        ON CONFLICT (task_id, version) DO UPDATE
                           SET title = COALESCE(EXCLUDED.title, pipeline_versions.title),
                               content = COALESCE(EXCLUDED.content, pipeline_versions.content),
                               stage_data = pipeline_versions.stage_data || EXCLUDED.stage_data
                        """,
                        task_id,
                        title,
                        content,
                        excerpt,
                        featured_image_url,
                        seo_title,
                        seo_description,
                        seo_keywords,
                        quality_score,
                        json.dumps(qa_feedback) if isinstance(qa_feedback, (dict, list)) else qa_feedback,
                        json.dumps(models_used_by_phase),
                        json.dumps(stage_data, default=str),
                        now,
                    )
            logger.info(
                "Task added: %s | user_id=%s | task_type=%s",
                task_id,
                task_data.get("user_id", "unknown"),
                task_type,
            )
            return str(task_id)
        except Exception as e:
            logger.error("Failed to add task: %s", e, exc_info=True)
            raise

    @log_query_performance(operation="bulk_add_tasks", category="task_write")
    async def bulk_add_tasks(self, tasks: list[dict[str, Any]]) -> list[str]:
        """
        Add multiple tasks in a single connection acquire using executemany.

        Inserts core task columns only (not content/SEO/image fields).
        For tasks that need all columns, use add_task() individually.

        Per #188: writes go directly to pipeline_tasks + pipeline_versions
        (the underlying tables) — content_tasks is a view and INSERTs into
        it raise ``ObjectNotInPrerequisiteStateError`` in production.

        Args:
            tasks: List of task data dicts with keys like task_name, topic, status, etc.

        Returns:
            List of created task IDs.
        """
        if not tasks:
            return []

        now = datetime.now(timezone.utc)
        pipeline_rows: list[tuple] = []
        version_rows: list[tuple] = []
        task_ids: list[str] = []

        for task_data in tasks:
            task_id = task_data.get("id", task_data.get("task_id", str(uuid4())))
            if isinstance(task_id, UUID):
                task_id = str(task_id)
            task_ids.append(task_id)

            metadata = task_data.get("task_metadata") or task_data.get("metadata", {})
            metadata = safe_json_load(metadata, fallback={})
            if not isinstance(metadata, dict):
                metadata = {}
            if "task_name" in task_data and "task_name" not in metadata:
                metadata["task_name"] = task_data["task_name"]
            # #231: fields that don't exist as columns get stashed in
            # task_metadata. See add_task() for the full story.
            for k in (
                "tags", "model_selections", "quality_preference",
                "publish_mode", "estimated_cost", "cost_breakdown",
                "request_type", "agent_id",
            ):
                if task_data.get(k) and k not in metadata:
                    metadata[k] = task_data.get(k)

            # Mirror the view's projection of task_metadata + metadata
            # via stage_data. See add_task() for the full rationale.
            stage_data: dict[str, Any] = {"task_metadata": metadata}
            raw_metadata = task_data.get("metadata")
            if raw_metadata is not None:
                _md = safe_json_load(raw_metadata, fallback={}) or {}
                if _md:
                    stage_data["metadata"] = _md

            pipeline_rows.append(
                (
                    task_id,
                    task_data.get("task_type", "blog_post"),
                    task_data.get("topic", ""),
                    task_data.get("status", "pending"),
                    "pending",  # stage
                    task_data.get("site_id"),
                    task_data.get("style", "technical"),
                    task_data.get("tone", "professional"),
                    task_data.get("target_length", 1500),
                    task_data.get("category"),
                    task_data.get("primary_keyword"),
                    task_data.get("target_audience"),
                    now,
                )
            )
            version_rows.append(
                (
                    task_id,
                    task_data.get("title") or task_data.get("task_name"),
                    json.dumps(stage_data, default=str),
                    now,
                )
            )

        pipeline_sql = """
            INSERT INTO pipeline_tasks (
                task_id, task_type, topic, status, stage,
                site_id, style, tone, target_length,
                category, primary_keyword, target_audience,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12,
                $13, $13
            )
        """

        version_sql = """
            INSERT INTO pipeline_versions (
                task_id, version, title, stage_data, created_at
            ) VALUES (
                $1, 1, $2, $3::jsonb, $4
            )
            ON CONFLICT (task_id, version) DO NOTHING
        """

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.executemany(pipeline_sql, pipeline_rows)
                    await conn.executemany(version_sql, version_rows)
            logger.info("Bulk created %d tasks", len(task_ids))
            return task_ids
        except Exception as e:
            logger.error("Failed to bulk add tasks: %s", e, exc_info=True)
            raise

    @log_query_performance(operation="get_task", category="task_retrieval")
    async def get_task(self, task_id: str) -> dict | None:
        """
        Get a task from content_tasks by ID.

        Supports:
        - Full UUID task IDs (exact match on task_id column)
        - Numeric IDs (exact match on id column, legacy format)
        - UUID prefix (8+ chars) — convenience for CLI/MCP tools that show short IDs

        Args:
            task_id: Task ID (full UUID, numeric, or 8+ char UUID prefix)

        Returns:
            Task dict or None if not found
        """
        try:
            async with self.pool.acquire() as conn:
                # First: exact match on task_id or numeric id
                row = await conn.fetchrow(
                    "SELECT * FROM content_tasks WHERE task_id = $1 OR id::text = $1 LIMIT 1",
                    str(task_id),
                )
                # Fallback: UUID prefix match (8+ chars, looks like a UUID prefix)
                if not row and len(task_id) >= 8 and "-" not in task_id[8:]:
                    row = await conn.fetchrow(
                        "SELECT * FROM content_tasks WHERE task_id LIKE $1 LIMIT 2",
                        f"{task_id}%",
                    )
                    # Reject ambiguous prefix matches
                    if row:
                        check = await conn.fetch(
                            "SELECT 1 FROM content_tasks WHERE task_id LIKE $1 LIMIT 2",
                            f"{task_id}%",
                        )
                        if len(check) > 1:
                            logger.warning("Ambiguous task_id prefix '%s' matches multiple tasks", task_id)
                            return None
                if row:
                    task_response = ModelConverter.to_task_response(row)
                    return ModelConverter.to_dict(task_response)
                return None
        except Exception as e:
            logger.error("Failed to get task %s: %s", task_id, e, exc_info=True)
            return None

    async def get_tasks_by_ids(self, task_ids: list[str]) -> dict[str, dict]:
        """
        Fetch multiple tasks in a single query.

        Used by bulk operations (bulk_approve, bulk_reject) to replace N
        individual get_task() calls with one SELECT ... WHERE task_id = ANY().

        Args:
            task_ids: List of task UUIDs to fetch

        Returns:
            Dict mapping task_id → task dict for each found task.
            Missing IDs are simply absent from the result.
        """
        if not task_ids:
            return {}
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM content_tasks WHERE task_id = ANY($1::text[])",
                    task_ids,
                )
                result = {}
                for row in rows:
                    task_response = ModelConverter.to_task_response(row)
                    task_dict = ModelConverter.to_dict(task_response)
                    result[task_dict["task_id"]] = task_dict
                return result
        except Exception as e:
            logger.error("[get_tasks_by_ids] Failed to bulk-fetch tasks: %s", e, exc_info=True)
            return {}

    @log_query_performance(operation="update_task_status", category="task_write")
    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Update task status in content_tasks.

        Supports both numeric IDs (legacy) and UUID task IDs.

        Args:
            task_id: Task ID (numeric or UUID)
            status: New status
            result: Optional result data

        Returns:
            Updated task dict or None
        """
        now = datetime.now(timezone.utc)

        try:
            builder = ParameterizedQueryBuilder()

            updates = {"status": status, "updated_at": now}

            if status in ("awaiting_approval", "approved", "published"):
                updates["error_message"] = None

            if result:
                updates["result"] = result

            # Use single connection for both resolve + update (#1206)
            async with self.pool.acquire() as conn:
                # Resolve actual task_id — caller may pass either id or task_id column value
                resolved = await conn.fetchval(
                    "SELECT task_id FROM content_tasks WHERE task_id = $1 OR id::text = $1 LIMIT 1",
                    str(task_id),
                )
                if resolved:
                    task_id = str(resolved)

                sql, params = builder.update(
                    table="content_tasks",
                    updates=updates,
                    where_clauses=[("task_id", SQLOperator.EQ, str(task_id))],
                    return_columns=["*"],
                )

                row = await conn.fetchrow(sql, *params)
                if row:
                    task_type = (
                        row.get("task_type", "unknown") if hasattr(row, "get") else "unknown"
                    )
                    logger.info(
                        "Task status updated: %s -> %s | task_type=%s",
                        task_id, status, task_type,
                    )
                    return self._convert_row_to_dict(row)
                return None
        except Exception as e:
            logger.error("Failed to update task status %s: %s", task_id, e, exc_info=True)
            return None

    @log_query_performance(operation="update_task", category="task_write")
    async def update_task(self, task_id: str, updates: dict[str, Any]) -> dict | None:
        """
        Update task fields in content_tasks.

        Extracts and normalizes fields from task_metadata into dedicated columns.

        Args:
            task_id: Task ID
            updates: Dict of fields to update

        Returns:
            Updated task dict or None
        """
        logger.debug("update_task(%s) keys=%s", task_id, list(updates.keys()))

        if not updates:
            return await self.get_task(task_id)

        # Extract task_metadata for normalization
        task_metadata = safe_json_load(updates.get("task_metadata"), fallback={})

        # Prepare normalized updates
        normalized_updates = dict(updates)

        # Handle task_name -> title mapping
        if "task_name" in normalized_updates and "title" not in normalized_updates:
            normalized_updates["title"] = normalized_updates.pop("task_name")

        # Extract specific fields to dedicated columns
        if task_metadata:
            if "content" not in normalized_updates and "content" in task_metadata:
                normalized_updates["content"] = task_metadata.get("content")
            if "excerpt" not in normalized_updates and "excerpt" in task_metadata:
                normalized_updates["excerpt"] = task_metadata.get("excerpt")
            if (
                "featured_image_url" not in normalized_updates
                and "featured_image_url" in task_metadata
            ):
                normalized_updates["featured_image_url"] = task_metadata.get("featured_image_url")
            if (
                "featured_image_data" not in normalized_updates
                and "featured_image_data" in task_metadata
            ):
                normalized_updates["featured_image_data"] = task_metadata.get("featured_image_data")
            if "qa_feedback" not in normalized_updates and "qa_feedback" in task_metadata:
                qa_fb = task_metadata.get("qa_feedback")
                if isinstance(qa_fb, list):
                    qa_fb = json.dumps(qa_fb) if qa_fb else None
                normalized_updates["qa_feedback"] = qa_fb
            if "quality_score" not in normalized_updates and "quality_score" in task_metadata:
                normalized_updates["quality_score"] = task_metadata.get("quality_score")
            if "seo_title" not in normalized_updates and "seo_title" in task_metadata:
                normalized_updates["seo_title"] = task_metadata.get("seo_title")
            if "seo_description" not in normalized_updates and "seo_description" in task_metadata:
                normalized_updates["seo_description"] = task_metadata.get("seo_description")
            if "seo_keywords" not in normalized_updates and "seo_keywords" in task_metadata:
                normalized_updates["seo_keywords"] = task_metadata.get("seo_keywords")
            if "stage" not in normalized_updates and "stage" in task_metadata:
                normalized_updates["stage"] = task_metadata.get("stage")
            if "percentage" not in normalized_updates and "percentage" in task_metadata:
                normalized_updates["percentage"] = task_metadata.get("percentage")
            if "message" not in normalized_updates and "message" in task_metadata:
                normalized_updates["message"] = task_metadata.get("message")
            if "actual_cost" not in normalized_updates and "actual_cost" in task_metadata:
                normalized_updates["actual_cost"] = task_metadata.get("actual_cost")
            if "cost_breakdown" not in normalized_updates and "cost_breakdown" in task_metadata:
                cost_breakdown = task_metadata.get("cost_breakdown")
                normalized_updates["cost_breakdown"] = (
                    json.dumps(cost_breakdown)
                    if isinstance(cost_breakdown, dict)
                    else cost_breakdown
                )
            if "published_at" not in normalized_updates and "published_at" in task_metadata:
                normalized_updates["published_at"] = task_metadata.get("published_at")

        # Defensive filter — content_tasks is a view whose column set is
        # strictly narrower than the runtime context dict. Historically
        # callers have passed context-only keys (`model_selection_log`,
        # `models_used_by_phase`, `attempted_providers`, etc.) directly
        # as top-level updates; those reach the SQL builder and Postgres
        # raises "column X of relation content_tasks does not exist",
        # halting the pipeline at finalize_task. Fold any non-column keys
        # into task_metadata (JSONB) instead so the data survives without
        # crashing the write.
        _VIEW_COLUMNS = {
            "id", "task_id", "task_type", "content_type", "title", "topic",
            "status", "stage", "style", "tone", "target_length", "category",
            "primary_keyword", "target_audience", "content", "excerpt",
            "featured_image_url", "featured_image_data", "quality_score",
            "qa_feedback", "seo_title", "seo_description", "seo_keywords",
            "percentage", "message", "model_used", "error_message",
            "models_used_by_phase", "metadata", "result", "task_metadata",
            "site_id", "created_at", "updated_at", "started_at",
            "completed_at", "approval_status", "approved_by",
            "human_feedback", "post_id", "post_slug", "published_at",
            "actual_cost", "cost_breakdown",
        }
        rerouted_to_metadata: dict[str, Any] = {}
        for stray_key in list(normalized_updates.keys()):
            if stray_key not in _VIEW_COLUMNS:
                rerouted_to_metadata[stray_key] = normalized_updates.pop(stray_key)
        if rerouted_to_metadata:
            existing_meta = safe_json_load(
                normalized_updates.get("task_metadata"), fallback={}
            )
            # safe_json_load can return None for "null"/empty/non-dict input —
            # fall back to an empty dict before merging rather than crashing
            # with AttributeError on existing_meta.update().
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            existing_meta.update(rerouted_to_metadata)
            normalized_updates["task_metadata"] = existing_meta
            logger.info(
                "[update_task] rerouted %d non-column keys into task_metadata: %s",
                len(rerouted_to_metadata),
                sorted(rerouted_to_metadata.keys()),
            )

        # Serialize values for PostgreSQL
        serialized_updates = {}
        for key, value in normalized_updates.items():
            serialized_updates[key] = serialize_value_for_postgres(value)

        try:
            # Use single connection for resolve + update (#1206)
            async with self.pool.acquire() as conn:
                # Resolve the actual task_id — caller may pass either id or task_id column value
                resolved = await conn.fetchrow(
                    "SELECT task_id, status FROM content_tasks WHERE task_id = $1 OR id::text = $1 LIMIT 1",
                    str(task_id),
                )
                if resolved:
                    task_id = str(resolved["task_id"])
                    # Guard: never overwrite cancelled/rejected tasks with pipeline updates.
                    # This prevents zombie tasks from resurrecting after manual cancellation.
                    current_status = resolved["status"]
                    if current_status in ("cancelled", "rejected") and serialized_updates.get("status") not in ("cancelled", "rejected", None):
                        logger.info("[GUARD] Skipping update for %s task %s (attempted status: %s)",
                                    current_status, task_id, serialized_updates.get("status"))
                        return None

                builder = ParameterizedQueryBuilder()
                sql, params = builder.update(
                    table="content_tasks",
                    updates=serialized_updates,
                    where_clauses=[("task_id", SQLOperator.EQ, str(task_id))],
                    return_columns=["*"],
                )

                row = await conn.fetchrow(sql, *params)
                if row:
                    task_response = ModelConverter.to_task_response(row)
                    return ModelConverter.to_dict(task_response)
                logger.warning("Update returned no row for task %s", task_id)
                return None
        except Exception as e:
            logger.error("Failed to update task %s: %s", task_id, e, exc_info=True)
            return None

    @log_query_performance(operation="heartbeat_task", category="task_write")
    async def heartbeat_task(self, task_id: str) -> bool:
        """Stamp ``pipeline_tasks.updated_at = NOW()`` without changing status.

        GH-90 AC #2: the stale-task sweeper cancels any ``in_progress`` row
        whose ``updated_at`` is older than ``stale_task_timeout_minutes``.
        During long writer/QA/image stages the worker would otherwise sit
        on a single row for hours without touching ``updated_at``, so the
        sweeper couldn't tell the difference between "actively processing"
        and "worker died mid-stage". This method is called on a timer by
        :class:`TaskExecutor` to keep the row fresh.

        The heartbeat explicitly does NOT change status — any row already
        in a terminal state (``failed``, ``cancelled``, ``rejected``,
        ``awaiting_approval``, ``published``) is left untouched. Returns
        True if a row was updated, False if the task does not exist or is
        already in a terminal state (signal to the caller that downstream
        work should abort).

        Args:
            task_id: Task ID to heartbeat.

        Returns:
            True if updated_at was refreshed, False if the task is already
            terminal or was not found.
        """
        if not task_id:
            return False
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE pipeline_tasks
                       SET updated_at = NOW()
                     WHERE task_id = $1
                       AND status IN ('pending', 'in_progress')
                 RETURNING task_id
                    """,
                    str(task_id),
                )
                return row is not None
        except Exception as e:
            # Heartbeat failure must NOT kill the worker. Log at debug so
            # a transient DB blip doesn't spam WARN, but surface the
            # reason for test assertions + debugging.
            logger.debug("heartbeat_task(%s) failed: %s", task_id, e)
            return False

    @log_query_performance(operation="update_task_status_guarded", category="task_write")
    async def update_task_status_guarded(
        self,
        task_id: str,
        new_status: str,
        allowed_from: tuple[str, ...] = ("in_progress", "pending"),
        **fields: Any,
    ) -> str | None:
        """Update status only if the current status is one of ``allowed_from``.

        GH-90 AC #3: before a terminal write (e.g. ``awaiting_approval``),
        the worker must confirm the row hasn't been flipped out from
        under it by the stale-task sweeper. This method wraps the UPDATE
        in a ``WHERE status = ANY(...)`` guard and returns the previous
        status via ``RETURNING``. A ``None`` return value means the
        guard blocked the write — the caller must abort downstream work
        (don't publish a post, don't charge for GPU, etc.).

        Additional columns can be set via ``fields`` — they're applied
        atomically with the status change. Only simple scalar values are
        supported (int, str, None); pass complex payloads through
        ``update_task`` instead.

        Args:
            task_id: Task ID.
            new_status: Status to set if the guard passes.
            allowed_from: Tuple of acceptable current statuses.
            **fields: Additional scalar columns to update atomically.

        Returns:
            The previous status string if the update succeeded, else None
            (row was cancelled, rejected, failed, or doesn't exist).
        """
        if not task_id:
            return None

        # Whitelist the column names we allow. Anything outside this set is
        # rejected rather than silently interpolated — avoids SQL-injection
        # surface if a caller ever shoves user input into **fields.
        _ALLOWED = {
            "error_message", "message", "percentage", "stage",
            "completed_at", "started_at", "model_used", "quality_score",
        }
        extra_sets: list[str] = []
        extra_vals: list[Any] = []
        for k, v in fields.items():
            if k not in _ALLOWED:
                logger.warning(
                    "update_task_status_guarded: ignoring non-whitelisted field %r",
                    k,
                )
                continue
            extra_sets.append(f"{k} = ${len(extra_vals) + 4}")
            extra_vals.append(v)

        extra_clause = (", " + ", ".join(extra_sets)) if extra_sets else ""
        try:
            async with self.pool.acquire() as conn:
                # Read prev_status first so we can return the value that
                # existed BEFORE the update. If we read it from RETURNING
                # we'd get the new value. Use a transaction to make the
                # read + write atomic.
                async with conn.transaction():
                    prev = await conn.fetchval(
                        "SELECT status FROM pipeline_tasks WHERE task_id = $1 FOR UPDATE",
                        str(task_id),
                    )
                    if prev is None:
                        return None
                    if prev not in allowed_from:
                        logger.warning(
                            "[GH-90] Terminal-write blocked: task=%s current_status=%r "
                            "not in allowed=%s — sweeper likely raced worker",
                            task_id, prev, list(allowed_from),
                        )
                        return None
                    params: list[Any] = [str(task_id), new_status, list(allowed_from), *extra_vals]
                    # nosec B608 line below — extra_clause built from local literals (column-name fragments); values use $N params
                    _sql = (
                        """\nUPDATE pipeline_tasks\n   SET status = $2,\n       updated_at = NOW()\n""" + extra_clause + """\n WHERE task_id = $1\n   AND status = ANY($3::text[])\n RETURNING task_id\n"""  # nosec B608
                    )
                    updated = await conn.fetchval(_sql, *params)
                    if updated is None:
                        return None
                    return prev
        except Exception as e:
            logger.error(
                "update_task_status_guarded(%s → %s) failed: %s",
                task_id, new_status, e, exc_info=True,
            )
            return None

    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: str | None = None,
        category: str | None = None,
        search: str | None = None,
        site_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Get paginated tasks from content_tasks with optional filtering.

        Args:
            offset: Result offset
            limit: Maximum results per page
            status: Filter by status
            category: Filter by category
            search: Optional keyword search across task_name/title, topic, and category.
                    Uses ILIKE with trigram index (pg_trgm) for efficient leading-wildcard
                    matching.  See migration 0027_add_trgm_indexes.py for the index.
            site_id: Optional site ID to scope tasks to a specific site.

        Returns:
            Tuple of (tasks list, total count)
        """
        # Build WHERE clause and params for a single round-trip using COUNT(*) OVER ()
        conditions = []
        params: list = []
        param_idx = 1

        if status:
            conditions.append(f"status = ${param_idx}")
            params.append(status)
            param_idx += 1
        if category:
            conditions.append(f"category = ${param_idx}")
            params.append(category)
            param_idx += 1
        if site_id:
            conditions.append(f"site_id = ${param_idx}")
            params.append(site_id)
            param_idx += 1
        if search:
            # Sanitize: keep alphanumeric, spaces, hyphens, underscores only
            safe_search = "%" + "".join(c for c in search if c.isalnum() or c in " -_") + "%"
            # ILIKE across task display name (title), topic, and category columns.
            # The trigram GIN indexes on these columns (migration 0027) allow
            # PostgreSQL to avoid a full sequential scan for '%term%' patterns.
            conditions.append(
                f"(title ILIKE ${param_idx} OR topic ILIKE ${param_idx} OR category ILIKE ${param_idx})"
            )
            params.append(safe_search)
            param_idx += 1

        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # LIMIT and OFFSET are always the last two params
        params.extend([limit, offset])
        limit_param = param_idx
        offset_param = param_idx + 1

        # Single round-trip: window function COUNT(*) OVER () returns total alongside rows
        sql_list = f"""
            SELECT *, COUNT(*) OVER () AS total_count
            FROM content_tasks
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
        """  # nosec B608  # where_sql built from local literals; limit/offset rendered as "${N}" placeholders; values use $N params

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql_list, *params)
                total = rows[0]["total_count"] if rows else 0
                tasks = [self._convert_row_to_dict(row) for row in rows]
                logger.info("Listed %d tasks (total: %d)", len(tasks), total)
                return tasks, total
        except Exception as e:
            logger.error("Failed to list tasks: %s", e, exc_info=True)
            return [], 0

    @log_query_performance(operation="get_task_counts", category="task_retrieval")
    async def get_task_counts(self) -> TaskCountsResponse:
        """
        Get task counts by status from content_tasks.

        Returns:
            TaskCountsResponse model with status-based counts
        """
        sql = """
            SELECT status, COUNT(*) as count
            FROM content_tasks
            GROUP BY status
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql)
                counts = {row["status"]: row["count"] for row in rows}
                return TaskCountsResponse(
                    total=sum(counts.values()),
                    pending=counts.get("pending", 0),
                    in_progress=counts.get("in_progress", 0),
                    completed=counts.get("completed", 0),
                    failed=counts.get("failed", 0),
                    awaiting_approval=counts.get("awaiting_approval", 0),
                    approved=counts.get("approved", 0),
                )
        except Exception as e:
            logger.error("Failed to get task counts: %s", e, exc_info=True)
            return TaskCountsResponse(
                total=0,
                pending=0,
                in_progress=0,
                completed=0,
                failed=0,
                awaiting_approval=0,
                approved=0,
            )

    async def get_queued_tasks(self, limit: int = 5) -> list[TaskResponse]:
        """
        Get top queued/pending tasks from content_tasks.

        Args:
            limit: Maximum tasks to return

        Returns:
            List of pending TaskResponse models
        """
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["*"],
            table="content_tasks",
            where_clauses=[("status", SQLOperator.EQ, "pending")],
            order_by=[("created_at", "ASC")],
            limit=limit,
        )
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [ModelConverter.to_task_response(row) for row in rows]
        except Exception as e:
            logger.error("Failed to get queued tasks: %s", e, exc_info=True)
            return []

    # Columns needed for analytics KPI calculations — excludes large content/JSONB blobs
    # (content, result, featured_image_data, qa_feedback, etc.)
    ANALYTICS_COLUMNS = [
        "id",
        "task_id",
        "status",
        "created_at",
        "updated_at",
        "completed_at",
        "quality_score",
        "estimated_cost",
        "actual_cost",
        "category",
        "content_type",
        "task_type",
        "stage",
        "percentage",
        "model_used",
        "task_metadata",
    ]

    async def get_tasks_by_date_range(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Get tasks from content_tasks within date range for analytics.

        Selects only lightweight columns needed for KPI calculations.
        Defaults to the last 30 days if no date range is specified.

        Args:
            start_date: Start of date range (UTC) - defaults to 30 days ago
            end_date: End of date range (UTC) - defaults to now
            status: Filter by status (e.g., 'completed', 'failed') - optional
            limit: Maximum results to return (capped at 500)

        Returns:
            List of task dicts with analytics-relevant fields only
        """
        # Default to last 30 days — callers wanting broader ranges must be explicit
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        if start_date is None:
            start_date = datetime.now(timezone.utc) - timedelta(days=30)

        # Cap limit to prevent unbounded result sets
        if limit > 500:
            logger.warning(
                "[get_tasks_by_date_range] requested limit=%d capped at 500; "
                "use pagination (offset loop) for larger result sets",
                limit,
            )
        limit = min(limit, 500)

        try:
            builder = ParameterizedQueryBuilder()
            where_clauses = [
                ("created_at", SQLOperator.GE, start_date),
                ("created_at", SQLOperator.LE, end_date),
            ]

            if status:
                where_clauses.append(("status", SQLOperator.EQ, status))  # type: ignore[arg-type]

            sql, params = builder.select(
                columns=self.ANALYTICS_COLUMNS,
                table="content_tasks",
                where_clauses=where_clauses,
                order_by=[("created_at", "DESC")],
                limit=limit,
            )

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                tasks = [dict(row) for row in rows]
                logger.debug(
                    "Retrieved %d tasks for analytics date range %s to %s",
                    len(tasks), start_date, end_date,
                )
                return tasks
        except Exception as e:
            logger.error("Failed to get tasks by date range: %s", e, exc_info=True)
            return []

    async def get_kpi_aggregates(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Compute KPI aggregates for the analytics dashboard using a single SQL query.

        Replaces the previous approach of fetching up to 500 raw task rows and
        aggregating them in Python loops (issue #696).

        Args:
            start_date: Start of date range (UTC); None means all-time
            end_date: End of date range (UTC); defaults to now

        Returns:
            Dict with keys:
                rows         — list of dicts: {status, model_used, task_type, day,
                               count, total_cost, avg_duration_s, completed_count}
                total_tasks  — int
        """
        if end_date is None:
            end_date = datetime.now(timezone.utc)

        params: list = [end_date]
        if start_date is not None:
            date_filter = "AND created_at >= $2"
            params.append(start_date)
        else:
            date_filter = ""

        sql = f"""
            SELECT
                status,
                COALESCE(model_used, 'unknown')                                   AS model_used,
                COALESCE(task_type,  'unknown')                                   AS task_type,
                date_trunc('day', created_at AT TIME ZONE 'UTC')::date            AS day,
                COUNT(*)                                                           AS count,
                SUM(COALESCE(actual_cost, estimated_cost, 0.0))                   AS total_cost,
                AVG(
                    EXTRACT(EPOCH FROM (completed_at - created_at))
                ) FILTER (WHERE completed_at IS NOT NULL AND completed_at > created_at)
                                                                                  AS avg_duration_s,
                COUNT(*) FILTER (WHERE status = 'completed')                      AS completed_count
            FROM content_tasks
            WHERE created_at <= $1
            {date_filter}
            GROUP BY status, model_used, task_type, day
            ORDER BY day ASC
        """  # nosec B608  # date_filter is one of two hardcoded literals ("AND created_at >= $2" or ""); values use $N params

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                result_rows = [dict(r) for r in rows]
                total = sum(int(r["count"]) for r in result_rows)
                logger.debug(
                    "[get_kpi_aggregates] %d aggregate rows, %d total tasks",
                    len(result_rows), total,
                )
                return {"rows": result_rows, "total_tasks": total}
        except Exception as e:
            logger.error(
                "[get_kpi_aggregates] Failed to compute KPI aggregates: %s",
                e, exc_info=True,
            )
            return {"rows": [], "total_tasks": 0}

    async def delete_task(self, task_id: str) -> bool:
        """
        Delete task from content_tasks.

        Supports both numeric IDs (legacy) and UUID task IDs.

        Args:
            task_id: Task ID (numeric or UUID)

        Returns:
            True if deleted, False if error
        """
        try:
            # Always look up by task_id (the actual primary key). Numeric id fallback
            # removed: content_tasks.id is UUID not INTEGER. (See issue #301)
            builder = ParameterizedQueryBuilder()
            sql, params = builder.delete(
                table="content_tasks", where_clauses=[("task_id", SQLOperator.EQ, str(task_id))]
            )

            async with self.pool.acquire() as conn:
                result = await conn.execute(sql, *params)
                deleted = "DELETE 1" in result or result == "DELETE 1"
                if deleted:
                    logger.info("Task deleted: %s", task_id)
                return deleted
        except Exception as e:
            logger.error("Error deleting task %s: %s", task_id, e, exc_info=True)
            return False

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> tuple:
        """
        Get draft tasks from content_tasks.

        Args:
            limit: Maximum tasks to return
            offset: Result offset

        Returns:
            Tuple of (drafts list, total count)
        """
        try:
            # Single round-trip: window function COUNT(*) OVER () returns total alongside rows
            sql = """
                SELECT *, COUNT(*) OVER () AS total_count FROM content_tasks
                WHERE status = $1 OR approval_status = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
            """
            params = ["pending", "pending", limit, offset]

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                total = rows[0]["total_count"] if rows else 0
                drafts = [self._convert_row_to_dict(row) for row in rows]
                return (drafts, total or 0)
        except Exception as e:
            logger.error("Error getting drafts: %s", e, exc_info=True)
            return ([], 0)

    async def log_status_change(
        self,
        task_id: str,
        old_status: str,
        new_status: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Log a status change to task_status_history table.

        Args:
            task_id: Task ID
            old_status: Previous status
            new_status: New status
            reason: Optional reason for the change
            metadata: Optional additional metadata (validation errors, etc.)

        Returns:
            True if logged successfully, False on error
        """
        try:
            sql = """
                INSERT INTO task_status_history (task_id, old_status, new_status, reason, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
            """

            now = datetime.now(timezone.utc)
            metadata_json = json.dumps(metadata or {})

            async with self.pool.acquire() as conn:
                await conn.execute(
                    sql, task_id, old_status, new_status, reason or "", metadata_json, now
                )
                logger.info("Status change logged: %s %s -> %s", task_id, old_status, new_status)
                return True
        except Exception as e:
            logger.error("Failed to log status change: %s", e, exc_info=True)
            return False

    async def get_status_history(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """
        Get status change history for a task.

        Args:
            task_id: Task ID
            limit: Maximum records to return

        Returns:
            List of status change records
        """
        try:
            if not self.pool:
                logger.error("[get_status_history] Database pool not initialized")
                return []

            # NOTE: Column was named "timestamp" (reserved word) until migration 0031
            # renamed it to "created_at".  SELECT both with aliases so this code works
            # against both pre- and post-migration schemas; the one that exists will
            # carry a non-NULL value.
            sql = """
                SELECT id, task_id, old_status, new_status, reason, metadata, created_at
                FROM task_status_history
                WHERE task_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            """
            # Pre-migration fallback (column still named "timestamp")
            sql_legacy = """
                SELECT id, task_id, old_status, new_status, reason, metadata,
                       "timestamp" AS created_at
                FROM task_status_history
                WHERE task_id = $1
                ORDER BY "timestamp" DESC
                LIMIT $2
            """

            async with self.pool.acquire() as conn:
                try:
                    rows = await conn.fetch(sql, task_id, limit)
                except Exception:
                    # Migration 0031 not yet applied — column is still "timestamp"
                    rows = await conn.fetch(sql_legacy, task_id, limit)

                history = []
                for row in rows:
                    history.append(
                        {
                            "id": row["id"],
                            "task_id": row["task_id"],
                            "old_status": row["old_status"],
                            "new_status": row["new_status"],
                            "reason": row["reason"],
                            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                            "timestamp": (
                                row["created_at"].isoformat() if row["created_at"] else None
                            ),
                        }
                    )

                logger.info("Retrieved %d status changes for task %s", len(history), task_id)
                return history
        except Exception as e:
            logger.error("Failed to get status history: %s", e, exc_info=True)
            return []

    async def get_validation_failures(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        Get all validation failures for a task by querying status history.

        Args:
            task_id: Task ID
            limit: Maximum records to return

        Returns:
            List of validation failure records with details
        """
        try:
            sql = """
                SELECT id, task_id, old_status, new_status, reason, metadata, created_at
                FROM task_status_history
                WHERE task_id = $1
                AND new_status IN ('validation_failed', 'validation_error')
                ORDER BY created_at DESC
                LIMIT $2
            """
            # Pre-migration fallback (column still named "timestamp")
            sql_legacy = """
                SELECT id, task_id, old_status, new_status, reason, metadata,
                       "timestamp" AS created_at
                FROM task_status_history
                WHERE task_id = $1
                AND new_status IN ('validation_failed', 'validation_error')
                ORDER BY "timestamp" DESC
                LIMIT $2
            """

            async with self.pool.acquire() as conn:
                try:
                    rows = await conn.fetch(sql, task_id, limit)
                except Exception:
                    # Migration 0031 not yet applied — column is still "timestamp"
                    rows = await conn.fetch(sql_legacy, task_id, limit)

                failures = []
                for row in rows:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                    failures.append(
                        {
                            "id": row["id"],
                            "timestamp": (
                                row["created_at"].isoformat() if row["created_at"] else None
                            ),
                            "reason": row["reason"],
                            "errors": metadata.get("validation_errors", []),
                            "context": metadata.get("context", {}),
                        }
                    )

                logger.info("Retrieved %d validation failures for task %s", len(failures), task_id)
                return failures
        except Exception as e:
            logger.error("Failed to get validation failures: %s", e, exc_info=True)
            return []

    @log_query_performance(operation="sweep_stale_tasks", category="task_write")
    async def sweep_stale_tasks(
        self,
        stale_threshold_minutes: int = 60,
        max_retries: int = 3,
    ) -> dict[str, int]:
        """
        Find and reset stale in-progress tasks atomically.

        Tasks stuck in 'in_progress' beyond the threshold are either reset
        to 'pending' (if retry count < max_retries) or marked 'failed'.
        All updates happen in a single transaction with batched queries.

        Args:
            stale_threshold_minutes: Minutes after which an in_progress task is stale
            max_retries: Maximum retry attempts before marking as failed

        Returns:
            Dict with 'reset' and 'failed' counts
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_threshold_minutes)

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Fetch all stale tasks in one query — lightweight columns only
                    stale_rows = await conn.fetch(
                        """
                        SELECT task_id, task_metadata
                        FROM content_tasks
                        WHERE status = 'in_progress'
                          AND updated_at < $1
                        """,
                        cutoff,
                    )

                    if not stale_rows:
                        return {"reset": 0, "failed": 0}

                    # Partition into reset vs. fail buckets
                    reset_ids: list[str] = []
                    fail_ids: list[str] = []

                    for row in stale_rows:
                        task_id = row["task_id"]
                        meta = json.loads(row["task_metadata"]) if row["task_metadata"] else {}
                        retry_count = meta.get("retry_count", 0)
                        if retry_count < max_retries:
                            reset_ids.append(task_id)
                        else:
                            fail_ids.append(task_id)

                    now = datetime.now(timezone.utc)

                    # Batch reset: set back to pending with incremented retry count
                    if reset_ids:
                        await conn.execute(
                            """
                            UPDATE content_tasks
                            SET status = 'pending',
                                updated_at = $1,
                                task_metadata = jsonb_set(
                                    COALESCE(task_metadata::jsonb, '{}'::jsonb),
                                    '{retry_count}',
                                    (COALESCE((task_metadata::jsonb->>'retry_count')::int, 0) + 1)::text::jsonb
                                )
                            WHERE task_id = ANY($2::text[])
                            """,
                            now,
                            reset_ids,
                        )

                    # Batch fail: mark as failed
                    if fail_ids:
                        await conn.execute(
                            """
                            UPDATE content_tasks
                            SET status = 'failed',
                                updated_at = $1,
                                error_message = 'Exceeded maximum retries after stale sweep'
                            WHERE task_id = ANY($2::text[])
                            """,
                            now,
                            fail_ids,
                        )

                    logger.info(
                        "Stale task sweep complete: %d reset, %d failed (threshold=%dm)",
                        len(reset_ids), len(fail_ids), stale_threshold_minutes,
                    )
                    return {"reset": len(reset_ids), "failed": len(fail_ids)}

        except Exception as e:
            logger.error("Failed to sweep stale tasks: %s", e, exc_info=True)
            return {"reset": 0, "failed": 0}

    async def bulk_update_task_statuses(
        self,
        task_ids: list[str],
        new_status: str,
    ) -> dict[str, Any]:
        """
        Validate and update multiple task statuses in two queries (not 2N).

        1. SELECT to find which task_ids actually exist
        2. UPDATE all existing tasks in one statement

        Args:
            task_ids: List of task UUIDs to update
            new_status: The target status

        Returns:
            Dict with 'updated_ids', 'missing_ids' lists
        """
        if not task_ids:
            return {"updated_ids": [], "missing_ids": []}

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # 1. Batch existence check
                    existing_rows = await conn.fetch(
                        "SELECT task_id FROM content_tasks WHERE task_id = ANY($1::text[])",
                        task_ids,
                    )
                    existing_ids = {row["task_id"] for row in existing_rows}
                    missing_ids = [tid for tid in task_ids if tid not in existing_ids]

                    # 2. Batch update all existing
                    if existing_ids:
                        updated_rows = await conn.fetch(
                            """
                            UPDATE content_tasks
                            SET status = $1, updated_at = $2
                            WHERE task_id = ANY($3::text[])
                            RETURNING task_id
                            """,
                            new_status,
                            datetime.now(timezone.utc),
                            list(existing_ids),
                        )
                        updated_ids = [row["task_id"] for row in updated_rows]
                    else:
                        updated_ids = []

                    return {"updated_ids": updated_ids, "missing_ids": missing_ids}

        except Exception as e:
            logger.error("Failed to bulk update task statuses: %s", e, exc_info=True)
            raise

    @log_query_performance(operation="claim_next_task", category="task_write")
    async def claim_next_task(
        self, worker_id: str, task_categories: list | None = None
    ) -> dict[str, Any] | None:
        """Atomically claim the next pending task using FOR UPDATE SKIP LOCKED.

        This prevents race conditions when multiple workers poll simultaneously.
        The task is locked and immediately set to 'in_progress' in a single query.

        Args:
            worker_id: The claiming worker's ID
            task_categories: Optional list of task categories this worker handles

        Returns:
            The claimed task dict, or None if no tasks available
        """
        try:
            async with self.pool.acquire() as conn:
                # Build category filter
                category_filter = ""
                params = [worker_id]
                if task_categories:
                    placeholders = ", ".join(
                        f"${i+2}" for i in range(len(task_categories))
                    )
                    category_filter = f"AND (task_category IN ({placeholders}) OR task_category IS NULL)"
                    params.extend(task_categories)

                row = await conn.fetchrow(
                    f"""
                    UPDATE content_tasks
                    SET status = 'in_progress',
                        assigned_worker = $1,
                        worker_claimed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = (
                        SELECT id FROM content_tasks
                        WHERE status = 'pending'
                        {category_filter}
                        ORDER BY
                            is_urgent DESC NULLS LAST,
                            created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                    """,  # nosec B608  # category_filter built from generated "${N}" placeholders only; task_categories values use $N params
                    *params,
                )

                if row:
                    return dict(row)
                return None
        except Exception:
            logger.error("[claim_next_task] Failed to claim task", exc_info=True)
            return None

    async def release_task(
        self, task_id: str, worker_id: str, error_message: str | None = None
    ):
        """Release a claimed task back to pending (e.g., on worker failure)."""
        try:
            status = "failed" if error_message else "pending"
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE content_tasks
                    SET status = $3,
                        assigned_worker = NULL,
                        worker_claimed_at = NULL,
                        error_message = $4,
                        updated_at = NOW()
                    WHERE task_id = $1 AND assigned_worker = $2
                    """,
                    task_id,
                    worker_id,
                    status,
                    error_message,
                )
        except Exception:
            logger.error(
                "[release_task] Failed to release task %s", task_id, exc_info=True
            )
