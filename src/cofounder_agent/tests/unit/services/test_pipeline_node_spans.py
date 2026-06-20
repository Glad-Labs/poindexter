"""Per-node OTel spans for the pipeline subprocess (poindexter#711 item 1).

``services.pipeline_architect._span_wrap`` wraps each graph node callable in a
span so the Prefect subprocess emits a per-node trace tree to Tempo. The
subprocess already wires the tracer provider (``setup_telemetry``), but no node
created a span, so Tempo only ever saw the FastAPI worker's HTTP spans. The
wrapper sits at the ``add_node`` seam so the delicate ``make_stage_node`` /
``_wrap_atom`` bodies stay untouched.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.errors import GraphInterrupt

from services import pipeline_architect as pa

pytestmark = pytest.mark.unit


class _FakeSpan:
    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}
        self.exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)

    def set_status(self, *_a: Any, **_k: Any) -> None:
        pass


class _FakeSpanCM:
    def __init__(self, span: _FakeSpan) -> None:
        self._span = span

    def __enter__(self) -> _FakeSpan:
        return self._span

    def __exit__(self, *_a: Any) -> bool:
        return False


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _FakeSpan]] = []

    def start_as_current_span(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> _FakeSpanCM:
        span = _FakeSpan()
        if attributes:
            span.attrs.update(attributes)
        self.spans.append((name, span))
        return _FakeSpanCM(span)


@pytest.fixture
def fake_tracer(monkeypatch: pytest.MonkeyPatch) -> _FakeTracer:
    tracer = _FakeTracer()
    monkeypatch.setattr(pa, "_NODE_TRACER", tracer)
    return tracer


@pytest.mark.asyncio
async def test_span_wrap_names_span_and_passes_through(fake_tracer: _FakeTracer) -> None:
    """The span is named ``pipeline.<kind>.<node_id>``, node identity lands as
    attributes, and the wrapped callable's args + result pass through intact."""

    async def inner(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        return {"ok": True, "saw_config": config}

    wrapped = pa._span_wrap(inner, "content.generate_draft", "atom")
    out = await wrapped({"task_id": "t1"}, {"configurable": {"x": 1}})

    assert out == {"ok": True, "saw_config": {"configurable": {"x": 1}}}
    assert fake_tracer.spans, "no span was created"
    name, span = fake_tracer.spans[0]
    assert name == "pipeline.atom.content.generate_draft"
    assert span.attrs.get("pipeline.node_id") == "content.generate_draft"
    assert span.attrs.get("pipeline.node_kind") == "atom"


@pytest.mark.asyncio
async def test_span_wrap_propagates_graph_interrupt_without_recording(
    fake_tracer: _FakeTracer,
) -> None:
    """GraphInterrupt is a langgraph pause (interrupt()-based approval gate),
    not a failure — it must propagate untouched and NOT be recorded as a span
    error, matching make_stage_node / _wrap_atom."""

    async def inner(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        raise GraphInterrupt(())

    wrapped = pa._span_wrap(inner, "atoms.approval_gate", "atom")
    with pytest.raises(GraphInterrupt):
        await wrapped({}, None)

    _name, span = fake_tracer.spans[0]
    assert span.exceptions == [], "GraphInterrupt must not be recorded as a span error"


@pytest.mark.asyncio
async def test_span_wrap_records_and_reraises_real_errors(
    fake_tracer: _FakeTracer,
) -> None:
    """A genuine stage failure is recorded on the span AND re-raised so the
    graph still halts (no swallowing)."""
    boom = ValueError("stage blew up")

    async def inner(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        raise boom

    wrapped = pa._span_wrap(inner, "stage.verify_task", "stage")
    with pytest.raises(ValueError):
        await wrapped({}, None)

    _name, span = fake_tracer.spans[0]
    assert boom in span.exceptions


@pytest.mark.asyncio
async def test_node_spans_nest_under_active_flow_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a real OTel tracer, per-node spans nest under whatever span is
    active when the node runs — so the flow-level span opened in
    ``content_generation`` parents EVERY node into one trace per run.

    This is the load-bearing behavior behind "one Tempo trace per run": it
    works only because ``_span_wrap`` uses ``start_as_current_span`` (which
    reads the active context), not a detached ``start_span``. Verified with a
    real in-memory SDK exporter rather than a mock, since the property under
    test is OTel context propagation, not a call we control.
    """
    trace_sdk = pytest.importorskip("opentelemetry.sdk.trace")
    export_mod = pytest.importorskip("opentelemetry.sdk.trace.export")
    in_memory_mod = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )

    exporter = in_memory_mod.InMemorySpanExporter()
    provider = trace_sdk.TracerProvider()
    provider.add_span_processor(export_mod.SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test.pipeline")
    # Route node spans through the SAME real provider as the flow span.
    monkeypatch.setattr(pa, "_NODE_TRACER", tracer)

    async def node_a(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        return {"a": 1}

    async def node_b(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        return {"b": 2}

    wrapped_a = pa._span_wrap(node_a, "content.generate_draft", "atom")
    wrapped_b = pa._span_wrap(node_b, "verify_task", "stage")

    # Open a flow-level span (what content_generation will do per run), then
    # run two nodes "inside" it the way the compiled graph does.
    with tracer.start_as_current_span("pipeline.content_generation") as flow_span:
        flow_ctx = flow_span.get_span_context()
        await wrapped_a({}, None)
        await wrapped_b({}, None)

    finished = exporter.get_finished_spans()
    by_name = {s.name: s for s in finished}
    assert "pipeline.atom.content.generate_draft" in by_name
    assert "pipeline.stage.verify_task" in by_name
    assert "pipeline.content_generation" in by_name

    # All three spans share the flow span's trace_id (one trace per run) ...
    assert {s.context.trace_id for s in finished} == {flow_ctx.trace_id}
    # ... and each node span's parent IS the flow span (true nesting).
    for node_name in (
        "pipeline.atom.content.generate_draft",
        "pipeline.stage.verify_task",
    ):
        assert by_name[node_name].parent is not None
        assert by_name[node_name].parent.span_id == flow_ctx.span_id
