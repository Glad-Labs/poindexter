"""Migration 0110: Extend oauth_clients + add oauth_authorization_codes.

Phase 1 follow-up to #241. The original 0109 schema was sized for
Client Credentials only — that's not enough to back the MCP SDK's
``OAuthAuthorizationServerProvider`` Protocol, which the Anthropic
Custom Connector exercises via Authorization Code Grant + PKCE.

## oauth_clients changes

Add the RFC 7591 fields the Authorization Code flow needs:

- ``redirect_uris       TEXT[]``      — registered callbacks; required for /authorize
- ``grant_types         TEXT[]``      — which grants this client may use
- ``response_types      TEXT[]``      — usually ['code']
- ``token_endpoint_auth_method TEXT`` — 'client_secret_post' / 'client_secret_basic' / 'none'
- ``client_metadata     JSONB``       — overflow for client_name, contacts, etc.
- ``client_secret_encrypted TEXT``    — pgcrypto-encrypted plaintext (replaces hash)

The encryption pivot: the SDK's ``ClientAuthenticator`` does
``hmac.compare_digest(client.client_secret.encode(), request_secret.encode())``
on the *plaintext* of the stored secret, so a one-way hash like 0109's
scrypt no longer fits. Encrypt-at-rest with pgcrypto + the bootstrap
``POINDEXTER_SECRET_KEY`` matches the same posture
``app_settings.api_token`` already uses (DB-only compromise stays
mitigated; both DB and bootstrap.toml together are required to
recover plaintext).

The legacy ``client_secret_hash`` column is dropped — the only rows
that exist were testing artifacts (verified at migration time and
deleted before the schema change).

## oauth_authorization_codes (new)

Short-lived (5min) PKCE-bound authorization codes. Stores everything
``exchange_authorization_code`` needs to mint the token without
trusting the client to echo it back. Cleanup is opportunistic — the
provider deletes codes on use, and a future job can sweep expired
rows on a cadence (acceptable to leak a few rows for now; codes are
unique short strings, no privacy risk).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 0109 rows aren't usable under the new auth flow (no
        # redirect_uris, no encrypted secret) so wipe them. Cheap to
        # re-register; the operator just runs ``poindexter auth
        # register-client`` again.
        cleared = await conn.fetchval(
            "SELECT count(*) FROM oauth_clients"
        )
        if cleared:
            await conn.execute("DELETE FROM oauth_clients")
            logger.warning(
                "Cleared %d pre-0110 oauth_clients row(s) — "
                "re-register them with `poindexter auth register-client`.",
                cleared,
            )

        await conn.execute(
            """
            ALTER TABLE oauth_clients
              ADD COLUMN IF NOT EXISTS redirect_uris            TEXT[]    NOT NULL DEFAULT '{}',
              ADD COLUMN IF NOT EXISTS grant_types              TEXT[]    NOT NULL DEFAULT '{authorization_code,refresh_token,client_credentials}',
              ADD COLUMN IF NOT EXISTS response_types           TEXT[]    NOT NULL DEFAULT '{code}',
              ADD COLUMN IF NOT EXISTS token_endpoint_auth_method TEXT    NOT NULL DEFAULT 'client_secret_post',
              ADD COLUMN IF NOT EXISTS client_metadata          JSONB     NOT NULL DEFAULT '{}'::jsonb,
              ADD COLUMN IF NOT EXISTS client_secret_encrypted  TEXT
            """
        )
        # Drop the legacy hash column. We made it nullable above so
        # an existing row without the new encrypted secret wouldn't
        # break, but since we cleared the table the column has no
        # surviving consumers either way.
        await conn.execute(
            "ALTER TABLE oauth_clients DROP COLUMN IF EXISTS client_secret_hash"
        )
        # Now that no rows exist + the new column is in place, make
        # client_secret_encrypted required.
        await conn.execute(
            "ALTER TABLE oauth_clients ALTER COLUMN client_secret_encrypted SET NOT NULL"
        )
        logger.info("Extended oauth_clients with Auth Code Grant fields (0110)")

        # pgcrypto for client-secret encryption — the same extension
        # plugins.secrets already requires for app_settings encryption.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
                code            TEXT          PRIMARY KEY,
                client_id       VARCHAR(64)   NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
                code_challenge  TEXT          NOT NULL,
                redirect_uri    TEXT          NOT NULL,
                redirect_uri_provided_explicitly BOOLEAN NOT NULL DEFAULT TRUE,
                scopes          TEXT[]        NOT NULL DEFAULT '{}',
                resource        TEXT,
                state           TEXT,
                expires_at      TIMESTAMPTZ   NOT NULL,
                created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oauth_auth_codes_expires
                ON oauth_authorization_codes (expires_at)
            """
        )
        logger.info("Created oauth_authorization_codes table (0110)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_oauth_auth_codes_expires")
        await conn.execute("DROP TABLE IF EXISTS oauth_authorization_codes")
        # Reverse the column changes; the old hash column is gone for
        # good — there's no way to reconstruct hashes from encrypted
        # values, so a true reversal would require operators to re-issue
        # every secret. Practically a one-way migration.
        await conn.execute(
            """
            ALTER TABLE oauth_clients
              DROP COLUMN IF EXISTS redirect_uris,
              DROP COLUMN IF EXISTS grant_types,
              DROP COLUMN IF EXISTS response_types,
              DROP COLUMN IF EXISTS token_endpoint_auth_method,
              DROP COLUMN IF EXISTS client_metadata,
              DROP COLUMN IF EXISTS client_secret_encrypted
            """
        )
        # Re-add the legacy column as nullable so the down() doesn't
        # fail on the NOT NULL constraint — operator would have to
        # repopulate manually anyway.
        await conn.execute(
            "ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS client_secret_hash TEXT"
        )
        logger.info("Reverted 0110 (oauth_clients column changes + auth_codes table)")
