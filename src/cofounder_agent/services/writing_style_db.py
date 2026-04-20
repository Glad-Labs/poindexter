"""
Writing Style Database Module

Handles all writing sample operations including:
- Create, read, update, delete writing samples
- Manage active writing sample for a user
- Retrieve writing samples for style matching
"""

from datetime import datetime, timezone
from typing import Any

from asyncpg import Pool

from services.logger_config import get_logger
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin
from .decorators import log_query_performance

logger = get_logger(__name__)


class WritingStyleDatabase(DatabaseServiceMixin):
    """Writing style/sample-related database operations."""

    def __init__(self, pool: Pool):
        """
        Initialize writing style database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    @log_query_performance(operation="create_writing_sample", category="writing_style_write")
    async def create_writing_sample(
        self,
        user_id: str,
        title: str,
        content: str,
        description: str | None = None,
        set_as_active: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new writing sample.

        Args:
            user_id: User ID (from auth)
            title: Sample title/name
            content: The actual writing sample text
            description: Optional description of the sample
            set_as_active: Whether to set this as the active sample

        Returns:
            Created sample dict with id, user_id, title, content, created_at, etc.
        """
        word_count = len(content.split())
        char_count = len(content)

        try:
            async with self.pool.acquire() as conn:
                # If setting as active, deactivate other samples for this user
                if set_as_active:
                    await conn.execute(
                        "UPDATE writing_samples SET is_active = FALSE WHERE user_id = $1", user_id
                    )

                # Insert new sample (let database auto-generate id)
                row = await conn.fetchrow(
                    """
                    INSERT INTO writing_samples (
                        user_id, title, description, content, 
                        is_active, word_count, char_count, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
                    RETURNING id, user_id, title, description, content, is_active, 
                              word_count, char_count, created_at, updated_at
                    """,
                    user_id,
                    title,
                    description or "",
                    content,
                    set_as_active,
                    word_count,
                    char_count,
                )

                sample_id = row.get("id") if row else None
                logger.info("Created writing sample: %s for user %s", sample_id, user_id)
                return self._format_sample(row)

        except Exception as e:
            logger.error(
                f"[create_writing_sample] Failed to create sample for user_id={user_id}, title='{title}': {str(e)}",
                exc_info=True,
            )
            raise

    @log_query_performance(operation="get_writing_sample", category="writing_style_retrieval")
    async def get_writing_sample(self, sample_id: str) -> dict[str, Any] | None:
        """
        Get a specific writing sample by ID.

        Args:
            sample_id: Sample ID

        Returns:
            Sample dict or None if not found
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id, title, description, content, is_active, 
                           word_count, char_count, metadata, created_at, updated_at
                    FROM writing_samples WHERE id = $1
                    """,
                    int(sample_id),
                )
                return self._format_sample(row) if row else None
        except Exception as e:
            logger.error(
                f"[get_writing_sample] Failed to get sample_id={sample_id}: {str(e)}",
                exc_info=True,
            )
            raise

    @log_query_performance(operation="get_user_writing_samples", category="writing_style_retrieval")
    async def get_user_writing_samples(
        self, user_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Get writing samples for a user.

        Args:
            user_id: User ID
            limit: Maximum samples to return (default 100)

        Returns:
            List of sample dicts
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, title, description, content, is_active,
                           word_count, char_count, metadata, created_at, updated_at
                    FROM writing_samples
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id,
                    limit,
                )
                return [self._format_sample(row) for row in rows]
        except Exception as e:
            logger.error(
                f"[get_user_writing_samples] Failed to get samples for user_id={user_id}: {str(e)}",
                exc_info=True,
            )
            raise

    @log_query_performance(
        operation="get_active_writing_sample", category="writing_style_retrieval"
    )
    async def get_active_writing_sample(self, user_id: str) -> dict[str, Any] | None:
        """
        Get the active/current writing sample for a user.

        Args:
            user_id: User ID

        Returns:
            Active sample dict or None if no active sample
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id, title, description, content, is_active, 
                           word_count, char_count, metadata, created_at, updated_at
                    FROM writing_samples 
                    WHERE user_id = $1 AND is_active = TRUE
                    LIMIT 1
                    """,
                    user_id,
                )
                return self._format_sample(row) if row else None
        except Exception as e:
            logger.error(
                f"[get_active_writing_sample] Failed to get active sample for user_id={user_id}: {str(e)}",
                exc_info=True,
            )
            raise

    @log_query_performance(operation="set_active_writing_sample", category="writing_style_write")
    async def set_active_writing_sample(self, user_id: str, sample_id: str) -> dict[str, Any]:
        """
        Set a writing sample as the active one for a user.

        Args:
            user_id: User ID
            sample_id: Sample ID to activate

        Returns:
            Updated sample dict
        """
        try:
            async with self.pool.acquire() as conn:
                # Deactivate other samples
                await conn.execute(
                    "UPDATE writing_samples SET is_active = FALSE WHERE user_id = $1 AND id != $2",
                    user_id,
                    int(sample_id),
                )

                # Activate the specified sample
                row = await conn.fetchrow(
                    """
                    UPDATE writing_samples 
                    SET is_active = TRUE, updated_at = NOW()
                    WHERE id = $1 AND user_id = $2
                    RETURNING id, user_id, title, description, content, is_active,
                              word_count, char_count, metadata, created_at, updated_at
                    """,
                    int(sample_id),
                    user_id,
                )

                if not row:
                    raise ValueError(f"Writing sample {sample_id} not found for user {user_id}")

                logger.info("Set writing sample %s as active for user %s", sample_id, user_id)
                return self._format_sample(row)

        except Exception as e:
            logger.error(
                f"[set_active_writing_sample] Failed to set active sample_id={sample_id} for user_id={user_id}: {str(e)}",
                exc_info=True,
            )
            raise

    @log_query_performance(operation="update_writing_sample", category="writing_style_write")
    async def update_writing_sample(
        self,
        sample_id: str,
        user_id: str,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """
        Update a writing sample.

        Args:
            sample_id: Sample ID
            user_id: User ID (for authorization)
            title: New title (optional)
            description: New description (optional)
            content: New content (optional)

        Returns:
            Updated sample dict
        """
        try:
            # Collect only the fields being changed
            update_dict: dict[str, Any] = {}
            if title is not None:
                update_dict["title"] = title
            if description is not None:
                update_dict["description"] = description
            if content is not None:
                update_dict["content"] = content
                update_dict["word_count"] = len(content.split())
                update_dict["char_count"] = len(content)

            if not update_dict:
                raise ValueError("No fields to update")

            # Always refresh updated_at with a Python datetime (not raw NOW())
            update_dict["updated_at"] = datetime.now(timezone.utc)

            # Use ParameterizedQueryBuilder so every column name passes
            # through SQLIdentifierValidator.safe_identifier() before execution.
            builder = ParameterizedQueryBuilder()
            sql, params = builder.update(
                table="writing_samples",
                updates=update_dict,
                where_clauses=[
                    ("id", SQLOperator.EQ, int(sample_id)),
                    ("user_id", SQLOperator.EQ, user_id),
                ],
                return_columns=[
                    "id",
                    "user_id",
                    "title",
                    "description",
                    "content",
                    "is_active",
                    "word_count",
                    "char_count",
                    "metadata",
                    "created_at",
                    "updated_at",
                ],
            )

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)

                if not row:
                    raise ValueError(f"Writing sample {sample_id} not found for user {user_id}")

                logger.info("Updated writing sample: %s", sample_id)
                return self._format_sample(row)

        except Exception as e:
            logger.error(
                f"[update_writing_sample] Failed to update sample_id={sample_id} for user_id={user_id}: {str(e)}",
                exc_info=True,
            )
            raise

    @log_query_performance(operation="delete_writing_sample", category="writing_style_write")
    async def delete_writing_sample(self, sample_id: str, user_id: str) -> bool:
        """
        Delete a writing sample.

        Args:
            sample_id: Sample ID
            user_id: User ID (for authorization)

        Returns:
            True if deleted, False if not found
        """
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM writing_samples WHERE id = $1 AND user_id = $2",
                    int(sample_id),
                    user_id,
                )

                # Parse result to check if row was deleted
                # asyncpg returns "DELETE 1" if one row was deleted
                success = "1" in result or result.endswith("1")

                if success:
                    logger.info("Deleted writing sample: %s", sample_id)
                else:
                    logger.warning("Writing sample %s not found for deletion", sample_id)

                return success

        except Exception as e:
            logger.error(
                f"[delete_writing_sample] Failed to delete sample_id={sample_id} for user_id={user_id}: {str(e)}",
                exc_info=True,
            )
            raise

    @staticmethod
    def _format_sample(row) -> dict[str, Any]:
        """Format database row into dict response"""
        if not row:
            return {}

        # Get metadata - ensure it's a dict
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            import json

            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, ValueError):
                metadata = {}

        return {
            "id": str(row["id"]),  # Convert integer ID to string for API response
            "user_id": row["user_id"],
            "title": row["title"],
            "description": row.get("description", ""),
            "content": row["content"],
            "is_active": row.get("is_active", False),
            "word_count": row.get("word_count", 0),
            "char_count": row.get("char_count", 0),
            "metadata": metadata,
            "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        }
