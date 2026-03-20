"""
Users Database Module

Handles all user-related database operations including:
- User CRUD operations (create, read, update)
- OAuth account management and linking
- User lookup by email, username, or ID
"""

import json
from services.logger_config import get_logger
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from asyncpg import Pool

from schemas.database_response_models import OAuthAccountResponse, UserResponse
from schemas.model_converter import ModelConverter
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

from .database_mixin import DatabaseServiceMixin
from .decorators import log_query_performance

logger = get_logger(__name__)
class UsersDatabase(DatabaseServiceMixin):
    """User-related database operations."""

    def __init__(self, pool: Pool):
        """
        Initialize users database module.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    @log_query_performance(operation="get_user_by_id", category="user_retrieval")
    async def get_user_by_id(self, user_id: str) -> Optional[UserResponse]:
        """
        Get user by ID.

        Args:
            user_id: UUID of user

        Returns:
            UserResponse model or None if not found
        """
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["*"], table="users", where_clauses=[("id", SQLOperator.EQ, user_id)]
        )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return ModelConverter.to_user_response(row) if row else None

    @log_query_performance(operation="get_user_by_email", category="user_retrieval")
    async def get_user_by_email(self, email: str) -> Optional[UserResponse]:
        """
        Get user by email address.

        Args:
            email: User email

        Returns:
            UserResponse model or None if not found
        """
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["*"], table="users", where_clauses=[("email", SQLOperator.EQ, email)]
        )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return ModelConverter.to_user_response(row) if row else None

    @log_query_performance(operation="get_user_by_username", category="user_retrieval")
    async def get_user_by_username(self, username: str) -> Optional[UserResponse]:
        """
        Get user by username.

        Args:
            username: User username

        Returns:
            UserResponse model or None if not found
        """
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["*"], table="users", where_clauses=[("username", SQLOperator.EQ, username)]
        )
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return ModelConverter.to_user_response(row) if row else None

    @log_query_performance(operation="create_user", category="user_write")
    async def create_user(self, user_data: Dict[str, Any]) -> UserResponse:
        """
        Create new user.

        Args:
            user_data: Dict with email, username, password_hash, is_active

        Returns:
            UserResponse model with all fields
        """
        user_id = user_data.get("id") or str(uuid4())
        now = datetime.now(timezone.utc)

        builder = ParameterizedQueryBuilder()
        insert_data = {
            "id": user_id,
            "email": user_data.get("email"),
            "username": user_data.get("username"),
            "password_hash": user_data.get("password_hash"),
            "is_active": user_data.get("is_active", True),
            "created_at": now,
            "updated_at": now,
        }
        sql, params = builder.insert(table="users", columns=insert_data, return_columns=["*"])

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return ModelConverter.to_user_response(row)

    @log_query_performance(operation="get_or_create_oauth_user", category="user_write")
    async def get_or_create_oauth_user(
        self,
        provider: str,
        provider_user_id: str,
        provider_data: Dict[str, Any],
    ) -> Optional[UserResponse]:
        """
        Get existing OAuth user or create new one from provider data.

        Handles three scenarios:
        1. OAuth account already linked → return existing user
        2. Email exists but OAuth not linked → link OAuth to existing user
        3. Neither exist → create new user and OAuth account

        Args:
            provider: OAuth provider name ('github', 'google', etc.)
            provider_user_id: User ID from provider
            provider_data: User data from provider {username, email, avatar_url, etc.}

        Returns:
            UserResponse model with id, email, username, is_active, created_at, updated_at
        """
        async with self.pool.acquire() as conn:
            # Use REPEATABLE READ isolation to close the read-check-write race window
            # (issue #767). Two concurrent OAuth callbacks for the same identity can both
            # pass the SELECT checks before either INSERT runs. The transaction prevents
            # the second coroutine from seeing a stale snapshot, and ON CONFLICT on the
            # oauth_accounts INSERT handles the case where both reach the INSERT simultaneously.
            async with conn.transaction(isolation="repeatable_read"):
                # Check if OAuthAccount already linked
                oauth_row = await conn.fetchrow(
                    """
                    SELECT oa.user_id
                    FROM oauth_accounts oa
                    WHERE oa.provider = $1 AND oa.provider_user_id = $2
                    """,
                    provider,
                    provider_user_id,
                )

                if oauth_row:
                    # OAuth account already linked, get existing user
                    user_id = oauth_row["user_id"]
                    logger.info("✅ OAuth account found, getting user: %s", user_id)

                    user = await conn.fetchrow(
                        "SELECT id, email, username, is_active, created_at, updated_at FROM users WHERE id = $1",
                        user_id,
                    )
                    return ModelConverter.to_user_response(user) if user else None

                # Check if user with same email already exists
                email = provider_data.get("email")
                existing_user = None

                if email:
                    existing_user = await conn.fetchrow(
                        "SELECT id, email, username, is_active, created_at, updated_at FROM users WHERE email = $1",
                        email,
                    )

                if existing_user:
                    # Email exists, link OAuth account to existing user
                    user_id = existing_user["id"]
                    logger.info("✅ Email found, linking OAuth to user: %s", user_id)

                    # Create OAuth account link; ON CONFLICT guards against concurrent inserts
                    provider_data_json = json.dumps(provider_data)
                    await conn.execute(
                        """
                        INSERT INTO oauth_accounts (
                            id, user_id, provider, provider_user_id,
                            provider_data, created_at, last_used
                        )
                        VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                        ON CONFLICT (provider, provider_user_id) DO NOTHING
                        """,
                        str(uuid4()),
                        user_id,
                        provider,
                        provider_user_id,
                        provider_data_json,
                    )

                    return ModelConverter.to_user_response(existing_user)

                # Create new user and OAuth account
                user_id = str(uuid4())
                logger.info("✅ Creating new user from OAuth: %s", user_id)

                # Create user
                user = await conn.fetchrow(
                    """
                    INSERT INTO users (
                        id, email, username, is_active, created_at, updated_at
                    )
                    VALUES ($1, $2, $3, $4, NOW(), NOW())
                    RETURNING *
                    """,
                    user_id,
                    email,
                    provider_data.get("username", email.split("@")[0] if email else "user"),
                    True,  # OAuth users are active by default
                )

                # ON CONFLICT handles the race window: if a concurrent coroutine won the
                # INSERT race, DO NOTHING lets us fall through and re-fetch the winner's row.
                provider_data_json = json.dumps(provider_data)
                await conn.execute(
                    """
                    INSERT INTO oauth_accounts (
                        id, user_id, provider, provider_user_id, provider_data, created_at, last_used
                    )
                    VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
                    ON CONFLICT (provider, provider_user_id) DO NOTHING
                    """,
                    str(uuid4()),
                    user_id,
                    provider,
                    provider_user_id,
                    provider_data_json,
                )

                # Re-fetch oauth_accounts in case a concurrent winner beat us to the insert
                winner_row = await conn.fetchrow(
                    """
                    SELECT oa.user_id
                    FROM oauth_accounts oa
                    WHERE oa.provider = $1 AND oa.provider_user_id = $2
                    """,
                    provider,
                    provider_user_id,
                )
                if winner_row and winner_row["user_id"] != user_id:
                    # A concurrent coroutine won — return the winner's user
                    winner_user = await conn.fetchrow(
                        "SELECT id, email, username, is_active, created_at, updated_at FROM users WHERE id = $1",
                        winner_row["user_id"],
                    )
                    logger.info(
                        "✅ Concurrent OAuth create detected — returning winner user: %s",
                        winner_row["user_id"],
                    )
                    return ModelConverter.to_user_response(winner_user) if winner_user else None

                logger.info("✅ Created new OAuth user: %s", user_id)
                return ModelConverter.to_user_response(user) if user else None

    @log_query_performance(
        operation="get_oauth_accounts", category="user_relationships", slow_threshold_ms=50
    )
    async def get_oauth_accounts(self, user_id: str) -> List[OAuthAccountResponse]:
        """
        Get all OAuth accounts linked to a user.

        Args:
            user_id: UUID of user

        Returns:
            List of OAuthAccountResponse models
        """
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=[
                "id",
                "provider",
                "provider_user_id",
                "provider_data",
                "created_at",
                "last_used",
            ],
            table="oauth_accounts",
            where_clauses=[("user_id", SQLOperator.EQ, user_id)],
            order_by=[("last_used", "DESC")],
        )
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [ModelConverter.to_oauth_account_response(row) for row in rows]

    @log_query_performance(operation="unlink_oauth_account", category="user_write")
    async def unlink_oauth_account(self, user_id: str, provider: str) -> bool:
        """
        Unlink OAuth account from user.

        Args:
            user_id: UUID of user
            provider: OAuth provider name

        Returns:
            True if account was unlinked, False if error occurred
        """
        try:
            sql = "DELETE FROM oauth_accounts WHERE user_id = $1::uuid AND provider = $2"
            params = [user_id, provider]
            async with self.pool.acquire() as conn:
                result = await conn.execute(sql, *params)
                # Result is a string like "DELETE 1"
                return "1" in result or "1" == result
        except Exception as e:
            logger.error(
                f"[unlink_oauth_account] Error unlinking OAuth account for user_id={user_id}, provider={provider}: {str(e)}",
                exc_info=True,
            )
            return False
