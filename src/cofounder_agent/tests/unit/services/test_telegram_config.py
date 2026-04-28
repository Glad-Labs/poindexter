"""
Unit tests for services/telegram_config.py.

Post-Phase-H (GH#95): module exposes three functions rather than
module-level constants resolved at import time. Tests construct a
MagicMock SiteConfig per case and pass it into each helper.

GH-107 / poindexter#156: ``telegram_bot_token`` is encrypted at rest
(``is_secret=true``), so ``get_telegram_bot_token`` and
``telegram_configured`` are now ``async def`` and read the token via
``site_config.get_secret(...)``. The ``_mock_sc`` fixture wires sync
``.get()`` to return ``enc:v1:<ciphertext>`` for the bot token so any
regression that drops the await would surface as ciphertext leaking
into the bot-API URL — tests below assert plaintext.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.telegram_config import (
    get_telegram_bot_token,
    get_telegram_chat_id,
    telegram_configured,
)


def _mock_sc(
    chat_id: str | None = None,
    bot_token: str | None = None,
) -> MagicMock:
    """Return a MagicMock shaped like SiteConfig.

    Parameters
    ----------
    chat_id : str | None
        Value site_config.get() returns for "telegram_chat_id".
        None means not configured (returns default).
    bot_token : str | None
        Plaintext value site_config.get_secret() returns for
        "telegram_bot_token". None means not configured (returns default).
        The sync .get() returns ciphertext so any regression that uses
        .get() on the secret key surfaces as a failed assertion.
    """
    mapping_sync = {
        "telegram_chat_id": chat_id,
        # If a regression re-adds sync .get() on the secret, this is
        # what would leak into https://api.telegram.org/bot{token}/...
        "telegram_bot_token": (
            f"enc:v1:CIPHERTEXT_FOR_{bot_token}" if bot_token else None
        ),
    }
    mapping_secret = {"telegram_bot_token": bot_token}
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": (
        mapping_sync[key] if mapping_sync.get(key) is not None else default
    )
    sc.get_secret = AsyncMock(
        side_effect=lambda key, default="": (
            mapping_secret[key] if mapping_secret.get(key) is not None else default
        )
    )
    return sc


# ---------------------------------------------------------------------------
# Chat ID resolution
# ---------------------------------------------------------------------------


class TestChatIdResolution:
    """get_telegram_chat_id should come from site_config (DB -> env -> default).

    chat_id is NOT a secret — sync .get() is correct here.
    """

    def test_db_value_takes_priority(self):
        assert get_telegram_chat_id(_mock_sc(chat_id="111")) == "111"

    def test_default_is_empty_when_nothing_set(self):
        # #198: no hardcoded chat_id default — missing config returns empty
        # so operators have to configure telegram_chat_id explicitly.
        assert get_telegram_chat_id(_mock_sc()) == ""


# ---------------------------------------------------------------------------
# Bot token resolution — GH-107 / poindexter#156: must use get_secret()
# ---------------------------------------------------------------------------


class TestBotTokenResolution:
    """get_telegram_bot_token must use the async get_secret() decryption
    path — sync .get() returns enc:v1:<ciphertext> on this key."""

    @pytest.mark.asyncio
    async def test_db_value_takes_priority(self):
        token = await get_telegram_bot_token(_mock_sc(bot_token="db-token"))
        assert token == "db-token"

    @pytest.mark.asyncio
    async def test_default_is_empty_string(self):
        token = await get_telegram_bot_token(_mock_sc())
        assert token == ""

    @pytest.mark.asyncio
    async def test_returns_plaintext_not_ciphertext(self):
        """Regression guard for the original bug class — if a future
        edit drops the await get_secret(...) and falls back to .get(),
        this test fails because .get() returns enc:v1:<...>."""
        token = await get_telegram_bot_token(_mock_sc(bot_token="123:ABC"))
        assert token == "123:ABC"
        assert "enc:v1:" not in token

    @pytest.mark.asyncio
    async def test_calls_get_secret_not_get(self):
        sc = _mock_sc(bot_token="abc")
        await get_telegram_bot_token(sc)
        sc.get_secret.assert_awaited_once_with("telegram_bot_token", "")
        # And — critically — the sync .get() must NOT have been used
        # to fetch the secret key.
        assert all(
            call.args[0] != "telegram_bot_token"
            for call in sc.get.mock_calls
            if call.args
        )


# ---------------------------------------------------------------------------
# Bot token validation helpers
# ---------------------------------------------------------------------------


class TestBotTokenValidation:
    """Token should be usable for conditional checks (truthy / falsy)."""

    @pytest.mark.asyncio
    async def test_token_truthy_when_set(self):
        assert await get_telegram_bot_token(_mock_sc(bot_token="123:ABC"))

    @pytest.mark.asyncio
    async def test_token_falsy_when_unset(self):
        assert not await get_telegram_bot_token(_mock_sc())

    @pytest.mark.asyncio
    async def test_token_with_colon_format_preserved(self):
        tok = await get_telegram_bot_token(_mock_sc(bot_token="123456:ABCdefGHI"))
        assert ":" in tok
        assert tok == "123456:ABCdefGHI"


# ---------------------------------------------------------------------------
# Type guarantees
# ---------------------------------------------------------------------------


class TestTypeGuarantees:
    """Both helpers must always return ``str``, never None."""

    def test_chat_id_is_str_default(self):
        assert isinstance(get_telegram_chat_id(_mock_sc()), str)

    @pytest.mark.asyncio
    async def test_bot_token_is_str_default(self):
        assert isinstance(await get_telegram_bot_token(_mock_sc()), str)

    def test_chat_id_from_db_is_str(self):
        assert isinstance(get_telegram_chat_id(_mock_sc(chat_id="999")), str)

    @pytest.mark.asyncio
    async def test_bot_token_from_db_is_str(self):
        assert isinstance(
            await get_telegram_bot_token(_mock_sc(bot_token="tok")), str
        )


# ---------------------------------------------------------------------------
# telegram_configured helper
# ---------------------------------------------------------------------------


class TestTelegramConfigured:
    """Boolean short-circuit: both chat_id AND bot_token must be set.

    Async because reading the bot token requires get_secret().
    """

    @pytest.mark.asyncio
    async def test_both_set_is_true(self):
        assert await telegram_configured(_mock_sc(chat_id="1", bot_token="t")) is True

    @pytest.mark.asyncio
    async def test_only_chat_id_is_false(self):
        assert await telegram_configured(_mock_sc(chat_id="1")) is False

    @pytest.mark.asyncio
    async def test_only_bot_token_is_false(self):
        assert await telegram_configured(_mock_sc(bot_token="t")) is False

    @pytest.mark.asyncio
    async def test_neither_is_false(self):
        assert await telegram_configured(_mock_sc()) is False


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    """Multi-value scenarios."""

    @pytest.mark.asyncio
    async def test_both_from_db(self):
        sc = _mock_sc(chat_id="100", bot_token="tok:abc")
        assert get_telegram_chat_id(sc) == "100"
        assert await get_telegram_bot_token(sc) == "tok:abc"

    @pytest.mark.asyncio
    async def test_all_defaults(self):
        sc = _mock_sc()
        assert get_telegram_chat_id(sc) == ""
        assert await get_telegram_bot_token(sc) == ""
