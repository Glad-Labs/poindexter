"""Tests for sentry_integration service."""

from importlib.util import find_spec
from unittest.mock import MagicMock, patch

import pytest

_has_sentry = find_spec("sentry_sdk") is not None


def _stub_site_config(values: dict | None = None) -> MagicMock:
    """Build a SiteConfig stub responding to ``.get``/``.get_float``/``.get_bool``.

    Mirrors the DI seam introduced in Phase H — sentry_integration no
    longer reaches for the deprecated module-level singleton, so tests
    inject a per-test stub instead of patching env vars.

    The typed accessors delegate to the same dict and reproduce the real
    ``SiteConfig`` coercion, including falling back to the default when the
    stored value is unparseable or the empty-string unset sentinel. A stub
    defining only ``.get`` would hand back a bare ``MagicMock`` from
    ``get_float`` and sail straight into ``sentry_sdk.init`` — assertions
    would pass while the real call site was broken.
    """
    cfg = MagicMock()
    data = values or {}

    def _get(key, default=""):
        return data.get(key, default)

    def _get_float(key, default=0.0):
        try:
            return float(data.get(key, default))
        except (ValueError, TypeError):
            return default

    def _get_bool(key, default=False):
        return str(data.get(key, default)).lower() in ("true", "1", "yes", "on")

    cfg.get = _get
    cfg.get_float = _get_float
    cfg.get_bool = _get_bool
    return cfg


@pytest.mark.skipif(not _has_sentry, reason="sentry-sdk not installed")
class TestSentryIntegration:
    """Tests for the SentryIntegration class."""

    def setup_method(self):
        """Reset class state before each test."""
        from services.sentry_integration import SentryIntegration

        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    def test_initialize_no_site_config_returns_false(self):
        """Without a DI'd SiteConfig the integration must skip cleanly."""
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        result = SentryIntegration.initialize(app)
        assert result is False
        assert SentryIntegration._initialized is False
        assert SentryIntegration._sentry_enabled is False

    def test_initialize_no_dsn_returns_false(self):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        cfg = _stub_site_config({"sentry_dsn": ""})
        result = SentryIntegration.initialize(app, cfg)
        assert result is False
        # _initialized intentionally stays False on the no-DSN path so the
        # lifespan re-init (after site_config loads) can retry once the DSN
        # is actually configured.
        assert SentryIntegration._initialized is False
        assert SentryIntegration._sentry_enabled is False

    def test_initialize_disabled_via_setting(self):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "false",
        })
        result = SentryIntegration.initialize(app, cfg)
        assert result is False
        assert SentryIntegration._sentry_enabled is False

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_initialize_success(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        app = MagicMock()
        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
            "environment": "production",
        })
        result = SentryIntegration.initialize(app, cfg)
        assert result is True
        assert SentryIntegration._sentry_enabled is True
        mock_sentry.init.assert_called_once()

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_sdk_debug_off_by_default_even_in_development(self, mock_sentry):
        """Default is debug=False everywhere — environment must not auto-enable it.

        The Sentry SDK's `debug=True` mode emits ~12 lines/sec under the
        `sentry_sdk.errors` logger name (DEBUG level, despite the name).
        Substring-matching error dashboards count every one as a false
        positive, producing ~290k spurious "errors"/day. Gating debug on
        an explicit DB key keeps the default quiet.
        """
        from services.sentry_integration import SentryIntegration

        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
            "environment": "development",
            # sentry_debug_logging deliberately absent → default false
        })
        SentryIntegration.initialize(MagicMock(), cfg)
        kwargs = mock_sentry.init.call_args.kwargs
        assert kwargs["debug"] is False, (
            "SDK debug-logging must default off even in development "
            "(false-positive error-count source)"
        )

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_sdk_debug_opt_in_via_legacy_alias(self, mock_sentry):
        """Legacy ``sentry_debug_logging`` still works (backcompat shim).

        This was the only name the code ever read, so an operator who set it
        by hand must keep working after the switch to ``sentry_sdk_debug``.
        """
        from services.sentry_integration import SentryIntegration

        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
            "environment": "production",
            "sentry_debug_logging": "true",
        })
        SentryIntegration.initialize(MagicMock(), cfg)
        kwargs = mock_sentry.init.call_args.kwargs
        assert kwargs["debug"] is True

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_sdk_debug_opt_in_via_canonical_key(self, mock_sentry):
        """``sentry_sdk_debug`` is the seeded key and must actually be read.

        Before this wiring it was seeded and documented but read by nothing,
        while the code read the never-seeded ``sentry_debug_logging`` — so the
        knob did nothing on any default install.
        """
        from services.sentry_integration import SentryIntegration

        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
            "environment": "production",
            "sentry_sdk_debug": "true",
        })
        SentryIntegration.initialize(MagicMock(), cfg)
        assert mock_sentry.init.call_args.kwargs["debug"] is True

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_sdk_debug_canonical_key_wins_over_legacy(self, mock_sentry):
        """An explicit canonical value overrides the legacy alias."""
        from services.sentry_integration import SentryIntegration

        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
            "environment": "production",
            "sentry_sdk_debug": "false",
            "sentry_debug_logging": "true",
        })
        SentryIntegration.initialize(MagicMock(), cfg)
        assert mock_sentry.init.call_args.kwargs["debug"] is False

    @patch("services.sentry_integration.sentry_sdk")
    def test_initialize_already_initialized_skips(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._initialized = True
        SentryIntegration._sentry_enabled = True
        app = MagicMock()
        cfg = _stub_site_config({"sentry_dsn": "https://key@sentry.io/123"})
        result = SentryIntegration.initialize(app, cfg)
        assert result is True
        mock_sentry.init.assert_not_called()

    @patch("services.sentry_integration.sentry_sdk")
    def test_initialize_sdk_init_raises(self, mock_sentry):
        from services.sentry_integration import SentryIntegration

        mock_sentry.init.side_effect = RuntimeError("init failed")
        app = MagicMock()
        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
        })
        result = SentryIntegration.initialize(app, cfg)
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

    def test_drops_graph_interrupt_control_flow_events(self):
        """LangGraph ``interrupt()`` pauses (approval gates) are control
        flow, not errors — Sentry's asyncio integration reports them from
        LangGraph's internal node tasks anyway (GlitchTip triage
        2026-07-02: 7 open issues were seo_refresh_gate pauses). The
        filter matches by class name across the MRO so it needs no
        langgraph import and catches subclasses."""
        from services.sentry_integration import SentryIntegration

        class GraphInterrupt(Exception):  # noqa: N818 — mirrors langgraph's name
            pass

        class ChildInterrupt(GraphInterrupt):
            pass

        for exc_cls in (GraphInterrupt, ChildInterrupt):
            exc = exc_cls("gate pause")
            hint = {"exc_info": (exc_cls, exc, None)}
            event = {"level": "error", "message": "boom"}
            assert SentryIntegration._before_send(event, hint) is None

    def test_keeps_real_exceptions_with_tuple_exc_info(self):
        """The GraphInterrupt drop must not swallow genuine errors."""
        from services.sentry_integration import SentryIntegration

        exc = RuntimeError("real failure")
        hint = {"exc_info": (RuntimeError, exc, None)}
        event = {"level": "error", "message": "boom"}
        assert SentryIntegration._before_send(event, hint) == event

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


class TestSetupSentryConvenience:
    """Test the setup_sentry convenience function."""

    def test_setup_sentry_delegates(self):
        from services.sentry_integration import SentryIntegration, setup_sentry

        app = MagicMock()
        cfg = _stub_site_config({"sentry_dsn": "https://k@s.io/1"})
        with patch.object(SentryIntegration, "initialize", return_value=True) as mock_init:
            result = setup_sentry(app, cfg, service_name="test-service")
        mock_init.assert_called_once_with(app, cfg, "test-service")
        assert result is True

    def test_setup_sentry_default_service_name(self):
        from services.sentry_integration import SentryIntegration, setup_sentry

        app = MagicMock()
        cfg = _stub_site_config({})
        with patch.object(SentryIntegration, "initialize", return_value=False) as mock_init:
            setup_sentry(app, cfg)
        mock_init.assert_called_once_with(app, cfg, "cofounder-agent")


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
        cfg = _stub_site_config({"sentry_dsn": "https://key@sentry.io/1"})
        result = SentryIntegration.initialize(app, cfg)
        assert result is False
        # Should not have set _initialized=True (returns early)
        assert SentryIntegration._initialized is False


@pytest.mark.skipif(not _has_sentry, reason="sentry-sdk not installed")
class TestSentrySampleRates:
    """Sample rates must come from app_settings, not a hardcoded literal.

    Both keys were seeded and documented in the settings reference, so an
    operator could set them — but ``sentry_sdk.init`` hardcoded
    ``0.1 if environment == "production" else 1.0`` and never read the rows.
    Setting either silently did nothing (Glad-Labs/poindexter#918), and every
    non-production process traced at 100%.
    """

    def setup_method(self):
        from services.sentry_integration import SentryIntegration

        SentryIntegration._initialized = False
        SentryIntegration._sentry_enabled = False

    def _init(self, mock_sentry, extra: dict) -> dict:
        from services.sentry_integration import SentryIntegration

        cfg = _stub_site_config({
            "sentry_dsn": "https://key@sentry.io/123",
            "sentry_enabled": "true",
            **extra,
        })
        SentryIntegration.initialize(MagicMock(), cfg)
        return mock_sentry.init.call_args.kwargs

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_operator_set_rates_are_honoured(self, mock_sentry):
        kwargs = self._init(mock_sentry, {
            "environment": "production",
            "sentry_traces_sample_rate": "0.5",
            "sentry_profiles_sample_rate": "0.25",
        })
        assert kwargs["traces_sample_rate"] == 0.5
        assert kwargs["profiles_sample_rate"] == 0.25

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    @pytest.mark.parametrize("environment", ["production", "development"])
    def test_unset_defaults_to_seeded_value_in_every_environment(
        self, mock_sentry, environment
    ):
        """Unset -> 0.1, matching the seeded default, in ALL environments.

        Deliberately NOT the old ``1.0`` outside production: the seed
        description records that literal as producing ~50 DEBUG lines/sec and
        a suspected contributor to the 2026-05-15 event-loop hang. The
        ``development`` case is the one that actually pins the fix — in
        production the old literal and the seeded default coincide at 0.1.
        """
        kwargs = self._init(mock_sentry, {"environment": environment})
        assert kwargs["traces_sample_rate"] == 0.1
        assert kwargs["profiles_sample_rate"] == 0.1

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_unparseable_and_empty_values_fall_back(self, mock_sentry):
        """'' is the documented unset sentinel; garbage must not crash init."""
        kwargs = self._init(mock_sentry, {
            "environment": "production",
            "sentry_traces_sample_rate": "",
            "sentry_profiles_sample_rate": "not-a-number",
        })
        assert kwargs["traces_sample_rate"] == 0.1
        assert kwargs["profiles_sample_rate"] == 0.1

    @patch("services.sentry_integration.SqlAlchemyIntegration", MagicMock())
    @patch("services.sentry_integration.sentry_sdk")
    def test_rates_are_real_floats_not_mocks(self, mock_sentry):
        """Guards the stub-shape trap that would make this suite vacuous.

        If the call site reads a typed accessor the stub does not define, the
        value silently becomes a ``MagicMock`` and the equality assertions
        above would still pass by identity.
        """
        kwargs = self._init(mock_sentry, {"environment": "production"})
        assert isinstance(kwargs["traces_sample_rate"], float)
        assert isinstance(kwargs["profiles_sample_rate"], float)
        assert isinstance(kwargs["debug"], bool)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("sentry_traces_sample_rate", "0.1"),
        ("sentry_profiles_sample_rate", "0.1"),
        ("sentry_sdk_debug", "false"),
    ],
)
def test_inline_default_matches_seeded_default(key: str, expected: str) -> None:
    """The call-site fallback must equal the seeded value.

    ``settings_seed_value_drift_lint`` locks the seed files to each other but
    structurally cannot see an inline ``site_config.get_float(key, <literal>)``
    fallback — an expression is not a seed row. If the two disagree, a fresh
    install (which gets the seeded row) and an install whose row was deleted
    (which gets the inline default) behave differently for the same key. Same
    rationale as ``test_inline_defaults_match_seed.py``.
    """
    import re
    from pathlib import Path

    seeds = (
        Path(__file__).resolve().parents[5]
        / "src"
        / "cofounder_agent"
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    ).read_text(encoding="utf-8")
    m = re.search(rf"VALUES \('{re.escape(key)}',\s*'([^']*)'", seeds)
    assert m, f"{key!r} is no longer seeded in 0000_baseline.seeds.sql"
    assert m.group(1) == expected, (
        f"seeded default for {key!r} is {m.group(1)!r} but the inline fallback in "
        f"services/sentry_integration.py is {expected!r} — make them agree"
    )
