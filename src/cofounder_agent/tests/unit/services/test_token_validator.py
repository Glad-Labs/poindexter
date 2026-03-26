"""
Unit tests for JWTTokenValidator and validate_access_token.

All tests mock the JWT secret so no real environment variables are needed.
No network I/O — purely in-process.
"""

import os
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import jwt
import pytest

from services.token_validator import (
    AuthConfig,
    JWTTokenValidator,
    TokenType,
    validate_access_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-key-for-unit-tests"
_ALGO = "HS256"


def _make_token(
    payload_overrides: dict | None = None,
    *,
    secret: str = _SECRET,
    algorithm: str = _ALGO,
) -> str:
    """Build a signed JWT with defaults that pass validation."""
    now = datetime.now(timezone.utc)
    base = {
        "sub": "testuser",
        "user_id": "user-123",
        "email": "test@example.com",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    if payload_overrides:
        base.update(payload_overrides)
    return jwt.encode(base, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# JWTTokenValidator.verify_token — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyTokenSuccess:
    def test_valid_token_returns_claims(self):
        token = _make_token()
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            claims = JWTTokenValidator.verify_token(token, TokenType.ACCESS)
        assert claims is not None
        assert claims["user_id"] == "user-123"
        assert claims["sub"] == "testuser"

    def test_claims_include_type_field(self):
        token = _make_token()
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            claims = JWTTokenValidator.verify_token(token)
        assert claims["type"] == "access"  # type: ignore[index]

    def test_refresh_token_type_accepted(self):
        token = _make_token({"type": "refresh"})
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            claims = JWTTokenValidator.verify_token(token, TokenType.REFRESH)
        assert claims["type"] == "refresh"  # type: ignore[index]


# ---------------------------------------------------------------------------
# JWTTokenValidator.verify_token — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyTokenErrors:
    def test_expired_token_raises(self):
        expired = _make_token(
            {
                "iat": datetime.now(timezone.utc) - timedelta(hours=2),
                "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            }
        )
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            with pytest.raises(jwt.ExpiredSignatureError):
                JWTTokenValidator.verify_token(expired)

    def test_wrong_secret_raises_invalid_token(self):
        token = _make_token(secret="wrong-secret")
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            with pytest.raises(jwt.InvalidTokenError):
                JWTTokenValidator.verify_token(token)

    def test_malformed_token_raises_invalid_token(self):
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            with pytest.raises(jwt.InvalidTokenError):
                JWTTokenValidator.verify_token("not.a.valid.jwt.at.all")

    def test_token_with_only_two_parts_raises(self):
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            with pytest.raises(jwt.InvalidTokenError):
                JWTTokenValidator.verify_token("header.payload")

    def test_wrong_token_type_raises(self):
        """Access token presented as refresh raises InvalidTokenError."""
        access_token = _make_token({"type": "access"})
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            with pytest.raises(jwt.InvalidTokenError):
                JWTTokenValidator.verify_token(access_token, TokenType.REFRESH)

    def test_empty_string_raises_invalid_token(self):
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            with pytest.raises(jwt.InvalidTokenError):
                JWTTokenValidator.verify_token("")


# ---------------------------------------------------------------------------
# JWTTokenValidator.validate_access_token — tuple API
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAccessToken:
    def test_valid_returns_true_and_claims(self):
        token = _make_token()
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            is_valid, claims = JWTTokenValidator.validate_access_token(token)
        assert is_valid is True
        assert claims is not None
        assert claims["user_id"] == "user-123"

    def test_expired_returns_false_with_error_key(self):
        expired = _make_token(
            {
                "iat": datetime.now(timezone.utc) - timedelta(hours=2),
                "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            }
        )
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            is_valid, claims = JWTTokenValidator.validate_access_token(expired)
        assert is_valid is False
        assert "error" in claims  # type: ignore[operator]
        assert "expired" in claims["error"].lower()  # type: ignore[index]

    def test_invalid_token_returns_false_with_error_key(self):
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            is_valid, claims = JWTTokenValidator.validate_access_token("garbage")
        assert is_valid is False
        assert "error" in claims  # type: ignore[operator]

    def test_module_level_convenience_function(self):
        """validate_access_token() module-level wrapper delegates to class method."""
        token = _make_token()
        with patch.object(AuthConfig, "SECRET_KEY", _SECRET):
            is_valid, claims = validate_access_token(token)
        assert is_valid is True
        assert claims["user_id"] == "user-123"  # type: ignore[index]


# ---------------------------------------------------------------------------
# DISABLE_AUTH_FOR_DEV bypass (gated on non-production environment)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDisableAuthBypass:
    def test_dev_bypass_returns_dev_claims_in_development(self):
        token = _make_token()
        with (
            patch.dict(
                os.environ,
                {"DISABLE_AUTH_FOR_DEV": "true", "DEVELOPMENT_MODE": "true"},
            ),
            patch.object(AuthConfig, "SECRET_KEY", _SECRET),
        ):
            claims = JWTTokenValidator.verify_token(token)
        assert claims["sub"] == "dev-user"  # type: ignore[index]

    def test_dev_bypass_disabled_in_production(self):
        """DISABLE_AUTH_FOR_DEV must be ignored when DEVELOPMENT_MODE is not true."""
        expired = _make_token(
            {
                "iat": datetime.now(timezone.utc) - timedelta(hours=2),
                "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            }
        )
        with (
            patch.dict(
                os.environ,
                {"DISABLE_AUTH_FOR_DEV": "true", "DEVELOPMENT_MODE": "false"},
            ),
            patch.object(AuthConfig, "SECRET_KEY", _SECRET),
        ):
            with pytest.raises(jwt.ExpiredSignatureError):
                JWTTokenValidator.verify_token(expired)
