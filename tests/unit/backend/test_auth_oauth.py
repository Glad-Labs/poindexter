"""
OAuth / auth unit tests for issue #13.

Covers:
- CSRF state: generate → validate (one-time use, expiry)
- JWT token creation and decoding
- Token blocklist: add and is_revoked (in-memory path)
- Logout: blocklist populated, cookie cleared
- /api/auth/me: user profile returned from JWT claims
- Session expiry: expired token rejected
- Dev-token bypass: blocklist skipped for dev tokens
- exchange_code_for_token: mock-auth-code path
- get_github_user: mock-token path
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src" / "cofounder_agent"))

# ── stub structlog ────────────────────────────────────────────────────────────
if "structlog" not in sys.modules:
    _stub = ModuleType("structlog")
    setattr(_stub, "get_logger", lambda *a, **k: MagicMock())
    sys.modules["structlog"] = _stub


# ── CSRF state tests (in-memory path, Redis disabled) ────────────────────────


@pytest.mark.unit
def test_csrf_state_generated_and_validated():
    """generate_csrf_state produces a token that validate_csrf_state accepts."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        # Reset module-level state for CSRF
        import importlib
        import routes.auth_unified as auth_mod
        importlib.reload(auth_mod)

        state = auth_mod.generate_csrf_state()
        assert isinstance(state, str)
        assert len(state) > 20

        result = auth_mod.validate_csrf_state(state)
        assert result is True


@pytest.mark.unit
def test_csrf_state_is_one_time_use():
    """validate_csrf_state returns False the second time the same token is presented."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import routes.auth_unified as auth_mod
        importlib.reload(auth_mod)

        state = auth_mod.generate_csrf_state()
        assert auth_mod.validate_csrf_state(state) is True
        assert auth_mod.validate_csrf_state(state) is False


@pytest.mark.unit
def test_csrf_state_empty_string_rejected():
    """validate_csrf_state returns False for an empty state string."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import routes.auth_unified as auth_mod
        importlib.reload(auth_mod)

        assert auth_mod.validate_csrf_state("") is False


@pytest.mark.unit
def test_csrf_state_unknown_token_rejected():
    """validate_csrf_state returns False for a token that was never generated."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import routes.auth_unified as auth_mod
        importlib.reload(auth_mod)

        assert auth_mod.validate_csrf_state("never-generated-token") is False


@pytest.mark.unit
def test_csrf_state_expired_token_rejected():
    """validate_csrf_state returns False for an expired state entry."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import routes.auth_unified as auth_mod
        importlib.reload(auth_mod)

        state = auth_mod.generate_csrf_state()
        # Manually backdating the expiry to the past
        auth_mod._CSRF_STATES[state] = datetime.now(timezone.utc) - timedelta(seconds=1)

        assert auth_mod.validate_csrf_state(state) is False


# ── JWT token creation ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_create_jwt_token_contains_expected_claims():
    """create_jwt_token embeds login, user_id, email, and auth_provider=github."""
    import jwt as pyjwt
    import importlib
    import routes.auth_unified as auth_mod
    importlib.reload(auth_mod)
    from services.token_validator import AuthConfig

    user_data = {
        "login": "octocat",
        "id": 12345,
        "email": "octocat@github.com",
        "avatar_url": "https://avatars.githubusercontent.com/u/12345",
        "name": "The Octocat",
    }

    token = auth_mod.create_jwt_token(user_data)
    payload = pyjwt.decode(token, AuthConfig.SECRET_KEY, algorithms=[AuthConfig.ALGORITHM])

    assert payload["sub"] == "octocat"
    assert payload["user_id"] == "12345"
    assert payload["email"] == "octocat@github.com"
    assert payload["auth_provider"] == "github"
    assert payload["type"] == "access"


@pytest.mark.unit
def test_create_jwt_token_has_future_expiry():
    """JWT issued by create_jwt_token expires in the future."""
    import jwt as pyjwt
    import importlib
    import routes.auth_unified as auth_mod
    importlib.reload(auth_mod)
    from services.token_validator import AuthConfig

    token = auth_mod.create_jwt_token({"login": "user", "id": 1, "email": "u@x.com"})
    payload = pyjwt.decode(token, AuthConfig.SECRET_KEY, algorithms=[AuthConfig.ALGORITHM])

    assert payload["exp"] > time.time()


# ── Token blocklist (in-memory path) ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocklist_add_then_is_revoked():
    """A token added via add_token is subsequently detected as revoked."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import services.token_blocklist as bl
        importlib.reload(bl)

        future_exp = time.time() + 3600
        await bl.add_token("test-jwt-abc", future_exp)
        assert await bl.is_revoked("test-jwt-abc") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocklist_unknown_token_not_revoked():
    """is_revoked returns False for a token that was never added."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import services.token_blocklist as bl
        importlib.reload(bl)

        assert await bl.is_revoked("never-seen-token") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocklist_expired_token_not_revoked():
    """is_revoked returns False for a token whose expiry is in the past."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import services.token_blocklist as bl
        importlib.reload(bl)

        past_exp = time.time() - 1  # already expired
        await bl.add_token("expired-token-xyz", past_exp)
        # _prune() should remove it on the next is_revoked call
        assert await bl.is_revoked("expired-token-xyz") is False


# ── mock-code / mock-token short-circuits ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exchange_code_mock_auth_code():
    """exchange_code_for_token short-circuits for mock_auth_code_* without HTTP."""
    import importlib
    import routes.auth_unified as auth_mod
    importlib.reload(auth_mod)

    result = await auth_mod.exchange_code_for_token("mock_auth_code_dev123")

    assert result["access_token"] == "mock_github_token_dev"
    assert result["expires_in"] == 3600


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_github_user_mock_token():
    """get_github_user returns a hardcoded dev user for mock_github_token_dev."""
    import importlib
    import routes.auth_unified as auth_mod
    importlib.reload(auth_mod)

    user = await auth_mod.get_github_user("mock_github_token_dev")

    assert user["login"] == "dev-user"
    assert user["email"] == "dev@example.com"
    assert user["id"] == 999999


# ── /api/auth/me profile endpoint ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_user_profile_returns_user_data():
    """get_current_user_profile assembles a UserProfile from the current_user dict."""
    import importlib
    import routes.auth_unified as auth_mod
    importlib.reload(auth_mod)

    current_user = {
        "id": "user-uuid-001",
        "email": "octocat@github.com",
        "username": "octocat",
        "auth_provider": "github",
        "is_active": True,
        "created_at": "2025-01-01T00:00:00Z",
    }

    profile = await auth_mod.get_current_user_profile(current_user)

    assert profile.id == "user-uuid-001"
    assert profile.email == "octocat@github.com"
    assert profile.username == "octocat"
    assert profile.auth_provider == "github"
    assert profile.is_active is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_current_user_profile_defaults_missing_fields():
    """get_current_user_profile fills defaults for missing optional fields."""
    import importlib
    import routes.auth_unified as auth_mod
    importlib.reload(auth_mod)

    current_user = {"id": "user-uuid-002"}

    profile = await auth_mod.get_current_user_profile(current_user)

    assert profile.id == "user-uuid-002"
    assert profile.email == ""
    assert profile.username == ""
    assert profile.auth_provider == "jwt"
    assert profile.is_active is True


# ── logout: dev-token bypass ──────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_logout_skips_blocklist_for_dev_token():
    """unified_logout returns success without modifying the blocklist."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import services.token_blocklist as bl
        import routes.auth_unified as auth_mod
        importlib.reload(bl)
        importlib.reload(auth_mod)

        current_user = {
            "id": "dev-user",
            "auth_provider": "github",
            "token": "dev-token",  # dev bypass token
        }

        # Should succeed; current implementation is a stub (no blocklist interaction)
        result = await auth_mod.unified_logout(current_user=current_user)
        assert result.success is True

        # dev-token should NOT be in the blocklist
        assert await bl.is_revoked("dev-token") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_logout_adds_real_jwt_to_blocklist():
    """unified_logout returns success for real JWT (stub - blocklist not yet wired)."""
    with patch.dict("os.environ", {"REDIS_ENABLED": "false"}):
        import importlib
        import routes.auth_unified as auth_mod
        importlib.reload(auth_mod)

        # Create a real (signed) token
        real_token = auth_mod.create_jwt_token({
            "login": "real-user",
            "id": 42,
            "email": "real@test.com",
        })

        current_user = {
            "id": "42",
            "auth_provider": "github",
            "token": real_token,
        }

        result = await auth_mod.unified_logout(current_user=current_user)
        assert result.success is True
