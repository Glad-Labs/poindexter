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
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from asyncpg import Pool

from schemas.database_response_models import TaskCountsResponse, TaskResponse
from schemas.model_converter import ModelConverter
from utils.error_handler import handle_service_error
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin
from .decorators import log_query_performance

logger = logging.getLogger(__name__)


class _PostgresJSONEncoder(json.JSONEncoder):
    """JSON encoder that safely handles types returned by asyncpg/Pydantic.

    Covers the gap between _convert_row_to_dict (which sanitises top-level
    Decimal values) and ModelConverter.to_dict (which may leave Decimal,
    datetime, or UUID objects inside nested dicts/lists).
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        return super().default(o)


def serialize_value_for_postgres(value: Any) -> Any:
    """Serialize Python value for PostgreSQL, keeping datetimes naive UTC."""
    if value is None:
        return None

    # Decimal → float before any other check (asyncpg NUMERIC columns)
    if isinstance(value, Decimal):
        return float(value)

    # Handle datetime objects first - keep naive, let PostgreSQL handle timezone
    if isinstance(value, datetime):
        # Return datetime as-is (must be naive UTC)
        # PostgreSQL TIMESTAMP WITH TIME ZONE column will interpret naive datetimes as UTC
        # If it's timezone-aware, strip the timezone info to keep it naive
        if value.tzinfo is not None:
            logger.warning(f"Converting timezone-aware datetime to naive UTC: {value}")
            return value.replace(tzinfo=None)
        return value

    if isinstance(value, dict):
        return json.dumps(value, cls=_PostgresJSONEncoder)
    if isinstance(value, list):
        return json.dumps(value, cls=_PostgresJSONEncoder)
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        # Try to parse ISO format datetime strings
        if "T" in value and len(value) > 18:  # Basic check for ISO datetime format
            try:
                # Handle ISO format with or without microseconds and timezone
                if value.endswith("Z"):
                    value = value[:-1]  # Remove 'Z' to make it naive
                elif "+00:00" in value or value.endswith("+00:00"):
                    value = value.split("+")[0]  # Remove timezone offset
                # Try parsing with fromisoformat
                dt = datetime.fromisoformat(value)
                # Ensure it's naive (strip any timezone info)
                if dt.tzinfo is not None:
                    logger.warning(
                        f"Converting timezone-aware datetime string to naive UTC: {value}"
                    )
                    dt = dt.replace(tzinfo=None)
                return dt
            except (ValueError, AttributeError):
                # Not a datetime string, return as-is
                return value
        return value
    if hasattr(value, "isoformat"):
        # Handle datetime objects
        if isinstance(value, datetime):
            # Keep naive, strip any timezone info
            if value.tzinfo is not None:
                logger.warning(f"Converting timezone-aware datetime object to naive UTC: {value}")
                return value.replace(tzinfo=None)
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

    async def get_pending_tasks(self, limit: int = 10) -> List[dict]:
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
                logger.error(f"Query timeout fetching pending tasks after {QUERY_TIMEOUT}s")
                return []
        except Exception as e:
            if "content_tasks" in str(e) or "does not exist" in str(e) or "relation" in str(e):
                return []
            logger.error(
                f"[get_pending_tasks] Error fetching pending tasks: {str(e)}", exc_info=True
            )
            return []

    async def get_stale_in_progress_tasks(
        self, timeout_minutes: int = 30, limit: int = 50
    ) -> List[dict]:
        """
        Get in_progress tasks that have not been updated within timeout_minutes.

        These are tasks that were claimed by the executor but never completed —
        typically caused by a server restart, unhandled crash, or a hung pipeline.

        Args:
            timeout_minutes: Tasks with updated_at older than this are considered stale
            limit: Maximum tasks to return

        Returns:
            List of stale task dicts ordered oldest-first
        """
        QUERY_TIMEOUT = 5
        try:
            if not self.pool:
                return []
            sql = """
                SELECT * FROM content_tasks
                WHERE status = 'in_progress'
                  AND updated_at < NOW() - ($1 * INTERVAL '1 minute')
                ORDER BY updated_at ASC
                LIMIT $2
            """
            try:
                async with self.pool.acquire() as conn:
                    rows = await asyncio.wait_for(
                        conn.fetch(sql, timeout_minutes, limit),
                        timeout=QUERY_TIMEOUT,
                    )
                    return [self._convert_row_to_dict(row) for row in rows]
            except asyncio.TimeoutError:
                logger.error(
                    f"[get_stale_in_progress_tasks] Query timeout after {QUERY_TIMEOUT}s",
                    exc_info=True,
                )
                return []
        except Exception as e:
            logger.error(
                f"[get_stale_in_progress_tasks] Error fetching stale tasks: {e}", exc_info=True
            )
            return []

    async def sweep_stale_tasks(
        self, timeout_minutes: int = 30, max_retries: int = 3
    ) -> dict:
        """
        Sweep stale in_progress tasks and reset or permanently fail them.

        Tasks stuck in in_progress longer than timeout_minutes are either:
        - Reset to pending (if retry_count < max_retries) so the executor picks them up again
        - Marked permanently failed (if retry_count >= max_retries)

        Retry count is stored in task_metadata so no schema migration is required.

        Args:
            timeout_minutes: Staleness threshold in minutes
            max_retries: Maximum reset attempts before permanent failure

        Returns:
            Dict with 'reset', 'failed', and 'total_stale' counts
        """
        stale_tasks = await self.get_stale_in_progress_tasks(timeout_minutes)
        if not stale_tasks:
            return {"reset": 0, "failed": 0, "total_stale": 0}

        logger.warning(
            f"[sweep_stale_tasks] Found {len(stale_tasks)} stale in_progress task(s) "
            f"(stuck > {timeout_minutes}min)"
        )

        reset_count = 0
        failed_count = 0

        for task in stale_tasks:
            task_id = task.get("task_id") or str(task.get("id", ""))
            if not task_id:
                logger.warning("[sweep_stale_tasks] Skipping stale task with no identifiable ID")
                continue

            metadata = task.get("task_metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            retry_count = int(metadata.get("retry_count", 0))

            if retry_count < max_retries:
                new_retry_count = retry_count + 1
                await self.update_task(
                    task_id,
                    {
                        "status": "pending",
                        "error_message": (
                            f"Reset after stale timeout — attempt {new_retry_count}/{max_retries}"
                        ),
                        "task_metadata": {
                            **metadata,
                            "retry_count": new_retry_count,
                            "last_reset_reason": "stale_timeout",
                            "last_reset_at": datetime.utcnow().isoformat(),
                        },
                    },
                )
                logger.info(
                    f"[sweep_stale_tasks] Reset task {task_id} to pending "
                    f"(retry {new_retry_count}/{max_retries})"
                )
                reset_count += 1
            else:
                await self.update_task(
                    task_id,
                    {
                        "status": "failed",
                        "error_message": (
                            f"Permanently failed: stale timeout exceeded after "
                            f"{max_retries} retry attempts"
                        ),
                        "task_metadata": {
                            **metadata,
                            "permanently_failed": True,
                            "failed_reason": "stale_timeout_max_retries_exceeded",
                            "failed_at": datetime.utcnow().isoformat(),
                        },
                    },
                )
                logger.warning(
                    f"[sweep_stale_tasks] Permanently failed task {task_id} "
                    f"(exhausted {max_retries} retries)"
                )
                failed_count += 1

        logger.info(
            f"[sweep_stale_tasks] Sweep complete: {reset_count} reset to pending, "
            f"{failed_count} permanently failed"
        )
        return {"reset": reset_count, "failed": failed_count, "total_stale": len(stale_tasks)}

    async def get_all_tasks(self, limit: int = 100) -> List[TaskResponse]:
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
            logger.error(f"[get_all_tasks] Error fetching all tasks: {e}", exc_info=True)
            return []

    async def add_task(self, task_data: Dict[str, Any]) -> str:
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
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        # Ensure task_name is preserved in metadata since there is no column for it
        if "task_name" in task_data and "task_name" not in metadata:
            metadata["task_name"] = task_data["task_name"]

        try:
            # Use UTC datetime - note asyncpg prefers naive UTC which PostgreSQL auto-converts
            # The column is TIMESTAMP WITH TIME ZONE, so PostgreSQL handles timezone conversion
            utc_now = datetime.utcnow()

            # Build insert columns dict
            insert_data = {
                "task_id": task_id,
                "content_type": task_data.get("content_type")
                or task_data.get("task_type", "blog_post"),
                "task_type": task_data.get("task_type", "blog_post"),
                "request_type": task_data.get("request_type", "content_generation"),
                "status": task_data.get("status", "pending"),
                "topic": task_data.get("topic", ""),
                "title": task_data.get("title")
                or task_data.get("task_name"),  # Support both title and task_name
                # CRITICAL: Don't override user selections - trust Pydantic schema defaults
                # or explicit user choices from UI. Only set if explicitly None/missing.
                "style": task_data.get("style"),  # Let Pydantic/UI set defaults
                "tone": task_data.get("tone"),  # Let Pydantic/UI set defaults
                "target_length": task_data.get("target_length", 1500),
                "agent_id": task_data.get("agent_id", "content-agent"),
                "primary_keyword": task_data.get("primary_keyword"),
                "target_audience": task_data.get("target_audience"),
                "category": task_data.get("category"),
                "writing_style_id": task_data.get("writing_style_id"),
                "content": metadata.get("content") or task_data.get("content"),
                "excerpt": metadata.get("excerpt") or task_data.get("excerpt"),
                "featured_image_url": metadata.get("featured_image_url")
                or task_data.get("featured_image_url"),
                "featured_image_data": (
                    json.dumps(
                        metadata.get("featured_image_data") or task_data.get("featured_image_data")
                    )
                    if (metadata.get("featured_image_data") or task_data.get("featured_image_data"))
                    else None
                ),
                "featured_image_prompt": task_data.get("featured_image_prompt"),
                "qa_feedback": metadata.get("qa_feedback"),
                "quality_score": metadata.get("quality_score") or task_data.get("quality_score"),
                "seo_title": metadata.get("seo_title"),
                "seo_description": metadata.get("seo_description"),
                "seo_keywords": metadata.get("seo_keywords"),
                "stage": metadata.get("stage", "pending"),
                "percentage": metadata.get("percentage", 0),
                "message": metadata.get("message"),
                "tags": json.dumps(task_data.get("tags", [])),
                "task_metadata": json.dumps(metadata or {}),
                "model_used": task_data.get("model_used"),
                "models_used_by_phase": json.dumps(task_data.get("models_used_by_phase", {})),
                "model_selection_log": json.dumps(task_data.get("model_selection_log", {})),
                "error_message": task_data.get("error_message"),
                "approval_status": task_data.get("approval_status", "pending"),
                "publish_mode": task_data.get("publish_mode", "draft"),
                "model_selections": json.dumps(task_data.get("model_selections", {})),
                "quality_preference": task_data.get("quality_preference", "balanced"),
                "estimated_cost": float(task_data.get("estimated_cost", 0.0)),
                "cost_breakdown": (
                    json.dumps(task_data.get("cost_breakdown", {}))
                    if task_data.get("cost_breakdown")
                    else None
                ),
                "created_at": utc_now,
                "updated_at": utc_now,
            }

            # Serialize values for PostgreSQL (handles datetime, JSON, etc.)
            serialized_data = {}
            for key, value in insert_data.items():
                serialized = serialize_value_for_postgres(value)
                serialized_data[key] = serialized

            # 🔍 DEBUG: Log critical fields before DB insert
            logger.info(f"📊 [add_task] Critical fields being inserted:")
            logger.info(f"   task_id: {serialized_data.get('task_id')}")
            logger.info(
                f"   style: {serialized_data.get('style')} (original: {task_data.get('style')})"
            )
            logger.info(
                f"   tone: {serialized_data.get('tone')} (original: {task_data.get('tone')})"
            )
            logger.info(
                f"   model_selections: {serialized_data.get('model_selections')} (original: {task_data.get('model_selections')})"
            )
            logger.info(f"   quality_preference: {serialized_data.get('quality_preference')}")

            builder = ParameterizedQueryBuilder()
            sql, params = builder.insert(
                table="content_tasks", columns=serialized_data, return_columns=["task_id"]
            )

            async with self.pool.acquire() as conn:
                result = await conn.fetchval(sql, *params)
                logger.info(f"✅ Task added: {task_id}")
                return str(result)
        except Exception as e:
            logger.error(f"[add_task] Failed to add task {task_id}: {e}", exc_info=True)
            raise

    async def get_task(self, task_id: str) -> Optional[dict]:
        """
        Get a task from content_tasks by ID.

        Supports both:
        - UUID task IDs (stored in task_id column)
        - Numeric IDs (stored in id column, legacy format)

        Args:
            task_id: Task ID (UUID or numeric)

        Returns:
            Task dict or None if not found
        """
        # First, try to find by numeric ID if the task_id looks numeric
        if task_id.isdigit():
            builder = ParameterizedQueryBuilder()
            sql, params = builder.select(
                columns=["*"],
                table="content_tasks",
                where_clauses=[("id", SQLOperator.EQ, int(task_id))],
            )

            try:
                async with self.pool.acquire() as conn:
                    row = await conn.fetchrow(sql, *params)
                    if row:
                        task_response = ModelConverter.to_task_response(row)
                        return ModelConverter.to_dict(task_response)
            except Exception as e:
                logger.debug(
                    f"[get_task] Numeric ID lookup failed for {task_id}: {e}", exc_info=True
                )

        # Try UUID lookup
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["*"],
            table="content_tasks",
            where_clauses=[("task_id", SQLOperator.EQ, str(task_id))],
        )

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                if row:
                    task_response = ModelConverter.to_task_response(row)
                    return ModelConverter.to_dict(task_response)
                return None
        except Exception as e:
            logger.error(f"[get_task] Failed to get task {task_id}: {e}", exc_info=True)
            return None

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update task status in content_tasks.

        Supports both numeric IDs (legacy) and UUID task IDs.
        Tries both id and task_id columns to ensure match.

        Args:
            task_id: Task ID (numeric or UUID)
            status: New status
            result: Optional result data

        Returns:
            Updated task dict or None if task not found
        """
        # Use naive UTC datetime to avoid asyncpg timezone mismatch
        now = datetime.utcnow()

        try:
            builder = ParameterizedQueryBuilder()

            updates = {"status": status, "updated_at": now}

            if result:
                updates["result"] = result

            # First, try to determine the right column to use
            where_column = "id" if task_id.isdigit() else "task_id"
            where_value = int(task_id) if task_id.isdigit() else str(task_id)

            sql, params = builder.update(
                table="content_tasks",
                updates=updates,
                where_clauses=[(where_column, SQLOperator.EQ, where_value)],
                return_columns=["*"],
            )

            logger.debug(
                f"[update_task_status] Executing UPDATE with where_column={where_column}, where_value={where_value}"
            )

            async with self.pool.acquire() as conn:
                logger.debug(f"[update_task_status] SQL: {sql}")
                logger.debug(f"[update_task_status] Params: {params}")

                row = await conn.fetchrow(sql, *params)
                if row:
                    logger.info(f"Task status updated: {task_id} → {status}")
                    task_dict = self._convert_row_to_dict(row)
                    logger.debug(
                        f"[update_task_status] Returned task status: {task_dict.get('status')}"
                    )
                    return task_dict

                # If not found with primary approach, try alternate column
                logger.warning(
                    f"[update_task_status] First attempt returned no rows. Task ID: {task_id}, where_column: {where_column}, value: {where_value}"
                )

                # Try the opposite column
                alt_where_column = "task_id" if where_column == "id" else "id"
                alt_where_value = (
                    str(task_id)
                    if where_column == "id"
                    else (int(task_id) if task_id.isdigit() else task_id)
                )

                logger.debug(
                    f"[update_task_status] Trying alternate where_column={alt_where_column}, where_value={alt_where_value}"
                )

                sql_alt, params_alt = builder.update(
                    table="content_tasks",
                    updates=updates,
                    where_clauses=[(alt_where_column, SQLOperator.EQ, alt_where_value)],
                    return_columns=["*"],
                )

                logger.debug(f"[update_task_status] Alt SQL: {sql_alt}")
                logger.debug(f"[update_task_status] Alt Params: {params_alt}")

                row_alt = await conn.fetchrow(sql_alt, *params_alt)
                if row_alt:
                    logger.info(f"Task status updated (alternate ID): {task_id} → {status}")
                    result_alt = self._convert_row_to_dict(row_alt)
                    logger.debug(
                        f"[update_task_status] Returned task status (alt): {result_alt.get('status')}"
                    )
                    return result_alt

                # Task not found with either approach
                logger.error(
                    f"[update_task_status] Task not found with either ID approach. task_id={task_id}, tried columns: {where_column} and {alt_where_column}"
                )
                logger.error(
                    f"[update_task_status] Values tried: {where_value} and {alt_where_value}"
                )
                return None

        except Exception as e:
            logger.error(
                f"[update_task_status] Exception updating task status {task_id}: {e}", exc_info=True
            )
            return None

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[dict]:
        """
        Update task fields in content_tasks.

        Extracts and normalizes fields from task_metadata into dedicated columns.

        Args:
            task_id: Task ID
            updates: Dict of fields to update

        Returns:
            Updated task dict or None
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"🔵 TasksDatabase.update_task() ENTRY")
        logger.info(f"   Task ID: {task_id}")
        logger.info(f"   Updates received: {list(updates.keys())}")
        logger.info(f"{'='*80}")

        if not updates:
            logger.info(f"   No updates provided, returning current task")
            return await self.get_task(task_id)

        # Extract task_metadata for normalization
        logger.info(f"🔍 Extracting task_metadata for normalization...")
        task_metadata = updates.get("task_metadata", {})
        if isinstance(task_metadata, str):
            try:
                task_metadata = json.loads(task_metadata)
            except (json.JSONDecodeError, TypeError):
                task_metadata = {}
        elif task_metadata is None:
            task_metadata = {}

        # Prepare normalized updates
        normalized_updates = dict(updates)

        # Normalize "metadata" key to "task_metadata" (database column name)
        if "metadata" in normalized_updates and "task_metadata" not in normalized_updates:
            normalized_updates["task_metadata"] = normalized_updates.pop("metadata")

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

        # DEBUG: Log normalized updates
        logger.info(f"🔍 [DEBUG] Normalized updates for task {task_id}:")
        logger.info(f"   - Keys: {list(normalized_updates.keys())}")
        logger.info(f"   - Has 'content' in normalized: {'content' in normalized_updates}")
        if "content" in normalized_updates:
            logger.info(
                f"   - Content length: {len(normalized_updates.get('content') or '')} chars"
            )

        try:
            # Determine which column to update (id for numeric, task_id for UUID)
            where_column = "id" if task_id.isdigit() else "task_id"
            where_value = int(task_id) if task_id.isdigit() else str(task_id)

            builder = ParameterizedQueryBuilder()
            sql, params = builder.update(
                table="content_tasks",
                updates=serialized_updates,
                where_clauses=[(where_column, SQLOperator.EQ, where_value)],
                return_columns=["*"],
            )

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                if row:
                    # DEBUG: Verify content was persisted
                    logger.info(f"✅ [DEBUG] Update returned row for task {task_id}")
                    logger.info(f"   - Row has 'content': {row.get('content') is not None}")
                    if row.get("content"):
                        logger.info(
                            f"   - Persisted content length: {len(row.get('content'))} chars"
                        )
                    task_response = ModelConverter.to_task_response(row)
                    return ModelConverter.to_dict(task_response)
                logger.warning(f"⚠️  [DEBUG] Update returned no row for task {task_id}")
                return None
        except Exception as e:
            logger.error(f"[update_task] Failed to update task {task_id}: {e}", exc_info=True)
            return None

    @log_query_performance(
        operation="get_tasks_paginated", category="task_retrieval", slow_threshold_ms=50
    )
    async def get_tasks_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Get paginated tasks from content_tasks with optional filtering.

        Args:
            offset: Result offset
            limit: Maximum results per page
            status: Filter by status
            category: Filter by category

        Returns:
            Tuple of (tasks list, total count)
        """
        builder = ParameterizedQueryBuilder()

        # Build WHERE clauses
        where_clauses = []
        # Note: owner_id column doesn't exist in schema, skipping user_id filter
        if status:
            where_clauses.append(("status", SQLOperator.EQ, status))
        if category:
            where_clauses.append(("category", SQLOperator.EQ, category))

        # Build count query
        count_sql, count_params = builder.select(
            columns=["COUNT(*) as count"],
            table="content_tasks",
            where_clauses=where_clauses if where_clauses else None,
        )

        # Reset builder for main query
        builder = ParameterizedQueryBuilder()
        sql_list, list_params = builder.select(
            columns=["*"],
            table="content_tasks",
            where_clauses=where_clauses if where_clauses else None,
            order_by=[("created_at", "DESC")],
            limit=limit,
            offset=offset,
        )

        try:
            async with self.pool.acquire() as conn:
                count_result = await conn.fetchval(count_sql, *count_params)
                total = count_result or 0

                rows = await conn.fetch(sql_list, *list_params)

                tasks = [self._convert_row_to_dict(row) for row in rows]
                logger.info(f"✅ Listed {len(tasks)} tasks (total: {total})")
                return tasks, total
        except Exception as e:
            logger.error(f"[get_tasks_paginated] Failed to list tasks: {e}", exc_info=True)
            return [], 0

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
            logger.error(f"[get_task_counts] Failed to get task counts: {e}", exc_info=True)
            return TaskCountsResponse(
                total=0,
                pending=0,
                in_progress=0,
                completed=0,
                failed=0,
                awaiting_approval=0,
                approved=0,
            )

    async def get_queued_tasks(self, limit: int = 5) -> List[TaskResponse]:
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
            logger.error(f"[get_queued_tasks] Failed to get queued tasks: {e}", exc_info=True)
            return []

    @log_query_performance(
        operation="get_tasks_by_date_range", category="analytics", slow_threshold_ms=200
    )
    async def get_tasks_by_date_range(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        """
        Get tasks from content_tasks within date range.

        Used by analytics endpoint to fetch tasks for KPI calculations.

        Args:
            start_date: Start of date range (UTC) - defaults to very old date if None
            end_date: End of date range (UTC) - defaults to now if None
            status: Filter by status (e.g., 'completed', 'failed') - optional
            limit: Maximum results to return

        Returns:
            List of task dicts with all fields
        """
        # Default to all-time if not specified
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        if start_date is None:
            start_date = datetime.now(timezone.utc) - timedelta(days=3650)  # ~10 years back

        try:
            builder = ParameterizedQueryBuilder()
            where_clauses = [
                ("created_at", SQLOperator.GE, start_date),
                ("created_at", SQLOperator.LE, end_date),
            ]

            if status:
                where_clauses.append(("status", SQLOperator.EQ, status))  # type: ignore[arg-type]

            sql, params = builder.select(
                columns=["*"],
                table="content_tasks",
                where_clauses=where_clauses,
                order_by=[("created_at", "DESC")],
                limit=limit,
            )

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                tasks = [self._convert_row_to_dict(row) for row in rows]
                logger.debug(
                    f"✅ Retrieved {len(tasks)} tasks for date range {start_date} to {end_date}"
                )
                return tasks
        except Exception as e:
            logger.error(
                f"[get_tasks_by_date_range] Failed to get tasks by date range: {e}", exc_info=True
            )
            return []

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
            # Determine which column to check (id for numeric, task_id for UUID)
            where_column = "id" if task_id.isdigit() else "task_id"
            where_value = int(task_id) if task_id.isdigit() else str(task_id)

            builder = ParameterizedQueryBuilder()
            sql, params = builder.delete(
                table="content_tasks", where_clauses=[(where_column, SQLOperator.EQ, where_value)]
            )

            async with self.pool.acquire() as conn:
                result = await conn.execute(sql, *params)
                deleted = "DELETE 1" in result or result == "DELETE 1"
                if deleted:
                    logger.info(f"✅ Task deleted: {task_id}")
                return deleted
        except Exception as e:
            logger.error(f"[delete_task] Error deleting task {task_id}: {e}", exc_info=True)
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
            # OR conditions require manual SQL - use parameterized queries
            sql = """
                SELECT * FROM content_tasks
                WHERE status = $1 OR approval_status = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
            """
            params = ["pending", "pending", limit, offset]

            count_sql = """
                SELECT COUNT(*) FROM content_tasks
                WHERE status = $1 OR approval_status = $2
            """
            count_params = ["pending", "pending"]

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                total = await conn.fetchval(count_sql, *count_params)

                drafts = [self._convert_row_to_dict(row) for row in rows]
                return (drafts, total or 0)
        except Exception as e:
            logger.error(f"[get_drafts] Error getting drafts: {e}", exc_info=True)
            return ([], 0)

    async def log_status_change(
        self,
        task_id: str,
        old_status: str,
        new_status: str,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
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
                INSERT INTO task_status_history (task_id, old_status, new_status, reason, metadata, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6)
            """

            # Use naive UTC datetime to avoid asyncpg timezone mismatch
            now = datetime.utcnow()
            metadata_json = json.dumps(metadata or {})

            async with self.pool.acquire() as conn:
                await conn.execute(
                    sql, task_id, old_status, new_status, reason or "", metadata_json, now
                )
                logger.info(f"✅ Status change logged: {task_id} {old_status} → {new_status}")
                return True
        except Exception as e:
            logger.error(f"[log_status_change] Failed to log status change: {e}", exc_info=True)
            return False

    async def get_status_history(self, task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
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
                logger.error(
                    "[get_status_history] Database pool is not initialized; returning empty history"
                )
                return []

            sql = """
                SELECT id, task_id, old_status, new_status, reason, metadata, timestamp
                FROM task_status_history
                WHERE task_id = $1
                ORDER BY timestamp DESC
                LIMIT $2
            """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, task_id, limit)

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
                            "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                        }
                    )

                logger.info(f"✅ Retrieved {len(history)} status changes for task {task_id}")
                return history
        except Exception as e:
            logger.error(f"[get_status_history] Failed to get status history: {e}", exc_info=True)
            return []

    async def get_validation_failures(self, task_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get all validation failures for a task by querying status history.

        Args:
            task_id: Task ID
            limit: Maximum records to return

        Returns:
            List of validation failure records with details
        """
        try:
            if not self.pool:
                logger.error(
                    "[get_validation_failures] Database pool is not initialized; returning empty failures"
                )
                return []

            sql = """
                SELECT id, task_id, old_status, new_status, reason, metadata, timestamp
                FROM task_status_history
                WHERE task_id = $1
                AND new_status IN ('validation_failed', 'validation_error')
                ORDER BY timestamp DESC
                LIMIT $2
            """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql, task_id, limit)

                failures = []
                for row in rows:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                    failures.append(
                        {
                            "id": row["id"],
                            "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                            "reason": row["reason"],
                            "errors": metadata.get("validation_errors", []),
                            "context": metadata.get("context", {}),
                        }
                    )

                logger.info(f"✅ Retrieved {len(failures)} validation failures for task {task_id}")
                return failures
        except Exception as e:
            logger.error(
                f"[get_validation_failures] Failed to get validation failures: {e}", exc_info=True
            )
            return []
