"""
Content Database Module

Handles all content-related database operations including:
- Post CRUD operations
- Quality evaluations and improvement tracking
- Category and tag management
- Author lookups
- Orchestrator training data
- Metrics calculation
"""

import json
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg
from asyncpg import Pool

from schemas.database_response_models import (
    AuthorResponse,
    CategoryResponse,
    MetricsResponse,
    OrchestratorTrainingDataResponse,
    PostResponse,
    QualityEvaluationResponse,
    QualityImprovementLogResponse,
    TagResponse,
)
from schemas.model_converter import ModelConverter
from services.logger_config import get_logger
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin
from .decorators import log_query_performance
from .error_handler import DatabaseError

logger = get_logger(__name__)


class ContentDatabase(DatabaseServiceMixin):
    """Content-related database operations (posts, quality, metrics)."""

    # In-memory cache TTL in seconds
    _CACHE_TTL = 60

    def __init__(self, pool: Pool):
        """
        Initialize content database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool
        self._cache: dict[str, tuple] = {}  # key -> (data, timestamp)

    @log_query_performance(operation="create_post", category="content_write")
    async def create_post(self, post_data: dict[str, Any]) -> PostResponse:
        """
        Create new post in posts table with all metadata fields.

        Args:
            post_data: Dict with title, slug, content, excerpt, featured_image_url, etc.

        Returns:
            Created post dict
        """
        post_id = post_data.get("id") or str(uuid4())

        # Validate and fix data types before insert
        seo_keywords = post_data.get("seo_keywords", "")
        if isinstance(seo_keywords, list):
            logger.warning("seo_keywords is list, converting to string: %s", seo_keywords)
            seo_keywords = ", ".join(str(kw) for kw in seo_keywords)  # Ensure each item is string
        elif isinstance(seo_keywords, str):
            # Already a string, no conversion needed (was causing character splitting)
            seo_keywords = seo_keywords
        else:
            # Convert other types to string
            seo_keywords = str(seo_keywords) if seo_keywords else ""

        tag_ids = post_data.get("tag_ids")
        if tag_ids and isinstance(tag_ids, str):
            logger.warning("tag_ids is string, converting to list: %s", tag_ids)
            tag_ids = [tag_ids]

        # Log insert details at DEBUG to avoid flooding INFO logs (#1327)
        logger.debug(
            "Inserting post",
            id=post_id,
            title=str(post_data.get("title") or "EMPTY")[:50],
            slug=post_data.get("slug"),
            status=post_data.get("status", "draft"),
            author_id=post_data.get("author_id"),
            category_id=post_data.get("category_id"),
            tag_ids=tag_ids,
        )

        async with self.pool.acquire() as conn:
            try:
                # Determine published_at timestamp based on status.
                # Callers (e.g. publish_service) may supply a future published_at
                # for scheduled spacing — honour it when present.
                is_published = post_data.get("status") == "published"
                explicit_published_at = post_data.get("published_at")

                row = await conn.fetchrow(
                    """
                    INSERT INTO posts (
                        id,
                        title,
                        slug,
                        content,
                        excerpt,
                        featured_image_url,
                        cover_image_url,
                        author_id,
                        category_id,
                        status,
                        published_at,
                        seo_title,
                        seo_description,
                        seo_keywords,
                        created_by,
                        updated_by,
                        created_at,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, NOW(), NOW())
                    RETURNING id, title, slug, content, excerpt, featured_image_url, cover_image_url,
                              author_id, category_id, status, published_at, created_at, updated_at
                    """,
                    post_id,
                    post_data.get("title"),
                    post_data.get("slug"),
                    post_data.get("content"),
                    post_data.get("excerpt"),
                    post_data.get("featured_image_url"),
                    post_data.get("cover_image_url"),
                    post_data.get("author_id"),
                    post_data.get("category_id"),
                    post_data.get("status", "draft"),
                    # published_at: use explicit value if provided, else NOW() for published
                    explicit_published_at or (datetime.now(timezone.utc) if is_published else None),
                    post_data.get("seo_title"),
                    post_data.get("seo_description"),
                    seo_keywords,
                    post_data.get("created_by"),
                    post_data.get("updated_by"),
                )
                if not row:
                    raise DatabaseError("Insert query returned no row - post creation failed")

                # Also write to the post_tags junction table (authoritative source per migration 014).
                # tag_ids on posts is kept in sync for backward compat but post_tags is canonical.
                # Single INSERT with unnest() replaces N per-tag round-trips (issue #703).
                if tag_ids:
                    clean_ids = [str(tid) for tid in tag_ids if tid]
                    if clean_ids:
                        await conn.execute(
                            """
                            INSERT INTO post_tags (post_id, tag_id)
                            SELECT $1, unnest($2::text[])
                            ON CONFLICT (post_id, tag_id) DO NOTHING
                            """,
                            post_id,
                            clean_ids,
                        )
                        logger.debug(
                            "Inserted tags into post_tags",
                            count=len(clean_ids),
                            post_id=post_id,
                        )

                logger.info(
                    "Post created successfully",
                    post_id=post_id,
                    status=post_data.get("status", "draft"),
                )
                return ModelConverter.to_post_response(row)
            except Exception as db_error:
                logger.error("DATABASE ERROR while creating post: %s", db_error, exc_info=True)
                raise DatabaseError(f"Failed to create post in database: {str(db_error)}") from db_error

    @log_query_performance(
        operation="get_post_by_slug", category="content_retrieval", slow_threshold_ms=50
    )
    async def get_post_by_slug(self, slug: str) -> PostResponse | None:
        """
        Get post by slug - used to check for existing posts before creation.

        Args:
            slug: The slug to search for

        Returns:
            Post dict if found, None otherwise
        """
        try:
            builder = ParameterizedQueryBuilder()
            sql, params = builder.select(
                columns=[
                    "id",
                    "title",
                    "slug",
                    "content",
                    "excerpt",
                    "featured_image_url",
                    "status",
                    "created_at",
                    "updated_at",
                ],
                table="posts",
                where_clauses=[("slug", SQLOperator.EQ, slug)],
                limit=1,
            )
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                return ModelConverter.to_post_response(row) if row else None
        except Exception as e:
            logger.error(
                "[_get_post_by_slug] Error getting post by slug '%s': %s", slug, e, exc_info=True
            )
            return None

    @log_query_performance(operation="update_post", category="content_write")
    async def update_post(self, post_id: int, updates: dict[str, Any]) -> bool:
        """
        Update a post with new values (e.g., featured_image_url, status).

        Args:
            post_id: Post ID
            updates: Dict of fields to update

        Returns:
            True if updated, False otherwise
        """
        try:
            # Allowlist of columns that may be updated via this method
            _ALLOWED_POST_COLUMNS = frozenset(
                [
                    "title",
                    "slug",
                    "content",
                    "excerpt",
                    "featured_image_url",
                    "status",
                    "tags",
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                    "published_at",
                ]
            )

            # Filter to only allowed fields; ParameterizedQueryBuilder will also
            # run SQLIdentifierValidator.safe_identifier() on each column name.
            filtered: dict[str, Any] = {
                k: v for k, v in updates.items() if k in _ALLOWED_POST_COLUMNS
            }
            for skipped in set(updates) - _ALLOWED_POST_COLUMNS:
                logger.warning("Skipping invalid column for update: %s", skipped)

            if not filtered:
                logger.warning("No valid columns to update for post %s", post_id)
                return False

            # Always refresh updated_at using a Python datetime
            filtered["updated_at"] = datetime.now(timezone.utc)

            builder = ParameterizedQueryBuilder()
            sql, params = builder.update(
                table="posts",
                updates=filtered,
                where_clauses=[("id", SQLOperator.EQ, post_id)],
                return_columns=["id", "title", "slug", "featured_image_url", "status"],
            )

            async with self.pool.acquire() as conn:
                result = await conn.fetchrow(sql, *params)

                if result:
                    logger.info("Updated post %s: %s", post_id, dict(result))
                    return True
                else:
                    logger.warning("Post not found for update: %s", post_id)
                    return False

        except Exception as e:
            logger.error("[_update_post] Error updating post %s: %s", post_id, e, exc_info=True)
            return False

    def _cache_get(self, key: str):
        """Return cached value if still valid, else None."""
        entry = self._cache.get(key)
        if entry and (time.monotonic() - entry[1]) < self._CACHE_TTL:
            return entry[0]
        return None

    def _cache_set(self, key: str, value):
        """Store a value in the cache with the current timestamp."""
        self._cache[key] = (value, time.monotonic())

    @log_query_performance(operation="get_all_categories", category="content_retrieval")
    async def get_all_categories(self) -> list[CategoryResponse]:
        """
        Get all categories for matching. Results are cached for 60s.

        Returns:
            List of category dicts
        """
        cached = self._cache_get("categories")
        if cached is not None:
            return cached
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, name, slug, description FROM categories ORDER BY name LIMIT 1000"
                )
                result = [ModelConverter.to_category_response(row) for row in rows]
                self._cache_set("categories", result)
                return result
        except Exception as e:
            logger.error("[_get_all_categories] Failed to fetch categories: %s", e, exc_info=True)
            return []

    @log_query_performance(operation="get_all_tags", category="content_retrieval")
    async def get_all_tags(self) -> list[TagResponse]:
        """
        Get all tags for matching. Results are cached for 60s.

        Returns:
            List of tag dicts
        """
        cached = self._cache_get("tags")
        if cached is not None:
            return cached
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, name, slug, description FROM tags ORDER BY name LIMIT 500"
                )
                result = [ModelConverter.to_tag_response(row) for row in rows]
                self._cache_set("tags", result)
                return result
        except Exception as e:
            logger.error("[_get_all_tags] Failed to fetch tags: %s", e, exc_info=True)
            return []

    @log_query_performance(operation="get_author_by_name", category="content_retrieval")
    async def get_author_by_name(self, name: str) -> AuthorResponse | None:
        """
        Get author by name.

        Args:
            name: Author name

        Returns:
            Author dict or None if not found
        """
        try:
            sql = "SELECT id, name, slug, email FROM authors WHERE LOWER(name) = LOWER($1)"
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, name)
                return ModelConverter.to_author_response(row) if row else None
        except Exception as e:
            logger.error(
                "[_get_author_by_name] Failed to fetch author by name: %s", e, exc_info=True
            )
            return None

    @log_query_performance(operation="create_quality_evaluation", category="content_write")
    async def create_quality_evaluation(
        self, eval_data: dict[str, Any]
    ) -> QualityEvaluationResponse:
        """
        Create quality evaluation record with enhanced context data and content metrics.

        Args:
            eval_data: Dict with content_id, task_id, overall_score, criteria scores, context_data, content_length, etc.

        Returns:
            Created quality_evaluation record
        """
        try:
            criteria = eval_data.get("criteria", {})

            # Extract context data for enriched evaluation
            context_data = eval_data.get("context_data", {})
            if not context_data and "context" in eval_data:
                context_data = eval_data["context"]

            # Calculate content_length if not provided
            content_length = eval_data.get("content_length")
            if not content_length and "content" in eval_data:
                content_length = len(eval_data.get("content", ""))

            sql = """
                INSERT INTO quality_evaluations (
                    content_id, task_id, overall_score, clarity, accuracy,
                    completeness, relevance, seo_quality, readability, engagement,
                    passing, feedback, suggestions, evaluated_by, evaluation_method,
                    context_data, content_length,
                    evaluation_timestamp
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, NOW())
                RETURNING *
            """
            params = [
                eval_data["content_id"],
                eval_data.get("task_id"),
                eval_data["overall_score"],
                criteria.get("clarity", 0),
                criteria.get("accuracy", 0),
                criteria.get("completeness", 0),
                criteria.get("relevance", 0),
                criteria.get("seo_quality", 0),
                criteria.get("readability", 0),
                criteria.get("engagement", 0),
                eval_data["overall_score"] >= 70,  # 0-100 scale; 70 = passing (7.0/10)
                eval_data.get("feedback"),
                json.dumps(eval_data.get("suggestions", [])),
                eval_data.get("evaluated_by", "QualityEvaluator"),
                eval_data.get("evaluation_method", "pattern-based"),
                json.dumps(context_data) if context_data else None,
                content_length,
            ]

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                logger.info("Created quality_evaluation for %s", eval_data['content_id'])
                return ModelConverter.to_quality_evaluation_response(row)
        except Exception as e:
            logger.error(
                "[_create_quality_evaluation] Error creating quality_evaluation: %s", e,
                exc_info=True,
            )
            raise

    @log_query_performance(operation="create_quality_improvement_log", category="content_write")
    async def create_quality_improvement_log(
        self, log_data: dict[str, Any]
    ) -> QualityImprovementLogResponse:
        """
        Log content quality improvement through refinement.

        Args:
            log_data: Dict with content_id, initial_score, improved_score, refinement_type, etc.

        Returns:
            Created quality_improvement_log record
        """
        try:
            initial = log_data["initial_score"]
            improved = log_data["improved_score"]

            sql = """
                INSERT INTO quality_improvement_logs (
                    content_id, initial_score, improved_score, score_improvement,
                    refinement_type, changes_made, refinement_timestamp, passed_after_refinement
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7)
                RETURNING *
            """
            params = [
                log_data["content_id"],
                initial,
                improved,
                improved - initial,
                log_data.get("refinement_type", "auto-critique"),
                log_data.get("changes_made"),
                improved >= 70,  # 0-100 scale; 70 = passing (7.0/10)
            ]

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                logger.info("Created quality_improvement_log: %.0f -> %.0f", initial, improved)
                return ModelConverter.to_quality_improvement_log_response(row)
        except Exception as e:
            logger.error(
                "[_create_quality_improvement_log] Error creating quality_improvement_log: %s", e,
                exc_info=True,
            )
            raise

    @log_query_performance(operation="get_metrics", category="analytics", slow_threshold_ms=200)
    async def get_metrics(self) -> MetricsResponse:
        """
        Get system metrics from content_tasks database.

        Returns:
            Dict with task counts, success rates, execution times, costs
        """
        try:
            async with self.pool.acquire() as conn:
                # Consolidate task counts into a single query using FILTER to avoid
                # 3 sequential COUNT scans (issue #472).
                counts_row = await conn.fetchrow("""
                    SELECT
                        COUNT(*)                                              AS total_tasks,
                        COUNT(*) FILTER (WHERE status = 'completed')         AS completed_tasks,
                        COUNT(*) FILTER (WHERE status = 'failed')            AS failed_tasks,
                        AVG(
                            EXTRACT(EPOCH FROM (updated_at - created_at))
                        ) FILTER (WHERE status = 'completed'
                                    AND updated_at IS NOT NULL)              AS avg_seconds
                    FROM content_tasks
                    """)

                total_tasks = int(counts_row["total_tasks"] or 0)
                completed_tasks = int(counts_row["completed_tasks"] or 0)
                failed_tasks = int(counts_row["failed_tasks"] or 0)

                # Calculate rates
                success_rate = (
                    (completed_tasks / (completed_tasks + failed_tasks) * 100)
                    if (completed_tasks + failed_tasks) > 0
                    else 0
                )

                # Average execution time (already computed in the same query)
                avg_execution_time = 0
                try:
                    raw_avg = counts_row["avg_seconds"]
                    if raw_avg is not None:
                        avg_execution_time = round(float(raw_avg), 2)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.error(
                        "Could not calculate avg execution time (data type error): %s", e,
                        exc_info=True,
                    )

                # Calculate total cost from financial tracking (if implemented).
                # Kept as a separate query because cost_logs is a different table and
                # may not exist in all environments.
                total_cost = 0
                try:
                    from services.site_config import site_config as _sc
                    _cost_days = _sc.get_int("cost_summary_window_days", 30)
                    cost_query = f"SELECT SUM(cost_usd) as total FROM cost_logs WHERE created_at >= NOW() - INTERVAL '{_cost_days} days'"
                    cost_result = await conn.fetchrow(cost_query)
                    if cost_result and cost_result["total"]:
                        total_cost = round(float(cost_result["total"]), 2)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.error(
                        "Could not calculate total cost (data type error): %s", e,
                        exc_info=True,
                    )
                except asyncpg.PostgresError as e:
                    # Table may not exist or permissions issue
                    logger.error(
                        "Cost tracking not available (database error): %s", type(e).__name__,
                        exc_info=True,
                    )
                except Exception as e:
                    logger.error(
                        "[get_metrics] Unexpected error calculating total cost: %s: %s", type(e).__name__, e,
                        exc_info=True,
                    )

                return MetricsResponse(
                    totalTasks=total_tasks,
                    completedTasks=completed_tasks,
                    failedTasks=failed_tasks,
                    successRate=round(success_rate, 2),
                    avgExecutionTime=avg_execution_time,
                    totalCost=total_cost,
                )
        except Exception as e:
            logger.error("[_get_metrics] Failed to get metrics: %s", e, exc_info=True)
            return MetricsResponse(
                totalTasks=0,
                completedTasks=0,
                failedTasks=0,
                successRate=0,
                avgExecutionTime=0,
                totalCost=0,
            )

    async def create_orchestrator_training_data(
        self, train_data: dict[str, Any]
    ) -> OrchestratorTrainingDataResponse:
        """
        Capture execution for training/learning pipeline.

        Args:
            train_data: Dict with execution_id, user_request, intent, execution_result, quality_score, success, tags, etc.

        Returns:
            Created training_data record
        """
        try:
            sql = """
                INSERT INTO orchestrator_training_data (
                    execution_id, user_request, intent, business_state, execution_result,
                    quality_score, success, tags, created_at, source_agent
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), $9)
                ON CONFLICT (execution_id) DO UPDATE SET
                    user_request = EXCLUDED.user_request,
                    intent = EXCLUDED.intent,
                    business_state = EXCLUDED.business_state,
                    execution_result = EXCLUDED.execution_result,
                    quality_score = EXCLUDED.quality_score,
                    success = EXCLUDED.success,
                    tags = EXCLUDED.tags,
                    created_at = NOW(),
                    source_agent = EXCLUDED.source_agent
                RETURNING *
            """
            params = [
                train_data["execution_id"],
                train_data.get("user_request"),
                train_data.get("intent"),
                json.dumps(train_data.get("business_state", {})),
                train_data.get("execution_result"),
                train_data.get("quality_score"),
                train_data.get("success", False),
                # Handle tags - can be a list or already a JSON string
                (
                    train_data.get("tags", [])
                    if isinstance(train_data.get("tags"), list)
                    else json.loads(train_data.get("tags", "[]"))
                ),
                train_data.get("source_agent", "content_agent"),
            ]

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                logger.info("Upserted orchestrator_training_data: %s", train_data['execution_id'])
                return ModelConverter.to_orchestrator_training_data_response(row)
        except Exception as e:
            logger.error(
                "[_create_orchestrator_training_data] Error creating orchestrator_training_data: %s", e,
                exc_info=True,
            )
            raise
