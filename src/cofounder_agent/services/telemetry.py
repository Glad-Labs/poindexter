import logging
import os
from typing import Any

# Try to import OpenTelemetry - it's optional for development.
# Uses the gRPC OTLP exporter (port 4317) — Tempo accepts both gRPC and
# HTTP, but gRPC is the default Otel collector convention and the
# exporter package is what we ship in the worker pyproject. The HTTP
# variant (proto.http) is intentionally NOT a dependency.
try:
    from opentelemetry import trace  # type: ignore[import-untyped]
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore
    from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import-untyped]

    OPENTELEMETRY_AVAILABLE = True
except (ImportError, AttributeError) as e:
    trace = None  # type: ignore[assignment]
    OTLPSpanExporter = None  # type: ignore[assignment,misc]
    FastAPIInstrumentor = None  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    BatchSpanProcessor = None  # type: ignore[assignment,misc]
    OPENTELEMETRY_AVAILABLE = False
    # Log as INFO, not ERROR with traceback — the package is intentionally
    # optional (not in pyproject core deps). The try/except exists
    # precisely because tracing is opt-in, so a missing import is expected,
    # not an error that should spam the boot log.
    logging.info(
        "[setup_telemetry] OpenTelemetry not available (%s) — tracing disabled",
        e,
    )

# Suppress connection-pool spam from urllib3 (connection refused to OTLP endpoint is a known
# startup warning, not an error that should fill logs).  The OTLP exporter itself is left at
# WARNING so that genuine export failures are visible (reverted from CRITICAL — see Issue #430).
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

# OpenAIInstrumentor wiring — restored 2026-04-25 for
# Glad-Labs/poindexter#132.
#
# Historical note: this import + call was deliberately removed in
# commit 68563f45 because nothing in the worker actually called the
# ``openai`` SDK — the rationale was "the instrumentor has nothing to
# wrap". With the OpenAICompatProvider plugin landing on top of
# ``openai.AsyncOpenAI``, that's no longer true: every cost-guarded
# OpenAI-compat call now flows through the SDK and benefits from the
# instrumentor's automatic span creation (chat.completions.create /
# embeddings.create / etc.). The dispatcher in
# ``services/llm_providers/dispatcher.py`` still emits its own
# higher-level ``llm.dispatch_complete`` spans; the SDK instrumentor
# adds the per-HTTP-request child spans inside that envelope.
#
# Both the legacy (``opentelemetry.instrumentation.openai``) and the
# v2 (``opentelemetry.instrumentation.openai_v2``) packages publish an
# ``OpenAIInstrumentor`` class. We try v2 first (newer, supports
# OpenTelemetry 1.28+), fall back to the legacy package, and finally
# accept ``None`` so dev shells without either installed don't fail
# import. The wiring code below short-circuits when the symbol is None.
try:
    from opentelemetry.instrumentation.openai_v2 import (  # type: ignore
        OpenAIInstrumentor,
    )
except ImportError:
    try:
        from opentelemetry.instrumentation.openai import (  # type: ignore
            OpenAIInstrumentor,
        )
    except ImportError:
        OpenAIInstrumentor = None  # type: ignore[assignment,misc]

# Idempotency latch — main.py calls setup_telemetry twice (once at
# module import time so the middleware stack is still mutable, and
# again inside the lifespan handler after site_config.load() pulls
# real values from the DB). The second call MUST NOT re-invoke
# FastAPIInstrumentor.instrument_app(...) because that translates to
# app.add_middleware(...), and FastAPI freezes the middleware stack as
# soon as the first request lands. Re-adding middleware after start
# raises ``RuntimeError: Cannot add middleware after an application
# has started`` — which 500s the request that triggered the lifespan
# re-init. See gitea bug-fix branch fix/telemetry-middleware-after-start
# and Glad-Labs/poindexter#120.
#
# Mirrors the SentryIntegration._initialized pattern in
# services/sentry_integration.py: the latch is set ONLY when we
# actually wired up middleware/instrumentation, so a no-op early return
# (tracing disabled, OTel package missing, OTel components mocked to
# None) lets the lifespan re-run actually take effect once site_config
# is populated.
_initialized = False


def setup_telemetry(app, site_config: Any, service_name: str = "cofounder-agent"):
    """
    Sets up OpenTelemetry tracing for the FastAPI application and OpenAI SDK.
    Simplified to handle trace exporting only (no logs/events to avoid dependency issues).

    Args:
        app: The FastAPI application instance.
        site_config: SiteConfig instance (DI — Phase H, GH#95). Must be
            passed explicitly — the module-level singleton import was
            removed. Supply from ``app.state.site_config`` in lifespan /
            route wiring.
        service_name: The name of the service to appear in traces.
    """
    global _initialized

    # Idempotency guard. Bail early if a previous call already wired up
    # middleware — re-running FastAPIInstrumentor.instrument_app after
    # the middleware stack has been frozen (which happens as soon as
    # the first request hits the app, or when SQLAdmin's sub-app mount
    # finalizes the stack) raises RuntimeError. See module-level
    # comment for the full repro chain.
    if _initialized:
        logging.debug(
            "[setup_telemetry] already initialized — skipping re-instrumentation"
        )
        return

    # Skip if OpenTelemetry is not available.
    # Do NOT set _initialized here — the module-level call may run
    # before the optional opentelemetry-* extras are importable in
    # certain dev shells; we want the lifespan re-run to retry once
    # the package is available. Same discipline as
    # SentryIntegration.initialize on the no-DSN branch.
    if (
        not OPENTELEMETRY_AVAILABLE
        or Resource is None
        or TracerProvider is None
        or OTLPSpanExporter is None
        or BatchSpanProcessor is None
        or trace is None
        or FastAPIInstrumentor is None
    ):
        logging.warning(
            f"[TELEMETRY] OpenTelemetry not available - tracing disabled for {service_name}"
        )
        return

    # Check if tracing is enabled via the injected site_config.
    # Do NOT set _initialized here either — module-level invocation
    # runs before site_config.load() pulls the DB row, so it sees the
    # default ``"false"`` and returns early. The lifespan re-init must
    # be allowed to retry once the real value is loaded.
    if site_config.get("enable_tracing", "false").lower() != "true":
        logging.debug(f"[TELEMETRY] OpenTelemetry tracing disabled for {service_name}")
        return

    try:
        # Enable capturing LLM message content in traces
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"

        # Create a resource to identify the service.
        # site_config.get falls back to the ENVIRONMENT env var when the
        # DB row is absent, so this preserves the old default path while
        # letting operators override per-deploy from app_settings.
        resource = Resource.create(
            attributes={
                "service.name": service_name,
                "deployment.environment": site_config.get(
                    "environment", "development",
                ) or "development",
            }
        )

        # Set up the TracerProvider
        provider = TracerProvider(resource=resource)

        # Configure the OTLP exporter only when an explicit endpoint is provided.
        # Defaulting to localhost is not safe in production (cloud deploy has no local OTLP
        # collector) — it causes a silent export-failure cycle that wastes CPU/memory.
        # Set OTEL_EXPORTER_OTLP_ENDPOINT to point at Grafana Tempo, Honeycomb, etc.
        otlp_endpoint = site_config.get("otel_exporter_otlp_endpoint")

        if not otlp_endpoint:
            logging.warning(
                "[setup_telemetry] OTEL_EXPORTER_OTLP_ENDPOINT not set — "
                "ENABLE_TRACING=true but traces will NOT be exported. "
                "Set OTEL_EXPORTER_OTLP_ENDPOINT to a reachable OTLP collector "
                "(e.g. Grafana Tempo, Honeycomb, or a local Jaeger instance)."
            )
            # Continue with a no-op provider so spans are created but not exported.
        else:
            try:
                otlp_exporter = OTLPSpanExporter(
                    endpoint=otlp_endpoint, timeout=5  # 5-second timeout to fail fast
                )

                # Add the BatchSpanProcessor to the provider
                processor = BatchSpanProcessor(otlp_exporter)
                provider.add_span_processor(processor)

                logging.info(
                    "[setup_telemetry] OpenTelemetry tracing enabled for %s (Endpoint: %s)",
                    service_name,
                    otlp_endpoint,
                )

            except Exception as e:
                logging.error(
                    f"[setup_telemetry] OTLP exporter setup failed ({otlp_endpoint}): {e}. "
                    f"Spans will not be exported.",
                    exc_info=True,
                )

        # Set the global TracerProvider (only once, will override if already set)
        try:
            trace.set_tracer_provider(provider)
        except RuntimeError as e:
            # Provider already set - this is ok, just use the existing one
            if "current TracerProvider" not in str(e):
                raise
            logging.error(
                f"[setup_telemetry] TracerProvider already set, using existing: {e}",
                exc_info=True,
            )

        # Instrument the FastAPI app. FastAPI 0.110+ finalizes the
        # middleware stack early (specifically when the OpenAPI schema
        # is generated for /docs, when the app is first invoked, or
        # when certain mount() / sub-app operations run). Once the
        # stack is built, ``add_middleware`` raises ``Cannot add
        # middleware after an application has started``. Setting
        # ``app.middleware_stack = None`` invalidates the cached stack
        # so the instrumenter's add_middleware call rebuilds it
        # cleanly. Documented Otel-Python escape hatch — see
        # opentelemetry-python/issues/2727.
        try:
            try:
                app.middleware_stack = None  # type: ignore[attr-defined]
            except Exception as e:
                logging.debug(
                    "[telemetry] middleware_stack invalidate failed "
                    "(non-fatal — instrument_app will surface a real "
                    "error if the rebuild fails): %s", e,
                )
            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
            # Latch ONLY after middleware was actually registered.
            # Subsequent calls (the lifespan re-init) will short-circuit
            # at the top instead of attempting another add_middleware
            # against the now-frozen middleware stack.
            _initialized = True
        except Exception as e:
            logging.error(f"[setup_telemetry] Failed to instrument FastAPI: {e}", exc_info=True)

        # Instrument the openai SDK if the instrumentor package is
        # installed. This is a no-op when OpenAIInstrumentor is None
        # (dev shells without opentelemetry-instrumentation-openai_v2).
        # The OpenAICompatProvider plugin (Glad-Labs/poindexter#132)
        # routes through ``openai.AsyncOpenAI``, so any chat/embed
        # call there gets automatic per-HTTP-request spans below the
        # dispatcher's ``llm.dispatch_*`` envelope.
        if OpenAIInstrumentor is not None:
            try:
                OpenAIInstrumentor().instrument(tracer_provider=provider)
                logging.debug(
                    "[setup_telemetry] OpenAI SDK instrumented for %s", service_name,
                )
            except Exception as e:
                logging.error(
                    "[setup_telemetry] Failed to instrument OpenAI SDK: %s", e,
                    exc_info=True,
                )

    except Exception as e:
        # If telemetry setup fails entirely, just log and continue
        logging.error(f"[setup_telemetry] Error setting up telemetry: {e}", exc_info=True)
        logging.exception("[TELEMETRY] Application will continue without OpenTelemetry tracing")
