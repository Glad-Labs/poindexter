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
"""

import os
from unittest.mock import MagicMock

import services.telemetry as telemetry_mod
from services.telemetry import setup_telemetry

# ---------------------------------------------------------------------------
# When OpenTelemetry is not available
# ---------------------------------------------------------------------------


class TestSetupTelemetryNotAvailable:
    def test_returns_early_when_not_available(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", False)
        app = MagicMock()
        # Should not raise and should not instrument anything
        setup_telemetry(app)

    def test_returns_early_when_trace_is_none(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.setattr(telemetry_mod, "trace", None)
        app = MagicMock()
        setup_telemetry(app)
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
        setup_telemetry(app)

    def test_returns_early_when_enable_tracing_missing(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.delenv("ENABLE_TRACING", raising=False)
        app = MagicMock()
        setup_telemetry(app)

    def test_returns_early_when_enable_tracing_mixed_case(self, monkeypatch):
        monkeypatch.setattr(telemetry_mod, "OPENTELEMETRY_AVAILABLE", True)
        monkeypatch.setenv("ENABLE_TRACING", "True")  # Not lowercase "true"
        # "True" != "true" per the .lower() == "true" check
        app = MagicMock()
        setup_telemetry(app)


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
        setup_telemetry(app, service_name="test-service")
        # Should complete without exception


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_opentelemetry_available_is_bool(self):
        assert isinstance(telemetry_mod.OPENTELEMETRY_AVAILABLE, bool)

    def test_openai_instrumentor_is_none_or_class(self):
        """Guard against silent removal/rename of OpenAIInstrumentor.

        telemetry.py always assigns ``OpenAIInstrumentor`` at module
        import time (either the imported class or ``None``). Use direct
        attribute access (not ``getattr(..., None)``) so a future
        opentelemetry-instrumentation-openai upgrade that drops or
        renames the symbol fails this test loudly instead of passing
        silently (closes #171).
        """
        oi = telemetry_mod.OpenAIInstrumentor
        # Either None (if not installed) or a class
        assert oi is None or callable(oi)

    def test_httpx_instrumentor_is_none_or_class(self):
        """Guard against silent removal/rename of HTTPXClientInstrumentor.

        Mirrors the OpenAIInstrumentor guard (#171): telemetry.py assigns
        the symbol at import time (the class or ``None``), so a future
        opentelemetry-instrumentation-httpx drop/rename fails loudly here
        instead of silently disabling egress trace propagation (#1997).
        """
        hi = telemetry_mod.HTTPXClientInstrumentor
        assert hi is None or callable(hi)

    def test_w3c_propagation_symbols_are_none_or_callable(self):
        """Guard the W3C trace-context propagation API used at setup.

        ``set_global_textmap`` + ``TraceContextTextMapPropagator`` live in
        opentelemetry-api (already a hard dep), so they should import; the
        None-tolerance keeps parity with the other guarded symbols.
        """
        assert telemetry_mod.set_global_textmap is None or callable(
            telemetry_mod.set_global_textmap
        )
        assert telemetry_mod.TraceContextTextMapPropagator is None or callable(
            telemetry_mod.TraceContextTextMapPropagator
        )


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

        setup_telemetry(MagicMock(), service_name="test-svc")

        mocks["_provider"].add_span_processor.assert_called_once()

    def test_fastapi_instrumented_when_endpoint_set(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        app = MagicMock()
        setup_telemetry(app, service_name="test-svc")

        mocks["FastAPIInstrumentor"].instrument_app.assert_called_once()

    def test_tracer_provider_set_globally(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock())

        mocks["trace"].set_tracer_provider.assert_called_once_with(mocks["_provider"])

    def test_service_name_passed_to_resource(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock(), service_name="my-custom-service")

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
        setup_telemetry(MagicMock())

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
        setup_telemetry(MagicMock())

    def test_set_tracer_provider_other_runtime_error_is_not_caught(self, monkeypatch):
        """RuntimeErrors about something else should propagate (caught by outer try/except)."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)
        mocks["trace"].set_tracer_provider.side_effect = RuntimeError("unrelated error")

        # The outer except Exception catches this and logs — should not propagate
        setup_telemetry(MagicMock())

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
        setup_telemetry(MagicMock())


class TestSetupTelemetryWithOpenAiInstrumentor:
    def test_openai_instrumentor_called_when_available(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        mock_oai_instance = MagicMock()
        mock_oai_cls = MagicMock(return_value=mock_oai_instance)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", mock_oai_cls)

        setup_telemetry(MagicMock())

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
        setup_telemetry(MagicMock())

    def test_openai_instrumentor_skipped_when_none(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        # No exception expected
        setup_telemetry(MagicMock())


class TestSetupTelemetryOtlpCaptureEnvVar:
    def test_genai_capture_env_var_set_when_tracing_enabled(self, monkeypatch):
        """OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT should be set to 'true'."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        setup_telemetry(MagicMock())

        assert os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT") == "true"


# ---------------------------------------------------------------------------
# httpx egress instrumentation + explicit global W3C propagator
# (Tier 1a — Glad-Labs/glad-labs-stack#1997). Outbound HTTP must inject the
# W3C `traceparent` header so trace context survives every egress hop and an
# adopter's own OTLP collector can stitch the run into a single trace.
# ---------------------------------------------------------------------------


class TestSetupTelemetryHttpxInstrumentation:
    def test_httpx_instrumented_when_tracing_enabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        mock_httpx_instance = MagicMock()
        mock_httpx_cls = MagicMock(return_value=mock_httpx_instance)
        monkeypatch.setattr(
            telemetry_mod, "HTTPXClientInstrumentor", mock_httpx_cls, raising=False
        )

        setup_telemetry(MagicMock(), service_name="test-svc")

        mock_httpx_instance.instrument.assert_called_once()

    def test_httpx_instrumentor_skipped_when_none(self, monkeypatch):
        """No crash when opentelemetry-instrumentation-httpx isn't installed."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)
        monkeypatch.setattr(telemetry_mod, "HTTPXClientInstrumentor", None, raising=False)

        setup_telemetry(MagicMock())

    def test_httpx_instrumentor_failure_is_caught(self, monkeypatch):
        """A failure inside httpx instrumentation must not crash setup."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        mock_httpx_instance = MagicMock()
        mock_httpx_instance.instrument.side_effect = RuntimeError("httpx instrument failed")
        mock_httpx_cls = MagicMock(return_value=mock_httpx_instance)
        monkeypatch.setattr(
            telemetry_mod, "HTTPXClientInstrumentor", mock_httpx_cls, raising=False
        )

        # Should not raise
        setup_telemetry(MagicMock())


class TestSetupTelemetryGlobalPropagator:
    def test_global_w3c_propagator_set_when_tracing_enabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        mock_propagator_instance = MagicMock()
        mock_propagator_cls = MagicMock(return_value=mock_propagator_instance)
        mock_set_global = MagicMock()
        monkeypatch.setattr(
            telemetry_mod,
            "TraceContextTextMapPropagator",
            mock_propagator_cls,
            raising=False,
        )
        monkeypatch.setattr(
            telemetry_mod, "set_global_textmap", mock_set_global, raising=False
        )

        setup_telemetry(MagicMock())

        mock_set_global.assert_called_once_with(mock_propagator_instance)

    def test_propagator_skipped_when_symbols_none(self, monkeypatch):
        """No crash when the propagation API isn't importable."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)
        monkeypatch.setattr(telemetry_mod, "set_global_textmap", None, raising=False)
        monkeypatch.setattr(
            telemetry_mod, "TraceContextTextMapPropagator", None, raising=False
        )

        setup_telemetry(MagicMock())

    def test_propagator_failure_is_caught(self, monkeypatch):
        """A failure setting the propagator must not crash setup."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        mock_set_global = MagicMock(side_effect=RuntimeError("propagator boom"))
        monkeypatch.setattr(
            telemetry_mod,
            "TraceContextTextMapPropagator",
            MagicMock(return_value=MagicMock()),
            raising=False,
        )
        monkeypatch.setattr(
            telemetry_mod, "set_global_textmap", mock_set_global, raising=False
        )

        # Should not raise
        setup_telemetry(MagicMock())
