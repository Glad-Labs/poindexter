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


# ---------------------------------------------------------------------------
# Helpers for the additional contract-pinning tests below.
# ---------------------------------------------------------------------------

def _stub_tracer():
    """Build a (tracer, span) MagicMock pair suitable for monkeypatching
    in. Each test gets a fresh pair so set_attribute / record_exception
    assertions don't leak across tests."""
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=None)
    tracer = MagicMock()
    tracer.start_as_current_span = MagicMock(return_value=span)
    return tracer, span


def _enable_otel(monkeypatch):
    """Flip the module-level toggles so traced_method takes the wrapping
    path. Returns the (tracer, span) pair the wrapped fn will run against."""
    tracer, span = _stub_tracer()
    monkeypatch.setattr(telemetry, "OPENTELEMETRY_AVAILABLE", True)
    monkeypatch.setattr(telemetry, "_HOT_PATH_TRACER", tracer)
    monkeypatch.setattr(telemetry, "trace", MagicMock())
    return tracer, span


@pytest.mark.asyncio
async def test_positional_args_resolve_attrs(monkeypatch):
    """attrs are read by NAME but via ``sig.bind_partial(*args, **kwargs)``,
    so positional calls must populate span attributes the same way kwargs
    do. Without this, every legacy positional caller would emit attribute-less
    spans and the user wouldn't notice until they tried to filter on model."""
    tracer, span = _enable_otel(monkeypatch)

    async def real(model: str, prompt: str) -> str:
        return f"{model}:{prompt}"

    decorated = telemetry.traced_method("ollama.generate", attrs=("model", "prompt"))(real)
    result = await decorated("glm-4.7-5090", "hello")  # positional!
    assert result == "glm-4.7-5090:hello"
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert set_attr_calls == {"model": "glm-4.7-5090", "prompt": "hello"}


@pytest.mark.asyncio
async def test_missing_attr_name_silently_skipped(monkeypatch):
    """Requesting an attr that the wrapped function doesn't accept must not
    raise — bound.arguments simply won't contain that key. Pins ``if attr_name
    in bound.arguments`` so a fat-fingered attrs tuple can't crash hot path."""
    tracer, span = _enable_otel(monkeypatch)

    async def real(model: str) -> str:
        return model

    decorated = telemetry.traced_method(
        "ollama.generate", attrs=("model", "nonexistent_arg"),
    )(real)
    assert await decorated(model="glm") == "glm"
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    # model got set; nonexistent_arg was skipped (not a crash, not an empty string).
    assert set_attr_calls == {"model": "glm"}
    assert "nonexistent_arg" not in set_attr_calls


@pytest.mark.asyncio
async def test_bind_partial_failure_skips_attrs_but_calls_fn(monkeypatch):
    """When ``sig.bind_partial`` raises TypeError (e.g. duplicated kwarg),
    the wrapper sets bound=None and skips attr-setting — but the wrapped
    function is still invoked (and will surface its own TypeError to the
    caller). Pins the 'don't swallow signature errors, but don't poison the
    span either' contract."""
    tracer, span = _enable_otel(monkeypatch)

    async def real(model: str) -> str:
        return model

    decorated = telemetry.traced_method("ollama.generate", attrs=("model",))(real)
    # Pass an unknown kwarg — Python raises TypeError when invoking real(),
    # but bind_partial also fails first. Either way, no attribute set call,
    # and the underlying TypeError propagates.
    with pytest.raises(TypeError):
        await decorated(model="glm", surprise_kwarg="boom")
    # Span was entered, but no attributes stamped because bind failed.
    assert span.set_attribute.call_count == 0


@pytest.mark.asyncio
async def test_non_string_attr_value_is_stringified(monkeypatch):
    """``_coerce_attr`` runs ``str(value)`` on non-str inputs. Pins the
    contract that ints, floats, dicts etc. are safe to declare as attrs
    without callers having to pre-stringify."""
    tracer, span = _enable_otel(monkeypatch)

    async def real(model: str, retries: int) -> str:
        return f"{model}-{retries}"

    decorated = telemetry.traced_method(
        "ollama.generate", attrs=("model", "retries"),
    )(real)
    await decorated(model="glm", retries=3)
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert set_attr_calls["retries"] == "3"  # int -> str, not 3
    assert isinstance(set_attr_calls["retries"], str)


@pytest.mark.asyncio
async def test_model_attr_never_truncated(monkeypatch):
    """``model`` is intentionally absent from ``_TRUNCATE_ATTRS`` because
    model IDs are short. If someone passes a 5000-char "model" name, it
    should still come through verbatim — losing the tail would silently
    misroute traces in Tempo."""
    tracer, span = _enable_otel(monkeypatch)

    async def real(model: str) -> str:
        return model

    long_model = "x" * (telemetry.PROMPT_ATTR_MAX_CHARS + 500)
    decorated = telemetry.traced_method("ollama.generate", attrs=("model",))(real)
    await decorated(model=long_model)
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert set_attr_calls["model"] == long_model  # no truncation
    assert not set_attr_calls["model"].endswith("…[truncated]")


async def _fn_system(system: str) -> str:
    return "ok"


async def _fn_messages(messages: str) -> str:
    return "ok"


async def _fn_text(text: str) -> str:
    return "ok"


async def _fn_content(content: str) -> str:
    return "ok"


@pytest.mark.parametrize(
    "attr_name,fn",
    [
        ("system", _fn_system),
        ("messages", _fn_messages),
        ("text", _fn_text),
        ("content", _fn_content),
    ],
)
@pytest.mark.asyncio
async def test_other_truncate_attrs_are_truncated(monkeypatch, attr_name, fn):
    """Every name in ``_TRUNCATE_ATTRS`` other than ``prompt`` (already
    covered) must also truncate. Parametrized so a future addition to the
    frozenset needs an explicit test, not a silent regression.

    Note: ``traced_method`` resolves attrs against named parameters only —
    ``inspect.signature(fn).bind_partial`` packs ``**kwargs`` under a
    single key. Each fixture below uses a real positional parameter."""
    _tracer, span = _enable_otel(monkeypatch)

    decorated = telemetry.traced_method("ollama.generate", attrs=(attr_name,))(fn)
    big = "y" * (telemetry.PROMPT_ATTR_MAX_CHARS + 200)
    await decorated(**{attr_name: big})
    set_attr_calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert set_attr_calls[attr_name].endswith("…[truncated]")
    assert len(set_attr_calls[attr_name]) <= (
        telemetry.PROMPT_ATTR_MAX_CHARS + len("…[truncated]")
    )


@pytest.mark.asyncio
async def test_functools_wraps_preserves_metadata(monkeypatch):
    """The decorator uses ``functools.wraps``; introspection tooling
    (pytest's collection, Langfuse's @observe stacked above us, sentry's
    breadcrumb capture) all rely on ``__name__`` / ``__wrapped__``
    being preserved."""
    _enable_otel(monkeypatch)

    async def real_named_function(model: str) -> str:
        """Real docstring."""
        return model

    decorated = telemetry.traced_method("ollama.generate", attrs=("model",))(real_named_function)
    assert decorated.__name__ == "real_named_function"
    assert decorated.__doc__ == "Real docstring."
    assert decorated.__wrapped__ is real_named_function


def test_coerce_attr_helper_directly():
    """Unit-test ``_coerce_attr`` without going through the full async/span
    machinery. Boundary at exactly ``PROMPT_ATTR_MAX_CHARS`` must NOT
    truncate (truncation triggers on ``len > MAX``, strict-greater)."""
    boundary = "z" * telemetry.PROMPT_ATTR_MAX_CHARS
    assert telemetry._coerce_attr("prompt", boundary) == boundary
    assert telemetry._coerce_attr("prompt", boundary + "x").endswith("…[truncated]")
    # Non-truncate attr — no truncation regardless of length.
    assert telemetry._coerce_attr("model", boundary + "x") == boundary + "x"
    # Non-string value — stringified.
    assert telemetry._coerce_attr("model", 42) == "42"
    assert telemetry._coerce_attr("model", None) == "None"


@pytest.mark.asyncio
async def test_set_status_import_failure_does_not_swallow_exception(monkeypatch):
    """The ``from opentelemetry.trace import Status, StatusCode`` block is
    wrapped in try/except — if Status import itself fails on some OTel
    versions, the original exception must still propagate to the caller.
    Pin via a meta_path finder that raises ImportError for that exact name."""
    tracer, span = _enable_otel(monkeypatch)

    # Sabotage `from opentelemetry.trace import Status` — when the wrapper
    # tries to import it inside the except block, raise ImportError. The
    # outer except: pass should swallow that and re-raise the *original*
    # ValueError, not the ImportError.
    import sys
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "opentelemetry.trace":
            raise ImportError("simulated SDK skew")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)  # ensure not cached
    monkeypatch.setattr("builtins.__import__", fake_import)

    async def real(model: str) -> str:
        raise ValueError("payload error")

    decorated = telemetry.traced_method("ollama.generate", attrs=("model",))(real)
    # Original ValueError survives even though Status import blew up.
    with pytest.raises(ValueError, match="payload error"):
        await decorated(model="glm")
    span.record_exception.assert_called_once()
    # set_status was NOT called (because the import failed), but
    # crucially the wrapper didn't crash with ImportError.
    span.set_status.assert_not_called()


@pytest.mark.asyncio
async def test_empty_attrs_skips_bind_entirely(monkeypatch):
    """When ``attrs=()``, the wrapper must not even call sig.bind_partial
    — pinning the ``if attrs:`` short-circuit. Avoids spending CPU
    binding arguments we won't read."""
    tracer, span = _enable_otel(monkeypatch)

    async def real(model: str) -> str:
        return model

    decorated = telemetry.traced_method("ollama.generate", attrs=())(real)
    assert await decorated(model="glm") == "glm"
    # Span was entered, but zero set_attribute calls.
    tracer.start_as_current_span.assert_called_once_with("ollama.generate")
    assert span.set_attribute.call_count == 0
