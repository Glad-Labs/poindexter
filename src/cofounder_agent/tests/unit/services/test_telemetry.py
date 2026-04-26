"""
Unit tests for services.telemetry.setup_telemetry

Tests cover:
- Function does not raise when OpenTelemetry is unavailable (OPENTELEMETRY_AVAILABLE=False)
- Function returns early when ENABLE_TRACING is not "true"
- Function returns early when ENABLE_TRACING=true but components are None
- Module-level constants and guard variables
- OTLP endpoint not configured — no-op provider, no span processor
- Idempotency latch (_initialized) — second call short-circuits
- No-op early returns DO NOT set the latch (so lifespan re-init can retry)

NOTE: 12 tests covering the OTLP endpoint, FastAPIInstrumentor, and
OpenAIInstrumentor paths were removed in commit on
``test/fix-stale-openai-instrumentor-tests`` (Glad-Labs/poindexter#129)
because every one called ``monkeypatch.setattr(telemetry_mod,
"OpenAIInstrumentor", ...)`` against a symbol that was deliberately
deleted in commit 68563f45 (drop dead OpenAI instrumentor). See the
inline note where the classes used to live for the full rationale.
"""

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


# ---------------------------------------------------------------------------
# OpenAIInstrumentor wiring — restored in Glad-Labs/poindexter#132.
#
# Historical context: an earlier version of this file held three test
# classes that monkeypatched the OpenAIInstrumentor symbol. They were
# deleted on `test/fix-stale-openai-instrumentor-tests`
# (Glad-Labs/poindexter#129) when commit 68563f45 dropped the
# instrumentor entirely — the worker had no callers of the openai SDK,
# so the instrumentor had nothing to wrap and the integration was dead
# code.
#
# With Glad-Labs/poindexter#132 landing OpenAICompatProvider on top of
# the openai AsyncOpenAI SDK, the instrumentor wiring is meaningful
# again. The block below re-establishes test coverage for the new
# wiring without re-introducing the old patterns. We patch
# ``OpenAIInstrumentor`` to a Mock so we can assert ``.instrument()``
# was called with the right tracer_provider, and so we can verify the
# graceful no-op path when the symbol is None (dev shells without
# ``opentelemetry-instrumentation-openai_v2``).
# ---------------------------------------------------------------------------


class TestSetupTelemetryOpenAIInstrumentor:
    def test_openai_instrumentor_called_when_available(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        instrumentor_instance = MagicMock()
        instrumentor_instance.instrument = MagicMock()
        instrumentor_cls = MagicMock(return_value=instrumentor_instance)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", instrumentor_cls)

        app = MagicMock()
        setup_telemetry(app, _sc(), service_name="svc")

        # Class should be instantiated once and instrument() invoked
        # with the provider produced upstream.
        instrumentor_cls.assert_called_once_with()
        instrumentor_instance.instrument.assert_called_once()
        kwargs = instrumentor_instance.instrument.call_args.kwargs
        assert kwargs.get("tracer_provider") is mocks["_provider"]

    def test_no_op_when_instrumentor_missing(self, monkeypatch):
        """When the optional package isn't installed the symbol is None
        and setup_telemetry must not raise — the rest of the wiring
        (FastAPI instrumentation, OTLP exporter) still runs."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", None)

        app = MagicMock()
        setup_telemetry(app, _sc(), service_name="svc")

        # FastAPI instrumentation still happens even without the openai
        # instrumentor — the two are independent.
        mocks["FastAPIInstrumentor"].instrument_app.assert_called_once()

    def test_instrumentor_failure_does_not_break_setup(self, monkeypatch):
        """A failing instrument() call must not crash app startup."""
        monkeypatch.setenv("ENABLE_TRACING", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
        mocks = _make_mock_components()
        _apply_mocks(monkeypatch, mocks)

        instrumentor_instance = MagicMock()
        instrumentor_instance.instrument = MagicMock(side_effect=RuntimeError("boom"))
        instrumentor_cls = MagicMock(return_value=instrumentor_instance)
        monkeypatch.setattr(telemetry_mod, "OpenAIInstrumentor", instrumentor_cls)

        app = MagicMock()
        # Should not raise — failure is logged and swallowed so a broken
        # optional instrumentor can never take down the worker.
        setup_telemetry(app, _sc(), service_name="svc")


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
