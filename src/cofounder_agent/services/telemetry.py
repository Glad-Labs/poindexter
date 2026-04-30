import logging
import os

# Try to import OpenTelemetry - it's optional for development
try:
    from opentelemetry import trace  # type: ignore[import-untyped]
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
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

# Try to import OpenAI instrumentation if available
try:
    from opentelemetry.instrumentation.openai import OpenAIInstrumentor  # type: ignore
except ImportError:
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor  # type: ignore
    except ImportError:
        OpenAIInstrumentor = None  # type: ignore[assignment,misc]


def setup_telemetry(app, service_name="cofounder-agent"):
    """
    Sets up OpenTelemetry tracing for the FastAPI application and OpenAI SDK.
    Simplified to handle trace exporting only (no logs/events to avoid dependency issues).

    Args:
        app: The FastAPI application instance.
        service_name: The name of the service to appear in traces.
    """
    # Skip if OpenTelemetry is not available
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

    # Check if tracing is enabled via environment variable
    from services.site_config import site_config
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

        # Instrument the FastAPI app
        try:
            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        except Exception as e:
            logging.error(f"[setup_telemetry] Failed to instrument FastAPI: {e}", exc_info=True)

        # Instrument OpenAI SDK (if available)
        if OpenAIInstrumentor is not None:
            try:
                OpenAIInstrumentor().instrument()
                logging.debug("[TELEMETRY] OpenAI SDK instrumented successfully")
            except Exception as e:
                logging.error(
                    f"[setup_telemetry] Failed to instrument OpenAI SDK: {e}", exc_info=True
                )

    except Exception as e:
        # If telemetry setup fails entirely, just log and continue
        logging.error(f"[setup_telemetry] Error setting up telemetry: {e}", exc_info=True)
        logging.exception("[TELEMETRY] Application will continue without OpenTelemetry tracing")
