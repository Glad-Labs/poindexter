"""
Unit tests for services/google_oauth.py

Tests GoogleOAuthProvider: initialization, authorization URL, token exchange,
and user info retrieval. HTTP calls are mocked.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.oauth_provider import OAuthException, OAuthUser


def make_google_provider(monkeypatch, client_id="google-id", client_secret="google-secret"):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", client_id)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", client_secret)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    from services.google_oauth import GoogleOAuthProvider
    return GoogleOAuthProvider()


def make_mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


class TestGoogleOAuthProviderInit:
    def test_initializes_with_env_vars(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        assert provider.client_id == "google-id"
        assert provider.client_secret == "google-secret"

    def test_raises_if_client_id_missing(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
        from services.google_oauth import GoogleOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            GoogleOAuthProvider()

    def test_raises_if_client_secret_missing(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        from services.google_oauth import GoogleOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            GoogleOAuthProvider()

    def test_default_redirect_uri(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        assert "localhost:8000" in provider.redirect_uri


class TestGoogleAuthorizationUrl:
    def test_url_starts_with_google_auth(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        url = provider.get_authorization_url("state-token")
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    def test_url_contains_client_id(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "google-id" in url

    def test_url_contains_state(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        url = provider.get_authorization_url("my-csrf-state")
        assert "my-csrf-state" in url

    def test_url_contains_openid_scope(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "openid" in url

    def test_url_contains_offline_access(self, monkeypatch):
        """Refresh token requested via access_type=offline."""
        provider = make_google_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "offline" in url


class TestGoogleExchangeCode:
    def test_successful_exchange(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        mock_resp = make_mock_response({"access_token": "ya29.test_token"})
        with patch("httpx.post", return_value=mock_resp):
            token = provider.exchange_code_for_token("auth-code")
        assert token == "ya29.test_token"

    def test_missing_access_token_raises(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        mock_resp = make_mock_response({"token_type": "Bearer"})
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OAuthException, match="No access token"):
                provider.exchange_code_for_token("code")

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        with patch("httpx.post", side_effect=httpx.HTTPError("401 Unauthorized")):
            with pytest.raises(OAuthException, match="Failed to exchange"):
                provider.exchange_code_for_token("bad-code")


class TestGoogleGetUserInfo:
    SAMPLE_USER = {
        "sub": "1234567890",
        "email": "user@gmail.com",
        "name": "Test User",
        "picture": "https://lh3.googleusercontent.com/photo.jpg",
    }

    def test_successful_user_info(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("ya29.token")
        assert isinstance(user, OAuthUser)
        assert user.provider == "google"
        assert user.provider_id == "1234567890"
        assert user.email == "user@gmail.com"
        assert user.display_name == "Test User"
        assert user.avatar_url == "https://lh3.googleusercontent.com/photo.jpg"

    def test_raw_data_included(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")
        assert user.raw_data == self.SAMPLE_USER

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_google_provider(monkeypatch)
        with patch("httpx.get", side_effect=httpx.HTTPError("403")):
            with pytest.raises(OAuthException, match="Failed to fetch user info"):
                provider.get_user_info("token")
