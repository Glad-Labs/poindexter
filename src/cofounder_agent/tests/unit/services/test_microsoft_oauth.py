"""
Unit tests for services/microsoft_oauth.py

Tests MicrosoftOAuthProvider: initialization, authorization URL, token exchange,
and user info retrieval. HTTP calls are mocked.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.oauth_provider import OAuthException, OAuthUser


def make_ms_provider(
    monkeypatch,
    client_id="ms-client-id",
    client_secret="ms-client-secret",
    tenant_id="common",
):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", client_id)
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", client_secret)
    monkeypatch.setenv("MICROSOFT_TENANT_ID", tenant_id)
    monkeypatch.delenv("MICROSOFT_REDIRECT_URI", raising=False)
    from services.microsoft_oauth import MicrosoftOAuthProvider
    return MicrosoftOAuthProvider()


def make_mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


class TestMicrosoftOAuthProviderInit:
    def test_initializes_with_env_vars(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        assert provider.client_id == "ms-client-id"
        assert provider.client_secret == "ms-client-secret"
        assert provider.tenant_id == "common"

    def test_raises_if_client_id_missing(self, monkeypatch):
        monkeypatch.delenv("MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("MICROSOFT_TENANT_ID", "common")
        from services.microsoft_oauth import MicrosoftOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            MicrosoftOAuthProvider()

    def test_raises_if_client_secret_missing(self, monkeypatch):
        monkeypatch.setenv("MICROSOFT_CLIENT_ID", "id")
        monkeypatch.delenv("MICROSOFT_CLIENT_SECRET", raising=False)
        monkeypatch.setenv("MICROSOFT_TENANT_ID", "common")
        from services.microsoft_oauth import MicrosoftOAuthProvider
        with pytest.raises(OAuthException, match="not configured"):
            MicrosoftOAuthProvider()

    def test_endpoints_include_tenant_id(self, monkeypatch):
        provider = make_ms_provider(monkeypatch, tenant_id="my-tenant-12345")
        assert "my-tenant-12345" in provider.authorization_url
        assert "my-tenant-12345" in provider.token_url

    def test_default_redirect_uri_contains_microsoft(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        assert "microsoft" in provider.redirect_uri


class TestMicrosoftAuthorizationUrl:
    def test_url_starts_with_microsoft_login(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        url = provider.get_authorization_url("state-token")
        assert url.startswith("https://login.microsoftonline.com")

    def test_url_contains_client_id(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "ms-client-id" in url

    def test_url_contains_state(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        url = provider.get_authorization_url("unique-csrf-state")
        assert "unique-csrf-state" in url

    def test_url_contains_openid_scope(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "openid" in url

    def test_url_contains_prompt_select_account(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        url = provider.get_authorization_url("state")
        assert "select_account" in url


class TestMicrosoftExchangeCode:
    def test_successful_exchange(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        mock_resp = make_mock_response({"access_token": "eyJ0_ms_token"})
        with patch("httpx.post", return_value=mock_resp):
            token = provider.exchange_code_for_token("ms-auth-code")
        assert token == "eyJ0_ms_token"

    def test_error_in_response_raises(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        mock_resp = make_mock_response({
            "error": "invalid_grant",
            "error_description": "AADSTS70008: Expired auth code",
        })
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OAuthException, match="AADSTS70008"):
                provider.exchange_code_for_token("expired-code")

    def test_missing_token_raises(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        mock_resp = make_mock_response({"token_type": "Bearer"})
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OAuthException, match="No access token"):
                provider.exchange_code_for_token("code")

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        with patch("httpx.post", side_effect=httpx.HTTPError("401 Unauthorized")):
            with pytest.raises(OAuthException, match="Failed to exchange"):
                provider.exchange_code_for_token("code")


class TestMicrosoftGetUserInfo:
    SAMPLE_USER = {
        "id": "ms-user-abc123",
        "mail": "user@company.com",
        "displayName": "Microsoft User",
        "userPrincipalName": "user@company.onmicrosoft.com",
    }

    def test_successful_user_info(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("eyJ0_token")
        assert isinstance(user, OAuthUser)
        assert user.provider == "microsoft"
        assert user.provider_id == "ms-user-abc123"
        assert user.email == "user@company.com"
        assert user.display_name == "Microsoft User"
        # Microsoft Graph doesn't include avatar in basic call
        assert user.avatar_url is None

    def test_falls_back_to_user_principal_name_if_no_mail(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        user_data = {**self.SAMPLE_USER, "mail": None}
        mock_resp = make_mock_response(user_data)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")
        assert user.email == "user@company.onmicrosoft.com"

    def test_error_in_response_raises(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        mock_resp = make_mock_response({"error": {"message": "Access token expired"}})
        with patch("httpx.get", return_value=mock_resp):
            with pytest.raises(OAuthException, match="Access token expired"):
                provider.get_user_info("expired-token")

    def test_http_error_raises_oauth_exception(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        with patch("httpx.get", side_effect=httpx.HTTPError("404 Not Found")):
            with pytest.raises(OAuthException, match="Failed to fetch user info"):
                provider.get_user_info("token")

    def test_raw_data_included(self, monkeypatch):
        provider = make_ms_provider(monkeypatch)
        mock_resp = make_mock_response(self.SAMPLE_USER)
        with patch("httpx.get", return_value=mock_resp):
            user = provider.get_user_info("token")
        assert user.raw_data == self.SAMPLE_USER
