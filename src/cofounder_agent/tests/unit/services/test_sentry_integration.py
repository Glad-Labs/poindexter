"""Tests for sentry_integration service."""

import os
from importlib.util import find_spec
from unittest.mock import MagicMock, patch

import pytest

_has_sentry = find_spec("sentry_sdk") is not None


def _mock_sc() -> MagicMock:
    """Return a MagicMock shaped like SiteConfig. Post-Phase-H, initialize()
    takes a site_config param rather than importing the module singleton.

    Keeps the existing env-var-driven tests working by falling back to
    ``os.getenv(KEY.upper())`` — the same contract the real SiteConfig.get
    honors for optional settings.
    """
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": os.getenv(k.upper(), d)
    return sc


@pytest.mark.skipif(not _has_sentry, reason="sentry-sdk not installed")
class TestSentryIntegration:
    """Tests for the SentryIntegration class."""

    def setup_method(self):
        """Reset class state before each test."""
        from services.sentry_integration import SentryIntegration

        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    def test_initialize_no_dsn_returns_false(self):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        with patch.dict("os.environ", {"SENTRY_DSN": ""}, clear=False):
            result = SentryIntegration.initialize(app, _mock_sc())
        assert result is False
        # _initialized intentionally stays False on the no-DSN path so the
        # lifespan re-init (after site_config loads) can retry once the DSN
        # is actually configured. See sentry_integration.py:102-104.
        assert SentryIntegration._initialized is False
        assert SentryIntegration._sentry_enabled is False

    def test_initialize_disabled_via_env(self):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        with patch.dict(
            "os.environ",
            {"SENTRY_DSN": "https://key@sentry.io/123", "SENTRY_ENABLED": "false"},
            clear=False,
        ):
            result = SentryIntegration.initialize(app, _mock_sc())
        assert result is False
        assert SentryIntegration._sentry_enabled is False

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_initialize_success(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        with patch.dict(
            "os.environ",
            {
                "SENTRY_DSN": "https://key@sentry.io/123",
                "SENTRY_ENABLED": "true",
                "ENVIRONMENT": "production",
            },
            clear=False,
        ):
            result = SentryIntegration.initialize(app, _mock_sc())
        assert result is True
        assert SentryIntegration._sentry_enabled is True
        mock_sentry.init.assert_called_once()

    @patch("services.sentry_integration.sentry_sdk")
    def test_initialize_already_initialized_skips(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._initialized = True
        SentryIntegration._sentry_enabled = True
        app = MagicMock()
        result = SentryIntegration.initialize(app, _mock_sc())
        assert result is True
        mock_sentry.init.assert_not_called()

    @patch("services.sentry_integration.sentry_sdk")
    def test_initialize_sdk_init_raises(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        mock_sentry.init.side_effect = RuntimeError("init failed")
        app = MagicMock()
        with patch.dict(
            "os.environ",
            {"SENTRY_DSN": "https://key@sentry.io/123", "SENTRY_ENABLED": "true"},
            clear=False,
        ):
            result = SentryIntegration.initialize(app, _mock_sc())
        assert result is False
        assert SentryIntegration._sentry_enabled is False

    def test_capture_exception_when_disabled_is_noop(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        SentryIntegration.capture_exception(ValueError("test"))

    @patch("services.sentry_integration.sentry_sdk")
    def test_capture_exception_with_context(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)
        err = ValueError("test")
        SentryIntegration.capture_exception(err, context={"extra": {"key": "val"}})
        mock_sentry.capture_exception.assert_called_once_with(err)

    def test_capture_message_when_disabled_is_noop(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        SentryIntegration.capture_message("test msg")

    @patch("services.sentry_integration.sentry_sdk")
    def test_capture_message_calls_sdk(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)
        SentryIntegration.capture_message("hello", level="warning")
        mock_sentry.capture_message.assert_called_once_with("hello", level="warning")

    @patch("services.sentry_integration.sentry_sdk")
    def test_set_user_context(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        SentryIntegration.set_user_context("u1", email="a@b.com", username="matt")
        mock_sentry.set_user.assert_called_once_with(
            {"id": "u1", "email": "a@b.com", "username": "matt"}
        )

    @patch("services.sentry_integration.sentry_sdk")
    def test_clear_user_context(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        SentryIntegration.clear_user_context()
        mock_sentry.set_user.assert_called_once_with(None)

    @patch("services.sentry_integration.sentry_sdk")
    def test_add_breadcrumb(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        SentryIntegration.add_breadcrumb("api.call", "fetched data", data={"url": "/api"})
        mock_sentry.add_breadcrumb.assert_called_once_with(
            category="api.call",
            message="fetched data",
            level="info",
            data={"url": "/api"},
        )

    @patch("services.sentry_integration.sentry_sdk")
    def test_start_transaction(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_sentry.start_transaction.return_value = MagicMock()
        txn = SentryIntegration.start_transaction("test-op", op="task")
        assert txn is not None
        mock_sentry.start_transaction.assert_called_once()

    def test_start_transaction_disabled_returns_none(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        result = SentryIntegration.start_transaction("test")
        assert result is None

    def test_get_initialized_status(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        assert SentryIntegration.get_initialized_status() is False
        SentryIntegration._sentry_enabled = True
        assert SentryIntegration.get_initialized_status() is True


class TestBeforeSend:
    """Tests for the _before_send event filter."""

    def test_redacts_authorization_header(self):
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "error",
            "request": {
                "headers": {"authorization": "Bearer secret123"},
                "url": "https://api.example.com/data",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert result["request"]["headers"]["authorization"] == "[REDACTED]"

    def test_redacts_api_key_in_url(self):
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "error",
            "request": {
                "headers": {},
                "url": "https://api.example.com/data?api_key=secret123",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert "secret123" not in result["request"]["url"]

    def test_passes_through_non_error_events(self):
        from services.sentry_integration import SentryIntegration

        event = {"level": "info", "message": "hello"}
        result = SentryIntegration._before_send(event, {})
        assert result == event

    def test_redacts_multiple_sensitive_headers(self):
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "error",
            "request": {
                "headers": {
                    "authorization": "Bearer x",
                    "cookie": "session=abc",
                    "x-api-key": "key123",
                    "x-token": "tok",
                },
                "url": "https://example.com",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        for h in ["authorization", "cookie", "x-api-key", "x-token"]:
            assert result["request"]["headers"][h] == "[REDACTED]"


class TestFingerprintNormalization:
    """Sentry's default fingerprint hashes the event message; structlog's
    ConsoleRenderer prepends a colorized ISO timestamp to every record, so
    identical errors fingerprint differently per occurrence and GlitchTip
    spawns a new issue every time. _before_send must strip the ANSI prefix
    and leading timestamp from message-shaped fields so the same logical
    error groups properly. See gitea #290."""

    def test_strips_ansi_and_iso_timestamp_from_logentry_message(self):
        from services.sentry_integration import SentryIntegration

        # Real example from a worker GlitchTip event (issue 994):
        raw = (
            "\x1b[2m2026-04-26T04:07:44.763943Z\x1b[0m [\x1b[31m\x1b[1mcritical \x1b[0m] "
            "[task_executor] Executor has not signalled in 1800 seconds"
        )
        event = {"level": "error", "logentry": {"message": raw, "formatted": raw}}
        result = SentryIntegration._before_send(event, {})
        cleaned = "[critical ] [task_executor] Executor has not signalled in 1800 seconds"
        assert result["logentry"]["message"] == cleaned
        assert result["logentry"]["formatted"] == cleaned

    def test_two_occurrences_normalize_to_identical_messages(self):
        """The whole point of the fix: a recurring error must produce the
        same Sentry event message regardless of when it happened."""
        from services.sentry_integration import SentryIntegration

        first = "\x1b[2m2026-04-26T04:07:44.763943Z\x1b[0m [task_executor] heartbeat lost"
        second = "\x1b[2m2026-04-26T04:37:50.123456Z\x1b[0m [task_executor] heartbeat lost"

        e1 = {"logentry": {"message": first}}
        e2 = {"logentry": {"message": second}}
        SentryIntegration._before_send(e1, {})
        SentryIntegration._before_send(e2, {})
        assert e1["logentry"]["message"] == e2["logentry"]["message"]

    def test_normalizes_top_level_message_too(self):
        """Manual capture_message() calls populate event['message'] directly,
        bypassing logentry. Normalize there too."""
        from services.sentry_integration import SentryIntegration

        event = {
            "message": "\x1b[2m2026-04-26T04:07:44.763943Z\x1b[0m [worker] something bad"
        }
        result = SentryIntegration._before_send(event, {})
        assert result["message"] == "[worker] something bad"

    def test_leaves_clean_message_untouched(self):
        from services.sentry_integration import SentryIntegration

        event = {"logentry": {"message": "plain message no prefix"}}
        result = SentryIntegration._before_send(event, {})
        assert result["logentry"]["message"] == "plain message no prefix"

    def test_handles_missing_logentry_gracefully(self):
        from services.sentry_integration import SentryIntegration

        # Event with neither logentry nor message — must not raise.
        event = {"level": "error", "request": {"headers": {}, "url": "https://x"}}
        result = SentryIntegration._before_send(event, {"exc_info": True})
        assert result is event  # mutated in place, returned as-is


class TestSetupSentryConvenience:
    """Test the setup_sentry convenience function."""

    def test_setup_sentry_delegates(self):
        from services.sentry_integration import SentryIntegration, setup_sentry

        app = MagicMock()
        sc = _mock_sc()
        with patch.object(SentryIntegration, "initialize", return_value=True) as mock_init:
            result = setup_sentry(app, sc, "test-service")
        mock_init.assert_called_once_with(app, sc, "test-service")
        assert result is True

    def test_setup_sentry_default_service_name(self):
        from services.sentry_integration import SentryIntegration, setup_sentry

        app = MagicMock()
        sc = _mock_sc()
        with patch.object(SentryIntegration, "initialize", return_value=False) as mock_init:
            setup_sentry(app, sc)
        mock_init.assert_called_once_with(app, sc, "cofounder-agent")


class TestCaptureExceptionEdgeCases:
    """Exception-path coverage for capture_exception."""

    def setup_method(self):
        from services.sentry_integration import SentryIntegration
        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    @patch("services.sentry_integration.sentry_sdk")
    def test_swallows_internal_exception(self, mock_sentry):
        """If sentry_sdk.capture_exception itself raises, the call should not propagate."""
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)
        mock_sentry.capture_exception.side_effect = RuntimeError("sentry down")

        # Should not raise
        SentryIntegration.capture_exception(ValueError("app error"))

    @patch("services.sentry_integration.sentry_sdk")
    def test_no_context_no_set_context(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)

        SentryIntegration.capture_exception(ValueError("e"))
        mock_scope.set_context.assert_not_called()
        mock_scope.set_level.assert_called_once_with("error")

    @patch("services.sentry_integration.sentry_sdk")
    def test_custom_level_passed_to_scope(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)

        SentryIntegration.capture_exception(ValueError("e"), level="warning")
        mock_scope.set_level.assert_called_once_with("warning")


class TestCaptureMessageEdgeCases:
    def setup_method(self):
        from services.sentry_integration import SentryIntegration
        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    @patch("services.sentry_integration.sentry_sdk")
    def test_swallows_internal_exception(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)
        mock_sentry.capture_message.side_effect = RuntimeError("down")

        SentryIntegration.capture_message("hello")  # should not raise

    @patch("services.sentry_integration.sentry_sdk")
    def test_with_context_sets_each_key(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)

        SentryIntegration.capture_message(
            "hello",
            level="info",
            context={"task": {"id": "abc"}, "user": {"id": "u1"}},
        )

        # set_context called for each key in context
        assert mock_scope.set_context.call_count == 2


class TestUserContextEdgeCases:
    def setup_method(self):
        from services.sentry_integration import SentryIntegration
        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    def test_set_user_disabled_is_noop(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        SentryIntegration.set_user_context("u1", "a@b.com", "matt")  # should not raise

    def test_clear_user_disabled_is_noop(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        SentryIntegration.clear_user_context()  # should not raise

    @patch("services.sentry_integration.sentry_sdk")
    def test_set_user_swallows_exception(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_sentry.set_user.side_effect = RuntimeError("down")
        SentryIntegration.set_user_context("u1")  # should not raise

    @patch("services.sentry_integration.sentry_sdk")
    def test_clear_user_swallows_exception(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_sentry.set_user.side_effect = RuntimeError("down")
        SentryIntegration.clear_user_context()  # should not raise

    @patch("services.sentry_integration.sentry_sdk")
    def test_set_user_default_email_and_username_empty(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        SentryIntegration.set_user_context("u1")
        mock_sentry.set_user.assert_called_once_with(
            {"id": "u1", "email": "", "username": ""}
        )


class TestBreadcrumbEdgeCases:
    def setup_method(self):
        from services.sentry_integration import SentryIntegration
        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    def test_disabled_is_noop(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = False
        SentryIntegration.add_breadcrumb("cat", "msg")  # should not raise

    @patch("services.sentry_integration.sentry_sdk")
    def test_swallows_exception(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_sentry.add_breadcrumb.side_effect = RuntimeError("down")
        SentryIntegration.add_breadcrumb("cat", "msg")  # should not raise

    @patch("services.sentry_integration.sentry_sdk")
    def test_default_data_is_empty_dict(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        SentryIntegration.add_breadcrumb("cat", "msg")
        kwargs = mock_sentry.add_breadcrumb.call_args.kwargs
        assert kwargs["data"] == {}


class TestStartTransactionEdgeCases:
    def setup_method(self):
        from services.sentry_integration import SentryIntegration
        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    @patch("services.sentry_integration.sentry_sdk")
    def test_swallows_exception_returns_none(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        mock_sentry.start_transaction.side_effect = RuntimeError("down")
        result = SentryIntegration.start_transaction("test")
        assert result is None

    @patch("services.sentry_integration.sentry_sdk")
    def test_passes_op_and_description(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._sentry_enabled = True
        SentryIntegration.start_transaction("my-task", op="task", description="A task")
        mock_sentry.start_transaction.assert_called_once_with(
            name="my-task", op="task", description="A task"
        )


class TestBeforeSendEdgeCases:
    def test_no_request_in_event(self):
        from services.sentry_integration import SentryIntegration

        event = {"level": "error", "message": "boom"}
        result = SentryIntegration._before_send(event, {"exc_info": True})
        # Should not crash, returns the event unchanged
        assert result is event

    def test_no_url_in_request(self):
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "error",
            "request": {"headers": {"authorization": "Bearer x"}},
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        # Authorization header redacted, no crash on missing URL
        assert result["request"]["headers"]["authorization"] == "[REDACTED]"

    def test_url_without_api_key(self):
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "error",
            "request": {
                "headers": {},
                "url": "https://api.example.com/posts",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        # URL unchanged
        assert result["request"]["url"] == "https://api.example.com/posts"

    def test_headers_without_sensitive_keys(self):
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "error",
            "request": {
                "headers": {"content-type": "application/json", "user-agent": "test"},
                "url": "https://example.com",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": True})
        # No redaction needed
        assert result["request"]["headers"]["content-type"] == "application/json"

    def test_warning_level_passes_through(self):
        from services.sentry_integration import SentryIntegration

        event = {"level": "warning", "message": "warn"}
        result = SentryIntegration._before_send(event, {})
        assert result == event

    def test_exc_info_in_hint_triggers_redaction(self):
        """Even if level isn't 'error', presence of exc_info in hint triggers redaction."""
        from services.sentry_integration import SentryIntegration

        event = {
            "level": "info",  # not error
            "request": {
                "headers": {"authorization": "Bearer secret"},
                "url": "https://example.com",
            },
        }
        result = SentryIntegration._before_send(event, {"exc_info": ValueError("e")})
        assert result["request"]["headers"]["authorization"] == "[REDACTED]"


class TestInitializeSdkUnavailable:
    def setup_method(self):
        from services.sentry_integration import SentryIntegration
        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    @patch("services.sentry_integration.SENTRY_AVAILABLE", False)
    @patch("services.sentry_integration.sentry_sdk", None)
    def test_returns_false_when_sdk_not_installed(self):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        result = SentryIntegration.initialize(app, _mock_sc())
        assert result is False
        # Should not have set _initialized=True (returns early)
        assert SentryIntegration._initialized is False
