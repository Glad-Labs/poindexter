"""
Unit tests for services/newsletter_service.py

Tests email sending, provider selection, subscriber fetching,
and graceful handling of disabled/misconfigured states.
All DB and email provider calls are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.newsletter_service import (  # noqa: E402
    _build_html,
    _get_active_subscribers,
    send_post_newsletter,
)
from services.site_config import SiteConfig

# #272 Phase-2b: newsletter_service no longer carries a lifespan-bound
# module global — every entry point takes a keyword-required
# ``site_config``. This module-level instance seeds the brand keys the
# HTML builder reads (site_url / company_name / site_name); tests pass it
# explicitly.
_SC = SiteConfig(initial_config={
    "site_url": "https://test.example.com",
    "company_name": "Test Company",
    "site_name": "Test Site",
})

# ---------------------------------------------------------------------------
# _build_html
# ---------------------------------------------------------------------------


_FAKE_TOKEN = "fake_unsubscribe_token_abcdef0123456789"


class TestBuildHtml:
    def test_includes_title_and_link(self):
        html = _build_html("My Post", "An excerpt", "my-post-slug", unsubscribe_token=_FAKE_TOKEN, site_config=_SC)
        assert "My Post" in html
        assert "my-post-slug" in html
        assert "test.example.com/posts/my-post-slug" in html

    def test_includes_first_name_greeting(self):
        html = _build_html("T", "E", "s", first_name="Matt", unsubscribe_token=_FAKE_TOKEN, site_config=_SC)
        assert "Hi Matt," in html

    def test_generic_greeting_when_no_name(self):
        html = _build_html("T", "E", "s", unsubscribe_token=_FAKE_TOKEN, site_config=_SC)
        assert "Hi there," in html

    def test_includes_unsubscribe_link(self):
        html = _build_html("T", "E", "s", unsubscribe_token=_FAKE_TOKEN, site_config=_SC)
        assert "unsubscribe" in html.lower()

    def test_unsubscribe_link_contains_per_subscriber_token(self):
        """Cycle-5 #252 — the email body's unsubscribe URL must carry
        the subscriber's token; without it the endpoint refuses the
        unsubscribe request."""
        html = _build_html("T", "E", "s", unsubscribe_token=_FAKE_TOKEN, site_config=_SC)
        assert f"token={_FAKE_TOKEN}" in html


# ---------------------------------------------------------------------------
# _get_active_subscribers
# ---------------------------------------------------------------------------


class TestGetActiveSubscribers:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@b.com", "first_name": "Alice", "unsubscribe_token": "tok_a"},
            {"id": 2, "email": "c@d.com", "first_name": None, "unsubscribe_token": "tok_b"},
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

    @pytest.mark.asyncio
    async def test_select_includes_unsubscribe_token(self):
        """Cycle-5 #252: every send needs the per-subscriber token to
        render the unsubscribe URL. Dropping the column from the SELECT
        would silently render emails with broken unsubscribe links."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await _get_active_subscribers(pool)
        sql = pool.fetch.await_args.args[0]
        assert "unsubscribe_token" in sql


# ---------------------------------------------------------------------------
# send_post_newsletter — disabled
# ---------------------------------------------------------------------------


class TestSendNewsletterDisabled:
    @pytest.mark.asyncio
    async def test_returns_skipped_when_disabled(self):
        pool = AsyncMock()
        mock_cfg = MagicMock()
        mock_cfg.get_bool.return_value = False
        mock_cfg.get.return_value = ""
        mock_cfg.get_int.return_value = 50
        mock_cfg.get_secret = AsyncMock(return_value="")
        result = await send_post_newsletter(pool, "Title", "Excerpt", "slug", site_config=mock_cfg)
        assert result["skipped_reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_returns_skipped_when_no_resend_key(self):
        pool = AsyncMock()
        mock_cfg = MagicMock()
        mock_cfg.get_bool.return_value = True
        mock_cfg.get.side_effect = lambda k, d="": {
            "newsletter_provider": "resend",
            "newsletter_from_email": "x@y.com",
            "newsletter_from_name": "Test",
            "smtp_host": "",
            "smtp_user": "",
        }.get(k, d)
        mock_cfg.get_int.return_value = 50
        # resend_api_key is is_secret=true — fetched via get_secret. This
        # test exercises the missing-key branch so return "" for both.
        mock_cfg.get_secret = AsyncMock(return_value="")
        result = await send_post_newsletter(pool, "T", "E", "s", site_config=mock_cfg)
        assert result["skipped_reason"] == "no_api_key"


# ---------------------------------------------------------------------------
# send_post_newsletter — success
# ---------------------------------------------------------------------------


class TestSendNewsletterSuccess:
    @pytest.mark.asyncio
    async def test_sends_to_all_subscribers(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@b.com", "first_name": "Alice", "unsubscribe_token": "tok_a"},
            {"id": 2, "email": "c@d.com", "first_name": "Bob", "unsubscribe_token": "tok_b"},
        ])
        pool.execute = AsyncMock()

        mock_cfg = MagicMock()
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
        }.get(k, d)
        mock_cfg.get_int.side_effect = lambda k, d=0: {
            "smtp_port": 587,
            "newsletter_batch_size": 50,
            "newsletter_batch_delay_seconds": 0,
        }.get(k, d)
        # smtp_password and resend_api_key are both is_secret=true rows
        # — fetched via the async get_secret path. Mock as a side-effect
        # function so resend_api_key still returns a usable value.
        async def _get_secret(k, d=""):
            return {
                "smtp_password": "",
                "resend_api_key": "re_test_key",
            }.get(k, d)
        mock_cfg.get_secret = AsyncMock(side_effect=_get_secret)

        with patch("services.newsletter_service._send_via_resend", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await send_post_newsletter(pool, "New Post", "Great stuff", "new-post", site_config=mock_cfg)

        assert result["sent"] == 2
        assert result["failed"] == 0
        assert result["total_subscribers"] == 2
        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_partial_failure(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@b.com", "first_name": "A", "unsubscribe_token": "tok_1"},
            {"id": 2, "email": "bad@fail.com", "first_name": "B", "unsubscribe_token": "tok_2"},
            {"id": 3, "email": "c@d.com", "first_name": "C", "unsubscribe_token": "tok_3"},
        ])
        pool.execute = AsyncMock()

        mock_cfg = MagicMock()
        mock_cfg.get_bool.side_effect = lambda k, d=False: {
            "newsletter_enabled": True,
            "smtp_use_tls": True,
        }.get(k, d)
        mock_cfg.get.side_effect = lambda k, d="": {
            "newsletter_provider": "resend",
            "newsletter_from_email": "x@y.com",
            "newsletter_from_name": "Test",
        }.get(k, d)
        mock_cfg.get_int.side_effect = lambda k, d=0: {
            "newsletter_batch_size": 50,
            "newsletter_batch_delay_seconds": 0,
        }.get(k, d)
        async def _get_secret(k, d=""):
            return {"smtp_password": "", "resend_api_key": "re_test_key"}.get(k, d)
        mock_cfg.get_secret = AsyncMock(side_effect=_get_secret)

        send_results = [True, False, True]
        with patch("services.newsletter_service._send_via_resend", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = send_results
            result = await send_post_newsletter(pool, "T", "E", "s", site_config=mock_cfg)

        assert result["sent"] == 2
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_no_subscribers_returns_zero(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        mock_cfg = MagicMock()
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
        mock_cfg.get_secret = AsyncMock(return_value="")

        result = await send_post_newsletter(pool, "T", "E", "s", site_config=mock_cfg)

        assert result["total_subscribers"] == 0
        assert result["sent"] == 0


# ---------------------------------------------------------------------------
# _send_via_resend
# ---------------------------------------------------------------------------


class TestSendViaResend:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        from services.newsletter_service import _send_via_resend

        cfg = {
            "resend_api_key": "test-key",
            "from_name": "Test",
            "from_email": "from@example.com",
        }

        fake_resend = MagicMock()
        fake_resend.api_key = ""
        fake_resend.Emails = MagicMock()
        fake_resend.Emails.send = MagicMock(return_value={"id": "msg-123"})

        with patch.dict("sys.modules", {"resend": fake_resend}):
            result = await _send_via_resend(cfg, "to@example.com", "Subject", "<html/>")

        assert result is True
        fake_resend.Emails.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_id_returns_false(self):
        from services.newsletter_service import _send_via_resend

        cfg = {
            "resend_api_key": "test-key",
            "from_name": "Test",
            "from_email": "from@example.com",
        }

        fake_resend = MagicMock()
        fake_resend.Emails = MagicMock()
        fake_resend.Emails.send = MagicMock(return_value={})

        with patch.dict("sys.modules", {"resend": fake_resend}):
            result = await _send_via_resend(cfg, "to@example.com", "S", "<html/>")

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        from services.newsletter_service import _send_via_resend

        cfg = {
            "resend_api_key": "test-key",
            "from_name": "Test",
            "from_email": "from@example.com",
        }

        fake_resend = MagicMock()
        fake_resend.Emails = MagicMock()
        fake_resend.Emails.send = MagicMock(side_effect=RuntimeError("api down"))

        with patch.dict("sys.modules", {"resend": fake_resend}):
            result = await _send_via_resend(cfg, "to@example.com", "S", "<html/>")

        assert result is False


# ---------------------------------------------------------------------------
# _send_via_smtp
# ---------------------------------------------------------------------------


class TestSendViaSmtp:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        from services.newsletter_service import _send_via_smtp

        cfg = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user",
            "smtp_password": "pass",
            "smtp_use_tls": True,
            "from_name": "Test",
            "from_email": "from@example.com",
        }

        fake_aiosmtplib = MagicMock()
        fake_aiosmtplib.send = AsyncMock()

        with patch.dict("sys.modules", {"aiosmtplib": fake_aiosmtplib}):
            result = await _send_via_smtp(
                cfg, "to@example.com", "Subject", "<html/>",
                unsubscribe_token=_FAKE_TOKEN,
                site_config=_SC,
            )

        assert result is True
        fake_aiosmtplib.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        from services.newsletter_service import _send_via_smtp

        cfg = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",
            "smtp_use_tls": True,
            "from_name": "Test",
            "from_email": "from@example.com",
        }

        fake_aiosmtplib = MagicMock()
        fake_aiosmtplib.send = AsyncMock(side_effect=ConnectionError("smtp down"))

        with patch.dict("sys.modules", {"aiosmtplib": fake_aiosmtplib}):
            result = await _send_via_smtp(
                cfg, "to@example.com", "S", "<html/>",
                unsubscribe_token=_FAKE_TOKEN,
                site_config=_SC,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_user_passes_none_to_aiosmtplib(self):
        """Empty smtp_user should be passed as None (not empty string) to aiosmtplib."""
        from services.newsletter_service import _send_via_smtp

        cfg = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 25,
            "smtp_user": "",  # empty
            "smtp_password": "",  # empty
            "smtp_use_tls": False,
            "from_name": "Test",
            "from_email": "from@example.com",
        }

        fake_aiosmtplib = MagicMock()
        fake_aiosmtplib.send = AsyncMock()

        with patch.dict("sys.modules", {"aiosmtplib": fake_aiosmtplib}):
            await _send_via_smtp(
                cfg, "to@example.com", "S", "<html/>",
                unsubscribe_token=_FAKE_TOKEN,
                site_config=_SC,
            )

        kwargs = fake_aiosmtplib.send.await_args.kwargs
        assert kwargs["username"] is None
        assert kwargs["password"] is None


# ---------------------------------------------------------------------------
# _log_send
# ---------------------------------------------------------------------------


class TestLogSend:
    @pytest.mark.asyncio
    async def test_writes_to_campaign_email_logs(self):
        from services.newsletter_service import _log_send

        pool = AsyncMock()
        pool.execute = AsyncMock()

        await _log_send(pool, 42, "Subject", "delivered")

        pool.execute.assert_awaited_once()
        args = pool.execute.await_args.args
        assert "INSERT INTO campaign_email_logs" in args[0]
        assert args[1] == 42
        assert args[2] == "post_published"
        assert args[3] == "Subject"
        assert args[4] == "delivered"
        assert args[5] is None  # error

    @pytest.mark.asyncio
    async def test_with_error_message(self):
        from services.newsletter_service import _log_send

        pool = AsyncMock()
        pool.execute = AsyncMock()

        await _log_send(pool, 7, "S", "failed", "smtp_timeout")

        args = pool.execute.await_args.args
        assert args[5] == "smtp_timeout"

    @pytest.mark.asyncio
    async def test_db_exception_swallowed(self):
        """Logging failure must not block the send pipeline."""
        from services.newsletter_service import _log_send

        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("logs table missing"))

        # Should not raise
        await _log_send(pool, 1, "S", "delivered")


# ---------------------------------------------------------------------------
# send_post_newsletter — additional paths
# ---------------------------------------------------------------------------


class TestSendNewsletterSmtpProvider:
    @pytest.mark.asyncio
    async def test_no_smtp_host_returns_skipped(self):
        from services.newsletter_service import send_post_newsletter

        pool = AsyncMock()

        mock_cfg = MagicMock()
        mock_cfg.get_bool.side_effect = lambda k, d=False: {
            "newsletter_enabled": True,
        }.get(k, d)
        mock_cfg.get.side_effect = lambda k, d="": {
            "newsletter_provider": "smtp",
            "smtp_host": "",  # missing
        }.get(k, d)
        mock_cfg.get_int.side_effect = lambda k, d=0: d
        mock_cfg.get_secret = AsyncMock(return_value="")

        result = await send_post_newsletter(pool, "T", "E", "s", site_config=mock_cfg)

        assert result["sent"] == 0
        assert result.get("skipped_reason") == "no_smtp_host"

    @pytest.mark.asyncio
    async def test_smtp_provider_routes_to_smtp_function(self):
        from services.newsletter_service import send_post_newsletter

        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@x.com", "first_name": "Alice", "unsubscribe_token": "tok_smtp"},
        ])
        pool.execute = AsyncMock()

        mock_cfg = MagicMock()
        mock_cfg.get_bool.side_effect = lambda k, d=False: {
            "newsletter_enabled": True, "smtp_use_tls": True,
        }.get(k, d)
        mock_cfg.get.side_effect = lambda k, d="": {
            "newsletter_provider": "smtp",
            "smtp_host": "smtp.example.com",
            "newsletter_from_email": "from@x.com",
            "newsletter_from_name": "Test",
            "smtp_user": "user",
        }.get(k, d)
        mock_cfg.get_int.side_effect = lambda k, d=0: d
        # smtp_password is now a secret — fetched via the async path.
        mock_cfg.get_secret = AsyncMock(return_value="pass")

        with patch("services.newsletter_service._send_via_smtp", new_callable=AsyncMock) as mock_smtp, \
             patch("services.newsletter_service._send_via_resend", new_callable=AsyncMock) as mock_resend:
            mock_smtp.return_value = True
            mock_resend.return_value = True

            result = await send_post_newsletter(pool, "T", "E", "s", site_config=mock_cfg)

        assert result["sent"] == 1
        mock_smtp.assert_awaited_once()
        mock_resend.assert_not_awaited()
