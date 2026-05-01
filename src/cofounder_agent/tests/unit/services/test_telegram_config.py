"""
Unit tests for services/telegram_config.py

Rewritten 2026-05-01 after the bug-class sweep that flipped
`telegram_bot_token` to is_secret=true. The module now exposes:

- `TELEGRAM_CHAT_ID` (sync, plaintext key — still a module attribute)
- `TELEGRAM_BOT_TOKEN` (always `""` — back-compat shim, forces callers
  to migrate to the async helper)
- `get_telegram_chat_id()` (sync helper, returns the plaintext)
- `get_telegram_bot_token()` (async, calls site_config.get_secret)
- `telegram_configured()` (async, checks both)

These tests mock `services.site_config.site_config` directly rather
than re-executing the module source, since the new API is method-based.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level back-compat shims
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    """The two legacy module attributes still exist for back-compat."""

    def test_chat_id_attribute_exists(self):
        from services import telegram_config
        assert hasattr(telegram_config, "TELEGRAM_CHAT_ID")
        assert isinstance(telegram_config.TELEGRAM_CHAT_ID, str)

    def test_bot_token_attribute_is_empty_shim(self):
        """TELEGRAM_BOT_TOKEN is intentionally always empty after the
        secrets-flip — caller must migrate to get_telegram_bot_token()."""
        from services import telegram_config
        assert hasattr(telegram_config, "TELEGRAM_BOT_TOKEN")
        assert telegram_config.TELEGRAM_BOT_TOKEN == ""


# ---------------------------------------------------------------------------
# Sync chat_id helper
# ---------------------------------------------------------------------------


class TestGetTelegramChatId:
    """`get_telegram_chat_id()` reads the plaintext chat_id at call time."""

    def test_returns_value_from_site_config(self):
        from services import telegram_config

        with patch.object(telegram_config, "site_config") as mock_sc:
            mock_sc.get.return_value = "12345"
            assert telegram_config.get_telegram_chat_id() == "12345"
            mock_sc.get.assert_called_once_with("telegram_chat_id", "")

    def test_returns_empty_when_unset(self):
        from services import telegram_config

        with patch.object(telegram_config, "site_config") as mock_sc:
            mock_sc.get.return_value = ""
            assert telegram_config.get_telegram_chat_id() == ""


# ---------------------------------------------------------------------------
# Async bot_token helper (the load-bearing fix)
# ---------------------------------------------------------------------------


class TestGetTelegramBotToken:
    """`get_telegram_bot_token()` uses get_secret to decrypt the is_secret row."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_token(self):
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get_secret = AsyncMock(return_value="123456:ABCdef")
        with patch.object(telegram_config, "site_config", mock_sc):
            assert await telegram_config.get_telegram_bot_token() == "123456:ABCdef"
        mock_sc.get_secret.assert_awaited_once_with("telegram_bot_token", "")

    @pytest.mark.asyncio
    async def test_returns_empty_when_unset(self):
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get_secret = AsyncMock(return_value="")
        with patch.object(telegram_config, "site_config", mock_sc):
            assert await telegram_config.get_telegram_bot_token() == ""

    @pytest.mark.asyncio
    async def test_uses_get_secret_not_sync_get(self):
        """Regression pin for the #325 bug class.

        If a future regression flips this back to sync `.get(...)`, the
        encrypted is_secret row would return ciphertext (`enc:v1:...`)
        and the Telegram bot API would silently 401. Asserting that
        get_secret is the only call surface prevents that drift.
        """
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get_secret = AsyncMock(return_value="real-token")
        # `.get` should NOT be called for the bot_token path.
        mock_sc.get = MagicMock(return_value="ciphertext-leak")
        with patch.object(telegram_config, "site_config", mock_sc):
            await telegram_config.get_telegram_bot_token()
        for call in mock_sc.get.mock_calls:
            if call.args:
                assert call.args[0] != "telegram_bot_token"


# ---------------------------------------------------------------------------
# Combined `telegram_configured` async check
# ---------------------------------------------------------------------------


class TestTelegramConfigured:
    """`telegram_configured()` is True iff both chat_id and bot_token are set."""

    @pytest.mark.asyncio
    async def test_true_when_both_set(self):
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get.return_value = "12345"
        mock_sc.get_secret = AsyncMock(return_value="tok:abc")
        with patch.object(telegram_config, "site_config", mock_sc):
            assert await telegram_config.telegram_configured() is True

    @pytest.mark.asyncio
    async def test_false_when_chat_id_missing(self):
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get.return_value = ""
        mock_sc.get_secret = AsyncMock(return_value="tok:abc")
        with patch.object(telegram_config, "site_config", mock_sc):
            assert await telegram_config.telegram_configured() is False

    @pytest.mark.asyncio
    async def test_false_when_token_missing(self):
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get.return_value = "12345"
        mock_sc.get_secret = AsyncMock(return_value="")
        with patch.object(telegram_config, "site_config", mock_sc):
            assert await telegram_config.telegram_configured() is False

    @pytest.mark.asyncio
    async def test_false_when_both_missing(self):
        from services import telegram_config

        mock_sc = MagicMock()
        mock_sc.get.return_value = ""
        mock_sc.get_secret = AsyncMock(return_value="")
        with patch.object(telegram_config, "site_config", mock_sc):
            assert await telegram_config.telegram_configured() is False
