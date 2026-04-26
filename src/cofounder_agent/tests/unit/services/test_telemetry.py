"""
Unit tests for services.telemetry.setup_telemetry

Tests cover:
- Function does not raise when OpenTelemetry is unavailable (OPENTELEMETRY_AVAILABLE=False)
- Function returns early when ENABLE_TRACING is not "true"
- Function returns early when ENABLE_TRACING=true but components are None
- Module-level constants and guard variables
- OTLP endpoint configured — span processor added, FastAPI instrumented
- OTLP endpoint not configured — no-op provider, no span processor
- OTLP exporter setup failure is caught and logged
- set_tracer_provider RuntimeError is caught when provider already set
- FastAPIInstrumentor error is caught and logged
- OpenAIInstrumentor instrumentation (available / unavailable)
- Idempotency latch (_initialized) — second call short-circuits
- No-op early returns DO NOT set the latch (so lifespan re-init can retry)
"""

import os
from unittest.mock import MagicMock

import pytest

import services.telemetry as telemetry_mod
from services.site_config import SiteConfig
from services.telemetry import setup_telemetry


@pytest.fixture(autouse=True)
def _reset_telemetry_initialized_latch():
    """Reset the module-level ``_initialized`` latch around every test.

    setup_telemetry uses a module-level latch (mirrors
    SentryIntegration._initialized) to make the lifespan re-init safe.
    Without a reset, the first test that successfully instruments the
    app sticks the latch True for the remainder of the test session,
    which makes every later test's ``setup_telemetry(...)`` short-circuit
    before reaching the assertions about Resource / TracerProvider /
    FastAPIInstrumentor.
    """
    telemetry_mod._initialized = False
    yield
    telemetry_mod._initialized = False


def _sc() -> SiteConfig:
    """Fresh SiteConfig instance — Phase H DI (GH#95).

    Constructs a stand-alone SiteConfig with no initial_config so the
    ``.get()`` lookups fall through to the env vars the tests are
    already setting via ``monkeypatch.setenv``.
    """
    return SiteConfig()

# ---------------------------------------------------------------------------
# When OpenTelemetry is not available
# ---------------------------------------------------------------------------


class TestSetupTelemetryNotAvailable:
    def test_returns_early_when_not_available(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", False)
        app = MagicMock()
        # Should not raise and should not instrument anything
        setup_telemetry(app, _sc())

    def test_returns_early_when_trace_is_none(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.setattr(telemetry_mod, "trace", None)
        app = MagicMock()
        setup_telemetry(app, _sc())
        # No instrumentation should happen — app should not have been touched
        app.assert_not_called()


# ---------------------------------------------------------------------------
# When tracing disabled via env var
# ---------------------------------------------------------------------------


class TestSetupTelemetryTracingDisabled:
    def test_returns_early_when_enable_tracing_false(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.setenv("ENABLE_TRACING", "false")
        app = MagicMock()
        setup_telemetry(app, _sc())

    def test_returns_early_when_enable_tracing_missing(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.delenv("ENABLE_TRACING", raising=False)
        app = MagicMock()
        setup_telemetry(app, _sc())

    def test_returns_early_when_enable_tracing_mixed_case(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.setenv("ENABLE_TRACING", "True")  # Not lowercase "true"
        # "True" != "true" per the .lower() == "true" check
        app = MagicMock()
        setup_telemetry(app, _sc())


# ---------------------------------------------------------------------------
# When everything is available (mocked) — no OTLP endpoint
# ---------------------------------------------------------------------------


class TestSetupTelemetryNoEndpoint:
    def test_no_otlp_endpoint_does_not_raise(self, monkeypatch):
        """When ENABLE_TRACING=true but no OTLP endpoint, we get a warning but no crash."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        # Mock all OTel components
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()

        mock_provider = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)

        mock_instrumentor_cls = MagicMock()
        mock_instrumentor_cls.instrument_app = MagicMock()

        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.setattr(telemetry_mod, "Resource", mock_resource_cls)
        monkeypatch.setattr(telemetry_mod, "TracerProvider", mock_provider_cls)
        monkeypatch.setattr(telemetry_mod, "OTLPSpanExporter", MagicMock())
        monkeypatch.setattr(telemetry_mod, "BatchSpanProcessor", MagicMock())
        monkeypatch.setattr(telemetry_mod, "FastAPIInstrumentor", mock_instrumentor_cls)

        mock_trace = MagicMock()
        monkeypatch.setattr(telemetry_mod, "trace", mock_trace)

        app = MagicMock()
        setup_telemetry(app, _sc(), service_name="test-service")
        # Should complete without exception


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_opentelemetry_available_is_bool(self):
        assert isinstance(telemetry_mod.OPENTELEMETRY_AVAILABLE, bool)

    def test_openai_instrumentor_is_none_or_class(self):
        oi = getattr(telemetry_mod, "OpenAIInstrumentor", None)
        # Either None (if not installed) or a class
        assert oi is None or callable(oi)


# ---------------------------------------------------------------------------
# Full setup path with OTLP endpoint configured
# ---------------------------------------------------------------------------


def _make_mock_components():
    """Return a dict of monkeypatch-ready mock OTel components."""
    mock_resource_cls = MagicMock()
    mock_resource_cls.create.return_value = MagicMock()

    mock_provider = MagicMock()
    mock_provider_cls = MagicMock(return_value=mock_provider)

    mock_exporter = MagicMock()
    mock_exporter_cls = MagicMock(return_value=mock_exporter)

    mock_processor = MagicMock()
    mock_processor_cls = MagicMock(return_value=mock_processor)

    mock_instrumentor_cls = MagicMock()
    mock_instrumentor_cls.instrument_app = MagicMock()

    mock_trace = MagicMock()

    return {
        "Resource": mock_resource_cls,
        "TracerProvider": mock_provider_cls,
        "OTLPSpanExporter": mock_exporter_cls,
        "BatchSpanProcessor": mock_processor_cls,
        "FastAPIInstrumentor": mock_instrumentor_cls,
        "trace": mock_trace,
        "_provider": mock_provider,
        "_exporter": mock_exporter,
        "_processor": mock_processor,
    }


def _apply_mocks(monkeypatch, mocks):
    monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
    for key in (
        "Resource",
        "TracerProvider",
        "OTLPSpanExporter",
        "BatchSpanProcessor",
        "FastAPIInstrumentor",
        "trace",
    ):
        monkeypatch.setattr(telemetry_mod, key, mocks[key])


class TestSetupTelemetryWithOtlpEndpoint:
    def test_span_processor_added_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock(), _sc(), service_name="test-svc")

        mocks["_provider"].add_span_processor.assert_called_once()

    def test_fastapi_instrumented_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        app = MagicMock()
        setup_telemetry(app, _sc(), service_name="test-svc")

        mocks["FastAPIInstrumentor"].instrument_app.assert_called_once()

    def test_tracer_provider_set_globally(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock(), _sc())

        mocks["trace"].set_tracer_provider.assert_called_once_with(mocks["_provider"])

    def test_service_name_passed_to_resource(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock(), _sc(), service_name="my-custom-service")

        call_kwargs = mocks["Resource"].create.call_args
        attrs = call_kwargs[1].get("attributes") or (call_kwargs[0][0] if call_kwargs[0] else {})
        assert attrs.get("service.name") == "my-custom-service"

    def test_otlp_exporter_setup_failure_is_caught(self, monkeypatch):
        """If OTLPSpanExporter(...) raises, setup should not propagate the exception."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        mocks["OTLPSpanExporter"].side_effect = RuntimeError("connection refused")
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        # Should not raise
        setup_telemetry(MagicMock(), _sc())

    def test_set_tracer_provider_runtime_error_is_caught(self, monkeypatch):
        """If set_tracer_provider raises RuntimeError about 'current TracerProvider', continue."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)
        mocks["trace"].set_tracer_provider.side_effect = RuntimeError(
            "current TracerProvider already set"
        )

        # Should not raise
        setup_telemetry(MagicMock(), _sc())

    def test_set_tracer_provider_other_runtime_error_is_not_caught(self, monkeypatch):
        """RuntimeErrors about something else should propagate (caught by outer try/except)."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)
        mocks["trace"].set_tracer_provider.side_effect = RuntimeError("unrelated error")

        # The outer except Exception catches this and logs — should not propagate
        setup_telemetry(MagicMock(), _sc())

    def test_fastapi_instrumentor_failure_is_caught(self, monkeypatch):
        """Failure in FastAPIInstrumentor.instrument_app should not crash setup."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)
        mocks["FastAPIInstrumentor"].instrument_app.side_effect = RuntimeError(
            "instrumentation failed"
        )

        # Should not raise
        setup_telemetry(MagicMock(), _sc())


class TestSetupTelemetryWithOpenAiInstrumentor:
    def test_openai_instrumentor_called_when_available(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        mock_oai_instance = MagicMock()
        mock_oai_cls = MagicMock(return_value=mock_oai_instance)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", mock_oai_cls)

        setup_telemetry(MagicMock(), _sc())

        mock_oai_instance.instrument.assert_called_once()

    def test_openai_instrumentor_failure_is_caught(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        mock_oai_instance = MagicMock()
        mock_oai_instance.instrument.side_effect = RuntimeError("openai instrument failed")
        mock_oai_cls = MagicMock(return_value=mock_oai_instance)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", mock_oai_cls)

        # Should not raise
        setup_telemetry(MagicMock(), _sc())

    def test_openai_instrumentor_skipped_when_none(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        # No exception expected
        setup_telemetry(MagicMock(), _sc())


class TestSetupTelemetryOtlpCaptureEnvVar:
    def test_genai_capture_env_var_set_when_tracing_enabled(self, monkeypatch):
        """OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT should be set to 'true'."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock(), _sc())

        assert os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT") == "true"


# ---------------------------------------------------------------------------
# Idempotency latch (_initialized) — Glad-Labs/poindexter#120
# ---------------------------------------------------------------------------
#
# main.py invokes setup_telemetry twice — once at module import time
# (so the middleware stack is still mutable) and once again from the
# lifespan handler after site_config.load() pulls real values from the
# DB. The second call MUST NOT call FastAPIInstrumentor.instrument_app
# again, because that translates to app.add_middleware(...) and
# FastAPI freezes the middleware stack as soon as the first request
# arrives. Re-adding middleware after start raises
# ``RuntimeError: Cannot add middleware after an application has
# started``. Mirrors the SentryIntegration._initialized latch.


class TestSetupTelemetryIdempotent:
    def test_setup_telemetry_idempotent(self, monkeypatch):
        """Second invocation must NOT re-instrument the FastAPI app once the
        first invocation successfully wired up middleware. This is the
        regression case for Glad-Labs/poindexter#120.
        """
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        app = MagicMock()
        # First call — should fully instrument.
        setup_telemetry(app, _sc(), service_name="svc")
        assert telemetry_mod._initialized is True
        assert mocks["FastAPIInstrumentor"].instrument_app.call_count == 1

        # Second call — must short-circuit. If the latch is honored,
        # instrument_app is NOT called again (which is what prevents the
        # `Cannot add middleware after an application has started`
        # RuntimeError in the lifespan re-init path).
        setup_telemetry(app, _sc(), service_name="svc")
        assert mocks["FastAPIInstrumentor"].instrument_app.call_count == 1

    def test_setup_telemetry_idempotent_does_not_raise_when_app_started(
        self, monkeypatch
    ):
        """Even if the app's middleware stack is frozen between calls
        (instrument_app would raise on re-entry), the second invocation
        must NOT propagate. The latch should make this an unconditional
        no-op — instrument_app should never be reached.
        """
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        app = MagicMock()
        setup_telemetry(app, _sc())

        # Simulate FastAPI's "app has started" state — any subsequent
        # add_middleware call would raise. If the latch fails, the
        # second setup_telemetry call would invoke instrument_app and
        # propagate this RuntimeError.
        mocks["FastAPIInstrumentor"].instrument_app.side_effect = RuntimeError(
            "Cannot add middleware after an application has started"
        )

        # Must NOT raise — the latch should short-circuit before
        # instrument_app is called.
        setup_telemetry(app, _sc())


class TestSetupTelemetryNoOpDoesNotLatch:
    def test_setup_telemetry_no_op_on_empty_config_does_not_latch(
        self, monkeypatch
    ):
        """When tracing is disabled in site_config (e.g. the module-level
        call running before site_config.load() pulls the real value),
        the function returns early and MUST NOT set ``_initialized``.
        Otherwise the lifespan re-init — which runs after
        site_config.load() makes ``enable_tracing=true`` visible — will
        be silently skipped and tracing will never start.
        """
        monkeypatch.setenv("ENABLE_TRACING", "false")
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)

        setup_telemetry(MagicMock(), _sc())

        assert telemetry_mod._initialized is False

    def test_setup_telemetry_no_op_on_missing_otel_does_not_latch(
        self, monkeypatch
    ):
        """When the OpenTelemetry packages aren't importable (the optional
        opentelemetry-* extras), the function returns early. It MUST
        NOT set ``_initialized`` — once the deps are installed the
        lifespan re-init should take effect on next start.
        """
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", False)

        setup_telemetry(MagicMock(), _sc())

        assert telemetry_mod._initialized is False

    def test_setup_telemetry_no_op_on_instrument_failure_does_not_latch(
        self, monkeypatch
    ):
        """If FastAPIInstrumentor.instrument_app fails on the first call,
        we did NOT successfully wire up middleware — the latch must stay
        False so a later retry (e.g. the lifespan re-init) can attempt
        instrumentation again.
        """
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        mocks["FastAPIInstrumentor"].instrument_app.side_effect = RuntimeError(
            "instrumentation failed"
        )

        setup_telemetry(MagicMock(), _sc())

        # instrument_app raised — middleware was not actually
        # registered, so the latch must NOT be set. Otherwise the next
        # setup attempt (lifespan re-init) would silently skip and
        # tracing would never come up.
        assert telemetry_mod._initialized is False
