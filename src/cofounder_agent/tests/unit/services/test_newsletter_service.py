"""
Unit tests for services/newsletter_service.py

Tests email sending, provider selection, subscriber fetching,
and graceful handling of disabled/misconfigured states.
All DB and email provider calls are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure site_config has test values before importing newsletter_service
from services.site_config import site_config
site_config._config["site_url"] = "https://test.example.com"
site_config._config["company_name"] = "Test Company"

from services.newsletter_service import (
    _build_html,
    _get_active_subscribers,
    send_post_newsletter,
)


# ---------------------------------------------------------------------------
# _build_html
# ---------------------------------------------------------------------------


class TestBuildHtml:
    def test_includes_title_and_link(self):
        html = _build_html("My Post", "An excerpt", "my-post-slug")
        assert "My Post" in html
        assert "my-post-slug" in html
        assert "test.example.com/posts/my-post-slug" in html

    def test_includes_first_name_greeting(self):
        html = _build_html("T", "E", "s", first_name="Matt")
        assert "Hi Matt," in html

    def test_generic_greeting_when_no_name(self):
        html = _build_html("T", "E", "s")
        assert "Hi there," in html

    def test_includes_unsubscribe_link(self):
        html = _build_html("T", "E", "s")
        assert "unsubscribe" in html.lower()


# ---------------------------------------------------------------------------
# _get_active_subscribers
# ---------------------------------------------------------------------------


class TestGetActiveSubscribers:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@b.com", "first_name": "Alice"},
            {"id": 2, "email": "c@d.com", "first_name": None},
        ])
        result = await _get_active_subscribers(pool)
        assert len(result) == 2
        assert result[0]["email"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_subscribers(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        result = await _get_active_subscribers(pool)
        assert result == []


# ---------------------------------------------------------------------------
# send_post_newsletter — disabled
# ---------------------------------------------------------------------------


class TestSendNewsletterDisabled:
    @pytest.mark.asyncio
    async def test_returns_skipped_when_disabled(self):
        pool = AsyncMock()
        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_bool.return_value = False
            mock_cfg.get.return_value = ""
            mock_cfg.get_int.return_value = 50
            result = await send_post_newsletter(pool, "Title", "Excerpt", "slug")
        assert result["skipped_reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_returns_skipped_when_no_resend_key(self):
        pool = AsyncMock()
        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_bool.return_value = True
            mock_cfg.get.side_effect = lambda k, d="": {
                "newsletter_provider": "resend",
                "newsletter_from_email": "x@y.com",
                "newsletter_from_name": "Test",
                "resend_api_key": "",
                "smtp_host": "",
                "smtp_user": "",
                "smtp_password": "",
            }.get(k, d)
            mock_cfg.get_int.return_value = 50
            result = await send_post_newsletter(pool, "T", "E", "s")
        assert result["skipped_reason"] == "no_api_key"


# ---------------------------------------------------------------------------
# send_post_newsletter — success
# ---------------------------------------------------------------------------


class TestSendNewsletterSuccess:
    @pytest.mark.asyncio
    async def test_sends_to_all_subscribers(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@b.com", "first_name": "Alice"},
            {"id": 2, "email": "c@d.com", "first_name": "Bob"},
        ])
        pool.execute = AsyncMock()

        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_bool.side_effect = lambda k, d=False: {
                "newsletter_enabled": True,
                "smtp_use_tls": True,
            }.get(k, d)
            mock_cfg.get.side_effect = lambda k, d="": {
                "newsletter_provider": "resend",
                "newsletter_from_email": "x@y.com",
                "newsletter_from_name": "Test",
                "resend_api_key": "re_test_key",
                "smtp_host": "",
                "smtp_user": "",
                "smtp_password": "",
            }.get(k, d)
            mock_cfg.get_int.side_effect = lambda k, d=0: {
                "smtp_port": 587,
                "newsletter_batch_size": 50,
                "newsletter_batch_delay_seconds": 0,
            }.get(k, d)

            with patch("services.newsletter_service._send_via_resend", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = True
                result = await send_post_newsletter(pool, "New Post", "Great stuff", "new-post")

        assert result["sent"] == 2
        assert result["failed"] == 0
        assert result["total_subscribers"] == 2
        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_partial_failure(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@b.com", "first_name": "A"},
            {"id": 2, "email": "bad@fail.com", "first_name": "B"},
            {"id": 3, "email": "c@d.com", "first_name": "C"},
        ])
        pool.execute = AsyncMock()

        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_bool.side_effect = lambda k, d=False: {
                "newsletter_enabled": True,
                "smtp_use_tls": True,
            }.get(k, d)
            mock_cfg.get.side_effect = lambda k, d="": {
                "newsletter_provider": "resend",
                "newsletter_from_email": "x@y.com",
                "newsletter_from_name": "Test",
                "resend_api_key": "re_test_key",
            }.get(k, d)
            mock_cfg.get_int.side_effect = lambda k, d=0: {
                "newsletter_batch_size": 50,
                "newsletter_batch_delay_seconds": 0,
            }.get(k, d)

            send_results = [True, False, True]
            with patch("services.newsletter_service._send_via_resend", new_callable=AsyncMock) as mock_send:
                mock_send.side_effect = send_results
                result = await send_post_newsletter(pool, "T", "E", "s")

        assert result["sent"] == 2
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_no_subscribers_returns_zero(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        with patch("services.site_config.site_config") as mock_cfg:
            mock_cfg.get_bool.side_effect = lambda k, d=False: {
                "newsletter_enabled": True,
            }.get(k, d)
            mock_cfg.get.side_effect = lambda k, d="": {
                "newsletter_provider": "resend",
                "resend_api_key": "key",
                "newsletter_from_email": "x@y.com",
                "newsletter_from_name": "Test",
            }.get(k, d)
            mock_cfg.get_int.side_effect = lambda k, d=0: {
                "newsletter_batch_size": 50,
                "newsletter_batch_delay_seconds": 0,
            }.get(k, d)

            result = await send_post_newsletter(pool, "T", "E", "s")

        assert result["total_subscribers"] == 0
        assert result["sent"] == 0
