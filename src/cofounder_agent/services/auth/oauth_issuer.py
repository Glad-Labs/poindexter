"""OAuth 2.1 JWT issuance + verification (Glad-Labs/poindexter#241).

This module is the JWT side of the OAuth surface — it does NOT speak
HTTP. The HTTP token endpoint is built on the MCP SDK's
``OAuthAuthorizationServerProvider`` (see :mod:`services.auth.oauth_provider`),
which calls into here when it needs to mint or verify a token.

## Token format

Symmetric JWT, HS256, signed with the bootstrap symmetric key
(``POINDEXTER_SECRET_KEY`` — the same key ``plugins.secrets`` already
reads for app_settings encryption). Payload:

    {
      "iss":   "poindexter",
      "sub":   "<client_id>",
      "scope": "mcp:read mcp:write",   # space-delimited per RFC 6749 §3.3
      "iat":   1714000000,
      "exp":   1714003600,
      "jti":   "<uuid4>"
    }

## Why PyJWT (and not Authlib)

The HTTP server framework (authorize / token / register endpoints,
PKCE verification, client authenticator) comes from the MCP SDK's
``mcp.server.auth`` package — which is the "mature OSS" the original
#241 plan named alongside Authlib. PyJWT is the JWT library underneath
that does the actual sign / verify. Authlib would be a duplicative
third layer; the SDK already covers the AS-flow plumbing.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

import jwt  # PyJWT, already pinned in pyproject

from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config knobs (DB-first; defaults here only kick in if the row is missing)
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS = 3600  # 60 min, per #241 LOCKED decision
ISSUER_CLAIM = "poindexter"
_ALGORITHM = "HS256"
_KEY_ENV = "POINDEXTER_SECRET_KEY"

# Initial scope set per #241; resource servers should reject any scope
# not in this allowlist when validating a token.
ALLOWED_SCOPES: frozenset[str] = frozenset({
    "mcp:read",
    "mcp:write",
    "api:read",
    "api:write",
})


class OAuthError(Exception):
    """Base class for OAuth issuer failures."""


class InvalidScope(OAuthError):
    """RFC 6749 §5.2 ``invalid_scope`` — requested scope outside the client's set."""


class InvalidToken(OAuthError):
    """RFC 6750 §3.1 ``invalid_token`` — bad signature, expired, malformed."""


# ---------------------------------------------------------------------------
# Signing key
# ---------------------------------------------------------------------------


def signing_key() -> str:
    """Return the symmetric signing key.

    Reuses ``POINDEXTER_SECRET_KEY`` so we have one rotation target
    instead of two. Fail loud per ``feedback_no_silent_defaults`` — a
    missing key here would mean every JWT verifies trivially and we'd
    rather refuse to start.
    """
    key = os.getenv(_KEY_ENV)
    if not key:
        raise OAuthError(
            f"{_KEY_ENV} env var is required for OAuth issuer. "
            "Bootstrap.toml should set it; if it's missing, run "
            "`poindexter setup --rotate-secrets` to regenerate."
        )
    return key


# ---------------------------------------------------------------------------
# Identifier generation (client_id / client_secret / authorization codes)
# ---------------------------------------------------------------------------


def generate_client_id() -> str:
    """``pdx_<32 hex chars>`` — short, opaque, prefix-tagged for grep/audit."""
    return f"pdx_{secrets.token_hex(16)}"


def generate_client_secret() -> str:
    """256 bits of randomness, urlsafe-base64. Shown to the operator once."""
    return secrets.token_urlsafe(32)


def generate_authorization_code() -> str:
    """OAuth 2.1 §10.10 mandates ≥128 bits of entropy; we ship 256 to
    leave headroom and stay on a tidy round number.
    """
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# JWT issue + verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenClaims:
    """Subset of JWT claims that the resource server actually consults."""

    client_id: str
    scopes: frozenset[str]
    issued_at: int
    expires_at: int
    jti: str


def issue_token(
    client_id: str,
    scopes: Iterable[str],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, TokenClaims]:
    """Mint a signed JWT. Returns (jwt_string, claims).

    Rejects scopes outside :data:`ALLOWED_SCOPES`; the token endpoint
    is supposed to have intersected against the client's grant before
    calling us, but we double-check as defence-in-depth.
    """
    scope_set = frozenset(scopes)
    bad = scope_set - ALLOWED_SCOPES
    if bad:
        raise InvalidScope(f"unknown scope(s): {sorted(bad)}")

    now = int(time.time())
    exp = now + ttl_seconds
    jti = uuid.uuid4().hex
    payload = {
        "iss": ISSUER_CLAIM,
        "sub": client_id,
        "scope": " ".join(sorted(scope_set)),
        "iat": now,
        "exp": exp,
        "jti": jti,
    }
    token = jwt.encode(payload, signing_key(), algorithm=_ALGORITHM)
    claims = TokenClaims(
        client_id=client_id,
        scopes=scope_set,
        issued_at=now,
        expires_at=exp,
        jti=jti,
    )
    return token, claims


def verify_token(token: str) -> TokenClaims:
    """Verify signature + expiry + issuer; return claims.

    Raises :class:`InvalidToken` for any failure — caller turns that
    into a 401 with ``WWW-Authenticate: Bearer error="invalid_token"``.
    """
    try:
        payload = jwt.decode(
            token,
            signing_key(),
            algorithms=[_ALGORITHM],
            issuer=ISSUER_CLAIM,
            options={"require": ["exp", "iat", "sub", "scope", "jti"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise InvalidToken("token expired") from e
    except jwt.InvalidIssuerError as e:
        raise InvalidToken("wrong issuer") from e
    except jwt.InvalidTokenError as e:
        raise InvalidToken(f"invalid token: {e}") from e

    return TokenClaims(
        client_id=str(payload["sub"]),
        scopes=frozenset(payload["scope"].split()) if payload.get("scope") else frozenset(),
        issued_at=int(payload["iat"]),
        expires_at=int(payload["exp"]),
        jti=str(payload["jti"]),
    )
