"""
Unit tests for services/oauth_provider.py.

Tests cover:
- OAuthException — is an Exception subclass, can be raised with a message
- OAuthUser — dataclass construction, optional fields, raw_data field
- OAuthProvider — abstract methods enforced; concrete subclass works correctly

No network calls, no DB, no env vars.
"""

import pytest
from services.oauth_provider import OAuthException, OAuthProvider, OAuthUser


# ---------------------------------------------------------------------------
# OAuthException
# ---------------------------------------------------------------------------


class TestOAuthException:
    def test_is_exception_subclass(self):
        assert issubclass(OAuthException, Exception)

    def test_can_be_raised_with_message(self):
        with pytest.raises(OAuthException, match="token exchange failed"):
            raise OAuthException("token exchange failed")

    def test_can_be_raised_and_caught_as_exception(self):
        try:
            raise OAuthException("error")
        except Exception as exc:
            assert isinstance(exc, OAuthException)
            assert str(exc) == "error"


# ---------------------------------------------------------------------------
# OAuthUser
# ---------------------------------------------------------------------------


class TestOAuthUser:
    def test_required_fields(self):
        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="user@example.com",
            display_name="Test User",
        )
        assert user.provider == "github"
        assert user.provider_id == "12345"
        assert user.email == "user@example.com"
        assert user.display_name == "Test User"

    def test_optional_fields_default_to_none(self):
        user = OAuthUser(
            provider="google",
            provider_id="67890",
            email="user@google.com",
            display_name="Google User",
        )
        assert user.avatar_url is None
        assert user.raw_data is None

    def test_optional_fields_can_be_set(self):
        raw = {"id": "123", "login": "user"}
        user = OAuthUser(
            provider="github",
            provider_id="123",
            email="e@g.com",
            display_name="User",
            avatar_url="https://example.com/avatar.png",
            raw_data=raw,
        )
        assert user.avatar_url == "https://example.com/avatar.png"
        assert user.raw_data == raw

    def test_providers_supported(self):
        """Test common provider identifiers can be stored."""
        for provider in ("github", "google", "facebook", "microsoft"):
            user = OAuthUser(
                provider=provider,
                provider_id="id",
                email="e@x.com",
                display_name="User",
            )
            assert user.provider == provider


# ---------------------------------------------------------------------------
# OAuthProvider (abstract base class)
# ---------------------------------------------------------------------------


class TestOAuthProvider:
    def test_cannot_instantiate_directly(self):
        """OAuthProvider is abstract — direct instantiation must raise TypeError."""
        with pytest.raises(TypeError):
            OAuthProvider()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_all_methods(self):
        """Partial implementations should also raise TypeError."""

        class PartialProvider(OAuthProvider):
            def get_authorization_url(self, state: str) -> str:
                return f"https://example.com/login?state={state}"

            # Missing exchange_code_for_token and get_user_info

        with pytest.raises(TypeError):
            PartialProvider()  # type: ignore[abstract]

    def test_full_concrete_subclass_works(self):
        """A fully implemented subclass can be instantiated and methods return expected values."""

        class MockOAuthProvider(OAuthProvider):
            def get_authorization_url(self, state: str) -> str:
                return f"https://example.com/auth?state={state}"

            def exchange_code_for_token(self, code: str) -> str:
                return f"token-{code}"

            def get_user_info(self, access_token: str) -> OAuthUser:
                return OAuthUser(
                    provider="mock",
                    provider_id="user-1",
                    email="user@mock.com",
                    display_name="Mock User",
                    raw_data={"token": access_token},
                )

        provider = MockOAuthProvider()

        url = provider.get_authorization_url("csrf-state")
        assert "csrf-state" in url

        token = provider.exchange_code_for_token("auth-code-xyz")
        assert token == "token-auth-code-xyz"

        user = provider.get_user_info("my-access-token")
        assert isinstance(user, OAuthUser)
        assert user.provider == "mock"
        assert user.email == "user@mock.com"
        assert user.raw_data is not None and user.raw_data["token"] == "my-access-token"

    def test_provider_can_raise_oauth_exception(self):
        """Providers should raise OAuthException on failure."""

        class FailingProvider(OAuthProvider):
            def get_authorization_url(self, state: str) -> str:
                return "https://x.com/auth"

            def exchange_code_for_token(self, code: str) -> str:
                raise OAuthException(f"Invalid code: {code}")

            def get_user_info(self, access_token: str) -> OAuthUser:
                raise OAuthException("Token expired")

        provider = FailingProvider()

        with pytest.raises(OAuthException, match="Invalid code"):
            provider.exchange_code_for_token("bad-code")

        with pytest.raises(OAuthException, match="Token expired"):
            provider.get_user_info("expired-token")
