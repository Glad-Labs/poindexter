"""Unit tests for the W3C context-propagation helpers in ``plugins/tracing.py``.

``inject_trace_context`` / ``extract_trace_context`` carry trace context across
the ``pipeline_tasks`` enqueue -> claim handoff so the Prefect flow's root span
links to the trace of whatever created the task (Tier 1b,
Glad-Labs/glad-labs-stack#1997).

API-only: a ``NonRecordingSpan`` wrapping a hand-built valid ``SpanContext`` is
enough to exercise the real W3C propagator roundtrip without the OTel SDK.
"""

from __future__ import annotations

import json

# A fixed, valid W3C trace/span id pair (the example from the W3C spec).
_TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
_SPAN_ID = 0x00F067AA0BA902B7


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
