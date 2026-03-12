import logging
import os

# Try to import OpenTelemetry - it's optional for development
try:
    from opentelemetry import trace  # type: ignore[import-untyped]
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import (  # type: ignore
        FastAPIInstrumentor,
    )
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
    logging.error(
        f"[setup_telemetry] OpenTelemetry not fully available: {e}. Tracing disabled.",
        exc_info=True,
    )

# Suppress verbose OTLP exporter logs in development
logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(
    logging.CRITICAL
)
logging.getLogger("opentelemetry.sdk._shared_internal").setLevel(logging.CRITICAL)
logging.getLogger("urllib3.connectionpool").setLevel(logging.CRITICAL)

# Try to import OpenAI instrumentation if available
try:
    from opentelemetry.instrumentation.openai import (  # type: ignore
        OpenAIInstrumentor,
    )
except ImportError:
    try:
        from opentelemetry.instrumentation.openai_v2 import (  # type: ignore
            OpenAIInstrumentor,
        )
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
    if os.getenv("ENABLE_TRACING", "false").lower() != "true":
        logging.debug(f"[TELEMETRY] OpenTelemetry tracing disabled for {service_name}")
        return

    try:
        # Enable capturing LLM message content in traces
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"

        # Create a resource to identify the service
        resource = Resource.create(
            attributes={
                "service.name": service_name,
                "deployment.environment": os.getenv("ENVIRONMENT", "development"),
            }
        )

        # Set up the TracerProvider
        provider = TracerProvider(resource=resource)

        # Configure the OTLP exporter (HTTP) with error handling
        # AI Toolkit's OTLP endpoint is http://localhost:4318
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

        try:
            otlp_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint, timeout=5  # 5-second timeout to fail fast if unavailable
            )

            # Add the BatchSpanProcessor to the provider
            processor = BatchSpanProcessor(otlp_exporter)
            provider.add_span_processor(processor)

            logging.info(
                "[setup_telemetry] OpenTelemetry tracing enabled for %s (Endpoint: %s)",
                service_name, otlp_endpoint,
            )

        except Exception as e:
            # If OTLP endpoint is not available, log warning but continue with no-op provider
            logging.error(
                f"[setup_telemetry] OTLP exporter not available ({otlp_endpoint}): {e}. "
                f"Spans will not be exported but application will continue.",
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
        logging.error(f"[TELEMETRY] Application will continue without OpenTelemetry tracing")
