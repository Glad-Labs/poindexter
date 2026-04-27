"""brain telemetry — OpenTelemetry tracing for the standalone daemon.

The brain runs in its own container with its own tiny dependency
footprint (asyncpg + nothing else, historically). The worker side
already emits spans via ``plugins/tracing.py`` — this module mirrors
the same pattern for the brain so the same Tempo dashboards see
brain cycles + worker LLM calls correlated by trace_id.

Why a separate module instead of importing from plugins/tracing.py
------------------------------------------------------------------

Brain stays independent of the worker package on purpose. If the
worker's pyproject.toml (or its venv) is broken, the brain still
boots and the watchdog still fires. Copying the ~50 lines of
import-or-noop helpers preserves that boundary.

Configuration
-------------

Reads the same two app_settings keys the worker uses, via direct DB
read at brain startup (no SiteConfig dependency):

- ``enable_tracing``             — "true" / "false" (default off)
- ``otel_exporter_otlp_endpoint`` — e.g. "http://tempo:4317"

When tracing is disabled or the SDK isn't installed, all helpers
become no-ops. Brain code paths don't fork on whether tracing is on.

Usage in brain_daemon.py::

    from telemetry import setup_brain_telemetry, traced_step, get_tracer

    await setup_brain_telemetry(pool)         # once, at startup
    _tracer = get_tracer("poindexter.brain")  # module-level

    async def run_cycle(pool):
        with _tracer.start_as_current_span("brain.cycle") as span:
            ...
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# No-op fallback — same surface as opentelemetry.trace.Tracer
# ---------------------------------------------------------------------------


class _NoopSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        pass


@contextmanager
def _noop_span(_name: str, **_kwargs: Any) -> Iterator[_NoopSpan]:
    yield _NoopSpan()


class _NoopTracer:
    start_as_current_span = staticmethod(_noop_span)


_tracer: Any = _NoopTracer()
_initialized = False


def get_tracer(name: str = "poindexter.brain") -> Any:
    """Return the configured tracer, or the no-op fallback.

    Safe to call before ``setup_brain_telemetry`` runs — returns the
    no-op tracer in that case. Idempotent: callers can stash the
    result at module load time without worrying about init order.
    """
    return _tracer


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def setup_brain_telemetry(pool: Any) -> bool:
    """Configure the OTel TracerProvider for the brain daemon.

    Reads ``enable_tracing`` + ``otel_exporter_otlp_endpoint`` from
    ``app_settings`` (no SiteConfig dependency — direct fetchrow). If
    tracing is off, an OTel package is missing, or the endpoint is
    blank, leaves ``_tracer`` as the no-op stub. Idempotent — second
    call is a no-op.

    Returns True iff tracing was actually enabled.
    """
    global _tracer, _initialized
    if _initialized:
        return not isinstance(_tracer, _NoopTracer)

    enabled = await _read_setting(pool, "enable_tracing", "false")
    if enabled.strip().lower() != "true":
        logger.info("[brain.telemetry] tracing disabled (enable_tracing != true)")
        _initialized = True
        return False

    endpoint = (await _read_setting(
        pool, "otel_exporter_otlp_endpoint", "",
    )).strip()
    if not endpoint:
        logger.warning(
            "[brain.telemetry] enable_tracing=true but "
            "otel_exporter_otlp_endpoint is empty — staying in no-op mode."
        )
        _initialized = True
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:  # pragma: no cover — minimal dev container
        logger.warning(
            "[brain.telemetry] opentelemetry-* not installed (%s) — "
            "brain will run without tracing. Install via brain/Dockerfile.",
            e,
        )
        _initialized = True
        return False

    try:
        provider = TracerProvider(
            resource=Resource.create({"service.name": "poindexter-brain"})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("poindexter.brain")
        logger.info(
            "[brain.telemetry] tracing enabled — exporting to %s",
            endpoint,
        )
        _initialized = True
        return True
    except Exception as e:
        logger.error(
            "[brain.telemetry] OTel setup failed (%s) — staying in no-op mode",
            e, exc_info=True,
        )
        _initialized = True
        return False


async def _read_setting(pool: Any, key: str, default: str) -> str:
    """Read one app_settings row with a string fallback."""
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
        if row and row["value"] is not None:
            return str(row["value"])
    except Exception as e:
        logger.debug("[brain.telemetry] setting read failed for %s: %s", key, e)
    return default


# ---------------------------------------------------------------------------
# traced_step — async context manager for run_cycle's _step wrapper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def traced_step(name: str, **attrs: Any) -> AsyncIterator[Any]:
    """Wrap a brain-cycle sub-step in a span.

    Designed to compose with ``run_cycle``'s existing
    try/except-and-keep-going pattern: the span is recorded, the
    exception is captured on the span, and re-raised so the outer
    ``_step`` sees the same failure it always did.
    """
    with _tracer.start_as_current_span(name) as span:
        for k, v in attrs.items():
            try:
                span.set_attribute(k, v)
            except Exception:
                try:
                    span.set_attribute(k, str(v))
                except Exception:
                    pass
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            raise


__all__ = [
    "get_tracer",
    "setup_brain_telemetry",
    "traced_step",
]
