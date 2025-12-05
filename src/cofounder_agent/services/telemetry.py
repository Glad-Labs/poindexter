import os
from opentelemetry import trace, _events, _logs
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._events import EventLoggerProvider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

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
    # Check if tracing is enabled via environment variable
    if os.getenv("ENABLE_TRACING", "false").lower() != "true":
        print(f"[TELEMETRY] OpenTelemetry tracing disabled for {service_name}")
        return

    # Capture message content as log events
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"

    # Create a resource to identify the service
    resource = Resource.create(attributes={
        "service.name": service_name,
        "deployment.environment": os.getenv("ENVIRONMENT", "development")
    })

    # Set up the TracerProvider
    provider = TracerProvider(resource=resource)
    
    # Configure the OTLP exporter (HTTP)
    # AI Toolkit's OTLP endpoint is http://localhost:4318
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    
    # Add the BatchSpanProcessor to the provider
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)
    
    # Set the global TracerProvider
    trace.set_tracer_provider(provider)
    
    # Configure logging and events
    _logs.set_logger_provider(LoggerProvider(resource=resource))
    log_endpoint = os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://localhost:4318/v1/logs")
    _logs.get_logger_provider().add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=log_endpoint))
    )
    _events.set_event_logger_provider(EventLoggerProvider())

    # Instrument the FastAPI app
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    
    # Instrument OpenAI SDK (if available)
    if OpenAIInstrumentor is not None:
        try:
            OpenAIInstrumentor().instrument()
        except Exception as e:
            print(f"[WARNING] Failed to instrument OpenAI SDK: {e}")
    
    print(f"[TELEMETRY] OpenTelemetry tracing enabled for {service_name} (Endpoint: {otlp_endpoint})")
