"""
Unit tests for middleware/api_token_auth.py — verify_api_token and verify_api_token_optional.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from middleware.api_token_auth import verify_api_token, verify_api_token_optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    @patch.dict("os.environ", {"API_TOKEN": "real-token"})
    async def test_invalid_token_returns_401(self):
        cred = _make_credentials("wrong-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        assert exc_info.value.status_code == 401
        assert "Invalid token" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"API_TOKEN": "real-token"})
    async def test_valid_token_returns_token(self):
        cred = _make_credentials("real-token")
        result = await verify_api_token(credentials=cred)
        assert result == "real-token"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_api_token_not_set_returns_500(self):
        cred = _make_credentials("any-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        assert exc_info.value.status_code == 500
        assert "API_TOKEN not configured" in exc_info.value.detail

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
            "api_token": "",
        }.get(k, default)
        # PR #325 made the production code call `await site_config.get_secret(...)`
        # for the encrypted api_token row. Mock as AsyncMock so the await resolves.
        mock_site_config.get_secret = AsyncMock(return_value="")
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
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"}, clear=True)
    async def test_dev_mode_wrong_token_returns_401(self):
        """Dev mode only accepts 'dev-token', not arbitrary tokens."""
        cred = _make_credentials("wrong-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(credentials=cred)
        # API_TOKEN is not set, so we get 500 (not configured)
        assert exc_info.value.status_code == 500


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
    @patch.dict("os.environ", {"API_TOKEN": "real-token"})
    async def test_valid_token_returns_token(self):
        cred = _make_credentials("real-token")
        result = await verify_api_token_optional(credentials=cred)
        assert result == "real-token"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"API_TOKEN": "real-token"})
    async def test_invalid_token_returns_none(self):
        cred = _make_credentials("bad-token")
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
            "api_token": "",
        }.get(k, default)
        # See note in TestVerifyApiToken — get_secret must be AsyncMock per #325.
        mock_site_config.get_secret = AsyncMock(return_value="")
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(credentials=cred)
        assert result == "dev-token"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"})
    async def test_dev_mode_no_header_returns_none(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        result = await verify_api_token_optional(credentials=None)
        assert result is None
