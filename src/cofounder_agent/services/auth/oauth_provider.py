"""PoindexterOAuthProvider — implements the MCP SDK's auth Protocol.

Backs the SDK's ``OAuthAuthorizationServerProvider`` with our
``oauth_clients`` + ``oauth_authorization_codes`` tables and the JWT
issuer in :mod:`services.auth.oauth_issuer`. The SDK takes care of
the HTTP plumbing (``/authorize``, ``/token``, ``/register``,
``/.well-known/oauth-authorization-server``, PKCE S256 verify, client
authentication, etc.); we just plug in the storage and the token mint.

## Solo-operator authorization

``authorize()`` doesn't render a consent screen. The whole point of
"clients" here is "tools/agents the operator owns" — there's no third
party we'd ask for permission. The handler immediately bounces the
browser back to ``redirect_uri`` with ``code`` + ``state`` set. If a
client gets a redirect from us, it's because the operator put the
client's secret into that client themselves.

## Client secret storage

Stored ``pgp_sym_encrypt``-ed in ``oauth_clients.client_secret_encrypted``
(see migration 0110). Decrypted by ``get_client`` via the same
bootstrap key the JWT signer uses. The SDK's ``ClientAuthenticator``
expects ``client.client_secret`` to be plaintext (it does
``hmac.compare_digest`` against the request's secret) so we have to
hand it back the plaintext — we just don't *store* it that way.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from services.auth.oauth_issuer import (
    ALLOWED_SCOPES,
    generate_authorization_code,
    generate_client_id,
    generate_client_secret,
    issue_token,
    signing_key,
    verify_token,
    InvalidToken,
)
from services.logger_config import get_logger

logger = get_logger(__name__)


# Auth codes are short-lived per RFC 6749 §10.5 — 5 minutes is plenty
# of time for the browser to bounce through the redirect dance and the
# client to POST the exchange.
_AUTH_CODE_TTL_SECONDS = 300


class PoindexterOAuthProvider(OAuthAuthorizationServerProvider[
    AuthorizationCode, RefreshToken, AccessToken,
]):
    """Single-pool provider; one instance per worker process.

    The pool is the live ``DatabaseService`` pool — same one the rest
    of the app uses, so writes here are visible to anything else
    holding a connection.
    """

    def __init__(self, pool):
        self._pool = pool

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_client(self, row: Any, client_secret_plain: str | None) -> OAuthClientInformationFull:
        """Build the SDK's client model from an asyncpg row + the
        decrypted client_secret. ``client_secret`` may be None for
        clients registered with ``token_endpoint_auth_method=none``
        (we don't currently support that, but the field is nullable in
        the SDK model).

        asyncpg hands JSONB columns back as raw JSON strings unless we
        register a type codec on the pool (which the rest of the worker
        doesn't), so ``client_metadata`` needs an explicit parse.
        """
        import json as _json
        raw_meta = row["client_metadata"]
        if isinstance(raw_meta, str):
            meta = _json.loads(raw_meta) if raw_meta else {}
        elif isinstance(raw_meta, dict):
            meta = raw_meta
        else:
            meta = {}

        return OAuthClientInformationFull(
            client_id=row["client_id"],
            client_secret=client_secret_plain,
            client_id_issued_at=int(row["created_at"].timestamp()),
            client_secret_expires_at=None,  # secrets don't expire automatically
            redirect_uris=list(row["redirect_uris"] or []),
            token_endpoint_auth_method=row["token_endpoint_auth_method"],
            grant_types=list(row["grant_types"] or []),
            response_types=list(row["response_types"] or []),
            scope=" ".join(row["scopes"] or ()) or None,
            client_name=meta.get("client_name") or row["name"],
            client_uri=meta.get("client_uri"),
            logo_uri=meta.get("logo_uri"),
            contacts=meta.get("contacts"),
        )

    # ------------------------------------------------------------------
    # Provider Protocol — client management
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT client_id, name, scopes, created_at, last_used_at, revoked_at,
                       redirect_uris, grant_types, response_types,
                       token_endpoint_auth_method, client_metadata,
                       pgp_sym_decrypt(decode(client_secret_encrypted, 'base64'), $2)::text
                           AS client_secret_plain
                  FROM oauth_clients
                 WHERE client_id = $1
                """,
                client_id,
                signing_key(),
            )
        if row is None or row["revoked_at"] is not None:
            return None
        return self._row_to_client(row, row["client_secret_plain"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """RFC 7591 Dynamic Client Registration.

        The SDK's ``RegistrationHandler`` has already populated
        ``client_id`` + ``client_secret`` (random values) and called
        in here to store them. We encrypt the secret and persist.
        """
        if not client_info.client_id:
            client_info.client_id = generate_client_id()
        if client_info.client_secret is None and client_info.token_endpoint_auth_method != "none":
            client_info.client_secret = generate_client_secret()

        scopes = (client_info.scope or "").split() if client_info.scope else []
        # Default to the safest scope set if the client didn't specify
        # — Custom Connector registrations sometimes leave scope blank.
        if not scopes:
            scopes = ["mcp:read", "mcp:write"]

        metadata = {
            k: v for k, v in {
                "client_name": client_info.client_name,
                "client_uri": str(client_info.client_uri) if client_info.client_uri else None,
                "logo_uri": str(client_info.logo_uri) if client_info.logo_uri else None,
                "contacts": client_info.contacts,
                "software_id": client_info.software_id,
                "software_version": client_info.software_version,
            }.items() if v is not None
        }

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO oauth_clients (
                    client_id, name, scopes,
                    redirect_uris, grant_types, response_types,
                    token_endpoint_auth_method, client_metadata,
                    client_secret_encrypted
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb,
                    encode(pgp_sym_encrypt($9::text, $10::text), 'base64')
                )
                """,
                client_info.client_id,
                client_info.client_name or client_info.client_id,
                scopes,
                [str(u) for u in (client_info.redirect_uris or [])],
                list(client_info.grant_types),
                list(client_info.response_types),
                client_info.token_endpoint_auth_method or "client_secret_post",
                _json_dumps(metadata),
                client_info.client_secret or "",
                signing_key(),
            )
        logger.info(
            "DCR registered client_id=%s name=%r scopes=%s redirect_uris=%s",
            client_info.client_id,
            client_info.client_name,
            scopes,
            client_info.redirect_uris,
        )

    # ------------------------------------------------------------------
    # Provider Protocol — authorization
    # ------------------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Solo-operator: skip the consent UI, mint a code immediately.

        We trust that anyone holding the client_id + client_secret IS
        the operator — the secret is the ground truth, not a separate
        user identity.
        """
        code = generate_authorization_code()
        scopes = list(params.scopes) if params.scopes else (
            (client.scope or "").split() if client.scope else ["mcp:read", "mcp:write"]
        )
        # Belt-and-suspenders: never store a scope outside the allowlist.
        scopes = [s for s in scopes if s in ALLOWED_SCOPES]

        expires_at = datetime.fromtimestamp(time.time() + _AUTH_CODE_TTL_SECONDS, tz=timezone.utc)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO oauth_authorization_codes (
                    code, client_id, code_challenge,
                    redirect_uri, redirect_uri_provided_explicitly,
                    scopes, resource, state, expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                code,
                client.client_id,
                params.code_challenge,
                str(params.redirect_uri),
                bool(params.redirect_uri_provided_explicitly),
                scopes,
                params.resource,
                params.state,
                expires_at,
            )
        logger.info(
            "issued auth code client_id=%s scopes=%s redirect_uri=%s state=%r",
            client.client_id, scopes, params.redirect_uri, params.state,
        )
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        from pydantic import AnyUrl

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT code, client_id, code_challenge, redirect_uri,
                       redirect_uri_provided_explicitly, scopes, resource,
                       expires_at
                  FROM oauth_authorization_codes
                 WHERE code = $1
                """,
                authorization_code,
            )
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=list(row["scopes"] or []),
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            resource=row["resource"],
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """The SDK has already done PKCE verify + expiry + redirect_uri
        match by the time we're called. Single-use the code, mint a JWT,
        bump last_used_at on the client, return the token.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                deleted = await conn.fetchval(
                    "DELETE FROM oauth_authorization_codes WHERE code = $1 RETURNING code",
                    authorization_code.code,
                )
                if deleted is None:
                    # Race or replay attempt — the SDK already proved
                    # we owned this code via load_authorization_code,
                    # so a missing row here is a concurrent reuse.
                    raise TokenError(
                        error="invalid_grant",
                        error_description="authorization code already redeemed",
                    )
                await conn.execute(
                    "UPDATE oauth_clients SET last_used_at = NOW() WHERE client_id = $1",
                    client.client_id,
                )

        access_token, claims = issue_token(
            client.client_id, authorization_code.scopes,
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=claims.expires_at - claims.issued_at,
            scope=" ".join(sorted(claims.scopes)) or None,
            refresh_token=None,
        )

    # ------------------------------------------------------------------
    # Provider Protocol — refresh tokens (not implemented yet)
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        # No refresh token issuance yet — clients re-run the auth flow
        # when their access token expires. Anthropic's Custom Connector
        # caches the client_id+secret and re-mints transparently, so the
        # UX cost of skipping refresh is zero in practice.
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:  # pragma: no cover
        raise TokenError(
            error="unsupported_grant_type",
            error_description="refresh tokens are not currently issued",
        )

    # ------------------------------------------------------------------
    # Provider Protocol — token introspection / revocation
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Stateless verification — JWT signature is the source of truth."""
        try:
            claims = verify_token(token)
        except InvalidToken:
            return None
        return AccessToken(
            token=token,
            client_id=claims.client_id,
            scopes=sorted(claims.scopes),
            expires_at=claims.expires_at,
            resource=None,
        )

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        """Best-effort revocation by client.

        Stateless JWTs aren't individually revocable — once minted, they
        verify until they expire. What we *can* do is mark the client
        revoked so no new tokens are issued for it. The 60-min TTL on
        the access token bounds the leak window.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE oauth_clients
                   SET revoked_at = NOW()
                 WHERE client_id = $1
                   AND revoked_at IS NULL
                """,
                token.client_id,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_dumps(obj: Any) -> str:
    """Asyncpg won't auto-serialize a dict for a jsonb column unless we
    register a type codec; cheaper to JSON-stringify here.
    """
    import json
    return json.dumps(obj, default=str)
