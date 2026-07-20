"""Unit tests for the W3C context-propagation helpers in ``plugins/tracing.py``.

``inject_trace_context`` / ``extract_trace_context`` carry trace context across
the ``pipeline_tasks`` enqueue -> claim handoff so the Prefect flow's root span
links to the trace of whatever created the task (Tier 1b,
Glad-Labs/poindexter#1997). ``stamp_langfuse_trace_url`` is the Tier-1c
by-product: it writes a Langfuse deep-link onto the root span so a Tempo viewer
can click through to the matching Langfuse trace.

API-only: a ``NonRecordingSpan`` wrapping a hand-built valid ``SpanContext`` is
enough to exercise the real W3C propagator roundtrip without the OTel SDK.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

# A fixed, valid W3C trace/span id pair (the example from the W3C spec).
_TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
_SPAN_ID = 0x00F067AA0BA902B7
# The same trace_id rendered as Langfuse keys it: lowercase 32-hex.
_TRACE_HEX = "0af7651916cd43dd8448eb211c80319c"


class _RecordingSpan:
    """Span double that records ``set_attribute`` calls and returns a fixed
    span context — mirrors the surface ``stamp_langfuse_trace_url`` touches."""

    def __init__(self, trace_id: int):
        self._trace_id = trace_id
        self.attributes: dict[str, str] = {}

    def get_span_context(self):
        return SimpleNamespace(trace_id=self._trace_id)

    def set_attribute(self, key, value):
        self.attributes[key] = value


def _ctx_with_span(trace_id: int, span_id: int):
    """Build an OTel Context carrying a valid (non-recording) span."""
    from opentelemetry import trace as ot
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    sc = SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return ot.set_span_in_context(NonRecordingSpan(sc))


class TestInjectTraceContext:
    def test_returns_none_without_active_span(self):
        """An empty context has no valid span, so the W3C propagator writes no
        traceparent — we return None so callers store NULL (today's behavior)."""
        from opentelemetry.context import Context

        from plugins.tracing import inject_trace_context

        assert inject_trace_context(context=Context()) is None

    def test_returns_carrier_with_traceparent_for_active_span(self):
        from plugins.tracing import inject_trace_context

        carrier = inject_trace_context(context=_ctx_with_span(_TRACE_ID, _SPAN_ID))

        assert carrier is not None
        assert "traceparent" in carrier
        # W3C format: 00-<32 hex trace_id>-<16 hex span_id>-<2 hex flags>
        assert carrier["traceparent"].startswith("00-")
        assert format(_TRACE_ID, "032x") in carrier["traceparent"]


class TestExtractTraceContext:
    def test_roundtrip_preserves_trace_and_span_ids(self):
        from opentelemetry import trace as ot

        from plugins.tracing import extract_trace_context, inject_trace_context

        carrier = inject_trace_context(context=_ctx_with_span(_TRACE_ID, _SPAN_ID))
        assert carrier is not None

        extracted = extract_trace_context(carrier)
        sc = ot.get_current_span(extracted).get_span_context()
        assert sc.trace_id == _TRACE_ID
        assert sc.span_id == _SPAN_ID

    def test_none_or_empty_returns_none(self):
        from plugins.tracing import extract_trace_context

        assert extract_trace_context(None) is None
        assert extract_trace_context({}) is None
        assert extract_trace_context("") is None

    def test_accepts_json_string_carrier(self):
        """asyncpg returns jsonb as a str unless a codec decodes it, so the
        claim path may hand us the carrier as JSON text — extract must cope."""
        from opentelemetry import trace as ot

        from plugins.tracing import extract_trace_context, inject_trace_context

        carrier = inject_trace_context(context=_ctx_with_span(_TRACE_ID, _SPAN_ID))
        assert carrier is not None

        extracted = extract_trace_context(json.dumps(carrier))
        sc = ot.get_current_span(extracted).get_span_context()
        assert sc.trace_id == _TRACE_ID
        assert sc.span_id == _SPAN_ID

    def test_malformed_carrier_returns_none(self):
        """A non-JSON string or junk dict must not raise — return None and let
        the consumer start a fresh root span."""
        from plugins.tracing import extract_trace_context

        assert extract_trace_context("not-json{") is None
        # A dict with no traceparent yields a context with no valid span.
        from opentelemetry import trace as ot

        extracted = extract_trace_context({"unrelated": "value"})
        # Either None, or a context whose span is invalid — never a crash.
        if extracted is not None:
            assert not ot.get_current_span(extracted).get_span_context().is_valid


class TestStampLangfuseTraceUrl:
    """Tier 1c (#1997): the Tempo -> Langfuse cross-link by-product.

    The link is keyed on the span's own OTel trace_id, which resolves because
    LiteLLM's OTEL integration parents LLM spans under the active span and
    Langfuse keys its trace on the OTLP trace_id — so the run's generations land
    under this same id. The helper only builds the URL + stamps attributes; it
    must degrade to a no-op (return None, stamp nothing) whenever there's no
    host, no valid trace context, or a noop span.
    """

    def test_builds_url_and_stamps_attributes(self):
        from plugins.tracing import stamp_langfuse_trace_url

        span = _RecordingSpan(_TRACE_ID)
        url = stamp_langfuse_trace_url(span, "http://localhost:3010")

        assert url == f"http://localhost:3010/trace/{_TRACE_HEX}"
        assert span.attributes["langfuse.trace_url"] == url
        assert span.attributes["langfuse.trace_id"] == _TRACE_HEX

    def test_normalizes_trailing_slash_on_host(self):
        from plugins.tracing import stamp_langfuse_trace_url

        span = _RecordingSpan(_TRACE_ID)
        url = stamp_langfuse_trace_url(span, "http://localhost:3010/")

        # No double slash before ``/trace/``.
        assert url == f"http://localhost:3010/trace/{_TRACE_HEX}"

    def test_returns_none_without_host(self):
        """No Langfuse host configured -> nothing to link to. Stamp nothing."""
        from plugins.tracing import stamp_langfuse_trace_url

        span = _RecordingSpan(_TRACE_ID)
        assert stamp_langfuse_trace_url(span, "") is None
        assert stamp_langfuse_trace_url(span, None) is None
        assert span.attributes == {}

    def test_returns_none_for_invalid_context(self):
        """trace_id == 0 is the OTel INVALID context (tracing disabled /
        sampled out) — no real trace to deep-link, so stamp nothing."""
        from plugins.tracing import stamp_langfuse_trace_url

        span = _RecordingSpan(0)
        assert stamp_langfuse_trace_url(span, "http://localhost:3010") is None
        assert span.attributes == {}

    def test_returns_none_for_noop_span(self):
        """A span lacking ``get_span_context`` is the SDK-absent noop span —
        guard with getattr, never raise."""
        from plugins.tracing import stamp_langfuse_trace_url

        class _NoCtxSpan:
            def set_attribute(self, *_a, **_k):
                raise AssertionError("must not stamp when there's no context")

        assert stamp_langfuse_trace_url(_NoCtxSpan(), "http://localhost:3010") is None
