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
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
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
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin

logger = logging.getLogger(__name__)


class ContentDatabase(DatabaseServiceMixin):
    """Content-related database operations (posts, quality, metrics)."""

    def __init__(self, pool: Pool):
        """
        Initialize content database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    async def create_post(self, post_data: Dict[str, Any]) -> PostResponse:
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
            logger.warning(f"⚠️  seo_keywords is list, converting to string: {seo_keywords}")
            seo_keywords = ", ".join(str(kw) for kw in seo_keywords)  # Ensure each item is string
        elif isinstance(seo_keywords, str):
            # Already a string, no conversion needed (was causing character splitting)
            seo_keywords = seo_keywords
        else:
            # Convert other types to string
            seo_keywords = str(seo_keywords) if seo_keywords else ""

        tag_ids = post_data.get("tag_ids")
        if tag_ids and isinstance(tag_ids, str):
            logger.warning(f"⚠️  tag_ids is string, converting to list: {tag_ids}")
            tag_ids = [tag_ids]

        # ✅ Log all values being inserted for debugging
        logger.info(f"🔍 INSERTING POST WITH THESE VALUES:")
        logger.info(f"   - id: {post_id}")
        logger.info(
            f"   - title: {post_data.get('title')[:50] if post_data.get('title') else 'EMPTY'}"
        )
        logger.info(f"   - slug: {post_data.get('slug')}")
        logger.info(f"   - featured_image_url: {post_data.get('featured_image_url')}")
        logger.info(f"   - seo_title: {post_data.get('seo_title')}")
        logger.info(
            f"   - seo_description: {post_data.get('seo_description')[:50] if post_data.get('seo_description') else 'EMPTY'}"
        )
        logger.info(f"   - seo_keywords: {seo_keywords}")
        logger.info(f"   - status: {post_data.get('status', 'draft')}")
        logger.info(f"   - author_id: {post_data.get('author_id')}")
        logger.info(f"   - category_id: {post_data.get('category_id')}")
        logger.info(f"   - tag_ids: {tag_ids}")

        async with self.pool.acquire() as conn:
            try:
                # Determine published_at timestamp based on status
                is_published = post_data.get("status") == "published"
                
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
                        tag_ids,
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
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, NOW(), NOW())
                    RETURNING id, title, slug, content, excerpt, featured_image_url, cover_image_url, 
                              author_id, category_id, tag_ids, status, published_at, created_at, updated_at
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
                    tag_ids,
                    post_data.get("status", "draft"),
                    # published_at: Set to NOW() if published, None if draft
                    datetime.utcnow() if is_published else None,
                    post_data.get("seo_title"),
                    post_data.get("seo_description"),
                    seo_keywords,
                    post_data.get("created_by"),
                    post_data.get("updated_by"),
                )
                if not row:
                    raise Exception("Insert query returned no row - post creation failed")

                logger.info(f"✅ POST CREATED SUCCESSFULLY in database with ID: {post_id}")
                logger.info(f"   - Status: {post_data.get('status', 'draft')}")
                logger.info(f"   - Published at: {row.get('published_at')}")
                return ModelConverter.to_post_response(row)
            except Exception as db_error:
                logger.error(f"❌ DATABASE ERROR while creating post: {db_error}", exc_info=True)
                raise Exception(f"Failed to create post in database: {str(db_error)}")

    async def get_post_by_slug(self, slug: str) -> Optional[PostResponse]:
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
            logger.error(f"❌ Error getting post by slug '{slug}': {e}")
            return None

    async def update_post(self, post_id: int, updates: Dict[str, Any]) -> bool:
        """
        Update a post with new values (e.g., featured_image_url, status).

        Args:
            post_id: Post ID
            updates: Dict of fields to update

        Returns:
            True if updated, False otherwise
        """
        try:
            # Build SET clause dynamically
            set_clauses = []
            values = []
            param_count = 1

            for key, value in updates.items():
                # Validate column exists
                if key not in [
                    "title",
                    "slug",
                    "content",
                    "excerpt",
                    "featured_image_url",
                    "status",
                    "tags",
                ]:
                    logger.warning(f"Skipping invalid column for update: {key}")
                    continue

                set_clauses.append(f"{key} = ${param_count}")
                values.append(value)
                param_count += 1

            if not set_clauses:
                logger.warning(f"No valid columns to update for post {post_id}")
                return False

            # Add post_id as final parameter
            values.append(post_id)
            param_count += 1

            query = f"""
                UPDATE posts
                SET {', '.join(set_clauses)}, updated_at = NOW()
                WHERE id = ${param_count - 1}
                RETURNING id, title, slug, featured_image_url, status
            """

            async with self.pool.acquire() as conn:
                result = await conn.fetchrow(query, *values)

                if result:
                    logger.info(f"✅ Updated post {post_id}: {dict(result)}")
                    return True
                else:
                    logger.warning(f"⚠️ Post not found for update: {post_id}")
                    return False

        except Exception as e:
            logger.error(f"❌ Error updating post {post_id}: {e}")
            return False

    async def get_all_categories(self) -> List[CategoryResponse]:
        """
        Get all categories for matching.

        Returns:
            List of category dicts
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, name, slug, description FROM categories ORDER BY name"
                )
                return [ModelConverter.to_category_response(row) for row in rows]
        except Exception as e:
            logger.warning(f"Could not fetch categories: {e}")
            return []

    async def get_all_tags(self) -> List[TagResponse]:
        """
        Get all tags for matching.

        Returns:
            List of tag dicts
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, name, slug, description FROM tags ORDER BY name"
                )
                return [ModelConverter.to_tag_response(row) for row in rows]
        except Exception as e:
            logger.warning(f"Could not fetch tags: {e}")
            return []

    async def get_author_by_name(self, name: str) -> Optional[AuthorResponse]:
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
            logger.warning(f"Could not fetch author by name: {e}")
            return None

    async def create_quality_evaluation(
        self, eval_data: Dict[str, Any]
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
                eval_data["overall_score"] >= 70,
                eval_data.get("feedback"),
                json.dumps(eval_data.get("suggestions", [])),
                eval_data.get("evaluated_by", "QualityEvaluator"),
                eval_data.get("evaluation_method", "pattern-based"),
                json.dumps(context_data) if context_data else None,
                content_length,
            ]

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                logger.info(f"✅ Created quality_evaluation for {eval_data['content_id']}")
                return ModelConverter.to_quality_evaluation_response(row)
        except Exception as e:
            logger.error(f"❌ Error creating quality_evaluation: {e}")
            raise

    async def create_quality_improvement_log(
        self, log_data: Dict[str, Any]
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
                improved >= 70,
            ]

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                logger.info(f"✅ Created quality_improvement_log: {initial:.0f} → {improved:.0f}")
                return ModelConverter.to_quality_improvement_log_response(row)
        except Exception as e:
            logger.error(f"❌ Error creating quality_improvement_log: {e}")
            raise

    async def get_metrics(self) -> MetricsResponse:
        """
        Get system metrics from content_tasks database.

        Returns:
            Dict with task counts, success rates, execution times, costs
        """
        try:
            async with self.pool.acquire() as conn:
                # Get task counts from content_tasks
                total_tasks = await conn.fetchval("SELECT COUNT(*) FROM content_tasks")

                # Use parameterized queries for status-based counts
                completed_tasks = await conn.fetchval(
                    "SELECT COUNT(*) FROM content_tasks WHERE status = $1", "completed"
                )
                failed_tasks = await conn.fetchval(
                    "SELECT COUNT(*) FROM content_tasks WHERE status = $1", "failed"
                )

                # Get pending/in-progress tasks
                pending_tasks = await conn.fetchval(
                    "SELECT COUNT(*) FROM content_tasks WHERE status IN ($1, $2, $3)",
                    "pending",
                    "in_progress",
                    "queued",
                )

                # Calculate rates
                success_rate = (
                    (completed_tasks / (completed_tasks + failed_tasks) * 100)
                    if (completed_tasks + failed_tasks) > 0
                    else 0
                )

                # Calculate average execution time from completed tasks
                avg_execution_time = 0
                try:
                    time_query = "SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) as avg_seconds FROM content_tasks WHERE status = $1 AND updated_at IS NOT NULL"
                    time_result = await conn.fetchrow(time_query, "completed")
                    if time_result and time_result["avg_seconds"]:
                        avg_execution_time = round(float(time_result["avg_seconds"]), 2)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.warning(f"Could not calculate avg execution time (data type error): {e}")
                except Exception as e:
                    logger.error(
                        f"Unexpected error calculating avg execution time: {type(e).__name__}: {e}"
                    )

                # Calculate total cost from financial tracking (if implemented)
                total_cost = 0
                try:
                    cost_query = "SELECT SUM(cost_usd) as total FROM task_costs WHERE created_at >= NOW() - INTERVAL '30 days'"
                    cost_result = await conn.fetchrow(cost_query)
                    if cost_result and cost_result["total"]:
                        total_cost = round(float(cost_result["total"]), 2)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.debug(f"Could not calculate total cost (data type error): {e}")
                except asyncpg.PostgresError as e:
                    # Table may not exist or permissions issue
                    logger.debug(
                        f"Cost tracking not available (database error): {type(e).__name__}"
                    )
                except Exception as e:
                    logger.error(
                        f"Unexpected error calculating total cost: {type(e).__name__}: {e}"
                    )

                return MetricsResponse(
                    total_tasks=total_tasks or 0,
                    completed_tasks=completed_tasks or 0,
                    failed_tasks=failed_tasks or 0,
                    pending_tasks=pending_tasks or 0,
                    success_rate=round(success_rate, 2),
                    avg_execution_time=avg_execution_time,
                    total_cost=total_cost,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get metrics: {e}")
            return MetricsResponse(
                total_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                pending_tasks=0,
                success_rate=0,
                avg_execution_time=0,
                total_cost=0,
            )

    async def create_orchestrator_training_data(
        self, train_data: Dict[str, Any]
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
                logger.info(f"✅ Created orchestrator_training_data: {train_data['execution_id']}")
                return ModelConverter.to_orchestrator_training_data_response(row)
        except Exception as e:
            logger.error(f"❌ Error creating orchestrator_training_data: {e}")
            raise
