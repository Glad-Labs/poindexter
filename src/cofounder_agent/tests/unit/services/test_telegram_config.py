"""
Unit tests for services/telegram_config.py.

Post-Phase-H (GH#95): module exposes three functions rather than
module-level constants resolved at import time. Tests construct a
MagicMock SiteConfig per case and pass it into each helper.
"""

from unittest.mock import MagicMock

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
        Value site_config.get() returns for "telegram_bot_token".
        None means not configured (returns default).
    """
    mapping = {"telegram_chat_id": chat_id, "telegram_bot_token": bot_token}
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": (
        mapping[key] if mapping.get(key) is not None else default
    )
    return sc


# ---------------------------------------------------------------------------
# Chat ID resolution
# ---------------------------------------------------------------------------


class TestChatIdResolution:
    """get_telegram_chat_id should come from site_config (DB -> env -> default)."""

    def test_db_value_takes_priority(self):
        assert get_telegram_chat_id(_mock_sc(chat_id="111")) == "111"

    def test_default_is_empty_when_nothing_set(self):
        # #198: no hardcoded chat_id default — missing config returns empty
        # so operators have to configure telegram_chat_id explicitly.
        assert get_telegram_chat_id(_mock_sc()) == ""


# ---------------------------------------------------------------------------
# Bot token resolution
# ---------------------------------------------------------------------------


class TestBotTokenResolution:
    """get_telegram_bot_token should come from site_config (DB -> env -> empty)."""

    def test_db_value_takes_priority(self):
        assert get_telegram_bot_token(_mock_sc(bot_token="db-token")) == "db-token"

    def test_default_is_empty_string(self):
        assert get_telegram_bot_token(_mock_sc()) == ""


# ---------------------------------------------------------------------------
# Bot token validation helpers
# ---------------------------------------------------------------------------


class TestBotTokenValidation:
    """Token should be usable for conditional checks (truthy / falsy)."""

    def test_token_truthy_when_set(self):
        assert get_telegram_bot_token(_mock_sc(bot_token="123:ABC"))

    def test_token_falsy_when_unset(self):
        assert not get_telegram_bot_token(_mock_sc())

    def test_token_with_colon_format_preserved(self):
        tok = get_telegram_bot_token(_mock_sc(bot_token="123456:ABCdefGHI"))
        assert ":" in tok
        assert tok == "123456:ABCdefGHI"


# ---------------------------------------------------------------------------
# Type guarantees
# ---------------------------------------------------------------------------


class TestTypeGuarantees:
    """Both helpers must always return ``str``, never None."""

    def test_chat_id_is_str_default(self):
        assert isinstance(get_telegram_chat_id(_mock_sc()), str)

    def test_bot_token_is_str_default(self):
        assert isinstance(get_telegram_bot_token(_mock_sc()), str)

    def test_chat_id_from_db_is_str(self):
        assert isinstance(get_telegram_chat_id(_mock_sc(chat_id="999")), str)

    def test_bot_token_from_db_is_str(self):
        assert isinstance(get_telegram_bot_token(_mock_sc(bot_token="tok")), str)


# ---------------------------------------------------------------------------
# telegram_configured helper
# ---------------------------------------------------------------------------


class TestTelegramConfigured:
    """Boolean short-circuit: both chat_id AND bot_token must be set."""

    def test_both_set_is_true(self):
        assert telegram_configured(_mock_sc(chat_id="1", bot_token="t")) is True

    def test_only_chat_id_is_false(self):
        assert telegram_configured(_mock_sc(chat_id="1")) is False

    def test_only_bot_token_is_false(self):
        assert telegram_configured(_mock_sc(bot_token="t")) is False

    def test_neither_is_false(self):
        assert telegram_configured(_mock_sc()) is False


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    """Multi-value scenarios."""

    def test_both_from_db(self):
        sc = _mock_sc(chat_id="100", bot_token="tok:abc")
        assert get_telegram_chat_id(sc) == "100"
        assert get_telegram_bot_token(sc) == "tok:abc"

    def test_all_defaults(self):
        sc = _mock_sc()
        assert get_telegram_chat_id(sc) == ""
        assert get_telegram_bot_token(sc) == ""
