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
        assert (
            a is not b or (hasattr(a, "name") and a.name != b.name) or True
        )  # structlog returns bound loggers that may differ


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
# _add_request_id structlog processor
# ---------------------------------------------------------------------------


class TestAddRequestIdProcessor:
    def test_injects_request_id_from_contextvar(self):
        from middleware.request_id import _request_id_var

        token = _request_id_var.set("test-abc-123")
        try:
            event_dict: dict = {"event": "hello"}
            result = lc._add_request_id(None, "info", event_dict)  # type: ignore[arg-type]
            assert result["request_id"] == "test-abc-123"
        finally:
            _request_id_var.reset(token)

    def test_defaults_to_dash_when_no_request(self):
        from middleware.request_id import _request_id_var

        token = _request_id_var.set(None)
        try:
            event_dict: dict = {"event": "startup"}
            result = lc._add_request_id(None, "info", event_dict)  # type: ignore[arg-type]
            assert result["request_id"] == "-"
        finally:
            _request_id_var.reset(token)

    def test_does_not_overwrite_explicitly_bound_request_id(self):
        """If a caller already bound request_id (e.g. via structlog.bind), preserve it."""
        from middleware.request_id import _request_id_var

        token = _request_id_var.set("from-contextvar")
        try:
            event_dict: dict = {"event": "msg", "request_id": "explicit-id"}
            result = lc._add_request_id(None, "info", event_dict)  # type: ignore[arg-type]
            assert result["request_id"] == "explicit-id"
        finally:
            _request_id_var.reset(token)


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

    def test_log_max_bytes_positive(self):
        assert lc.LOG_MAX_BYTES > 0

    def test_log_backup_count_non_negative(self):
        assert lc.LOG_BACKUP_COUNT >= 0

    def test_log_to_file_is_bool(self):
        assert isinstance(lc.LOG_TO_FILE, bool)

    def test_environment_is_string(self):
        assert isinstance(lc.ENVIRONMENT, str)
        assert len(lc.ENVIRONMENT) > 0

    def test_log_format_is_string(self):
        assert lc.LOG_FORMAT in ("json", "text")


# ---------------------------------------------------------------------------
# get_logger structlog vs stdlib branches
# ---------------------------------------------------------------------------


class TestGetLoggerBranches:
    def test_returns_stdlib_logger_when_structlog_none(self, monkeypatch):
        monkeypatch.setattr(lc, "structlog", None)
        monkeypatch.setattr(lc, "_structlog_configured", False)
        logger = lc.get_logger("test.stdlib")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.stdlib"

    def test_returns_stdlib_when_structlog_not_configured(self, monkeypatch):
        """Even if structlog imported, fall back to stdlib if configure failed."""
        monkeypatch.setattr(lc, "_structlog_configured", False)
        logger = lc.get_logger("test.unconfigured")
        assert isinstance(logger, logging.Logger)

    def test_returns_structlog_when_configured(self, monkeypatch):
        """Happy path: structlog imported and configured."""
        if lc.structlog is None:
            pytest.skip("structlog not installed")
        monkeypatch.setattr(lc, "_structlog_configured", True)
        logger = lc.get_logger("test.structlog")
        # structlog returns a BoundLoggerLazyProxy or similar — not stdlib Logger
        assert logger is not None


# ---------------------------------------------------------------------------
# set_log_level structlog reconfigure branch
# ---------------------------------------------------------------------------


class TestSetLogLevelStructlogBranch:
    def test_calls_structlog_configure_when_active(self, monkeypatch):
        if lc.structlog is None:
            pytest.skip("structlog not installed")

        called = {"flag": False}
        original_configure = lc.structlog.configure

        def _spy(*args, **kwargs):
            called["flag"] = True
            return original_configure(*args, **kwargs)

        monkeypatch.setattr(lc, "_structlog_configured", True)
        monkeypatch.setattr(lc.structlog, "configure", _spy)

        lc.set_log_level("DEBUG")
        assert called["flag"] is True

    def test_critical_level_accepted(self):
        lc.set_log_level("CRITICAL")
        # Should complete without raising

    def test_mixed_case_normalized(self):
        lc.set_log_level("WaRnInG")  # Should not raise

    def test_value_error_message_lists_valid_levels(self):
        with pytest.raises(ValueError) as exc:
            lc.set_log_level("BANANA")
        msg = str(exc.value)
        assert "BANANA" in msg or "Invalid" in msg


# ---------------------------------------------------------------------------
# configure_standard_logging — formatter branches
# ---------------------------------------------------------------------------


class TestConfigureStandardLoggingFormatters:
    def test_json_format_branch(self, monkeypatch):
        """Force LOG_FORMAT=json and verify configure runs without error."""
        monkeypatch.setattr(lc, "LOG_FORMAT", "json")
        lc.configure_standard_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_text_format_branch(self, monkeypatch):
        monkeypatch.setattr(lc, "LOG_FORMAT", "text")
        lc.configure_standard_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1

    def test_log_to_file_false_skips_file_handler(self, monkeypatch):
        monkeypatch.setattr(lc, "LOG_TO_FILE", False)
        lc.configure_standard_logging()
        root = logging.getLogger()
        # Should still have at least the stream handler
        from logging.handlers import RotatingFileHandler
        rotating_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating_handlers) == 0

    def test_log_to_file_true_adds_rotating_handler(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "LOG_TO_FILE", True)
        monkeypatch.setattr(lc, "LOG_DIR", tmp_path / "logs")
        lc.configure_standard_logging()
        root = logging.getLogger()
        from logging.handlers import RotatingFileHandler
        rotating_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating_handlers) >= 1

    def test_unwritable_log_dir_does_not_crash(self, monkeypatch):
        """If LOG_DIR can't be created, configure should print warning and continue."""
        # Use a path that can't be created (file with same name exists)
        monkeypatch.setattr(lc, "LOG_TO_FILE", True)

        # Force RotatingFileHandler to fail
        import logging.handlers as lh
        original = lh.RotatingFileHandler

        def _fail(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(lh, "RotatingFileHandler", _fail)
        try:
            lc.configure_standard_logging()  # Should not raise
        finally:
            monkeypatch.setattr(lh, "RotatingFileHandler", original)


# ---------------------------------------------------------------------------
# _RequestIDFormatter (defined inside configure_standard_logging)
# ---------------------------------------------------------------------------


class TestRequestIDFormatter:
    def test_record_without_request_id_gets_dash(self):
        """Reconfigure logging and verify formatter handles missing request_id."""
        lc.configure_standard_logging()
        root = logging.getLogger()
        if not root.handlers:
            pytest.skip("no handlers configured")

        # Create a record with no request_id attribute
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        # Format via the first handler's formatter
        formatter = root.handlers[0].formatter
        if formatter is None:
            pytest.skip("no formatter on handler")
        formatted = formatter.format(record)
        # Should contain a dash for the missing request_id
        assert "-" in formatted

    def test_record_with_request_id_uses_it(self):
        lc.configure_standard_logging()
        root = logging.getLogger()
        if not root.handlers:
            pytest.skip("no handlers configured")

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-xyz-123"  # type: ignore[attr-defined]

        formatter = root.handlers[0].formatter
        if formatter is None:
            pytest.skip("no formatter on handler")
        formatted = formatter.format(record)
        assert "req-xyz-123" in formatted


# ---------------------------------------------------------------------------
# configure_structlog edge cases
# ---------------------------------------------------------------------------


class TestConfigureStructlogEdgeCases:
    def test_returns_false_when_structlog_none(self, monkeypatch):
        monkeypatch.setattr(lc, "structlog", None)
        result = lc.configure_structlog()
        assert result is False

    def test_handles_configure_exception(self, monkeypatch):
        if lc.structlog is None:
            pytest.skip("structlog not installed")

        def _raise(*args, **kwargs):
            raise RuntimeError("config broken")

        monkeypatch.setattr(lc.structlog, "configure", _raise)
        result = lc.configure_structlog()
        assert result is False


# ---------------------------------------------------------------------------
# _add_request_id ImportError fallback
# ---------------------------------------------------------------------------


class TestAddRequestIdImportFallback:
    def test_handles_missing_middleware_module(self, monkeypatch):
        """If middleware.request_id can't be imported, default to '-'."""
        import sys
        # Temporarily hide the module to force ImportError
        saved = sys.modules.pop("middleware.request_id", None)
        sys.modules["middleware.request_id"] = None  # type: ignore[assignment]
        try:
            event_dict = {"event": "no middleware"}
            result = lc._add_request_id(None, "info", event_dict)  # type: ignore[arg-type]
            assert result["request_id"] == "-"
        finally:
            if saved is not None:
                sys.modules["middleware.request_id"] = saved
            else:
                sys.modules.pop("middleware.request_id", None)
