"""
Unit tests for services/facebook_oauth.py

Tests FacebookOAuthProvider: initialization, authorization URL, token exchange,
and user info retrieval. HTTP calls are mocked.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.oauth_provider import OAuthException, OAuthUser


def make_fb_provider(monkeypatch, client_id="fb-app-id", client_secret="fb-app-secret"):
    monkeypatch.setenv("FACEBOOK_CLIENT_ID", client_id)
    monkeypatch.setenv("FACEBOOK_CLIENT_SECRET", client_secret)
    monkeypatch.delenv("FACEBOOK_REDIRECT_URI", raising=False)
    from services.facebook_oauth import FacebookOAuthProvider
    return FacebookOAuthProvider()


def make_mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


class TestFacebookOAuthProviderInit:
    def test_initializes_with_env_vars(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        assert provider.client_id == "fb-app-id"
        assert provider.client_secret == "fb-app-secret"

    def test_raises_if_client_id_missing(self, monkeypatch):
        monkeypatch.delenv("FACEBOOK_CLIENT_ID", raising=False)
        monkeypatch.setenv("FACEBOOK_CLIENT_SECRET", "secret")
        from services.facebook_oauth import FacebookOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            FacebookOAuthProvider()

    def test_raises_if_client_secret_missing(self, monkeypatch):
        monkeypatch.setenv("FACEBOOK_CLIENT_ID", "id")
        monkeypatch.delenv("FACEBOOK_CLIENT_SECRET", raising=False)
        from services.facebook_oauth import FacebookOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            FacebookOAuthProvider()

    def test_default_redirect_uri_contains_facebook(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        assert "facebook" in provider.redirect_uri


class TestFacebookAuthorizationUrl:
    def test_url_starts_with_facebook(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        url = provider.get_authorization_url("csrf-state")
        assert url.startswith("https://www.facebook.com")

    def test_url_contains_client_id(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "fb-app-id" in url

    def test_url_contains_state(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        url = provider.get_authorization_url("my-unique-state")
        assert "my-unique-state" in url

    def test_url_contains_email_scope(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "email" in url


class TestFacebookExchangeCode:
    def test_successful_exchange(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        mock_resp = make_mock_response({"access_token": "EAA_fb_token"})
        with patch("httpx.get", return_value=mock_resp):
            token = provider.exchange_code_for_token("fb-auth-code")
        assert token == "EAA_fb_token"

    def test_error_in_response_raises(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        mock_resp = make_mock_response({
            "error": {"message": "Invalid OAuth access token", "code": 190}
        })
        with patch("httpx.get", return_value=mock_resp):
            with pytest.raises(OAuthException, match="Invalid OAuth"):
                provider.exchange_code_for_token("bad-code")

    def test_missing_token_raises(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        mock_resp = make_mock_response({"token_type": "bearer"})
        with patch("httpx.get", return_value=mock_resp):
            with pytest.raises(OAuthException, match="No access token"):
                provider.exchange_code_for_token("code")

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        with patch("httpx.get", side_effect=httpx.HTTPError("403 Forbidden")):
            with pytest.raises(OAuthException, match="Failed to exchange"):
                provider.exchange_code_for_token("code")


class TestFacebookGetUserInfo:
    SAMPLE_USER = {
        "id": "fb-user-12345",
        "email": "user@example.com",
        "name": "Facebook User",
        "picture": {
            "data": {"url": "https://graph.facebook.com/photo.jpg"}
        },
    }

    def test_successful_user_info(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("EAA_token")
        assert isinstance(user, OAuthUser)
        assert user.provider == "facebook"
        assert user.provider_id == "fb-user-12345"
        assert user.email == "user@example.com"
        assert user.display_name == "Facebook User"
        assert user.avatar_url == "https://graph.facebook.com/photo.jpg"

    def test_no_picture_in_response(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        user_data = {**self.SAMPLE_USER}
        del user_data["picture"]
        mock_resp = make_mock_response(user_data)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")
        assert user.avatar_url is None

    def test_error_in_response_raises(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        mock_resp = make_mock_response({"error": {"message": "Token expired"}})
        with patch("httpx.get", return_value=mock_resp):
            with pytest.raises(OAuthException, match="Token expired"):
                provider.get_user_info("expired-token")

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        with patch("httpx.get", side_effect=httpx.HTTPError("500 Server Error")):
            with pytest.raises(OAuthException, match="Failed to fetch user info"):
                provider.get_user_info("token")

    def test_raw_data_included(self, monkeypatch):
        provider = make_fb_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")
        assert user.raw_data == self.SAMPLE_USER
