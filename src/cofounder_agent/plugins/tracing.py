"""plugins.tracing — shared OpenTelemetry helpers for plugin authors.

Every Protocol family (LLMProvider, ImageProvider, TTSProvider,
PublishAdapter, MediaCompositor, CaptionProvider, Stage, Tap, Probe,
Job, ...) wants to emit traces for its hot methods. Without a shared
helper, each plugin author copies the import-or-noop boilerplate and
the per-method ``start_as_current_span`` calls — that's ~30 lines of
ceremony plus ~10 per method. Multiplied across a growing plugin
ecosystem it becomes the single biggest source of instrumentation
inconsistency (and the reason `ollama_client.py` had zero spans
despite the SDK being installed for months).

This module centralizes the pattern so a new provider gets traces in
**1-2 lines** instead of 30+:

    # Top of the plugin module
    from plugins.tracing import get_tracer, traced_method, traced_span

    _tracer = get_tracer("poindexter.image_providers.midjourney")

    class MidjourneyProvider:
        @traced_method("midjourney.generate", attrs=("model", "prompt_chars"))
        async def generate(self, prompt: str, model: str = "v6") -> bytes:
            ...

Three entry points:

- :func:`get_tracer` — returns a tracer instance that's either real
  (when opentelemetry-sdk is installed) or a no-op stub. Replaces the
  30-line try/except dance that's currently duplicated in
  ``services/llm_providers/dispatcher.py`` and
  ``services/ollama_client.py``.

- :func:`traced_method` — decorator for the common case: wrap an async
  method with a span named after the operation, optionally pulling
  attribute values from the bound arguments. Records exceptions and
  re-raises.

- :func:`traced_span` — context manager for inline use when the
  span isn't a perfect 1:1 with a method (e.g. wrapping an inner loop
  or a third-party SDK call inside a larger function).

Why a stub instead of forcing the SDK
-------------------------------------

The opentelemetry-* packages are extras, not core deps for every
plugin. A plugin written for a self-hoster who hasn't enabled tracing
should still import cleanly. The ``_NoopTracer`` returned when the
SDK isn't available has the same surface as the real Tracer (the
methods used here only — ``start_as_current_span``,
``record_exception``, ``set_attribute``, ``set_status``) so call
sites are identical either way.

This is the same shape the existing two instrumented files (dispatcher,
ollama_client) implemented inline; this module just makes that shape
the canonical pattern instead of duplicated boilerplate.
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

# ---------------------------------------------------------------------------
# No-op fallback — same surface as opentelemetry.trace.Tracer
# ---------------------------------------------------------------------------


class _NoopSpan:
    """Stand-in Span used when the OTel SDK isn't installed.

    Exposes the four span methods plugin authors actually call. Doing
    nothing is the contract.
    """

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_attributes(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        pass


@contextmanager
def _noop_span(_name: str, **_kwargs: Any) -> Iterator[_NoopSpan]:
    yield _NoopSpan()


class _NoopTracer:
    """Stand-in Tracer with the one method this helper exposes."""

    start_as_current_span = staticmethod(_noop_span)


# ---------------------------------------------------------------------------
# get_tracer — single import-or-noop dance shared by all plugins
# ---------------------------------------------------------------------------


def get_tracer(instrumenting_module_name: str) -> Any:
    """Return a tracer for the named module, or a no-op stub.

    Plugin authors call this once at module load time and use the
    returned tracer everywhere. The argument is the OTel
    "instrumenting module name" — convention is the package path, e.g.
    ``"poindexter.image_providers.midjourney"``. It shows up as the
    span's instrumentation scope in Tempo/Jaeger/etc.

    When ``opentelemetry-api`` isn't importable the function returns a
    no-op tracer with the same call surface, so plugin code paths
    don't fork on whether the SDK is present.
    """
    try:
        from opentelemetry import trace as _otel_trace  # type: ignore[import-untyped]

        return _otel_trace.get_tracer(instrumenting_module_name)
    except ImportError:  # pragma: no cover — exercised in minimal dev envs
        return _NoopTracer()


# ---------------------------------------------------------------------------
# inject_trace_context / extract_trace_context — carry W3C trace context
# across a NON-HTTP boundary (the ``pipeline_tasks`` enqueue -> claim handoff).
# HTTP hops are covered by the FastAPI / httpx instrumentors; the DB queue is
# not, so without these two helpers a content run starts a fresh root trace
# disconnected from whatever created the task (Glad-Labs/glad-labs-stack#1997
# Tier 1b).
# ---------------------------------------------------------------------------


def inject_trace_context(context: Any = None) -> dict[str, str] | None:
    """Serialize the current (or given) OTel context to a W3C carrier dict.

    Returns ``None`` when opentelemetry isn't installed or there is no active
    span context — so an enqueuer with no live trace stores NULL and the
    consumer just starts a fresh root span (the pre-Tier-1b behavior). Used at
    task-enqueue time (``tasks_db.add_task``) to stash the creator's trace on
    the ``pipeline_tasks`` row; the claiming flow re-hydrates it via
    :func:`extract_trace_context`.
    """
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return None
    carrier: dict[str, str] = {}
    # The default (W3C) propagator only writes ``traceparent`` when the context
    # carries a VALID span; an empty / sampled-out context leaves the carrier
    # empty, which we normalize to None.
    inject(carrier, context=context)
    return carrier or None


def extract_trace_context(carrier: Any) -> Any | None:
    """Re-hydrate an OTel parent context from a stored W3C carrier.

    Accepts the carrier as a dict (in-process) OR a JSON string (asyncpg returns
    ``jsonb`` as text unless a codec decodes it). Returns ``None`` for empty /
    malformed input or when opentelemetry isn't installed, so the consumer
    falls back to a fresh root span. Never raises.
    """
    if not carrier:
        return None
    if isinstance(carrier, str):
        try:
            carrier = json.loads(carrier)
        except (ValueError, TypeError):
            return None
    if not isinstance(carrier, dict):
        return None
    try:
        from opentelemetry.propagate import extract
    except ImportError:
        return None
    try:
        return extract(carrier)
    except Exception:
        # silent-ok: a corrupt/incompatible carrier must degrade to "no parent
        # context" (fresh root span) — telemetry re-hydration never breaks the
        # pipeline. Identical outcome to the row having stored NULL.
        return None


def stamp_langfuse_trace_url(span: Any, langfuse_host: str | None) -> str | None:
    """Stamp a Langfuse deep-link onto ``span`` so a Tempo viewer can click
    straight through to the matching Langfuse trace.

    The link is ``{langfuse_host}/trace/{trace_id}``, keyed on the span's own
    OTel trace_id. It resolves because LiteLLM's OTEL integration parents every
    LLM span under the active span (auto-detected from the global context, see
    ``litellm.integrations.opentelemetry._get_span_context`` Priority 3), and
    Langfuse keys its trace on the ingested OTLP trace_id — so the run's
    generations land under this same id. This is the Tempo -> Langfuse
    by-product of coherent W3C propagation (Glad-Labs/glad-labs-stack#1997
    Tier 1c): cheap precisely because Tier 1a/1b already made the trace_id shared.

    Returns the URL (handy for tests / a debug log) or ``None`` — stamping
    nothing — when there is no host, no ``get_span_context`` (the SDK-absent
    noop span), or an invalid/sampled-out context (``trace_id == 0``). Pure
    string work on a real span, so no broad except is needed; the two guards
    below cover every degrade path without one.
    """
    if not langfuse_host:
        return None
    get_ctx = getattr(span, "get_span_context", None)
    if get_ctx is None:
        # Noop span (opentelemetry SDK not installed) — nothing to link.
        return None
    trace_id = getattr(get_ctx(), "trace_id", 0)
    if not trace_id:
        # INVALID_SPAN_CONTEXT — tracing disabled or this span was sampled out.
        return None
    trace_hex = format(trace_id, "032x")
    url = f"{langfuse_host.rstrip('/')}/trace/{trace_hex}"
    span.set_attribute("langfuse.trace_url", url)
    span.set_attribute("langfuse.trace_id", trace_hex)
    return url


# ---------------------------------------------------------------------------
# traced_span — context manager (the inline case)
# ---------------------------------------------------------------------------


@contextmanager
def traced_span(
    tracer: Any,
    name: str,
    /,
    **attributes: Any,
) -> Iterator[Any]:
    """Open a span, set attributes, yield it. Re-raises on exception
    after recording it on the span.

    Use when the span doesn't map 1:1 to a function. The decorator
    form (:func:`traced_method`) is the better fit when it does.

    Example:

        with traced_span(_tracer, "midjourney.poll", job_id=job_id) as span:
            result = await poll(job_id)
            span.set_attribute("midjourney.frames", len(result.frames))
    """
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            try:
                span.set_attribute(k, v)
            except Exception:
                # OTel rejects some types (None, sets, custom objects).
                # Cast and retry; if that fails, drop the attribute
                # rather than failing the call.
                try:
                    span.set_attribute(k, str(v))
                except Exception:
                    pass
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            raise


# ---------------------------------------------------------------------------
# traced_method — decorator (the common case)
# ---------------------------------------------------------------------------


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def traced_method(
    span_name: str,
    /,
    *,
    attrs: Sequence[str] = (),
    tracer_attr: str = "_tracer",
) -> Callable[[F], F]:
    """Wrap an async method with a span named ``span_name``.

    The wrapper opens a span, optionally extracts attribute values
    from the bound method arguments by name, runs the method, records
    any exception, and re-raises. Designed for the Protocol-method
    case: ``generate``, ``embed``, ``upload``, ``search``, etc.

    Args:
        span_name: Span name (e.g. ``"openai.complete"``,
            ``"r2.upload"``). Goes verbatim into the trace.
        attrs: Names of bound arguments whose values should be set as
            span attributes. Each name is looked up in the method's
            signature; if the call site supplies it (positional or
            keyword), its value lands on the span as
            ``llm.<name>`` for typed numeric / string args, falling
            back to a stringified value for anything else. Lengths
            of strings/lists are also auto-emitted as
            ``<name>_chars`` / ``<name>_count`` so plugin authors
            don't have to write that manually.
        tracer_attr: Name of the attribute on ``self`` holding the
            tracer instance. Defaults to ``_tracer`` — plugins should
            stash ``get_tracer(...)`` on the class or instance under
            that name. If not set, the decorator looks for a module
            global ``_tracer`` as a fallback.

    Example:

        class MidjourneyProvider:
            _tracer = get_tracer("poindexter.image_providers.midjourney")

            @traced_method("midjourney.generate", attrs=("model", "prompt"))
            async def generate(self, prompt: str, model: str = "v6") -> bytes:
                ...

    The resulting span carries:

        midjourney.generate {
            model: "v6",
            prompt_chars: 142,        # auto-derived from string length
        }

    """

    def decorator(func: F) -> F:
        sig = inspect.signature(func)

        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            tracer = getattr(self, tracer_attr, None)
            if tracer is None:
                # Module-level fallback — let plugins that store the
                # tracer at module scope work without an instance attr.
                module = inspect.getmodule(func)
                tracer = getattr(module, tracer_attr, None) if module else None
            if tracer is None:
                # No tracer wired anywhere — treat as no-op.
                return await func(self, *args, **kwargs)

            # Bind args to extract attribute values by name.
            bound: dict[str, Any] = {}
            try:
                ba = sig.bind_partial(self, *args, **kwargs)
                ba.apply_defaults()
                bound = dict(ba.arguments)
            except TypeError:
                # Mismatch shouldn't happen; if it does, just drop
                # attributes rather than fail the call.
                pass

            attr_kwargs: dict[str, Any] = {}
            for name in attrs:
                if name not in bound:
                    continue
                v = bound[name]
                # Numeric/bool: attach as-is. String: BOTH the value
                # itself (truncated to keep span size reasonable) AND
                # ``<name>_chars`` so dashboards can group by model
                # name and chart prompt size separately. List/tuple:
                # ``<name>_count`` only — don't dump contents.
                if isinstance(v, (bool, int, float)):
                    attr_kwargs[name] = v
                elif isinstance(v, str):
                    attr_kwargs[name] = v[:200]
                    attr_kwargs[f"{name}_chars"] = len(v)
                elif isinstance(v, (list, tuple, set)):
                    attr_kwargs[f"{name}_count"] = len(v)
                elif v is None:
                    attr_kwargs[name] = ""
                else:
                    attr_kwargs[name] = str(v)[:200]

            with traced_span(tracer, span_name, **attr_kwargs):
                return await func(self, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = [
    "extract_trace_context",
    "get_tracer",
    "inject_trace_context",
    "stamp_langfuse_trace_url",
    "traced_method",
    "traced_span",
]
