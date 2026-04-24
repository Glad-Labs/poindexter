"""
Unit tests for middleware/api_token_auth.py — verify_api_token and verify_api_token_optional.

Phase H (GH#95): site_config is now read off ``request.app.state.site_config``.
Tests build a MagicMock Request whose ``.app.state.site_config`` is a
mock-SiteConfig configured per case.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from middleware.api_token_auth import (
    get_operator_identity,
    verify_api_token,
    verify_api_token_optional,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credentials(token: str):
    """Return a mock HTTPAuthorizationCredentials with the given token."""
    cred = MagicMock()
    cred.credentials = token
    return cred


def _make_request(api_token: str = "", dev_mode: bool = False, operator_id: str = "operator"):
    """Build a FastAPI Request stand-in whose ``.app.state.site_config``
    returns the supplied values.

    Post-GH-107: ``api_token`` lives behind ``get_secret`` (async) since
    it's an is_secret row in app_settings. The mock exposes both ``get``
    (sync) and ``get_secret`` (async) — non-secret keys go through
    ``get``, the api_token resolves via ``get_secret``.
    """
    mapping = {
        "api_token": api_token,
        "development_mode": "true" if dev_mode else "",
        "operator_id": operator_id,
    }
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda k, d="": mapping.get(k, d))
    sc.get_secret = AsyncMock(side_effect=lambda k, d="": mapping.get(k, d))
    req = MagicMock()
    req.app.state.site_config = sc
    return req


# ---------------------------------------------------------------------------
# verify_api_token
# ---------------------------------------------------------------------------


class TestVerifyApiToken:
    """Tests for the strict verify_api_token dependency."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_401(self):
        req = _make_request(api_token="real-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(req, credentials=None)
        assert exc_info.value.status_code == 401
        assert "Missing authorization header" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self):
        req = _make_request(api_token="real-token")
        cred = _make_credentials("wrong-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(req, credentials=cred)
        assert exc_info.value.status_code == 401
        assert "Invalid token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_token_returns_token(self):
        req = _make_request(api_token="real-token")
        cred = _make_credentials("real-token")
        result = await verify_api_token(req, credentials=cred)
        assert result == "real-token"

    @pytest.mark.asyncio
    async def test_api_token_not_set_returns_500(self):
        req = _make_request(api_token="")
        cred = _make_credentials("any-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(req, credentials=cred)
        assert exc_info.value.status_code == 500
        assert "API_TOKEN not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_dev_mode_accepts_dev_token(self):
        # Dev-token bypass is only blocked when ENVIRONMENT=production;
        # clearing os.environ ensures we're in "development" territory.
        req = _make_request(api_token="", dev_mode=True)
        cred = _make_credentials("dev-token")
        result = await verify_api_token(req, credentials=cred)
        assert result == "dev-token"

    @pytest.mark.asyncio
    async def test_dev_mode_no_header_returns_401(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        req = _make_request(api_token="", dev_mode=True)
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(req, credentials=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_dev_mode_wrong_token_returns_401(self):
        """Dev mode only accepts 'dev-token', not arbitrary tokens."""
        req = _make_request(api_token="", dev_mode=True)
        cred = _make_credentials("wrong-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(req, credentials=cred)
        # API_TOKEN is not set, so we get 500 (not configured)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ENVIRONMENT": "production"})
    async def test_dev_token_blocked_in_production(self):
        """With ENVIRONMENT=production AND DEVELOPMENT_MODE=true, the
        dev-token must be refused (defence-in-depth against a misconfig
        that re-enables dev mode in a real prod environment)."""
        req = _make_request(api_token="", dev_mode=True)
        cred = _make_credentials("dev-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(req, credentials=cred)
        assert exc_info.value.status_code == 401
        assert "not allowed in production" in exc_info.value.detail


# ---------------------------------------------------------------------------
# verify_api_token_optional
# ---------------------------------------------------------------------------


class TestVerifyApiTokenOptional:
    """Tests for the optional verify_api_token_optional dependency."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self):
        req = _make_request(api_token="real-token")
        result = await verify_api_token_optional(req, credentials=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_token_returns_token(self):
        req = _make_request(api_token="real-token")
        cred = _make_credentials("real-token")
        result = await verify_api_token_optional(req, credentials=cred)
        assert result == "real-token"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        req = _make_request(api_token="real-token")
        cred = _make_credentials("bad-token")
        result = await verify_api_token_optional(req, credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_dev_mode_accepts_dev_token(self):
        req = _make_request(api_token="", dev_mode=True)
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(req, credentials=cred)
        assert result == "dev-token"

    @pytest.mark.asyncio
    async def test_dev_mode_no_header_returns_none(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        req = _make_request(api_token="", dev_mode=True)
        result = await verify_api_token_optional(req, credentials=None)
        assert result is None

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ENVIRONMENT": "production"})
    async def test_dev_token_blocked_in_production(self):
        req = _make_request(api_token="", dev_mode=True)
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(req, credentials=cred)
        # In production, dev-token is refused — optional variant returns None
        assert result is None


# ---------------------------------------------------------------------------
# get_operator_identity
# ---------------------------------------------------------------------------


class TestGetOperatorIdentity:
    def test_returns_default_when_no_site_config(self):
        identity = get_operator_identity()
        assert identity["id"] == "operator"
        assert identity["username"] == "operator"
        assert identity["auth_provider"] == "api_token"
        assert identity["is_active"] is True

    def test_reads_operator_id_from_site_config(self):
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda k, d="": {"operator_id": "matt"}.get(k, d))
        identity = get_operator_identity(sc)
        assert identity["id"] == "matt"
