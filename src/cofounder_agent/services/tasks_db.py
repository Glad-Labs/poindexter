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
from typing import Any, Dict, List, Optional
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
        # Pipeline tables are now the primary store (#211 Phase 4).
        # content_tasks is a VIEW with INSTEAD OF triggers that route
        # all writes to pipeline_tasks + pipeline_versions automatically.
        # No dual-write needed — the DB handles it.

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
        Add a new task to the database using content_tasks table.

        Consolidates both manual and automated task creation pipelines.

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

        # Ensure task_name is preserved in metadata since there is no column for it
        if "task_name" in task_data and "task_name" not in metadata:
            metadata["task_name"] = task_data["task_name"]

        try:
            # Use naive UTC datetime for PostgreSQL 'timestamp without time zone' columns
            now = datetime.now(timezone.utc)

            # #231: The content_tasks view does NOT expose these columns,
            # so previously-hardcoded INSERTs of request_type / agent_id /
            # writing_style_id / featured_image_data / featured_image_prompt
            # / tags / model_selection_log / publish_mode / model_selections
            # / quality_preference / estimated_cost / cost_breakdown would
            # raise or silently drop the whole row.
            #
            # Audit (2026-04-16) showed: 9 of those columns have ZERO readers
            # anywhere in the codebase, so they're dropped. The remaining 3
            # (tags, model_selections, quality_preference) are stashed in
            # task_metadata JSONB where callers can still recover them, and
            # task_executor.py's existing `task.get("tags", [])` pattern
            # naturally returns [] when the column doesn't exist.
            meta_extras: dict[str, Any] = dict(metadata or {})
            if task_data.get("tags"):
                meta_extras["tags"] = task_data.get("tags")
            if task_data.get("model_selections"):
                meta_extras["model_selections"] = task_data.get("model_selections")
            if task_data.get("quality_preference"):
                meta_extras["quality_preference"] = task_data.get("quality_preference")
            if task_data.get("featured_image_data"):
                meta_extras["featured_image_data"] = task_data.get("featured_image_data")
            if task_data.get("featured_image_prompt"):
                meta_extras["featured_image_prompt"] = task_data.get("featured_image_prompt")
            if task_data.get("cost_breakdown"):
                meta_extras["cost_breakdown"] = task_data.get("cost_breakdown")
            if task_data.get("estimated_cost"):
                meta_extras["estimated_cost"] = float(task_data.get("estimated_cost", 0.0))
            if task_data.get("model_selection_log"):
                meta_extras["model_selection_log"] = task_data.get("model_selection_log")

            # Build insert columns dict — ONLY fields that actually exist
            # in the content_tasks view. Extras go into task_metadata.
            insert_data = {
                "task_id": task_id,
                "content_type": task_data.get("content_type")
                or task_data.get("task_type", "blog_post"),
                "task_type": task_data.get("task_type", "blog_post"),
                "status": task_data.get("status", "pending"),
                "topic": task_data.get("topic", ""),
                "title": task_data.get("title")
                or task_data.get("task_name"),  # Support both title and task_name
                "style": task_data.get("style", "technical"),
                "tone": task_data.get("tone", "professional"),
                "target_length": task_data.get("target_length", 1500),
                "primary_keyword": task_data.get("primary_keyword"),
                "target_audience": task_data.get("target_audience"),
                "category": task_data.get("category"),
                "content": metadata.get("content") or task_data.get("content"),
                "excerpt": metadata.get("excerpt") or task_data.get("excerpt"),
                "featured_image_url": metadata.get("featured_image_url")
                or task_data.get("featured_image_url"),
                "qa_feedback": metadata.get("qa_feedback"),
                "quality_score": metadata.get("quality_score") or task_data.get("quality_score"),
                "seo_title": metadata.get("seo_title"),
                "seo_description": metadata.get("seo_description"),
                "seo_keywords": metadata.get("seo_keywords"),
                "stage": metadata.get("stage", "pending"),
                "percentage": metadata.get("percentage", 0),
                "message": metadata.get("message"),
                "task_metadata": json.dumps(meta_extras),
                "metadata": json.dumps(task_data.get("metadata") or {}),
                "model_used": task_data.get("model_used"),
                "models_used_by_phase": json.dumps(task_data.get("models_used_by_phase", {})),
                "error_message": task_data.get("error_message"),
                "approval_status": task_data.get("approval_status", "pending"),
                "site_id": task_data.get("site_id"),
                "created_at": now,
                "updated_at": now,
            }

            builder = ParameterizedQueryBuilder()
            sql, params = builder.insert(
                table="content_tasks", columns=insert_data, return_columns=["task_id"]
            )

            async with self.pool.acquire() as conn:
                result = await conn.fetchval(sql, *params)
                logger.info(
                    "Task added: %s | user_id=%s | task_type=%s",
                    task_id,
                    task_data.get("user_id", "unknown"),
                    task_data.get("task_type", "unknown"),
                )
                return str(result)
        except Exception as e:
            logger.error("Failed to add task: %s", e, exc_info=True)
            raise

    @log_query_performance(operation="bulk_add_tasks", category="task_write")
    async def bulk_add_tasks(self, tasks: list[dict[str, Any]]) -> list[str]:
        """
        Add multiple tasks in a single connection acquire using executemany.

        Inserts core task columns only (not content/SEO/image fields).
        For tasks that need all columns, use add_task() individually.

        Args:
            tasks: List of task data dicts with keys like task_name, topic, status, etc.

        Returns:
            List of created task IDs.
        """
        if not tasks:
            return []

        now = datetime.now(timezone.utc)
        rows = []
        task_ids = []

        for task_data in tasks:
            task_id = task_data.get("id", task_data.get("task_id", str(uuid4())))
            if isinstance(task_id, UUID):
                task_id = str(task_id)
            task_ids.append(task_id)

            metadata = task_data.get("task_metadata") or task_data.get("metadata", {})
            metadata = safe_json_load(metadata, fallback={})
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

            rows.append(
                (
                    task_id,
                    task_data.get("content_type") or task_data.get("task_type", "blog_post"),
                    task_data.get("task_type", "blog_post"),
                    task_data.get("status", "pending"),
                    task_data.get("topic", ""),
                    task_data.get("title") or task_data.get("task_name"),
                    task_data.get("style", "technical"),
                    task_data.get("tone", "professional"),
                    task_data.get("target_length", 1500),
                    task_data.get("primary_keyword"),
                    task_data.get("target_audience"),
                    task_data.get("category"),
                    task_data.get("approval_status", "pending"),
                    json.dumps(metadata or {}),
                    task_data.get("site_id"),
                    now,
                    now,
                )
            )

        sql = """
            INSERT INTO content_tasks (
                task_id, content_type, task_type, status, topic,
                title, style, tone, target_length, primary_keyword,
                target_audience, category, approval_status, task_metadata,
                site_id, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17
            )
        """

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.executemany(sql, rows)
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
        """

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
        """

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
        self, worker_id: str, task_categories: list = None
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
                    """,
                    *params,
                )

                if row:
                    return dict(row)
                return None
        except Exception:
            logger.error("[claim_next_task] Failed to claim task", exc_info=True)
            return None

    async def release_task(
        self, task_id: str, worker_id: str, error_message: str = None
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
