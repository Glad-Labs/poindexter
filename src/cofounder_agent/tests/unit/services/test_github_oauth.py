"""
Unit tests for services/github_oauth.py

Tests GitHubOAuthProvider: initialization, authorization URL generation,
token exchange, and user info retrieval. HTTP calls are mocked.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.oauth_provider import OAuthException, OAuthUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_github_provider(monkeypatch, client_id="gh-client-id", client_secret="gh-secret"):
    """Create a GitHubOAuthProvider with env vars set."""
    monkeypatch.setenv("GH_OAUTH_CLIENT_ID", client_id)
    monkeypatch.setenv("GH_OAUTH_CLIENT_SECRET", client_secret)
    monkeypatch.setenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/auth/callback/github")
    from services.github_oauth import GitHubOAuthProvider
    return GitHubOAuthProvider()


def make_mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestGitHubOAuthProviderInit:
    def test_initializes_with_env_vars(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        assert provider.client_id == "gh-client-id"
        assert provider.client_secret == "gh-secret"

    def test_raises_if_client_id_missing(self, monkeypatch):
        monkeypatch.delenv("GH_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.setenv("GH_OAUTH_CLIENT_SECRET", "secret")
        from services.github_oauth import GitHubOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            GitHubOAuthProvider()

    def test_raises_if_client_secret_missing(self, monkeypatch):
        monkeypatch.setenv("GH_OAUTH_CLIENT_ID", "id")
        monkeypatch.delenv("GH_OAUTH_CLIENT_SECRET", raising=False)
        from services.github_oauth import GitHubOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            GitHubOAuthProvider()

    def test_redirect_uri_defaults_when_not_set(self, monkeypatch):
        monkeypatch.setenv("GH_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("GH_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.delenv("GITHUB_REDIRECT_URI", raising=False)
        from services.github_oauth import GitHubOAuthProvider
        provider = GitHubOAuthProvider()
        assert "localhost:8000" in provider.redirect_uri


# ---------------------------------------------------------------------------
# get_authorization_url
# ---------------------------------------------------------------------------


class TestGitHubAuthorizationUrl:
    def test_url_contains_client_id(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        url = provider.get_authorization_url("csrf-state-token")
        assert "gh-client-id" in url

    def test_url_contains_state(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        url = provider.get_authorization_url("my-state-token")
        assert "my-state-token" in url

    def test_url_starts_with_github_auth(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert url.startswith("https://github.com/login/oauth/authorize")

    def test_url_contains_email_scope(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "user:email" in url

    def test_url_contains_redirect_uri(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "localhost" in url


# ---------------------------------------------------------------------------
# exchange_code_for_token
# ---------------------------------------------------------------------------


class TestGitHubExchangeCode:
    def test_successful_exchange_returns_token(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        mock_resp = make_mock_response({"access_token": "gho_test_token_123"})

        with patch("httpx.post", return_value=mock_resp):
            token = provider.exchange_code_for_token("auth-code-123")

        assert token == "gho_test_token_123"

    def test_error_in_response_raises_oauth_exception(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        mock_resp = make_mock_response(
            {"error": "bad_verification_code", "error_description": "Code was invalid"}
        )

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OAuthException, match="Code was invalid"):
                provider.exchange_code_for_token("invalid-code")

    def test_missing_access_token_raises_exception(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        mock_resp = make_mock_response({"token_type": "bearer"})

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OAuthException, match="No access token"):
                provider.exchange_code_for_token("code")

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_github_provider(monkeypatch)

        with patch("httpx.post", side_effect=httpx.HTTPError("connection refused")):
            with pytest.raises(OAuthException, match="Failed to exchange code"):
                provider.exchange_code_for_token("code")


# ---------------------------------------------------------------------------
# get_user_info
# ---------------------------------------------------------------------------


class TestGitHubGetUserInfo:
    SAMPLE_USER_DATA = {
        "id": 12345,
        "login": "testuser",
        "name": "Test User",
        "email": "test@example.com",
        "avatar_url": "https://avatars.githubusercontent.com/u/12345",
    }

    def test_successful_user_info_returns_oauth_user(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER_DATA)

        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("gho_token_123")

        assert isinstance(user, OAuthUser)
        assert user.provider == "github"
        assert user.provider_id == "12345"
        assert user.email == "test@example.com"
        assert user.display_name == "Test User"
        assert user.avatar_url == "https://avatars.githubusercontent.com/u/12345"

    def test_no_email_uses_login_fallback(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        user_data = {**self.SAMPLE_USER_DATA, "email": None, "login": "octocat"}
        mock_resp = make_mock_response(user_data)

        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")

        assert user.email == "octocat@github.local"

    def test_no_name_uses_login_as_display_name(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        user_data = {**self.SAMPLE_USER_DATA, "name": None, "login": "octocat"}
        mock_resp = make_mock_response(user_data)

        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")

        assert user.display_name == "octocat"

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_github_provider(monkeypatch)

        with patch("httpx.get", side_effect=httpx.HTTPError("403 Forbidden")):
            with pytest.raises(OAuthException, match="Failed to fetch user info"):
                provider.get_user_info("bad-token")

    def test_raw_data_included_in_user(self, monkeypatch):
        provider = make_github_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER_DATA)

        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")

        assert user.raw_data == self.SAMPLE_USER_DATA
