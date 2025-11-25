import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def setup_telemetry(app, service_name="cofounder-agent"):
    """
    Sets up OpenTelemetry tracing for the FastAPI application.
    
    Args:
        app: The FastAPI application instance.
        service_name: The name of the service to appear in traces.
    """
    # Check if tracing is enabled via environment variable
    if os.getenv("ENABLE_TRACING", "false").lower() != "true":
        return

    # Create a resource to identify the service
    resource = Resource.create(attributes={
        "service.name": service_name,
        "deployment.environment": os.getenv("ENVIRONMENT", "development")
    })

    # Set up the TracerProvider
    provider = TracerProvider(resource=resource)
    
    # Configure the OTLP exporter (defaults to localhost:4317)
    # Can be overridden by OTEL_EXPORTER_OTLP_ENDPOINT env var
    otlp_exporter = OTLPSpanExporter()
    
    # Add the BatchSpanProcessor to the provider
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)
    
    # Set the global TracerProvider
    trace.set_tracer_provider(provider)
    
    # Instrument the FastAPI app
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    
    print(f"🔭 OpenTelemetry tracing enabled for {service_name}")
