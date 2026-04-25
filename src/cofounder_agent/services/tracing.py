"""OpenTelemetry tracing → Tempo.

LGTM+P stack piece. When ``enable_tracing`` is true in app_settings, the
worker initializes an OTLP gRPC exporter pointing at Tempo
(``otel_exporter_otlp_endpoint`` app_setting, default
``http://tempo:4317`` to match the docker-compose service). FastAPI is
auto-instrumented for HTTP request spans; the dispatcher in
``services/llm_providers/`` already creates custom spans through
``opentelemetry.trace`` that this hookup finally gives somewhere to
land.

Opt-in via app_settings, not env vars — matches the ``enable_pyroscope``
pattern in :mod:`services.profiling`. Safe to call even when the
opentelemetry packages aren't installed; the function logs and returns
cleanly so dev environments without the heavy deps still boot.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


_initialized = False


def setup_tracing(
    site_config: Any,
    service_name: str = "cofounder-agent",
) -> bool:
    """Configure the OTel TracerProvider + OTLP exporter.

    Idempotent: subsequent calls are no-ops. Returns True when tracing
    is now active (either configured this call or a prior call), False
    when disabled or unavailable.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
        service_name: Application label that lands on every span as
            the ``service.name`` resource attribute. Tempo uses it for
            service-level filtering in Grafana.
    """
    global _initialized

    enabled = site_config.get_bool("enable_tracing", False)
    if not enabled:
        logger.debug("[TRACING] disabled via app_settings.enable_tracing")
        return False

    if _initialized:
        return True

    endpoint = site_config.get(
        "otel_exporter_otlp_endpoint", "http://tempo:4317"
    )

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning(
            "[TRACING] opentelemetry packages not installed but "
            "enable_tracing=true: %s. Install via the worker pyproject.",
            exc,
        )
        return False

    environment = site_config.get("environment", "development") or "development"

    try:
        resource = Resource.create({
            "service.name": service_name,
            "service.namespace": "poindexter",
            "deployment.environment": environment,
        })
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _initialized = True
        logger.info(
            "[TRACING] OTLP exporter configured — service=%s endpoint=%s env=%s",
            service_name, endpoint, environment,
        )
        return True
    except Exception as exc:
        logger.warning("[TRACING] configure failed: %s", exc, exc_info=True)
        return False


def instrument_fastapi(app: Any) -> bool:
    """Auto-instrument a FastAPI app once the TracerProvider exists.

    Called from the lifespan after :func:`setup_tracing`. Safe when
    instrumentation packages aren't installed or tracing is disabled.
    Returns True when instrumentation was applied.
    """
    if not _initialized:
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as exc:
        logger.warning(
            "[TRACING] opentelemetry-instrumentation-fastapi not installed: %s",
            exc,
        )
        return False
    try:
        FastAPIInstrumentor.instrument_app(app)
        logger.info("[TRACING] FastAPI auto-instrumentation enabled")
        return True
    except Exception as exc:
        logger.warning("[TRACING] FastAPI instrumentation failed: %s", exc, exc_info=True)
        return False
