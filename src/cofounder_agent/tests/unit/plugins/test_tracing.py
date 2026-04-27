"""Unit tests for ``plugins/tracing.py``.

Verifies the shared OTel helper does what the docstring promises:

- ``get_tracer`` returns a real tracer when OTel is installed and a
  no-op when it isn't.
- ``traced_span`` opens / sets attributes / records exceptions /
  re-raises.
- ``traced_method`` wraps async methods, extracts attribute values
  from bound arguments by name, auto-derives ``_chars`` / ``_count``
  for strings/lists, and falls back to a module-level ``_tracer``
  when ``self._tracer`` is missing.
"""

from __future__ import annotations

from typing import Any

import pytest

from plugins.tracing import (
    _NoopTracer,
    get_tracer,
    traced_method,
    traced_span,
)


# ---------------------------------------------------------------------------
# Recording fakes — capture span lifecycle without depending on the OTel SDK
# ---------------------------------------------------------------------------


class _RecordingSpan:
    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}
        self.exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        pass


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _RecordingSpan]] = []

    def start_as_current_span(self, name: str, **_kwargs: Any):
        span = _RecordingSpan()
        self.spans.append((name, span))

        from contextlib import contextmanager

        @contextmanager
        def cm():
            yield span

        return cm()


# ---------------------------------------------------------------------------
# get_tracer
# ---------------------------------------------------------------------------


class TestGetTracer:
    def test_returns_real_tracer_when_otel_available(self):
        # opentelemetry-api is in the project deps, so this path should hit.
        tracer = get_tracer("test.module")
        # Real tracer has start_as_current_span; that's enough for our use.
        assert hasattr(tracer, "start_as_current_span")
        # Real tracer is NOT the noop stub.
        assert not isinstance(tracer, _NoopTracer)

    def test_noop_tracer_supports_full_surface(self):
        """If OTel were unavailable, the no-op stub still has the four
        methods plugin code calls. Verified directly on the class."""
        noop = _NoopTracer()
        with noop.start_as_current_span("any") as span:
            span.set_attribute("k", "v")
            span.record_exception(ValueError("ok"))
            span.set_status("ok")


# ---------------------------------------------------------------------------
# traced_span
# ---------------------------------------------------------------------------


class TestTracedSpan:
    def test_sets_attributes_on_span(self):
        tracer = _RecordingTracer()
        with traced_span(tracer, "op.do", model="gemma3", count=3) as span:
            span.set_attribute("inner", "yes")
        name, recorded = tracer.spans[0]
        assert name == "op.do"
        assert recorded.attrs == {"model": "gemma3", "count": 3, "inner": "yes"}

    def test_records_exception_and_reraises(self):
        tracer = _RecordingTracer()
        with pytest.raises(ValueError, match="boom"):
            with traced_span(tracer, "op.do", x=1):
                raise ValueError("boom")
        _, span = tracer.spans[0]
        assert any(isinstance(e, ValueError) for e in span.exceptions)

    def test_unsupported_attribute_value_does_not_break(self):
        """OTel rejects some types — span.set_attribute(k, set()) raises.
        The helper must not let that fail the wrapped call."""
        class _PickyTracer(_RecordingTracer):
            def start_as_current_span(self, name: str, **_kwargs: Any):
                from contextlib import contextmanager

                outer = self

                class _PickySpan(_RecordingSpan):
                    def set_attribute(self, key: str, value: Any) -> None:
                        if isinstance(value, set):
                            raise TypeError("nope")
                        super().set_attribute(key, value)

                span = _PickySpan()
                outer.spans.append((name, span))

                @contextmanager
                def cm():
                    yield span

                return cm()

        tracer = _PickyTracer()
        with traced_span(tracer, "op.do", bad={"x"}, good="yes"):
            pass
        _, span = tracer.spans[0]
        # bad got stringified-fallback; good landed normally.
        assert span.attrs["good"] == "yes"
        # bad either was set as str() or dropped entirely — both are fine.
        # The contract is "don't crash."


# ---------------------------------------------------------------------------
# traced_method
# ---------------------------------------------------------------------------


class _Provider:
    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    @traced_method("provider.gen", attrs=("model", "prompt", "items"))
    async def gen(
        self,
        prompt: str,
        model: str = "default",
        items: list[str] | None = None,
        count: int = 0,
    ) -> str:
        return f"out:{prompt}:{model}:{len(items or [])}:{count}"


@pytest.mark.asyncio
class TestTracedMethod:
    async def test_wraps_method_with_attrs(self):
        tracer = _RecordingTracer()
        p = _Provider(tracer)
        result = await p.gen("hi", model="gemma3", items=["a", "b", "c"])
        assert result == "out:hi:gemma3:3:0"

        name, span = tracer.spans[0]
        assert name == "provider.gen"
        # String → _chars; list → _count; numeric arg "model" was a str
        # so it landed as model_chars; "model" was supplied so itself
        # is recorded as a string-fallback.
        assert span.attrs.get("prompt_chars") == 2  # "hi"
        assert span.attrs.get("items_count") == 3
        # Bare model name landed (string < 200 chars → stringified fallback).
        assert "gemma3" in str(span.attrs.get("model"))

    async def test_records_exception_through_decorator(self):
        tracer = _RecordingTracer()

        class _Bad:
            def __init__(self) -> None:
                self._tracer = tracer

            @traced_method("provider.bad")
            async def go(self) -> None:
                raise RuntimeError("oops")

        with pytest.raises(RuntimeError, match="oops"):
            await _Bad().go()

        _, span = tracer.spans[0]
        assert any(isinstance(e, RuntimeError) for e in span.exceptions)

    async def test_no_tracer_attr_falls_through_to_noop(self):
        """Missing ``self._tracer`` AND no module-level ``_tracer`` —
        the wrapper just calls the wrapped function unchanged."""

        class _NoTracer:
            @traced_method("provider.untraced")
            async def go(self) -> str:
                return "ok"

        result = await _NoTracer().go()
        assert result == "ok"

    async def test_attrs_includes_numeric_directly(self):
        tracer = _RecordingTracer()
        p = _Provider(tracer)
        await p.gen("x", count=42)  # count is int → set as-is
        # gen's attrs spec only includes ("model", "prompt", "items"),
        # so `count` not in attrs even though it's numeric. Verify the
        # whitelist is honored.
        _, span = tracer.spans[0]
        assert "count" not in span.attrs

    async def test_module_level_tracer_fallback(self, monkeypatch):
        """When ``self._tracer`` is missing, the decorator looks up
        ``_tracer`` on the function's module. ``_ModuleScopedProvider``
        is defined at module scope below so ``inspect.getmodule`` can
        resolve to this test module."""
        import plugins.tracing as tmod

        tracer = _RecordingTracer()
        # Patch the test module — _ModuleScopedProvider.go's module is
        # this test file, so the fallback finds _tracer here.
        import sys
        test_mod = sys.modules[__name__]
        monkeypatch.setattr(test_mod, "_tracer", tracer, raising=False)

        await _ModuleScopedProvider().go()
        assert len(tracer.spans) == 1
        assert tracer.spans[0][0] == "provider.mod"
        # Sanity: didn't accidentally pollute plugins.tracing module.
        assert not hasattr(tmod, "_tracer")


# Defined at module scope so inspect.getmodule(go) resolves to this
# test file (the decorator's module-level fallback path).
class _ModuleScopedProvider:
    @traced_method("provider.mod")
    async def go(self) -> str:
        return "ok"
