"""OAuth 2.1 Client Credentials issuer (Glad-Labs/poindexter#241 Phase 1).

Issues short-lived JWT access tokens to registered clients (CLI, MCP
servers, OpenClaw skills, brain daemon, Grafana webhooks, etc.).
*Not* an end-user authentication system — every client is a tool /
agent owned by the operator.

## Token format

Symmetric JWT, HS256, signed with the bootstrap symmetric key
(``POINDEXTER_SECRET_KEY``, the same one
``plugins.secrets`` already reads). Payload:

    {
      "iss": "poindexter",
      "sub": "<client_id>",
      "scope": "mcp:read mcp:write",   # space-delimited per RFC 8693
      "iat": 1714000000,
      "exp": 1714003600,
      "jti": "<uuid4>"
    }

## Why PyJWT instead of Authlib

The Phase 1 plan in #241 named Authlib as the issuer library. Once we
sat with the actual scope — Client Credentials Grant only, JWT-only
verification, no refresh tokens, no DCR, no auth-code flow — the
Authlib surface stopped paying for itself: PyJWT (already pinned in
``pyproject.toml`` for CVE GHSA-752w-5fwx-jx9f) covers everything we
need in ~50 lines, and the resource server (``api_token_auth``) verifies
the same way both sides of the bridge. We can swap to Authlib later if
we add a flow it actually carries weight for; nothing in the wire format
or DB schema would change.

## Client secret hashing

Plaintext secrets are 256 bits of randomness — brute-forcing them is
infeasible regardless of the hash. We still use ``hashlib.scrypt`` so
the stored hash is opaque to a DB-only compromise: an attacker who
exfiltrates ``oauth_clients`` rows can't replay the secret without
also obtaining the signing key (which lives outside the DB in
``bootstrap.toml``).
"""

from __future__ import annotations

import hashlib
import hmac
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

_DEFAULT_TTL_SECONDS = 3600  # 60 min, per #241 LOCKED decision
_ISSUER = "poindexter"
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

# scrypt params — N=2^14, r=8, p=1 is the widely-recommended baseline
# for online password hashing. Our inputs are 256-bit random secrets,
# not human passwords, so this is comfortably more cost than the
# attacker actually faces.
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_BYTES = 16


class OAuthError(Exception):
    """Base class for OAuth issuer failures (token mint, verify, register)."""


class InvalidClient(OAuthError):
    """RFC 6749 §5.2 ``invalid_client`` — bad id or wrong secret."""


class InvalidScope(OAuthError):
    """RFC 6749 §5.2 ``invalid_scope`` — requested scope outside the client's set."""


class InvalidToken(OAuthError):
    """RFC 6750 §3.1 ``invalid_token`` — bad signature, expired, malformed."""


# ---------------------------------------------------------------------------
# Signing key
# ---------------------------------------------------------------------------


def _signing_key() -> str:
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
# Client secret hashing (scrypt)
# ---------------------------------------------------------------------------


def hash_secret(plaintext: str) -> str:
    """Hash a client secret for storage.

    Format: ``scrypt$<N>$<r>$<p>$<salt_hex>$<hash_hex>``. Self-describing
    so we can change params later without ambiguity.
    """
    salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
    h = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${h.hex()}"


def verify_secret(plaintext: str, stored_hash: str) -> bool:
    """Constant-time verify a client secret against the stored scrypt hash."""
    try:
        algo, n_s, r_s, p_s, salt_hex, hash_hex = stored_hash.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
    )
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# Identifier generation (client_id / client_secret)
# ---------------------------------------------------------------------------


def generate_client_id() -> str:
    """``pdx_<32 hex chars>`` — short, opaque, prefix-tagged for grep/audit."""
    return f"pdx_{secrets.token_hex(16)}"


def generate_client_secret() -> str:
    """256 bits of randomness, urlsafe-base64. Shown to the operator once."""
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
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> tuple[str, TokenClaims]:
    """Mint a signed JWT. Returns (jwt_string, claims) — claims are useful
    for logging or for the caller to record.

    Rejects scopes outside :data:`ALLOWED_SCOPES`; the token endpoint
    is supposed to have intersected against the client's grant before
    calling us, but we double-check as a defence-in-depth.
    """
    scope_set = frozenset(scopes)
    bad = scope_set - ALLOWED_SCOPES
    if bad:
        raise InvalidScope(f"unknown scope(s): {sorted(bad)}")

    now = int(time.time())
    exp = now + ttl_seconds
    jti = uuid.uuid4().hex
    payload = {
        "iss": _ISSUER,
        "sub": client_id,
        "scope": " ".join(sorted(scope_set)),
        "iat": now,
        "exp": exp,
        "jti": jti,
    }
    token = jwt.encode(payload, _signing_key(), algorithm=_ALGORITHM)
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
            _signing_key(),
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
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


# ---------------------------------------------------------------------------
# DB operations (oauth_clients)
# ---------------------------------------------------------------------------


async def register_client(
    pool,
    *,
    name: str,
    scopes: Iterable[str],
) -> tuple[str, str]:
    """Create a new client row. Returns ``(client_id, client_secret_plaintext)``.

    The plaintext is shown to the operator once — we hash before
    storing and never have a way to recover it.
    """
    scope_list = sorted(set(scopes))
    bad = set(scope_list) - ALLOWED_SCOPES
    if bad:
        raise InvalidScope(f"unknown scope(s): {sorted(bad)}")

    client_id = generate_client_id()
    plaintext = generate_client_secret()
    secret_hash = hash_secret(plaintext)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO oauth_clients
                (client_id, client_secret_hash, name, scopes)
            VALUES ($1, $2, $3, $4)
            """,
            client_id,
            secret_hash,
            name,
            scope_list,
        )
    logger.info(
        "Registered OAuth client client_id=%s name=%r scopes=%s",
        client_id, name, scope_list,
    )
    return client_id, plaintext


async def mint_token_from_credentials(
    pool,
    *,
    client_id: str,
    client_secret: str,
    requested_scopes: Iterable[str] | None = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> tuple[str, TokenClaims]:
    """Full Client Credentials Grant: verify creds, intersect scopes,
    issue JWT, bump ``last_used_at``.

    ``requested_scopes`` may be empty/None — RFC 6749 says omit the
    ``scope`` parameter and the AS issues the client's full set.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT client_secret_hash, scopes, revoked_at
              FROM oauth_clients
             WHERE client_id = $1
            """,
            client_id,
        )

        if row is None:
            # Don't reveal whether the id exists; same error for
            # "no such client" as for "wrong secret". Brief work
            # against the pwhash so timing matches the secret-check
            # path roughly.
            verify_secret(client_secret, "scrypt$16384$8$1$00$00")
            raise InvalidClient("invalid client credentials")

        if row["revoked_at"] is not None:
            raise InvalidClient("client revoked")

        if not verify_secret(client_secret, row["client_secret_hash"]):
            raise InvalidClient("invalid client credentials")

        granted = frozenset(row["scopes"] or ())
        if requested_scopes:
            requested = frozenset(requested_scopes)
            extras = requested - granted
            if extras:
                raise InvalidScope(
                    f"scope(s) not granted to client: {sorted(extras)}"
                )
            issued_scopes = requested
        else:
            issued_scopes = granted

        await conn.execute(
            "UPDATE oauth_clients SET last_used_at = NOW() WHERE client_id = $1",
            client_id,
        )

    return issue_token(client_id, issued_scopes, ttl_seconds=ttl_seconds)
