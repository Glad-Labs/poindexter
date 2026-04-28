"""
Unit tests for services/newsletter_service.py

Tests email sending, provider selection, subscriber fetching,
and graceful handling of disabled/misconfigured states.
All DB and email provider calls are mocked. Post-Phase-H, site_config
is passed in as a parameter rather than read from the module singleton —
tests build a ``MagicMock`` shaped like SiteConfig and pass it through.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.newsletter_service import (
    _build_html,
    _cfg,
    _get_active_subscribers,
    send_post_newsletter,
)


def _mock_site_config(
    *,
    enabled: bool = True,
    provider: str = "resend",
    resend_api_key: str = "re_test_key",
    smtp_host: str = "",
    site_url: str = "https://test.example.com",
    company_name: str = "Test Company",
    site_name: str = "Test Site",
    batch_delay: int = 0,
) -> MagicMock:
    """Build a MagicMock with the methods newsletter_service calls.

    GH-107 / poindexter#156: ``resend_api_key`` is encrypted at rest
    (``is_secret=true``). Production code reads it via the async
    ``site_config.get_secret(...)``. The mock here serves the
    plaintext through ``get_secret`` and a ``enc:v1:<ciphertext>``
    blob through the sync ``.get()`` so any regression that drops the
    await would surface as a Resend 401 in the assertions.
    """
    sc = MagicMock()

    def _get_bool(k: str, d: bool = False) -> bool:
        return {
            "newsletter_enabled": enabled,
            "smtp_use_tls": True,
        }.get(k, d)

    def _get(k: str, d: str = "") -> str:
        sync_overrides = {
            # Regression bait — if production reverts to sync .get() on
            # the encrypted resend_api_key, this is what would land in
            # the Resend Authorization header.
            "resend_api_key": (
                f"enc:v1:CIPHERTEXT_FOR_{resend_api_key}" if resend_api_key else ""
            ),
        }
        if k in sync_overrides:
            return sync_overrides[k]
        return {
            "newsletter_provider": provider,
            "newsletter_from_email": "from@test.com",
            "newsletter_from_name": "Test Sender",
            "smtp_host": smtp_host,
            "smtp_user": "user",
            "smtp_password": "pass",
            "company_name": company_name,
            "site_name": site_name,
        }.get(k, d)

    async def _get_secret(k: str, d: str = "") -> str:
        return {
            "resend_api_key": resend_api_key,
        }.get(k, d)

    def _get_int(k: str, d: int = 0) -> int:
        return {
            "smtp_port": 587,
            "newsletter_batch_size": 50,
            "newsletter_batch_delay_seconds": batch_delay,
        }.get(k, d)

    sc.get_bool.side_effect = _get_bool
    sc.get.side_effect = _get
    sc.get_secret = AsyncMock(side_effect=_get_secret)
    sc.get_int.side_effect = _get_int
    sc.require.side_effect = lambda k: site_url if k == "site_url" else ""
    return sc


_TEST_CFG = {
    "site_url": "https://test.example.com",
    "company_name": "Test Company",
    "site_name": "Test Site",
    "from_name": "Test Sender",
    "from_email": "from@test.com",
}


# ---------------------------------------------------------------------------
# _cfg — GH-107 / poindexter#156 regression guard
# ---------------------------------------------------------------------------


class TestCfgSecretResolution:
    """``resend_api_key`` is encrypted in app_settings — _cfg must use
    the async get_secret() decryption helper, not sync .get()."""

    @pytest.mark.asyncio
    async def test_cfg_returns_plaintext_resend_key(self):
        sc = _mock_site_config(resend_api_key="re_real_key_abc123")
        cfg = await _cfg(sc)
        assert cfg["resend_api_key"] == "re_real_key_abc123"
        # Ciphertext leak guard — _mock_site_config wires .get() to
        # return enc:v1:CIPHERTEXT_FOR_<key> on this key, so any
        # regression that drops `await` would yield a value with
        # this prefix.
        assert "enc:v1:" not in cfg["resend_api_key"]

    @pytest.mark.asyncio
    async def test_cfg_calls_get_secret_for_resend_api_key(self):
        sc = _mock_site_config(resend_api_key="re_test")
        await _cfg(sc)
        sc.get_secret.assert_awaited_with("resend_api_key", "")

    @pytest.mark.asyncio
    async def test_cfg_empty_resend_key_yields_empty(self):
        sc = _mock_site_config(resend_api_key="")
        cfg = await _cfg(sc)
        assert cfg["resend_api_key"] == ""


# ---------------------------------------------------------------------------
# _build_html
# ---------------------------------------------------------------------------


class TestBuildHtml:
    def test_includes_title_and_link(self):
        html = _build_html(_TEST_CFG, "My Post", "An excerpt", "my-post-slug")
        assert "My Post" in html
        assert "my-post-slug" in html
        assert "test.example.com/posts/my-post-slug" in html

    def test_includes_first_name_greeting(self):
        html = _build_html(_TEST_CFG, "T", "E", "s", first_name="Matt")
        assert "Hi Matt," in html

    def test_generic_greeting_when_no_name(self):
        html = _build_html(_TEST_CFG, "T", "E", "s")
        assert "Hi there," in html

    def test_includes_unsubscribe_link(self):
        html = _build_html(_TEST_CFG, "T", "E", "s")
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
# send_post_newsletter — disabled / misconfigured
# ---------------------------------------------------------------------------


class TestSendNewsletterDisabled:
    @pytest.mark.asyncio
    async def test_returns_skipped_when_disabled(self):
        pool = AsyncMock()
        sc = _mock_site_config(enabled=False)
        result = await send_post_newsletter(pool, "Title", "Excerpt", "slug", sc)
        assert result["skipped_reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_returns_skipped_when_no_resend_key(self):
        pool = AsyncMock()
        sc = _mock_site_config(provider="resend", resend_api_key="")
        result = await send_post_newsletter(pool, "T", "E", "s", sc)
        assert result["skipped_reason"] == "no_api_key"


# ---------------------------------------------------------------------------
# send_post_newsletter — success paths
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

        sc = _mock_site_config()
        with patch(
            "services.newsletter_service._send_via_resend", new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = True
            result = await send_post_newsletter(
                pool, "New Post", "Great stuff", "new-post", sc,
            )

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

        sc = _mock_site_config()
        with patch(
            "services.newsletter_service._send_via_resend", new_callable=AsyncMock,
        ) as mock_send:
            mock_send.side_effect = [True, False, True]
            result = await send_post_newsletter(pool, "T", "E", "s", sc)

        assert result["sent"] == 2
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_no_subscribers_returns_zero(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        sc = _mock_site_config()
        result = await send_post_newsletter(pool, "T", "E", "s", sc)
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
            "site_url": "https://test.example.com",
        }

        fake_aiosmtplib = MagicMock()
        fake_aiosmtplib.send = AsyncMock()

        with patch.dict("sys.modules", {"aiosmtplib": fake_aiosmtplib}):
            result = await _send_via_smtp(cfg, "to@example.com", "Subject", "<html/>")

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
            "site_url": "https://test.example.com",
        }

        fake_aiosmtplib = MagicMock()
        fake_aiosmtplib.send = AsyncMock(side_effect=ConnectionError("smtp down"))

        with patch.dict("sys.modules", {"aiosmtplib": fake_aiosmtplib}):
            result = await _send_via_smtp(cfg, "to@example.com", "S", "<html/>")

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
            "site_url": "https://test.example.com",
        }

        fake_aiosmtplib = MagicMock()
        fake_aiosmtplib.send = AsyncMock()

        with patch.dict("sys.modules", {"aiosmtplib": fake_aiosmtplib}):
            await _send_via_smtp(cfg, "to@example.com", "S", "<html/>")

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
# send_post_newsletter — SMTP provider routing
# ---------------------------------------------------------------------------


class TestSendNewsletterSmtpProvider:
    @pytest.mark.asyncio
    async def test_no_smtp_host_returns_skipped(self):
        pool = AsyncMock()
        sc = _mock_site_config(provider="smtp", smtp_host="")
        result = await send_post_newsletter(pool, "T", "E", "s", sc)
        assert result["sent"] == 0
        assert result.get("skipped_reason") == "no_smtp_host"

    @pytest.mark.asyncio
    async def test_smtp_provider_routes_to_smtp_function(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": 1, "email": "a@x.com", "first_name": "Alice"},
        ])
        pool.execute = AsyncMock()

        sc = _mock_site_config(provider="smtp", smtp_host="smtp.example.com")
        with (
            patch(
                "services.newsletter_service._send_via_smtp",
                new_callable=AsyncMock,
            ) as mock_smtp,
            patch(
                "services.newsletter_service._send_via_resend",
                new_callable=AsyncMock,
            ) as mock_resend,
        ):
            mock_smtp.return_value = True
            mock_resend.return_value = True
            result = await send_post_newsletter(pool, "T", "E", "s", sc)

        assert result["sent"] == 1
        mock_smtp.assert_awaited_once()
        mock_resend.assert_not_awaited()
