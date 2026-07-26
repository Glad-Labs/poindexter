"""langfuse_shim trace stamping (poindexter#902).

The console's ``/api/traces`` list reads TRACE-level fields; observation-level
model never reaches it. These tests pin the two shim behaviours that close the
gap: ``_stamp_trace_attrs`` writes the reserved ``langfuse.trace.*`` OTEL
attributes on the current span, and the v4 ``update_current_observation``
compat mirrors ``model`` onto the trace.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import langfuse_shim


class _RecordingSpan:
    def __init__(self, recording: bool = True):
        self._recording = recording
        self.attrs: list[tuple[str, Any]] = []

    def is_recording(self) -> bool:
        return self._recording

    def set_attribute(self, key: str, value: Any) -> None:
        self.attrs.append((key, value))


@pytest.fixture
def span(monkeypatch):
    """Patch opentelemetry's current span with a recorder."""
    otel_trace = pytest.importorskip("opentelemetry.trace")
    fake = _RecordingSpan()
    monkeypatch.setattr(otel_trace, "get_current_span", lambda: fake)
    return fake


def test_stamp_trace_attrs_sets_reserved_keys(span):
    langfuse_shim._stamp_trace_attrs(
        metadata={"model": "gemma-4-31B", "task_id": "t-1", "skipped": None},
        session_id="t-1",
        name="my_trace",
    )
    assert ("langfuse.trace.name", "my_trace") in span.attrs
    assert ("session.id", "t-1") in span.attrs
    assert ("langfuse.trace.metadata.model", "gemma-4-31B") in span.attrs
    assert ("langfuse.trace.metadata.task_id", "t-1") in span.attrs
    # None-valued metadata entries are dropped, not stamped as "None".
    assert not any(k == "langfuse.trace.metadata.skipped" for k, _ in span.attrs)


def test_stamp_trace_attrs_noop_on_non_recording_span(monkeypatch):
    otel_trace = pytest.importorskip("opentelemetry.trace")
    fake = _RecordingSpan(recording=False)
    monkeypatch.setattr(otel_trace, "get_current_span", lambda: fake)
    langfuse_shim._stamp_trace_attrs(metadata={"model": "m"})
    assert fake.attrs == []


def test_stamp_trace_attrs_swallows_errors(monkeypatch):
    otel_trace = pytest.importorskip("opentelemetry.trace")

    def _boom():
        raise RuntimeError("otel exploded")

    monkeypatch.setattr(otel_trace, "get_current_span", _boom)
    langfuse_shim._stamp_trace_attrs(metadata={"model": "m"})  # must not raise


@pytest.mark.skipif(
    not langfuse_shim.LANGFUSE_AVAILABLE, reason="langfuse SDK not installed"
)
def test_update_current_observation_mirrors_model_to_trace(span, monkeypatch):
    """Every existing @observe call site stamps model via
    update_current_observation — the mirror gives the trace list its model
    column with zero per-site changes."""
    if not hasattr(langfuse_shim, "_lf_get_client"):
        pytest.skip("legacy v3 langfuse path — mirror is v4-compat only")

    class _FakeClient:
        def update_current_generation(self, **_kwargs: Any) -> None:
            return None

    monkeypatch.setattr(langfuse_shim, "_lf_get_client", lambda: _FakeClient())
    langfuse_shim.langfuse_context.update_current_observation(
        model="gemma-4-31B", input=[{"role": "user", "content": "hi"}]
    )
    assert ("langfuse.trace.metadata.model", "gemma-4-31B") in span.attrs


@pytest.mark.skipif(
    not langfuse_shim.LANGFUSE_AVAILABLE, reason="langfuse SDK not installed"
)
def test_update_current_trace_maps_kwargs(span):
    if not hasattr(langfuse_shim, "_lf_get_client"):
        pytest.skip("legacy v3 langfuse path — compat mapping is v4 only")
    langfuse_shim.langfuse_context.update_current_trace(
        session_id="t-2", metadata={"task_id": "t-2"}
    )
    assert ("session.id", "t-2") in span.attrs
    assert ("langfuse.trace.metadata.task_id", "t-2") in span.attrs
