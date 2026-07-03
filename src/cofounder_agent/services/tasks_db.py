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
from typing import Any, cast
from uuid import UUID, uuid4

from asyncpg import Pool

from plugins.tracing import inject_trace_context
from schemas.database_response_models import TaskCountsResponse, TaskResponse
from schemas.model_converter import ModelConverter
from schemas.typed_records import PaginatedTasksResult, TaskRecord
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


def _parse_rowcount(status: Any) -> int:
    """Extract the affected-row count from an asyncpg command-status tag.

    asyncpg's ``Connection.execute`` returns the command tag string, e.g.
    ``"DELETE 7"`` / ``"UPDATE 0"``. The row count is the trailing integer.
    Returns 0 for anything unparseable (None, empty, non-string mocks),
    so callers can sum counts without guarding each call.
    """
    if not isinstance(status, str):
        return 0
    parts = status.rsplit(" ", 1)
    if len(parts) != 2:
        return 0
    try:
        return int(parts[1])
    except ValueError:
        return 0


async def _resolve_default_template_slug(pool: Pool) -> str:
    """Read ``app_settings.default_template_slug``.

    Lane C cutover seam (Glad-Labs/poindexter#355). Empty string +
    missing row both return ``""`` so the caller's ``or None`` collapse
    produces a NULL ``template_slug`` and the legacy chunked
    StageRunner path runs unchanged. Operators flip to
    ``'canonical_blog'`` to route every new task through TemplateRunner.

    Best-effort — any DB error returns the empty string so a transient
    setting-table hiccup never breaks task creation. The legacy path
    is the safe fallback.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings "
                "WHERE key = 'default_template_slug' AND is_active = true "
                "LIMIT 1"
            )
    except Exception:
        return ""
    if row is None:
        return ""
    return str(row["value"] or "").strip()


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
        # Reads go through the view (so consumers see one flat row shape);
        # writes go DIRECTLY to the underlying base tables for explicit
        # control over the column split. The INSTEAD OF insert/update/delete
        # triggers that redirect view-writes ARE present (seeded by
        # 0000_baseline.schema.sql, verified on prod 2026-06-19) — the
        # earlier "#188 not reliably present" concern is stale — but the app
        # deliberately writes the base tables itself rather than leaning on
        # them. See add_task() for the column routing.

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
        ``content_tasks`` is a VIEW (since migration 0125). Its INSTEAD OF
        INSERT trigger (``content_tasks_insert_redirect``) IS present —
        seeded by ``0000_baseline.schema.sql`` and verified on prod
        (2026-06-19); the earlier "not reliably present" framing is stale.
        The app nonetheless writes the base tables directly rather than
        INSERTing the view, for explicit control over the column split —
        mirroring what the trigger would do, in application code:

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

            # Lane C cutover (Glad-Labs/poindexter#355) — resolve the
            # template_slug for this task. Caller-supplied value wins;
            # otherwise we read app_settings.default_template_slug. Empty
            # string / None → NULL slug → legacy chunked StageRunner flow
            # in content_router_service runs (preserves pre-#355
            # behavior). Setting to 'canonical_blog' routes every new
            # task through TemplateRunner + the LangGraph
            # canonical_blog template.
            template_slug = task_data.get("template_slug")
            if not template_slug:
                template_slug = await _resolve_default_template_slug(self.pool)
            template_slug = template_slug or None  # '' / falsy → NULL

            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # ``category`` is intentionally omitted from this column
                    # list. It is a vestigial, base-table-only field (superseded
                    # by ``niche_slug``, #796): the physical
                    # ``pipeline_tasks.category`` column exists only so
                    # ``claim_pending_task``'s SELECT resolves (it was dropped by
                    # 20260622_032938 and re-added by 20260622_055500), but
                    # nothing populates it and the ``content_tasks`` /
                    # ``pipeline_tasks_view`` views project a NULL shim — so a
                    # value written here would never surface through them anyway.
                    # Do NOT re-add it; see TestAddTaskAgainstRealDb.
                    # Tier 1b (Glad-Labs/poindexter#1997): stamp the
                    # enqueuer's W3C trace context onto the row so the claiming
                    # Prefect flow can link its root span to the trace of whatever
                    # created this task (the DB queue is a non-HTTP boundary the
                    # instrumentors don't cross). NULL when there's no active span
                    # — the flow then starts a fresh root span exactly as before.
                    trace_carrier = inject_trace_context()
                    trace_context_json = (
                        json.dumps(trace_carrier) if trace_carrier else None
                    )
                    await conn.execute(
                        """
                        INSERT INTO pipeline_tasks (
                            task_id, task_type, topic, status, stage,
                            site_id, style, tone, target_length,
                            primary_keyword, target_audience,
                            percentage, message, model_used,
                            error_message, template_slug, niche_slug,
                            created_at, updated_at, trace_context
                        ) VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, $11,
                            $12, $13, $14,
                            $15, $16, $17,
                            $18, $18, $19::jsonb
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
                        task_data.get("primary_keyword"),
                        task_data.get("target_audience"),
                        metadata.get("percentage", 0),
                        metadata.get("message"),
                        task_data.get("model_used"),
                        task_data.get("error_message"),
                        template_slug,
                        task_data.get("niche_slug") or None,
                        now,
                        trace_context_json,
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

        # Lane C cutover (#355): resolve the default once per bulk batch
        # rather than per-row. The setting rarely changes, and bulk
        # inserts of N tasks shouldn't fire N separate setting reads.
        default_slug = await _resolve_default_template_slug(self.pool)

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

            # Per-task template_slug wins; otherwise use the batch-resolved
            # default; otherwise NULL (legacy chunked StageRunner path).
            row_slug = task_data.get("template_slug") or default_slug or None

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
                    task_data.get("primary_keyword"),
                    task_data.get("target_audience"),
                    row_slug,
                    task_data.get("niche_slug") or None,
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

        # ``category`` intentionally omitted — base-table-only vestige that
        # nothing populates and the views don't surface; see add_task().
        pipeline_sql = """
            INSERT INTO pipeline_tasks (
                task_id, task_type, topic, status, stage,
                site_id, style, tone, target_length,
                primary_keyword, target_audience,
                template_slug, niche_slug, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11,
                $12, $13, $14, $14
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
    async def get_task(self, task_id: str) -> TaskRecord | None:
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
                # Fallback: UUID prefix match (8+ chars, looks like a UUID prefix).
                # One scan: fetch up to 2 matches so an ambiguous prefix (>1 row)
                # is detectable from the result length — no second identical LIKE
                # query (#702 item 4).
                if not row and len(task_id) >= 8 and "-" not in task_id[8:]:
                    prefix_rows = await conn.fetch(
                        "SELECT * FROM content_tasks WHERE task_id LIKE $1 LIMIT 2",
                        f"{task_id}%",
                    )
                    if len(prefix_rows) > 1:
                        logger.warning("Ambiguous task_id prefix '%s' matches multiple tasks", task_id)
                        return None
                    row = prefix_rows[0] if prefix_rows else None
                if row:
                    task_response = ModelConverter.to_task_response(row)
                    return cast(TaskRecord, ModelConverter.to_dict(task_response))
                return None
        except Exception as e:
            logger.error("Failed to get task %s: %s", task_id, e, exc_info=True)
            return None

    async def get_tasks_by_ids(self, task_ids: list[str]) -> dict[str, dict[str, Any]]:
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
    async def update_task(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
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
            return cast(dict[str, Any] | None, await self.get_task(task_id))

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
            # featured_image_data / actual_cost / cost_breakdown are
            # deliberately NOT extracted to top-level: they have no dedicated
            # content_tasks column (the view projects featured_image_url, never
            # featured_image_data). They ride inside task_metadata (JSONB) and
            # the INSTEAD OF UPDATE trigger maps them to
            # stage_data -> 'task_metadata'. See _VIEW_COLUMNS below.
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
        #
        # This set MUST mirror the real content_tasks view columns (verify
        # against information_schema.columns, not intuition). featured_image_data
        # / actual_cost / cost_breakdown are intentionally absent — they are
        # task_metadata JSONB keys, never view columns. Listing them here made
        # the filter wave them through to `SET <key> = …`, which raised
        # UndefinedColumnError and failed the whole update (dropping its sibling
        # column writes too). featured_image_data lands on posts.featured_image_data
        # at publish via task_metadata; the cost blobs ride in task_metadata.
        _VIEW_COLUMNS = {
            "id", "task_id", "task_type", "content_type", "title", "topic",
            "status", "stage", "style", "tone", "target_length",
            "primary_keyword", "target_audience", "content", "excerpt",
            "featured_image_url", "quality_score",
            "qa_feedback", "seo_title", "seo_description", "seo_keywords",
            "percentage", "message", "model_used", "error_message",
            "models_used_by_phase", "metadata", "result", "task_metadata",
            "site_id", "created_at", "updated_at", "started_at",
            "completed_at", "approval_status", "approved_by",
            "human_feedback", "post_id", "post_slug", "published_at",
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
                    return str(prev)  # prev is status text from SELECT; fetchval returns Any
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
        light: bool = False,
    ) -> PaginatedTasksResult:
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

        # The content_tasks view's SELECT * pulls heavy blobs (full content,
        # qa_feedback, stage_data-derived metadata/result) AND evaluates 5
        # correlated subqueries per row (approval_status / approved_by /
        # human_feedback / post_id / post_slug). The polled approvals list only
        # needs a content PREVIEW + a few scalars, so light=True projects just
        # those and lets Postgres prune the unused view expressions (#619).
        if light:
            select_cols = (
                "id, task_id, task_type, title, topic, status, created_at, "
                "quality_score, featured_image_url, "
                "LEFT(content, 250) AS content, task_metadata"
            )
        else:
            select_cols = "*"

        # Single round-trip: window function COUNT(*) OVER () returns total alongside rows
        sql_list = f"""
            SELECT {select_cols}, COUNT(*) OVER () AS total_count
            FROM content_tasks
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
        """  # nosec B608  # select_cols + where_sql built from local literals; limit/offset are "${N}" placeholders; values use $N params

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(sql_list, *params)
                total = rows[0]["total_count"] if rows else 0
                tasks: list[TaskRecord] = [cast(TaskRecord, self._convert_row_to_dict(row)) for row in rows]
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
        to 'pending' (if retry_count < max_retries) or marked 'failed'.
        All updates happen in a single transaction with batched queries.

        Cycle-5 #253: the prior implementation read + wrote the
        ``content_tasks`` *view* (a JOIN of pipeline_tasks x
        pipeline_versions). PostgreSQL refuses UPDATE on views with
        multiple base tables and the ``task_metadata`` column the
        sweeper expected was a computed expression
        (``pv.stage_data->'task_metadata'``) — the sweeper silently
        failed on every fire, stale rows piled up in ``in_progress``
        forever, retries never incremented, max-retries-exceeded never
        triggered. This rewrite targets ``pipeline_tasks`` directly
        and uses the real ``retry_count`` column added by migration
        20260527_183209.

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
                    # Fetch stale rows directly from pipeline_tasks. The
                    # ``retry_count`` column is a real integer (NOT NULL
                    # DEFAULT 0 since migration 20260527_183209) — no
                    # JSON arithmetic, no view dependency.
                    stale_rows = await conn.fetch(
                        """
                        SELECT task_id, retry_count
                        FROM pipeline_tasks
                        WHERE status = 'in_progress'
                          AND awaiting_gate IS NULL
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
                        retry_count = row["retry_count"] or 0
                        if retry_count < max_retries:
                            reset_ids.append(task_id)
                        else:
                            fail_ids.append(task_id)

                    now = datetime.now(timezone.utc)

                    # Batch reset: set back to pending with incremented
                    # retry_count. The atom-column form lets the
                    # database handle the increment, no read-modify-write.
                    if reset_ids:
                        await conn.execute(
                            """
                            UPDATE pipeline_tasks
                            SET status = 'pending',
                                retry_count = retry_count + 1,
                                updated_at = $1
                            WHERE task_id = ANY($2::text[])
                            """,
                            now,
                            reset_ids,
                        )

                    # Batch fail: mark as failed; retry_count is left
                    # at its final value so the audit trail shows how
                    # many sweeps it survived before the kill.
                    if fail_ids:
                        await conn.execute(
                            """
                            UPDATE pipeline_tasks
                            SET status = 'failed',
                                updated_at = $1,
                                error_message = 'Exceeded maximum retries after stale sweep'
                            WHERE task_id = ANY($2::text[])
                            """,
                            now,
                            fail_ids,
                        )

                    # Clear the LangGraph Postgres checkpoint for every
                    # swept task so any retry runs a FRESH graph instead
                    # of resuming a half-written (poisoned) checkpoint.
                    #
                    # Failure mode this prevents: a ``canonical_blog`` run
                    # killed mid-pipeline (worker restart / crash / OOM)
                    # leaves a partial checkpoint keyed by
                    # ``thread_id == task_id`` in the ``checkpoints`` /
                    # ``checkpoint_blobs`` / ``checkpoint_writes`` tables
                    # (template_runner sets ``thread_id`` to the bare
                    # task_id when ``template_runner_use_postgres_checkpointer``
                    # is on). When this sweeper resets the orphaned task to
                    # ``pending`` and the dispatcher re-claims it, LangGraph
                    # RESUMES the poisoned checkpoint → the graph runs ~1
                    # node (``verify_task``) and exits "complete" at score 0
                    # (``preview_token`` missing). The task can never
                    # regenerate until the checkpoint rows are deleted.
                    # Verified in prod: clearing the rows makes the next
                    # run execute the full 21-node graph normally.
                    #
                    # Covers BOTH buckets (reset→pending AND →failed): a
                    # failed task may still be retried by an operator, and
                    # neither should ever resume a stale checkpoint.
                    swept_ids = reset_ids + fail_ids
                    await self._clear_checkpoints_for_threads(conn, swept_ids)

                    logger.info(
                        "Stale task sweep complete: %d reset, %d failed (threshold=%dm)",
                        len(reset_ids), len(fail_ids), stale_threshold_minutes,
                    )

            # poindexter#807 — the crash→sweep→requeue cycle was invisible
            # (worker-log line only); the queue backlog was the first
            # operator-visible symptom. Emit one warn finding per swept task
            # (outside the transaction — the status writes are committed)
            # with a per-task dedup key so a task looping through repeated
            # sweeps re-surfaces after the routing cooldown.
            self._emit_sweep_findings(
                stale_rows=stale_rows,
                reset_ids=reset_ids,
                fail_ids=fail_ids,
                stale_threshold_minutes=stale_threshold_minutes,
                max_retries=max_retries,
            )
            return {"reset": len(reset_ids), "failed": len(fail_ids)}

        except Exception as e:
            logger.error("Failed to sweep stale tasks: %s", e, exc_info=True)
            return {"reset": 0, "failed": 0}

    def _emit_sweep_findings(
        self,
        *,
        stale_rows: list[Any],
        reset_ids: list[str],
        fail_ids: list[str],
        stale_threshold_minutes: int,
        max_retries: int,
    ) -> None:
        """Emit ``stale_task_reclaimed`` / ``task_retries_exhausted`` findings.

        Observability garnish — never raises (a failing emitter must not fail
        a sweep whose DB writes already committed).
        """
        try:
            from utils.findings import emit_finding

            retry_by_id = {
                row["task_id"]: int(row["retry_count"] or 0) for row in stale_rows
            }
            for task_id in reset_ids:
                prior = retry_by_id.get(task_id, 0)
                emit_finding(
                    source="sweep_stale_tasks",
                    kind="stale_task_reclaimed",
                    title=f"Stale in_progress task reclaimed → pending "
                    f"(retry {prior + 1}/{max_retries})",
                    body=(
                        f"Task {task_id} sat in_progress for over "
                        f"{stale_threshold_minutes}m with no progress — "
                        "typically a flow run killed mid-graph (probe "
                        "auto-crash, worker restart, or a wedged GPU wait). "
                        f"Reset to pending with retry_count={prior + 1}; its "
                        "LangGraph checkpoint was cleared so the retry runs "
                        "a fresh graph. Repeated findings for the same task "
                        "mean each retry is hitting the same wall."
                    ),
                    severity="warn",
                    dedup_key=f"stale-task-reclaimed:{task_id}",
                    extra={
                        "task_id": task_id,
                        "retry_count": prior + 1,
                        "max_retries": max_retries,
                    },
                )
            for task_id in fail_ids:
                prior = retry_by_id.get(task_id, 0)
                emit_finding(
                    source="sweep_stale_tasks",
                    kind="task_retries_exhausted",
                    title="Task failed after exhausting stale-sweep retries",
                    body=(
                        f"Task {task_id} was reclaimed from a stale "
                        f"in_progress state {prior} time(s) (max_retries="
                        f"{max_retries}) and never completed — marked "
                        "'failed'. Investigate what killed every attempt "
                        "before manually re-queueing."
                    ),
                    severity="warn",
                    dedup_key=f"task-retries-exhausted:{task_id}",
                    extra={
                        "task_id": task_id,
                        "retry_count": prior,
                        "max_retries": max_retries,
                    },
                )
        except Exception:
            logger.warning(
                "sweep_stale_tasks: emitting sweep findings failed", exc_info=True
            )

    async def _clear_checkpoints_for_threads(
        self,
        conn: Any,
        thread_ids: list[str],
    ) -> int:
        """Delete LangGraph Postgres-checkpointer rows for the given threads.

        The LangGraph checkpointer persists per-run graph state keyed by
        ``thread_id`` across three tables — ``checkpoint_writes``,
        ``checkpoint_blobs`` and ``checkpoints`` (``checkpoint_migrations``
        has no ``thread_id`` and is intentionally left alone). For the
        content pipeline the ``thread_id`` is the bare ``task_id`` (see
        ``template_runner.TemplateRunner.run``), so clearing a swept
        task's rows forces its next run to start a fresh graph.

        Safety / robustness:

        * **No-op when there are no threads** — returns 0 without touching
          the DB.
        * **Safe when checkpointing is off / fresh install** — guarded by
          ``to_regclass('public.checkpoints') IS NOT NULL`` so the sweeper
          never breaks on installs that don't run the Postgres checkpointer
          (the tables may simply not exist).
        * **Best-effort, never blocks the status reset** — any error here
          is logged and swallowed. The caller runs this inside the sweep
          transaction; letting a checkpoint-clear failure raise would roll
          back the far-more-important status reset, so we contain it.

        ``thread_id`` / ``task_id`` are ``varchar``/``text`` — cast
        ``::text[]`` (NOT ``::uuid[]``).

        Returns the number of checkpoint rows deleted across all three
        tables (0 when skipped).
        """
        if not thread_ids:
            return 0

        try:
            # Guard: the checkpoint tables only exist when the Postgres
            # checkpointer has been initialised. ``to_regclass`` returns
            # NULL for a missing relation instead of raising.
            tables_present = await conn.fetchval(
                "SELECT to_regclass('public.checkpoints') IS NOT NULL"
            )
            if not tables_present:
                logger.debug(
                    "Checkpoint tables absent — skipping checkpoint clear "
                    "for %d swept task(s)",
                    len(thread_ids),
                )
                return 0

            cleared = 0
            for table in (
                "checkpoint_writes",
                "checkpoint_blobs",
                "checkpoints",
            ):
                status = await conn.execute(
                    # nosec B608 — `table` is a fixed literal from the loop above
                    # (no user input); `thread_ids` is bound via the $1 parameter.
                    f"DELETE FROM {table} WHERE thread_id = ANY($1::text[])",  # nosec B608
                    thread_ids,
                )
                cleared += _parse_rowcount(status)

            logger.info(
                "Cleared %d LangGraph checkpoint row(s) for %d swept task(s)",
                cleared, len(thread_ids),
            )
            return cleared

        except Exception as e:
            # Best-effort: a checkpoint-clear failure must never roll back
            # or block the status reset (the reset is what actually frees
            # the stuck task). Log and move on.
            logger.warning(
                "Failed to clear LangGraph checkpoints for %d swept task(s): %s",
                len(thread_ids), e, exc_info=True,
            )
            return 0

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

