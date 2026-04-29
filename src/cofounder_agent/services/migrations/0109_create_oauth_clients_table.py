"""Migration 0109: Create oauth_clients table for OAuth 2.1 Client Credentials.

Phase 1 of the static-Bearer → OAuth 2.1 migration (Glad-Labs/poindexter#241).

Each row is one tool/agent (CLI, MCP server, OpenClaw, brain daemon, Grafana
webhook, etc.) — NOT a human user. Client secrets are hashed with scrypt
before storage; the plaintext is shown to the operator exactly once at
``poindexter auth register-client`` time and never written to disk by us.

Schema:

- ``client_id``           opaque short identifier (e.g. ``pdx_<16hex>``); PK
- ``client_secret_hash``  scrypt hash of the secret (never the plaintext)
- ``name``                operator-supplied human label ("brain-daemon",
                          "openclaw-laptop", ...)
- ``scopes``              text[] — subset of {mcp:read, mcp:write, api:read,
                          api:write}; every issued JWT carries these
- ``created_at``          when the client was registered
- ``last_used_at``        bumped on every successful token mint (NULL until
                          first use); lets us spot dead clients during the
                          Phase 3 cleanup
- ``revoked_at``          set when the client is killed; NULL means active.
                          Token endpoint refuses revoked clients; existing
                          JWTs continue to verify until they expire (60min
                          TTL bounds the leak window)

Idempotent — re-running is a no-op.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id          VARCHAR(64)  PRIMARY KEY,
                client_secret_hash TEXT         NOT NULL,
                name               TEXT         NOT NULL,
                scopes             TEXT[]       NOT NULL DEFAULT '{}',
                created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                last_used_at       TIMESTAMPTZ,
                revoked_at         TIMESTAMPTZ
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oauth_clients_active
                ON oauth_clients (client_id) WHERE revoked_at IS NULL
            """
        )
        logger.info("Created oauth_clients table + active-client index (0109)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_oauth_clients_active")
        await conn.execute("DROP TABLE IF EXISTS oauth_clients")
        logger.info("Dropped oauth_clients table (0109 down)")
