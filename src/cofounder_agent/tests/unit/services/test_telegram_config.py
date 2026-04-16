"""
Unit tests for services/telegram_config.py

Tests DB-first config loading via site_config. All site_config calls
are mocked — no real DB required.

Because the module resolves its constants at *import time*, each test
executes the module source in an isolated namespace with the
appropriate mocks.
"""

import os
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Path to the actual module source
_SRC = Path(__file__).resolve().parents[3] / "services" / "telegram_config.py"

# No hardcoded default — missing config returns empty string (#198).
_DEFAULT_CHAT_ID = ""


def _load_telegram_config(
    db_chat_id=None,
    db_bot_token=None,
):
    """Execute ``telegram_config.py`` in an isolated namespace.

    Parameters
    ----------
    db_chat_id : str | None
        Value site_config.get() returns for "telegram_chat_id".
        None means not configured (returns default).
    db_bot_token : str | None
        Value site_config.get() returns for "telegram_bot_token".
        None means not configured (returns default).
    """
    source = _SRC.read_text(encoding="utf-8")

    mapping = {
        "telegram_chat_id": db_chat_id,
        "telegram_bot_token": db_bot_token,
    }

    fake_sc = MagicMock()

    def _mock_get(key, default=""):
        val = mapping.get(key)
        return val if val is not None else default

    fake_sc.site_config.get.side_effect = _mock_get

    mod = types.ModuleType("services.telegram_config")
    mod.__file__ = str(_SRC)

    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _fake_import(name, *args, **kwargs):
        if name == "services.site_config":
            return fake_sc
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fake_import):
        exec(compile(source, str(_SRC), "exec"), mod.__dict__)  # noqa: S102

    return mod


# ---------------------------------------------------------------------------
# Chat ID resolution
# ---------------------------------------------------------------------------


class TestChatIdResolution:
    """TELEGRAM_CHAT_ID should come from site_config (DB -> env -> default)."""

    def test_db_value_takes_priority(self):
        mod = _load_telegram_config(db_chat_id="111")
        assert mod.TELEGRAM_CHAT_ID == "111"

    def test_default_is_empty_when_nothing_set(self):
        # #198: no hardcoded chat_id default — missing config returns empty
        # so operators have to configure telegram_chat_id explicitly.
        mod = _load_telegram_config(db_chat_id=None)
        assert mod.TELEGRAM_CHAT_ID == ""


# ---------------------------------------------------------------------------
# Bot token resolution
# ---------------------------------------------------------------------------


class TestBotTokenResolution:
    """TELEGRAM_BOT_TOKEN should come from site_config (DB -> env -> empty)."""

    def test_db_value_takes_priority(self):
        mod = _load_telegram_config(db_bot_token="db-token")
        assert mod.TELEGRAM_BOT_TOKEN == "db-token"

    def test_default_is_empty_string(self):
        mod = _load_telegram_config(db_bot_token=None)
        assert mod.TELEGRAM_BOT_TOKEN == ""


# ---------------------------------------------------------------------------
# Bot token validation helpers
# ---------------------------------------------------------------------------


class TestBotTokenValidation:
    """Token should be usable for conditional checks (truthy / falsy)."""

    def test_token_truthy_when_set(self):
        mod = _load_telegram_config(db_bot_token="123:ABC")
        assert mod.TELEGRAM_BOT_TOKEN

    def test_token_falsy_when_unset(self):
        mod = _load_telegram_config(db_bot_token=None)
        assert not mod.TELEGRAM_BOT_TOKEN

    def test_token_with_colon_format_preserved(self):
        mod = _load_telegram_config(db_bot_token="123456:ABCdefGHI")
        assert ":" in mod.TELEGRAM_BOT_TOKEN
        assert mod.TELEGRAM_BOT_TOKEN == "123456:ABCdefGHI"


# ---------------------------------------------------------------------------
# Type guarantees
# ---------------------------------------------------------------------------


class TestTypeGuarantees:
    """Both exports must always be ``str``, never None."""

    def test_chat_id_is_str_default(self):
        mod = _load_telegram_config()
        assert isinstance(mod.TELEGRAM_CHAT_ID, str)

    def test_bot_token_is_str_default(self):
        mod = _load_telegram_config()
        assert isinstance(mod.TELEGRAM_BOT_TOKEN, str)

    def test_chat_id_from_db_is_str(self):
        mod = _load_telegram_config(db_chat_id="999")
        assert isinstance(mod.TELEGRAM_CHAT_ID, str)

    def test_bot_token_from_db_is_str(self):
        mod = _load_telegram_config(db_bot_token="tok")
        assert isinstance(mod.TELEGRAM_BOT_TOKEN, str)


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------


class TestModuleExports:
    """Module must expose TELEGRAM_CHAT_ID and TELEGRAM_BOT_TOKEN."""

    def test_exports_chat_id(self):
        mod = _load_telegram_config()
        assert hasattr(mod, "TELEGRAM_CHAT_ID")

    def test_exports_bot_token(self):
        mod = _load_telegram_config()
        assert hasattr(mod, "TELEGRAM_BOT_TOKEN")


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    """Multi-value scenarios."""

    def test_both_from_db(self):
        mod = _load_telegram_config(db_chat_id="100", db_bot_token="tok:abc")
        assert mod.TELEGRAM_CHAT_ID == "100"
        assert mod.TELEGRAM_BOT_TOKEN == "tok:abc"

    def test_all_defaults(self):
        mod = _load_telegram_config()
        assert mod.TELEGRAM_CHAT_ID == _DEFAULT_CHAT_ID
        assert mod.TELEGRAM_BOT_TOKEN == ""
