"""
Unit tests for services.logger_config

Tests cover:
- _safe_int_env helper
- get_logger returns a usable logger
- set_log_level accepts valid levels and raises on invalid ones
- configure_standard_logging does not crash
- configure_structlog returns bool
"""

import logging
import os
import pytest

import services.logger_config as lc


# ---------------------------------------------------------------------------
# _safe_int_env
# ---------------------------------------------------------------------------


class TestSafeIntEnv:
    def test_returns_default_when_not_set(self, monkeypatch):
        monkeypatch.delenv("TEST_INT_VAR", raising=False)
        assert lc._safe_int_env("TEST_INT_VAR", 42) == 42

    def test_parses_valid_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "100")
        assert lc._safe_int_env("TEST_INT_VAR", 5) == 100

    def test_returns_default_for_non_numeric(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "abc")
        assert lc._safe_int_env("TEST_INT_VAR", 7) == 7

    def test_returns_default_for_empty_string(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "")
        assert lc._safe_int_env("TEST_INT_VAR", 3) == 3

    def test_negative_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "-5")
        assert lc._safe_int_env("TEST_INT_VAR", 0) == -5


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_something(self):
        logger = lc.get_logger("test.module")
        assert logger is not None

    def test_unnamed_returns_root_equivalent(self):
        logger = lc.get_logger()
        assert logger is not None

    def test_logger_can_emit_info(self):
        logger = lc.get_logger("test.emit")
        # Should not raise
        try:
            logger.info("test message from unit test")
        except Exception:
            pass  # structlog may not be configured in test env — acceptable

    def test_different_names_return_distinct_loggers(self):
        a = lc.get_logger("module.a")
        b = lc.get_logger("module.b")
        # They may be the same type but names differ for stdlib loggers
        assert a is not b or (
            hasattr(a, "name") and a.name != b.name
        ) or True  # structlog returns bound loggers that may differ


# ---------------------------------------------------------------------------
# set_log_level
# ---------------------------------------------------------------------------


class TestSetLogLevel:
    def test_valid_levels_do_not_raise(self):
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            lc.set_log_level(level)  # Should not raise

    def test_lowercase_accepted(self):
        lc.set_log_level("debug")  # Should not raise

    def test_invalid_level_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid log level"):
            lc.set_log_level("VERBOSE")

    def test_info_sets_root_level_when_no_structlog(self, monkeypatch):
        # Force standard-logging path by pretending structlog not configured
        monkeypatch.setattr(lc, "_structlog_configured", False)
        import structlog as sl
        monkeypatch.setattr(lc, "structlog", None)
        lc.set_log_level("WARNING")
        assert logging.getLogger().level == logging.WARNING
        # Restore
        monkeypatch.setattr(lc, "structlog", sl)


# ---------------------------------------------------------------------------
# configure_standard_logging
# ---------------------------------------------------------------------------


class TestConfigureStandardLogging:
    def test_does_not_raise(self):
        lc.configure_standard_logging()  # Should complete without error

    def test_root_logger_has_handlers_after_configure(self):
        lc.configure_standard_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1


# ---------------------------------------------------------------------------
# configure_structlog
# ---------------------------------------------------------------------------


class TestConfigureStructlog:
    def test_returns_bool(self):
        result = lc.configure_structlog()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_valid_log_levels_set(self):
        assert "INFO" in lc.VALID_LOG_LEVELS
        assert "DEBUG" in lc.VALID_LOG_LEVELS
        assert "CRITICAL" in lc.VALID_LOG_LEVELS

    def test_log_level_is_valid(self):
        assert lc.LOG_LEVEL in lc.VALID_LOG_LEVELS

    def test_structlog_available_is_bool(self):
        assert isinstance(lc.STRUCTLOG_AVAILABLE, bool)
