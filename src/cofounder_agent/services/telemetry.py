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


def setup_telemetry(app, site_config=None, service_name="cofounder-agent"):
    """
    Sets up OpenTelemetry tracing for the FastAPI application and OpenAI SDK.
    Simplified to handle trace exporting only (no logs/events to avoid dependency issues).

    Args:
        app: The FastAPI application instance.
        site_config: SiteConfig instance for app_settings reads. When None,
            falls back to a fresh env-fallback instance.
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

    if site_config is None:
        from services.site_config import SiteConfig
        site_config = SiteConfig()

    # Check if tracing is enabled via app_settings (DI seam, #330).
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


# ---------------------------------------------------------------------------
# traced_method — span-emitting decorator for individual hot-path calls.
#
# Glad-Labs/poindexter#416: until the LiteLLM dispatcher routing lands and
# every primary content-gen call flows through services/llm_providers/
# dispatcher (which already emits spans), the OllamaClient hot path is
# invisible to Tempo. Decorating OllamaClient.generate / chat / embed
# with this helper buys span coverage immediately, and the wrapper
# survives the eventual dispatcher migration.
#
# Behavior:
#
# - When the opentelemetry SDK isn't installed (OPENTELEMETRY_AVAILABLE is
#   False), the decorator is a no-op pass-through. No try/except in hot
#   path, no attribute lookups — just the original function.
# - When the SDK is installed but `enable_tracing` is false in app_settings,
#   spans are emitted but the global tracer-provider's noop processor
#   drops them. Same code path; no special-case here.
# - The decorator pulls span attribute values from the wrapped function's
#   arguments by name (positional or keyword), via inspect.signature. This
#   is the same shape as the issue's example — `@traced_method("ollama.generate",
#   attrs=("model", "prompt"))` reads ``model`` and ``prompt`` off the
#   call. Non-string attrs are stringified; missing names are skipped.
# - Exceptions in the wrapped function record a span event + status=ERROR
#   then re-raise. We DO NOT swallow.
# - The prompt attribute is truncated to PROMPT_ATTR_MAX_CHARS to keep
#   span size sane; full bodies belong in Langfuse via the @observe
#   decorator that already wraps these calls (poindexter#401).
# ---------------------------------------------------------------------------

import functools
import inspect
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")

_HOT_PATH_TRACER: Any = None
if OPENTELEMETRY_AVAILABLE and trace is not None:
    _HOT_PATH_TRACER = trace.get_tracer("poindexter.hot_path")

# Truncate prompt-like attributes so a 30 KB prompt doesn't blow up Tempo's
# per-span byte budget. Full prompt body lives in Langfuse via @observe.
PROMPT_ATTR_MAX_CHARS = 1000

# Attributes whose values we should always truncate (likely-long strings).
_TRUNCATE_ATTRS = frozenset({"prompt", "system", "messages", "text", "content"})


def _coerce_attr(name: str, value: Any) -> str:
    """Stringify a span attribute value; truncate the long ones."""
    s = str(value) if not isinstance(value, str) else value
    if name in _TRUNCATE_ATTRS and len(s) > PROMPT_ATTR_MAX_CHARS:
        return s[:PROMPT_ATTR_MAX_CHARS] + "…[truncated]"
    return s


def traced_method(
    span_name: str, attrs: tuple[str, ...] = (),
) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Wrap an async function so each call emits one OTel span.

    Args:
        span_name: The span name reported to Tempo (e.g. ``"ollama.generate"``).
        attrs: Argument names to copy onto the span as attributes (e.g.
            ``("model", "prompt")``). Read by name via the wrapped
            function's signature, so positional or keyword form both work.

    No-op when ``OPENTELEMETRY_AVAILABLE`` is False — the wrapper returns
    the original function unchanged so production hot-path import time is
    unaffected on operator boxes without the SDK installed.

    The ``ParamSpec`` + ``TypeVar`` plumbing keeps the wrapped function's
    signature visible to type checkers; without it Pyright reports every
    ``model=...`` / ``prompt=...`` kwarg as "no parameter named ..." on
    decorated calls.
    """
    if not OPENTELEMETRY_AVAILABLE or _HOT_PATH_TRACER is None or trace is None:
        def _passthrough(fn: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
            return fn
        return _passthrough

    def _decorator(fn: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        # Bind argument-name lookup once at decoration time, not per call.
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def _wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with _HOT_PATH_TRACER.start_as_current_span(span_name) as span:
                if attrs:
                    try:
                        bound = sig.bind_partial(*args, **kwargs)
                    except TypeError:
                        bound = None
                    if bound is not None:
                        for attr_name in attrs:
                            if attr_name in bound.arguments:
                                span.set_attribute(
                                    attr_name,
                                    _coerce_attr(attr_name, bound.arguments[attr_name]),
                                )
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    # Status.ERROR import is annoying — set via the constant
                    # without importing the enum, mirroring dispatcher.py's
                    # tolerance-of-noop-tracer pattern.
                    try:
                        from opentelemetry.trace import Status, StatusCode  # type: ignore
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                    except Exception:
                        pass
                    raise

        return _wrapped

    return _decorator
