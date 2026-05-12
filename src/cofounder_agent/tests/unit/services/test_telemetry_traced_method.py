"""Unit tests for services.telemetry.traced_method (poindexter#416).

When OpenTelemetry isn't installed the decorator returns the function
unchanged (no-op). When it IS installed, each call enters a span; we
mock the tracer to capture the span name + attribute set without
needing a real OTLP exporter.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import services.telemetry as telemetry


@pytest.mark.asyncio
async def test_passthrough_when_opentelemetry_unavailable(monkeypatch):
    """Decorator should return the wrapped function unchanged when SDK absent."""
    monkeypatch.setattr(telemetry, "OPENTELEMETRY_AVAILABLE", False)
    monkeypatch.setattr(telemetry, "_HOT_PATH_TRACER", None)
    monkeypatch.setattr(telemetry, "trace", None)

    async def real(model: str, prompt: str) -> str:
        return f"{model}:{prompt}"

    decorated = telemetry.traced_method("ollama.generate", attrs=("model", "prompt"))(real)
    assert decorated is real  # identity — no wrapping
    assert await decorated("glm", "hi") == "glm:hi"


@pytest.mark.asyncio
async def test_span_emitted_with_attrs(monkeypatch):
    """When the SDK is installed, every call goes through start_as_current_span."""
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=None)
    tracer = MagicMock()
    tracer.start_as_current_span = MagicMock(return_value=span)

    monkeypatch.setattr(telemetry, "OPENTELEMETRY_AVAILABLE", True)
    monkeypatch.setattr(telemetry, "_HOT_PATH_TRACER", tracer)
    monkeypatch.setattr(telemetry, "trace", MagicMock())

    async def real(model: str, prompt: str) -> str:
        return f"{model}:{prompt}"

    decorated = telemetry.traced_method("ollama.generate", attrs=("model", "prompt"))(real)
    result = await decorated(model="glm-4.7-5090", prompt="hello world")
    assert result == "glm-4.7-5090:hello world"
    tracer.start_as_current_span.assert_called_once_with("ollama.generate")
    # Both attrs should have been stamped on the span.
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert set_attr_calls == {"model": "glm-4.7-5090", "prompt": "hello world"}


@pytest.mark.asyncio
async def test_long_prompt_truncated(monkeypatch):
    """`prompt` longer than PROMPT_ATTR_MAX_CHARS is truncated for span size."""
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=None)
    tracer = MagicMock()
    tracer.start_as_current_span = MagicMock(return_value=span)

    monkeypatch.setattr(telemetry, "OPENTELEMETRY_AVAILABLE", True)
    monkeypatch.setattr(telemetry, "_HOT_PATH_TRACER", tracer)
    monkeypatch.setattr(telemetry, "trace", MagicMock())

    big_prompt = "x" * (telemetry.PROMPT_ATTR_MAX_CHARS + 500)

    async def real(model: str, prompt: str) -> str:
        return "ok"

    decorated = telemetry.traced_method("ollama.generate", attrs=("model", "prompt"))(real)
    await decorated(model="glm", prompt=big_prompt)
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    prompt_attr = set_attr_calls["prompt"]
    assert len(prompt_attr) <= telemetry.PROMPT_ATTR_MAX_CHARS + len("…[truncated]")
    assert prompt_attr.endswith("…[truncated]")


@pytest.mark.asyncio
async def test_exception_recorded_and_reraised(monkeypatch):
    """Exceptions go through record_exception + are re-raised, not swallowed."""
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=None)
    tracer = MagicMock()
    tracer.start_as_current_span = MagicMock(return_value=span)

    monkeypatch.setattr(telemetry, "OPENTELEMETRY_AVAILABLE", True)
    monkeypatch.setattr(telemetry, "_HOT_PATH_TRACER", tracer)
    monkeypatch.setattr(telemetry, "trace", MagicMock())

    async def real(model: str) -> str:
        raise ValueError("nope")

    decorated = telemetry.traced_method("ollama.generate", attrs=("model",))(real)
    with pytest.raises(ValueError, match="nope"):
        await decorated(model="glm")
    span.record_exception.assert_called_once()
    # Argument is the exception itself.
    recorded = span.record_exception.call_args.args[0]
    assert isinstance(recorded, ValueError)
