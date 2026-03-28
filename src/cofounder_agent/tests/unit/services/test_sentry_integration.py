"""
Unit tests for services/sentry_integration.py

Tests the SentryIntegration class and setup_sentry helper.
Because Sentry is optional infrastructure, all tests run with
_sentry_enabled = False (no real Sentry calls).
"""

from unittest.mock import MagicMock

import pytest

from services.sentry_integration import SentryIntegration, setup_sentry


@pytest.fixture(autouse=True)
def reset_sentry_state():
    """Reset class-level state between tests."""
    SentryIntegration._initialized = False
    SentryIntegration._sentry_enabled = False
    yield
    SentryIntegration._initialized = False
    SentryIntegration._sentry_enabled = False


class TestSentryInitialize:
    def test_returns_false_when_dsn_not_set(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        app = MagicMock()
        result = SentryIntegration.initialize(app)
        assert result is False
        assert SentryIntegration._initialized is True
        assert SentryIntegration._sentry_enabled is False

    def test_returns_false_when_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.io/123")
        monkeypatch.setenv("SENTRY_ENABLED", "false")
        app = MagicMock()
        result = SentryIntegration.initialize(app)
        assert result is False

    def test_skips_reinitialization_if_already_initialized(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        app = MagicMock()
        SentryIntegration.initialize(app)
        # Mark as enabled manually to detect second call behavior
        SentryIntegration._sentry_enabled = True
        result = SentryIntegration.initialize(app)
        # Should return current _sentry_enabled without re-running init
        assert result is True

    def test_get_initialized_status_false_by_default(self):
        assert SentryIntegration.get_initialized_status() is False


class TestSentryDisabledOperations:
    """When Sentry is disabled, all operations must be no-ops (no exceptions)."""

    def test_capture_exception_noop_when_disabled(self):
        SentryIntegration._sentry_enabled = False
        # Should not raise
        SentryIntegration.capture_exception(ValueError("test error"))

    def test_capture_message_noop_when_disabled(self):
        SentryIntegration._sentry_enabled = False
        SentryIntegration.capture_message("hello", level="info")

    def test_set_user_context_noop_when_disabled(self):
        SentryIntegration._sentry_enabled = False
        SentryIntegration.set_user_context("user-1", "user@example.com")

    def test_clear_user_context_noop_when_disabled(self):
        SentryIntegration._sentry_enabled = False
        SentryIntegration.clear_user_context()

    def test_add_breadcrumb_noop_when_disabled(self):
        SentryIntegration._sentry_enabled = False
        SentryIntegration.add_breadcrumb("auth", "user logged in")

    def test_start_transaction_returns_none_when_disabled(self):
        SentryIntegration._sentry_enabled = False
        result = SentryIntegration.start_transaction("my-tx", op="task")
        assert result is None


class TestBeforeSendFilter:
    def test_redacts_authorization_header(self):
        event = {
            "level": "error",
            "request": {"headers": {"authorization": "Bearer secret-token"}},
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert result is not None
        assert result["request"]["headers"]["authorization"] == "[REDACTED]"

    def test_redacts_cookie_header(self):
        event = {
            "level": "error",
            "request": {"headers": {"cookie": "session=abc123"}},
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert result is not None
        assert result["request"]["headers"]["cookie"] == "[REDACTED]"

    def test_redacts_api_key_header(self):
        event = {
            "level": "error",
            "request": {"headers": {"x-api-key": "my-api-key"}},
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert result is not None
        assert result["request"]["headers"]["x-api-key"] == "[REDACTED]"

    def test_non_sensitive_header_preserved(self):
        event = {
            "level": "error",
            "request": {"headers": {"content-type": "application/json"}},
        }
        result = SentryIntegration._before_send(event, {})
        assert result is not None
        assert result["request"]["headers"]["content-type"] == "application/json"

    def test_event_without_request_passes_through(self):
        event = {"level": "error", "message": "something failed"}
        result = SentryIntegration._before_send(event, {})
        assert result is event

    def test_returns_event_unchanged_for_non_error_without_exc_info(self):
        event = {"level": "info", "message": "hello"}
        result = SentryIntegration._before_send(event, {})
        assert result is event

    def test_redacts_api_key_query_param(self):
        event = {
            "level": "error",
            "request": {
                "headers": {},
                "url": "https://api.example.com/search?api_key=secret&q=test",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert result is not None
        assert "api_key=[REDACTED]" in result["request"]["url"]
        assert "secret" not in result["request"]["url"]


class TestSetupSentry:
    def test_setup_sentry_delegates_to_initialize(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        app = MagicMock()
        result = setup_sentry(app, service_name="test-service")
        assert result is False  # DSN not configured → False
