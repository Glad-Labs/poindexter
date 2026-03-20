"""
Lightweight Token Manager

Integrates with existing infrastructure:
- oauth_accounts table for token storage
- JWTTokenValidator for JWT operations
- Structlog for audit logging
- database_service for all persistence

No duplication - extends what exists rather than creating parallel systems.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


class TokenManager:
    """
    Token lifecycle manager using existing infrastructure.

    Coordinates with:
    - oauth_accounts table (already exists with provider_data JSONB field)
    - existing audit logging patterns
    - JWTTokenValidator for JWT operations
    """

    def __init__(self, database_service):
        """Initialize with database service."""
        self.db_service = database_service
        logger.info("✅ TokenManager initialized (oauth_accounts backend)")

    async def store_oauth_token(
        self,
        user_id: str,
        provider: str,
        oauth_response: Dict[str, Any],
    ) -> bool:
        """
        Store OAuth token response in oauth_accounts table.

        Args:
            user_id: User UUID
            provider: OAuth provider name
            oauth_response: Full response from OAuth provider

        Returns:
            True if stored successfully
        """
        try:
            token_id = oauth_response.get("access_token", "")[:8]  # For reference

            # Calculate expiration
            expires_in = oauth_response.get("expires_in", 3600)
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            # Store full response in provider_data JSONB
            provider_data = {
                "access_token": oauth_response.get("access_token"),
                "token_type": oauth_response.get("token_type", "bearer"),
                "expires_in": expires_in,
                "expires_at": expires_at,
                "refresh_token": oauth_response.get("refresh_token"),
                "scope": oauth_response.get("scope"),
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }

            sql = """
            INSERT INTO oauth_accounts (
                id, user_id, provider, provider_user_id, provider_data, created_at, last_used
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, provider) DO UPDATE SET
                provider_data = excluded.provider_data,
                last_used = excluded.last_used
            """

            async with self.db_service.pool.acquire() as conn:
                from uuid import uuid4

                await conn.execute(
                    sql,
                    str(uuid4()),  # id
                    user_id,
                    provider,
                    oauth_response.get("user_id", f"{provider}-{token_id}"),
                    json.dumps(provider_data),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                )

            logger.info(f"✅ Stored {provider} OAuth token for user {user_id}")
            await self._audit_log("stored_token", user_id, provider, "success")
            return True

        except Exception as e:
            logger.error(f"[_store_oauth_token] ❌ Failed to store OAuth token: {e}", exc_info=True)
            await self._audit_log("stored_token", user_id, provider, "failed")
            return False

    async def get_oauth_token(self, user_id: str, provider: str) -> Optional[str]:
        """
        Retrieve OAuth access token for a user/provider.

        Args:
            user_id: User UUID
            provider: OAuth provider name

        Returns:
            Access token if exists and not expired, None otherwise
        """
        try:
            sql = """
            SELECT provider_data FROM oauth_accounts 
            WHERE user_id = $1::uuid AND provider = $2
            LIMIT 1
            """

            async with self.db_service.pool.acquire() as conn:
                row = await conn.fetchrow(sql, user_id, provider)

                if not row:
                    return None

                provider_data = json.loads(row["provider_data"])

                # Check expiration
                expires_at_str = provider_data.get("expires_at")
                if expires_at_str:
                    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    if expires_at < datetime.now(timezone.utc):
                        logger.warning(f"OAuth token for {provider} expired")
                        return None

                return provider_data.get("access_token")

        except Exception as e:
            logger.error(f"[_get_oauth_token] Error retrieving OAuth token: {e}", exc_info=True)
            return None

    async def mark_token_expired(self, user_id: str, provider: str) -> bool:
        """Mark a token as expired/revoked."""
        try:
            sql = """
            UPDATE oauth_accounts 
            SET provider_data = jsonb_set(
                provider_data,
                '{revoked_at}',
                to_jsonb($1::text)
            )
            WHERE user_id = $2::uuid AND provider = $3
            """

            async with self.db_service.pool.acquire() as conn:
                await conn.execute(
                    sql,
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                    provider,
                )

            logger.info(f"✅ Marked {provider} token revoked for user {user_id}")
            await self._audit_log("revoked_token", user_id, provider, "success")
            return True

        except Exception as e:
            logger.error(f"[_mark_token_expired] ❌ Failed to revoke token: {e}", exc_info=True)
            return False

    async def cleanup_old_tokens(self, days: int = 90) -> int:
        """
        Remove old revoked/replaced tokens.

        Args:
            days: Keep tokens newer than this many days

        Returns:
            Number of tokens deleted
        """
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            sql = """
            DELETE FROM oauth_accounts
            WHERE created_at < $1::timestamp
            AND provider_data->>'revoked_at' IS NOT NULL
            """

            async with self.db_service.pool.acquire() as conn:
                result = await conn.execute(sql, cutoff)
                count = int(result.split()[-1])  # "DELETE N" format
                logger.info(f"✅ Cleaned up {count} old revoked tokens")
                return count

        except Exception as e:
            logger.error(f"[_cleanup_old_tokens] ❌ Cleanup failed: {e}", exc_info=True)
            return 0

    # ==== PRIVATE ====

    async def _audit_log(self, operation: str, user_id: str, provider: str, status: str) -> None:
        """Log token operation."""
        try:
            logger.info(
                f"🔐 Token operation: {operation}",
                extra={"user_id": user_id, "provider": provider, "status": status},
            )
        except Exception as e:
            logger.error(f"[_audit_log] Audit log failed: {e}", exc_info=True)
