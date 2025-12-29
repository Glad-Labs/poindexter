import os
import logging

# Try to import OpenTelemetry - it's optional for development
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    OPENTELEMETRY_AVAILABLE = True
except (ImportError, AttributeError) as e:
    OPENTELEMETRY_AVAILABLE = False
    logging.warning(f"OpenTelemetry not fully available: {e}. Tracing disabled.")

# Suppress verbose OTLP exporter logs in development
logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(
    logging.CRITICAL
)
logging.getLogger("opentelemetry.sdk._shared_internal").setLevel(logging.CRITICAL)
logging.getLogger("urllib3.connectionpool").setLevel(logging.CRITICAL)

# Try to import OpenAI instrumentation if available
try:
    from opentelemetry.instrumentation.openai import OpenAIInstrumentor
except ImportError:
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
    except ImportError:
        OpenAIInstrumentor = None


def setup_telemetry(app, service_name="cofounder-agent"):
    """
    Sets up OpenTelemetry tracing for the FastAPI application and OpenAI SDK.

    Args:
        app: The FastAPI application instance.
        service_name: The name of the service to appear in traces.
    """
    # Skip if OpenTelemetry is not available
    if not OPENTELEMETRY_AVAILABLE:
        logging.warning(
            f"[TELEMETRY] OpenTelemetry not available - tracing disabled for {service_name}"
        )
        return

    # Check if tracing is enabled via environment variable
    if os.getenv("ENABLE_TRACING", "false").lower() != "true":
        print(f"[TELEMETRY] OpenTelemetry tracing disabled for {service_name}")
        return

    try:
        # Capture message content as log events
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

            # Set the global TracerProvider
            trace.set_tracer_provider(provider)

            # Configure logging and events
            _logs.set_logger_provider(LoggerProvider(resource=resource))
            log_endpoint = os.getenv(
                "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://localhost:4318/v1/logs"
            )
            try:
                _logs.get_logger_provider().add_log_record_processor(
                    BatchLogRecordProcessor(OTLPLogExporter(endpoint=log_endpoint, timeout=5))
                )
            except Exception as e:
                print(f"[TELEMETRY] Warning: Could not configure log export: {e}")

            _events.set_event_logger_provider(EventLoggerProvider())

            print(
                f"[TELEMETRY] OpenTelemetry tracing enabled for {service_name} (Endpoint: {otlp_endpoint})"
            )

        except Exception as e:
            # If OTLP endpoint is not available, set up NoOp provider and continue
            print(f"[TELEMETRY] Warning: OTLP exporter not available ({otlp_endpoint}): {e}")
            print(f"[TELEMETRY] Continuing with no-op tracing provider")
            # Use the provider without exporters - traces will be no-ops but app continues
            trace.set_tracer_provider(provider)
            _logs.set_logger_provider(LoggerProvider(resource=resource))
            _events.set_event_logger_provider(EventLoggerProvider())

        # Instrument the FastAPI app
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

        # Instrument OpenAI SDK (if available)
        if OpenAIInstrumentor is not None:
            try:
                OpenAIInstrumentor().instrument()
            except Exception as e:
                print(f"[TELEMETRY] Warning: Failed to instrument OpenAI SDK: {e}")

    except Exception as e:
        # If telemetry setup fails entirely, just log and continue
        print(f"[TELEMETRY] Error setting up telemetry: {e}")
        print(f"[TELEMETRY] Application will continue without OpenTelemetry tracing")
