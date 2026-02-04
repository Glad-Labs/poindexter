"""
Writing Style Database Module

Handles all writing sample operations including:
- Create, read, update, delete writing samples
- Manage active writing sample for a user
- Retrieve writing samples for style matching
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from asyncpg import Pool

from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin

logger = logging.getLogger(__name__)


class WritingStyleDatabase(DatabaseServiceMixin):
    """Writing style/sample-related database operations."""

    def __init__(self, pool: Pool):
        """
        Initialize writing style database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    async def create_writing_sample(
        self,
        user_id: str,
        title: str,
        content: str,
        description: Optional[str] = None,
        set_as_active: bool = False,
    ) -> Dict[str, Any]:
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
                logger.info(f"✅ Created writing sample: {sample_id} for user {user_id}")
                return self._format_sample(row)

        except Exception as e:
            logger.error(f"❌ Failed to create writing sample: {e}")
            raise

    async def get_writing_sample(self, sample_id: str) -> Optional[Dict[str, Any]]:
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
                    sample_id,
                )
                return self._format_sample(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get writing sample: {e}")
            raise

    async def get_user_writing_samples(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all writing samples for a user.

        Args:
            user_id: User ID

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
                    """,
                    user_id,
                )
                return [self._format_sample(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get user writing samples: {e}")
            raise

    async def get_active_writing_sample(self, user_id: str) -> Optional[Dict[str, Any]]:
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
            logger.error(f"❌ Failed to get active writing sample: {e}")
            raise

    async def set_active_writing_sample(self, user_id: str, sample_id: str) -> Dict[str, Any]:
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
                    sample_id,
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
                    sample_id,
                    user_id,
                )

                if not row:
                    raise ValueError(f"Writing sample {sample_id} not found for user {user_id}")

                logger.info(f"✅ Set writing sample {sample_id} as active for user {user_id}")
                return self._format_sample(row)

        except Exception as e:
            logger.error(f"❌ Failed to set active writing sample: {e}")
            raise

    async def update_writing_sample(
        self,
        sample_id: str,
        user_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            # Build dynamic update query
            updates = []
            params = [sample_id, user_id]
            param_count = 2

            if title is not None:
                param_count += 1
                updates.append(f"title = ${param_count}")
                params.append(title)

            if description is not None:
                param_count += 1
                updates.append(f"description = ${param_count}")
                params.append(description)

            if content is not None:
                word_count = len(content.split())
                char_count = len(content)
                param_count += 1
                updates.append(f"content = ${param_count}")
                params.append(content)
                param_count += 1
                updates.append(f"word_count = ${param_count}")
                params.append(word_count)
                param_count += 1
                updates.append(f"char_count = ${param_count}")
                params.append(char_count)

            if not updates:
                raise ValueError("No fields to update")

            updates.append("updated_at = NOW()")
            update_clause = ", ".join(updates)

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    UPDATE writing_samples 
                    SET {update_clause}
                    WHERE id = $1 AND user_id = $2
                    RETURNING id, user_id, title, description, content, is_active, 
                              word_count, char_count, metadata, created_at, updated_at
                    """,
                    *params,
                )

                if not row:
                    raise ValueError(f"Writing sample {sample_id} not found for user {user_id}")

                logger.info(f"✅ Updated writing sample: {sample_id}")
                return self._format_sample(row)

        except Exception as e:
            logger.error(f"❌ Failed to update writing sample: {e}")
            raise

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
                    "DELETE FROM writing_samples WHERE id = $1 AND user_id = $2", sample_id, user_id
                )

                # Parse result to check if row was deleted
                # asyncpg returns "DELETE 1" if one row was deleted
                success = "1" in result or result.endswith("1")

                if success:
                    logger.info(f"✅ Deleted writing sample: {sample_id}")
                else:
                    logger.warning(f"⚠️  Writing sample {sample_id} not found for deletion")

                return success

        except Exception as e:
            logger.error(f"❌ Failed to delete writing sample: {e}")
            raise

    @staticmethod
    def _format_sample(row) -> Dict[str, Any]:
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
