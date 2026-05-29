"""
Unit tests for services/telegram_config.py

Rewritten 2026-05-28 (SiteConfig DI migration PR 3) after the module
was converted from free functions + module-level ``site_config``
singleton to a ``TelegramConfig`` class with constructor DI. The class
now exposes:

- ``get_telegram_chat_id()`` (sync, plaintext)
- ``get_telegram_bot_token()`` (async, calls ``site_config.get_secret``)
- ``telegram_configured()`` (async, checks both)

Tests construct ``TelegramConfig(site_config=stub_site_config)``
directly with a mock SiteConfig — zero shared module state.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.telegram_config import TelegramConfig


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestTelegramConfigConstruction:
    """``TelegramConfig`` is a thin wrapper holding only an injected SiteConfig."""

    def test_constructs_with_site_config_kwarg(self):
        site_config = MagicMock()
        tg = TelegramConfig(site_config=site_config)
        # SiteConfig is stored as a private attr; we don't test the
        # attribute name directly (encapsulation), only that the class
        # round-trips by reading via the public methods below.
        assert tg is not None

    def test_site_config_is_required(self):
        """No default — passing nothing must raise per fail-loud principle."""
        with pytest.raises(TypeError):
            TelegramConfig()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Sync chat_id helper
# ---------------------------------------------------------------------------


class TestGetTelegramChatId:
    """``get_telegram_chat_id()`` reads the plaintext chat_id at call time."""

    def test_returns_value_from_site_config(self):
        mock_sc = MagicMock()
        mock_sc.get.return_value = "12345"
        tg = TelegramConfig(site_config=mock_sc)

        assert tg.get_telegram_chat_id() == "12345"
        mock_sc.get.assert_called_once_with("telegram_chat_id", "")

    def test_returns_empty_when_unset(self):
        mock_sc = MagicMock()
        mock_sc.get.return_value = ""
        tg = TelegramConfig(site_config=mock_sc)

        assert tg.get_telegram_chat_id() == ""


# ---------------------------------------------------------------------------
# Async bot_token helper (the load-bearing fix)
# ---------------------------------------------------------------------------


class TestGetTelegramBotToken:
    """``get_telegram_bot_token()`` uses get_secret to decrypt the is_secret row."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_token(self):
        mock_sc = MagicMock()
        mock_sc.get_secret = AsyncMock(return_value="123456:ABCdef")
        tg = TelegramConfig(site_config=mock_sc)

        assert await tg.get_telegram_bot_token() == "123456:ABCdef"
        mock_sc.get_secret.assert_awaited_once_with("telegram_bot_token", "")

    @pytest.mark.asyncio
    async def test_returns_empty_when_unset(self):
        mock_sc = MagicMock()
        mock_sc.get_secret = AsyncMock(return_value="")
        tg = TelegramConfig(site_config=mock_sc)

        assert await tg.get_telegram_bot_token() == ""

    @pytest.mark.asyncio
    async def test_uses_get_secret_not_sync_get(self):
        """Regression pin for the #325 bug class.

        If a future regression flips this back to sync ``.get(...)``,
        the encrypted is_secret row would return ciphertext
        (``enc:v1:...``) and the Telegram bot API would silently 401.
        Asserting that get_secret is the only call surface prevents
        that drift.
        """
        mock_sc = MagicMock()
        mock_sc.get_secret = AsyncMock(return_value="real-token")
        # ``.get`` should NOT be called for the bot_token path.
        mock_sc.get = MagicMock(return_value="ciphertext-leak")
        tg = TelegramConfig(site_config=mock_sc)

        await tg.get_telegram_bot_token()

        for call in mock_sc.get.mock_calls:
            if call.args:
                assert call.args[0] != "telegram_bot_token"


# ---------------------------------------------------------------------------
# Combined ``telegram_configured`` async check
# ---------------------------------------------------------------------------


class TestTelegramConfigured:
    """``telegram_configured()`` is True iff both chat_id and bot_token are set."""

    @pytest.mark.asyncio
    async def test_true_when_both_set(self):
        mock_sc = MagicMock()
        mock_sc.get.return_value = "12345"
        mock_sc.get_secret = AsyncMock(return_value="tok:abc")
        tg = TelegramConfig(site_config=mock_sc)

        assert await tg.telegram_configured() is True

    @pytest.mark.asyncio
    async def test_false_when_chat_id_missing(self):
        mock_sc = MagicMock()
        mock_sc.get.return_value = ""
        mock_sc.get_secret = AsyncMock(return_value="tok:abc")
        tg = TelegramConfig(site_config=mock_sc)

        assert await tg.telegram_configured() is False

    @pytest.mark.asyncio
    async def test_false_when_token_missing(self):
        mock_sc = MagicMock()
        mock_sc.get.return_value = "12345"
        mock_sc.get_secret = AsyncMock(return_value="")
        tg = TelegramConfig(site_config=mock_sc)

        assert await tg.telegram_configured() is False

    @pytest.mark.asyncio
    async def test_false_when_both_missing(self):
        mock_sc = MagicMock()
        mock_sc.get.return_value = ""
        mock_sc.get_secret = AsyncMock(return_value="")
        tg = TelegramConfig(site_config=mock_sc)

        assert await tg.telegram_configured() is False


# ---------------------------------------------------------------------------
# Container wiring (PR 3: cached_property on AppContainer)
# ---------------------------------------------------------------------------


class TestAppContainerWiring:
    """``AppContainer.telegram_config`` returns a memoised TelegramConfig."""

    def test_app_container_exposes_telegram_config(self):
        from services.container import AppContainer
        from services.site_config import SiteConfig

        site_config = SiteConfig(initial_config={"telegram_chat_id": "999"})
        container = AppContainer(site_config=site_config, pool=MagicMock())

        tg = container.telegram_config
        assert isinstance(tg, TelegramConfig)
        assert tg.get_telegram_chat_id() == "999"

    def test_cached_property_memoises(self):
        """Two reads return the same instance (cached_property contract)."""
        from services.container import AppContainer
        from services.site_config import SiteConfig

        container = AppContainer(site_config=SiteConfig(), pool=MagicMock())
        first = container.telegram_config
        second = container.telegram_config
        assert first is second
