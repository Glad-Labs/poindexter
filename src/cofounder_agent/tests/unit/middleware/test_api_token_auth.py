"""
Unit tests for middleware/api_token_auth.py — verify_api_token and verify_api_token_optional.

Phase 3 (Glad-Labs/poindexter#249) removed the static-Bearer fallback.
The middleware now accepts OAuth JWTs only (with the dev-token bypass
left intact for local testing).
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from middleware.api_token_auth import verify_api_token, verify_api_token_optional


def _request(dev_mode: bool = False, environment: str = "development") -> MagicMock:
    """Build a fake FastAPI Request with site_config on app.state.

    Replaces the legacy ``patch("middleware.api_token_auth.site_config")``
    pattern after the middleware migrated to the DI seam
    (glad-labs-stack#330).

    ``environment`` controls the DB ``environment`` app_setting the
    middleware reads to decide whether the dev-token bypass is allowed
    (Glad-Labs/poindexter#606). Defaults to ``"development"`` so existing
    dev-token tests keep passing; pass ``"production"`` / ``""`` / etc. to
    exercise the fail-closed block.
    """
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": (
        "true"
        if (k == "development_mode" and dev_mode)
        else (
            "true"
            if (k == "disable_auth_for_dev" and dev_mode)
            else environment if k == "environment" else d
        )
    )
    app = MagicMock()
    # DI seam (#272): middleware reads site_config off app.state.container.
    app.state.container.site_config = sc
    request = MagicMock()
    request.app = app
    return request


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
# _is_dev_token_blocked — DB-environment fail-closed (Glad-Labs/poindexter#606)
# ---------------------------------------------------------------------------


class TestIsDevTokenBlocked:
    """The dev-token bypass must fail CLOSED.

    Pre-#606 the block only fired when the ``ENVIRONMENT`` env var was
    literally ``"production"``. Because config is DB-first (app_settings),
    ``ENVIRONMENT`` is usually unset on the worker — so a stray
    ``development_mode=true`` row would have re-enabled ``dev-token`` as
    full auth on the Tailscale-Funnel-exposed routes. The fix derives the
    production check from the DB ``environment`` setting too and BLOCKS
    unless the environment is positively a development one.
    """

    def _sc(self, *, dev_mode: bool, environment: str) -> MagicMock:
        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": (
            ("true" if dev_mode else "false")
            if k == "development_mode"
            else environment if k == "environment" else d
        )
        return sc

    def test_blocked_when_db_environment_production_and_env_unset(self, monkeypatch):
        """DB says production, ENVIRONMENT unset → dev-token BLOCKED."""
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        sc = self._sc(dev_mode=True, environment="production")
        assert _is_dev_token_blocked(sc) is True

    def test_blocked_when_environment_indeterminate(self, monkeypatch):
        """Empty DB environment + unset env var → indeterminate → BLOCKED."""
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        sc = self._sc(dev_mode=True, environment="")
        assert _is_dev_token_blocked(sc) is True

    def test_blocked_when_environment_is_staging(self, monkeypatch):
        """Staging is not a genuine dev environment → BLOCKED."""
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        sc = self._sc(dev_mode=True, environment="staging")
        assert _is_dev_token_blocked(sc) is True

    def test_blocked_when_env_var_production_overrides_dev_db(self, monkeypatch):
        """ENVIRONMENT=production blocks even if DB says development.

        The two sources are OR-combined for the production verdict — a
        positive production signal from EITHER source blocks the bypass.
        """
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.setenv("ENVIRONMENT", "production")
        sc = self._sc(dev_mode=True, environment="development")
        assert _is_dev_token_blocked(sc) is True

    def test_allowed_only_when_environment_is_development(self, monkeypatch):
        """Genuine development environment → dev-token NOT blocked."""
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        sc = self._sc(dev_mode=True, environment="development")
        assert _is_dev_token_blocked(sc) is False

    @pytest.mark.parametrize("env_value", ["development", "dev", "local"])
    def test_allowed_for_dev_synonyms(self, monkeypatch, env_value):
        """Accept the established dev synonyms (matches connection_health)."""
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        sc = self._sc(dev_mode=True, environment=env_value)
        assert _is_dev_token_blocked(sc) is False

    def test_blocked_when_site_config_missing(self, monkeypatch):
        """No SiteConfig at all → can't assert dev → BLOCKED (fail-closed)."""
        from middleware.api_token_auth import _is_dev_token_blocked

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        assert _is_dev_token_blocked(None) is True


# ---------------------------------------------------------------------------
# verify_api_token
# ---------------------------------------------------------------------------


class TestVerifyApiToken:
    """Tests for the strict verify_api_token dependency."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(request=_request(), credentials=None)
        assert exc_info.value.status_code == 401
        assert "Missing authorization header" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_token(self):
        token = _mint_jwt()
        cred = _make_credentials(token)
        result = await verify_api_token(request=_request(), credentials=cred)
        assert result == token

    @pytest.mark.asyncio
    async def test_expired_jwt_returns_401_with_generic_detail(self):
        """#724: an expired JWT is rejected with a GENERIC detail.

        The detail must not echo the JWT library's exception text — which
        names exactly which claim/step failed (expiry, signature, audience)
        and is useful feedback to someone forging tokens. The specifics are
        logged server-side (``logger.warning`` in ``_verify_oauth_jwt``), not
        returned to the client. Pre-fix this echoed ``invalid_token: <exc>``.
        """
        token = _mint_jwt(ttl_seconds=-60)
        cred = _make_credentials(token)
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(request=_request(), credentials=cred)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "invalid_token"

    @pytest.mark.asyncio
    async def test_malformed_jwt_returns_401(self):
        cred = _make_credentials("not.a.jwt")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(request=_request(), credentials=cred)
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
            await verify_api_token(request=_request(), credentials=cred)
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
            await verify_api_token(request=_request(), credentials=cred)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_dev_mode_accepts_dev_token(self):
        # Post glad-labs-stack#330: dev_mode is read per-request from
        # request.app.state.site_config. The _request(dev_mode=True)
        # helper sets development_mode=true on the site_config mock.
        # ENVIRONMENT defaults to non-production, so _is_dev_token_blocked
        # returns False and the dev-token is accepted.
        cred = _make_credentials("dev-token")
        result = await verify_api_token(
            request=_request(dev_mode=True),
            credentials=cred,
        )
        assert result == "dev-token"

    @pytest.mark.asyncio
    async def test_dev_token_blocked_when_db_environment_production(self, monkeypatch):
        """#606: dev-token refused when DB environment=production, ENV unset."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        cred = _make_credentials("dev-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(
                request=_request(dev_mode=True, environment="production"),
                credentials=cred,
            )
        assert exc_info.value.status_code == 401
        assert "production" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_dev_token_blocked_when_environment_indeterminate(self, monkeypatch):
        """#606: fail-closed — empty DB environment + unset ENV → refused."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        cred = _make_credentials("dev-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(
                request=_request(dev_mode=True, environment=""),
                credentials=cred,
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"})
    async def test_dev_mode_no_header_returns_401(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_token(request=_request(), credentials=None)
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
            await verify_api_token(request=_request(), credentials=cred)
        # Not a JWT shape, not a JWT signature → invalid_token 401.
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# verify_api_token_optional
# ---------------------------------------------------------------------------


class TestVerifyApiTokenOptional:
    """Tests for the optional verify_api_token_optional dependency."""

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self):
        result = await verify_api_token_optional(request=_request(), credentials=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_token(self):
        token = _mint_jwt()
        cred = _make_credentials(token)
        result = await verify_api_token_optional(request=_request(), credentials=cred)
        assert result == token

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        cred = _make_credentials("bad-token")
        result = await verify_api_token_optional(request=_request(), credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    async def test_static_bearer_shaped_token_returns_none(self):
        """Phase 3 regression: legacy-shaped tokens silently fail (no auth)."""
        legacy_token = "poindexter-deadbeefcafebabefeedfacebadc0ffee0123456789abcdef"
        cred = _make_credentials(legacy_token)
        result = await verify_api_token_optional(request=_request(), credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_jwt_returns_none(self):
        token = _mint_jwt(ttl_seconds=-60)
        cred = _make_credentials(token)
        result = await verify_api_token_optional(request=_request(), credentials=cred)
        assert result is None

    @pytest.mark.asyncio
    async def test_dev_mode_accepts_dev_token(self):
        # Same DI-seam pattern as the non-optional variant above.
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(
            request=_request(dev_mode=True),
            credentials=cred,
        )
        assert result == "dev-token"

    @pytest.mark.asyncio
    async def test_dev_token_blocked_when_db_environment_production(self, monkeypatch):
        """#606: optional path returns None instead of trusting dev-token."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(
            request=_request(dev_mode=True, environment="production"),
            credentials=cred,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dev_token_blocked_when_environment_indeterminate(self, monkeypatch):
        """#606: fail-closed — empty DB environment + unset ENV → None."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        cred = _make_credentials("dev-token")
        result = await verify_api_token_optional(
            request=_request(dev_mode=True, environment=""),
            credentials=cred,
        )
        assert result is None

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DEVELOPMENT_MODE": "true"})
    async def test_dev_mode_no_header_returns_none(self):
        """After the fix, dev mode no longer auto-authenticates missing headers."""
        result = await verify_api_token_optional(request=_request(), credentials=None)
        assert result is None
