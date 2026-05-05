"""
Unit tests for middleware/api_token_auth.py — verify_api_token and verify_api_token_optional.

Phase 3 (Glad-Labs/poindexter#249) removed the static-Bearer fallback.
The middleware now accepts OAuth JWTs only (with the dev-token bypass
left intact for local testing).
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from middleware.api_token_auth import verify_api_token, verify_api_token_optional


# ---------------------------------------------------------------------------
# Test fixtures: real OAuth JWTs minted with a deterministic signing key
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    """Set the OAuth signing key for the test session.

    The middleware delegates to ``services.auth.oauth_issuer.verify_token``,
    which requires ``POINDEXTER_SECRET_KEY`` to validate signatures.
    Tests that mint tokens use the same key so verify succeeds.
    """
    monkeypatch.setenv("POINDEXTER_SECRET_KEY", "test-signing-key-for-unit-tests")


def _mint_jwt(scopes: tuple[str, ...] = ("api:read",), ttl_seconds: int = 60) -> str:
    """Mint a real OAuth JWT for use in tests."""
    from services.auth.oauth_issuer import issue_token

    token, _claims = issue_token(
        client_id="pdx_test_client",
        scopes=scopes,
        ttl_seconds=ttl_seconds,
    )
    return token


def _make_credentials(token: str):
    """Return a mock HTTPAuthorizationCredentials with the given token."""
    cred = MagicMock()
    cred.credentials = token
    return cred


# ---------------------------------------------------------------------------
# verify_api_token
# ---------------------------------------------------------------------------


class TestVerifyApiToken:
    """Tests for the strict verify_api_token dependency."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=None)
        assert exc_info.value.status_code == 401
        assert "Missing authorization header" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_token(self):
        token = _mint_jwt()
        cred = _make_credentials(token)
        result = await verify_api_token(credentials=cred)
        assert result == token

    @pytest.mark.asyncio
    async def test_expired_jwt_returns_401(self):
        token = _mint_jwt(ttl_seconds=-60)
        cred = _make_credentials(token)
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_malformed_jwt_returns_401(self):
        cred = _make_credentials("not.a.jwt")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_static_bearer_shaped_token_returns_401(self):
        """Regression test for Glad-Labs/poindexter#249.

        After Phase 3 cleanup, a static-Bearer-shaped token (the legacy
        ``poindexter-<hex>`` format that never had three dot-separated
        JWT segments) MUST be rejected with 401. Previously this token
        would have fallen through to ``hmac.compare_digest`` against
        ``app_settings.api_token`` — that path is gone.
        """
        legacy_token = "poindexter-deadbeefcafebabefeedfacebadc0ffee0123456789abcdef"
        cred = _make_credentials(legacy_token)
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bad_signature_jwt_returns_401(self):
        """A JWT signed with the wrong key fails verification."""
        import jwt as _jwt

        # Sign a payload with a DIFFERENT key than POINDEXTER_SECRET_KEY.
        bad = _jwt.encode(
            {
                "iss": "poindexter",
                "sub": "pdx_test_client",
                "scope": "api:read",
                "iat": 1700000000,
                "exp": 9999999999,
                "jti": "abc",
            },
            "wrong-key",
            algorithm="HS256",
        )
        cred = _make_credentials(bad)
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("middleware.api_token_auth._dev_token_blocked", False)
    @patch("middleware.api_token_auth.site_config")
    async def test_dev_mode_accepts_dev_token(self, mock_site_config):
        # middleware._dev_token_blocked is evaluated at MODULE IMPORT time
        # based on ENVIRONMENT + DEVELOPMENT_MODE. Patching os.environ in
        # the test doesn't retroactively reset it. Patch the module-level
        # flag directly so this test exercises the dev-mode path even
        # when the worker imported the module with ENVIRONMENT=production.
        mock_site_config.get.side_effect = lambda k, default="": {
            "development_mode": "true",
        }.get(k, default)
        cred = _make_credentials("dev-token")
        result = await verify_api_token(credentials=cred)
        assert result == "dev-token"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"})
    async def test_dev_mode_no_header_returns_401(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"}, clear=False)
    async def test_dev_mode_wrong_token_returns_401(self):
        """Dev mode only accepts 'dev-token', not arbitrary tokens.

        Re-set POINDEXTER_SECRET_KEY because clear=False above keeps
        the autouse-fixture's value, but explicit assertion below
        relies on the malformed token being rejected by the JWT path.
        """
        cred = _make_credentials("wrong-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        # Not a JWT shape, not a JWT signature → invalid_token 401.
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# verify_api_token_optional
# ---------------------------------------------------------------------------


class TestVerifyApiTokenOptional:
    """Tests for the optional verify_api_token_optional dependency."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self):
        result = await verify_api_token_optional(credentials=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_token(self):
        token = _mint_jwt()
        cred = _make_credentials(token)
        result = await verify_api_token_optional(credentials=cred)
        assert result == token

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        cred = _make_credentials("bad-token")
        result = await verify_api_token_optional(credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    async def test_static_bearer_shaped_token_returns_none(self):
        """Phase 3 regression: legacy-shaped tokens silently fail (no auth)."""
        legacy_token = "poindexter-deadbeefcafebabefeedfacebadc0ffee0123456789abcdef"
        cred = _make_credentials(legacy_token)
        result = await verify_api_token_optional(credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_jwt_returns_none(self):
        token = _mint_jwt(ttl_seconds=-60)
        cred = _make_credentials(token)
        result = await verify_api_token_optional(credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    @patch("middleware.api_token_auth._dev_token_blocked", False)
    @patch("middleware.api_token_auth.site_config")
    async def test_dev_mode_accepts_dev_token(self, mock_site_config):
        # Same patching rationale as the non-optional variant above —
        # _dev_token_blocked is set at module import time.
        mock_site_config.get.side_effect = lambda k, default="": {
            "development_mode": "true",
        }.get(k, default)
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(credentials=cred)
        assert result == "dev-token"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"})
    async def test_dev_mode_no_header_returns_none(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        result = await verify_api_token_optional(credentials=None)
        assert result is None
