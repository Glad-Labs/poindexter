"""
Migration 0036: Create oauth_accounts table.

Addresses issue #542: get_or_create_oauth_user() in users_db.py executes three
queries against the oauth_accounts table, but the table does not exist in the
live database schema. Every GitHub/Google OAuth login attempt raises
asyncpg.UndefinedTableError and returns HTTP 500.

Changes:
1. Create oauth_accounts table with columns:
   - id            UUID PK default gen_random_uuid()
   - user_id       UUID NOT NULL FK -> users(id) ON DELETE CASCADE
   - provider      VARCHAR(50) NOT NULL
   - provider_user_id VARCHAR(255) NOT NULL
   - provider_data JSONB DEFAULT '{}'
   - created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   - last_used     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   - UNIQUE (provider, provider_user_id)
2. Create supporting indexes for common query patterns.

Rollback: drop the table and its indexes.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Create the oauth_accounts table if it does not already exist."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'oauth_accounts')"
        )
        if table_exists:
            logger.info("oauth_accounts table already exists — no action needed")
            return

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_accounts (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider         VARCHAR(50) NOT NULL,
                provider_user_id VARCHAR(255) NOT NULL,
                provider_data    JSONB DEFAULT '{}',
                created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                last_used        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE (provider, provider_user_id)
            )
            """
        )
        logger.info("Created oauth_accounts table")

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oauth_accounts_user_id
                ON oauth_accounts(user_id)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oauth_accounts_provider
                ON oauth_accounts(provider, provider_user_id)
            """
        )
        logger.info("Created indexes on oauth_accounts")


async def down(pool) -> None:
    """Drop the oauth_accounts table and its indexes."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_oauth_accounts_provider")
        await conn.execute("DROP INDEX IF EXISTS idx_oauth_accounts_user_id")
        await conn.execute("DROP TABLE IF EXISTS oauth_accounts")
        logger.info("Dropped oauth_accounts table (0036 down)")
