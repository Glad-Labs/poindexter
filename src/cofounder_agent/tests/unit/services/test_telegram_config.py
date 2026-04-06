"""
Unit tests for services/telegram_config.py

Tests DB-first config loading, env var fallback chains, bot token
resolution, and chat ID handling.  All database / site_config calls
are mocked — no real DB required.

Because the module resolves its constants at *import time*, each test
executes the module source in an isolated namespace with the
appropriate mocks, avoiding the heavy ``services.__init__`` import chain.
"""

import os
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Path to the actual module source
_SRC = Path(__file__).resolve().parents[3] / "services" / "telegram_config.py"


def _load_telegram_config(
    db_chat_id=None,
    db_bot_token=None,
    db_raises=False,
    env_overrides=None,
):
    """Execute ``telegram_config.py`` in an isolated namespace.

    Parameters
    ----------
    db_chat_id : str | None
        Value returned by ``site_config.get("telegram_chat_id")``.
    db_bot_token : str | None
        Value returned by ``site_config.get("telegram_bot_token")``.
    db_raises : bool
        If True, importing site_config will raise an exception so the
        module falls through to env vars.
    env_overrides : dict | None
        Extra env vars to inject during execution.
    """
    source = _SRC.read_text(encoding="utf-8")

    # Build a fake services.site_config module
    if db_raises:
        fake_sc = MagicMock()
        fake_sc.site_config.get.side_effect = Exception("db down")
    else:
        mapping = {
            "telegram_chat_id": db_chat_id,
            "telegram_bot_token": db_bot_token,
        }
        fake_sc = MagicMock()
        fake_sc.site_config.get.side_effect = lambda key: mapping.get(key)

    # Patch the import machinery so ``from services.site_config import site_config``
    # resolves to our fake.
    import sys

    env = env_overrides or {}
    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in (
            "TELEGRAM_CHAT_ID",
            "OPENCLAW_TELEGRAM_CHAT_ID",
            "TELEGRAM_BOT_TOKEN",
            "OPENCLAW_TELEGRAM_BOT_TOKEN",
        )
    }
    clean_env.update(env)

    mod = types.ModuleType("services.telegram_config")
    mod.__file__ = str(_SRC)

    # Provide a controlled builtins.__import__ that intercepts site_config
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _fake_import(name, *args, **kwargs):
        if name == "services.site_config":
            return fake_sc
        return real_import(name, *args, **kwargs)

    with patch.dict(os.environ, clean_env, clear=True):
        with patch("builtins.__import__", side_effect=_fake_import):
            exec(compile(source, str(_SRC), "exec"), mod.__dict__)  # noqa: S102

    return mod


# ---------------------------------------------------------------------------
# Chat ID resolution
# ---------------------------------------------------------------------------


class TestChatIdResolution:
    """TELEGRAM_CHAT_ID should follow: DB -> env -> legacy env -> default."""

    def test_db_value_takes_priority(self):
        mod = _load_telegram_config(
            db_chat_id="111",
            env_overrides={"TELEGRAM_CHAT_ID": "222"},
        )
        assert mod.TELEGRAM_CHAT_ID == "111"

    def test_env_var_when_db_returns_none(self):
        mod = _load_telegram_config(
            db_chat_id=None,
            env_overrides={"TELEGRAM_CHAT_ID": "333"},
        )
        assert mod.TELEGRAM_CHAT_ID == "333"

    def test_legacy_env_var_fallback(self):
        mod = _load_telegram_config(
            db_chat_id=None,
            env_overrides={"OPENCLAW_TELEGRAM_CHAT_ID": "444"},
        )
        assert mod.TELEGRAM_CHAT_ID == "444"

    def test_preferred_env_beats_legacy(self):
        mod = _load_telegram_config(
            db_chat_id=None,
            env_overrides={
                "TELEGRAM_CHAT_ID": "555",
                "OPENCLAW_TELEGRAM_CHAT_ID": "666",
            },
        )
        assert mod.TELEGRAM_CHAT_ID == "555"

    def test_default_when_nothing_set(self):
        mod = _load_telegram_config(db_chat_id=None)
        assert mod.TELEGRAM_CHAT_ID == "5318613610"

    def test_db_exception_falls_through_to_env(self):
        mod = _load_telegram_config(
            db_raises=True,
            env_overrides={"TELEGRAM_CHAT_ID": "777"},
        )
        assert mod.TELEGRAM_CHAT_ID == "777"

    def test_db_exception_falls_through_to_default(self):
        mod = _load_telegram_config(db_raises=True)
        assert mod.TELEGRAM_CHAT_ID == "5318613610"

    def test_empty_string_db_value_treated_as_falsy(self):
        mod = _load_telegram_config(
            db_chat_id="",
            env_overrides={"TELEGRAM_CHAT_ID": "888"},
        )
        assert mod.TELEGRAM_CHAT_ID == "888"


# ---------------------------------------------------------------------------
# Bot token resolution
# ---------------------------------------------------------------------------


class TestBotTokenResolution:
    """TELEGRAM_BOT_TOKEN should follow: DB -> env -> legacy env -> empty."""

    def test_db_value_takes_priority(self):
        mod = _load_telegram_config(
            db_bot_token="db-token",
            env_overrides={"TELEGRAM_BOT_TOKEN": "env-token"},
        )
        assert mod.TELEGRAM_BOT_TOKEN == "db-token"

    def test_env_var_when_db_returns_none(self):
        mod = _load_telegram_config(
            db_bot_token=None,
            env_overrides={"TELEGRAM_BOT_TOKEN": "env-token"},
        )
        assert mod.TELEGRAM_BOT_TOKEN == "env-token"

    def test_legacy_env_var_fallback(self):
        mod = _load_telegram_config(
            db_bot_token=None,
            env_overrides={"OPENCLAW_TELEGRAM_BOT_TOKEN": "legacy-token"},
        )
        assert mod.TELEGRAM_BOT_TOKEN == "legacy-token"

    def test_preferred_env_beats_legacy(self):
        mod = _load_telegram_config(
            db_bot_token=None,
            env_overrides={
                "TELEGRAM_BOT_TOKEN": "new-token",
                "OPENCLAW_TELEGRAM_BOT_TOKEN": "old-token",
            },
        )
        assert mod.TELEGRAM_BOT_TOKEN == "new-token"

    def test_default_is_empty_string(self):
        mod = _load_telegram_config(db_bot_token=None)
        assert mod.TELEGRAM_BOT_TOKEN == ""

    def test_db_exception_falls_through_to_env(self):
        mod = _load_telegram_config(
            db_raises=True,
            env_overrides={"TELEGRAM_BOT_TOKEN": "rescue-token"},
        )
        assert mod.TELEGRAM_BOT_TOKEN == "rescue-token"

    def test_db_exception_falls_through_to_empty(self):
        mod = _load_telegram_config(db_raises=True)
        assert mod.TELEGRAM_BOT_TOKEN == ""

    def test_empty_string_db_value_treated_as_falsy(self):
        mod = _load_telegram_config(
            db_bot_token="",
            env_overrides={"TELEGRAM_BOT_TOKEN": "env-token"},
        )
        assert mod.TELEGRAM_BOT_TOKEN == "env-token"


# ---------------------------------------------------------------------------
# Bot token validation helpers
# ---------------------------------------------------------------------------


class TestBotTokenValidation:
    """Token should be usable for conditional checks (truthy / falsy)."""

    def test_token_truthy_when_set(self):
        mod = _load_telegram_config(db_bot_token="123:ABC")
        assert mod.TELEGRAM_BOT_TOKEN  # truthy

    def test_token_falsy_when_unset(self):
        mod = _load_telegram_config(db_bot_token=None)
        assert not mod.TELEGRAM_BOT_TOKEN  # empty string is falsy

    def test_token_with_colon_format_preserved(self):
        """Bot tokens typically look like '123456:ABC-DEF'. Ensure the
        module doesn't mangle them."""
        token = "123456789:ABCdefGHI-jklMNO_pqr"
        mod = _load_telegram_config(db_bot_token=token)
        assert mod.TELEGRAM_BOT_TOKEN == token


# ---------------------------------------------------------------------------
# Type guarantees
# ---------------------------------------------------------------------------


class TestTypeGuarantees:
    """Both exports should always be strings."""

    def test_chat_id_is_str_default(self):
        mod = _load_telegram_config()
        assert isinstance(mod.TELEGRAM_CHAT_ID, str)

    def test_bot_token_is_str_default(self):
        mod = _load_telegram_config()
        assert isinstance(mod.TELEGRAM_BOT_TOKEN, str)

    def test_chat_id_from_db_is_str(self):
        mod = _load_telegram_config(db_chat_id="12345")
        assert isinstance(mod.TELEGRAM_CHAT_ID, str)

    def test_bot_token_from_db_is_str(self):
        mod = _load_telegram_config(db_bot_token="tok:123")
        assert isinstance(mod.TELEGRAM_BOT_TOKEN, str)


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------


class TestModuleExports:
    """The module should expose exactly the expected public names."""

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
    """End-to-end resolution with both chat ID and bot token together."""

    def test_both_from_db(self):
        mod = _load_telegram_config(
            db_chat_id="99999",
            db_bot_token="db:secret",
        )
        assert mod.TELEGRAM_CHAT_ID == "99999"
        assert mod.TELEGRAM_BOT_TOKEN == "db:secret"

    def test_mixed_sources(self):
        """Chat ID from DB, token from env."""
        mod = _load_telegram_config(
            db_chat_id="11111",
            db_bot_token=None,
            env_overrides={"TELEGRAM_BOT_TOKEN": "env-secret"},
        )
        assert mod.TELEGRAM_CHAT_ID == "11111"
        assert mod.TELEGRAM_BOT_TOKEN == "env-secret"

    def test_all_defaults(self):
        mod = _load_telegram_config()
        assert mod.TELEGRAM_CHAT_ID == "5318613610"
        assert mod.TELEGRAM_BOT_TOKEN == ""

    def test_legacy_vars_only(self):
        mod = _load_telegram_config(
            env_overrides={
                "OPENCLAW_TELEGRAM_CHAT_ID": "legacy-chat",
                "OPENCLAW_TELEGRAM_BOT_TOKEN": "legacy-tok",
            },
        )
        assert mod.TELEGRAM_CHAT_ID == "legacy-chat"
        assert mod.TELEGRAM_BOT_TOKEN == "legacy-tok"
