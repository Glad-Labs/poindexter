"""Unit tests for ``configure_langfuse_callback`` (poindexter#373).

Exercises the four acceptance criteria from the issue:

1. ``langfuse_tracing_enabled=false`` → no callback registered, env vars
   not stamped, no error raised. Lets the operator kill tracing without
   nuking prompt management.
2. ``langfuse_tracing_enabled=true`` + all credentials present → env
   vars stamped + ``litellm.success_callback`` / ``failure_callback``
   set to ``["langfuse"]``.
3. ``langfuse_tracing_enabled=true`` + missing credential → raises
   :class:`LangfuseConfigError`. Per ``feedback_no_silent_defaults``,
   no quiet skip.
4. Idempotent — calling twice doesn't re-register or raise.

The test stubs ``litellm`` via ``sys.modules`` so it runs without the
real package + isolates ``litellm.success_callback`` mutations between
tests via the ``_reset_module_state`` fixture.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub ``litellm`` before importing the provider so the lazy import
# resolves to our controllable mock regardless of whether the real
# package is installed in the test env.
_litellm_stub = MagicMock(name="litellm")
_litellm_stub.success_callback = []
_litellm_stub.failure_callback = []

# 2026-05-06 fix: previously ``sys.modules.setdefault`` was used here,
# which permanently wedged the MagicMock into ``sys.modules`` for every
# subsequent test. Other test modules that import the real LiteLLM
# (notably ``test_cost_lookup.py``) then resolved ``litellm.model_cost``
# to a MagicMock and computed bogus per-token costs. The fixture-scoped
# ``monkeypatch.setitem`` below restores the real module on teardown so
# downstream tests see authoritative LiteLLM data.
from services.llm_providers import litellm_provider  # noqa: E402
from services.llm_providers.litellm_provider import (  # noqa: E402
    LangfuseConfigError,
    LiteLLMProvider,
    configure_langfuse_callback,
)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Wipe global registration flag + env vars between tests so each
    test exercises a fresh start. Patches the ``litellm`` stub's
    callback lists so no test sees state leaked from another.

    Crucially, scopes the ``sys.modules["litellm"]`` stub to this test
    file via ``monkeypatch.setitem`` so the MagicMock is removed on
    teardown. Without this, downstream tests that import the real
    LiteLLM (e.g. ``test_cost_lookup.py``) get the lingering MagicMock
    and compute nonsense costs.
    """
    monkeypatch.setitem(sys.modules, "litellm", _litellm_stub)
    monkeypatch.setattr(
        litellm_provider, "_LANGFUSE_CALLBACK_REGISTERED", False,
    )
    for var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    _litellm_stub.success_callback = []
    _litellm_stub.failure_callback = []
    yield


def _fake_site_config(
    *,
    enabled: bool = True,
    host: str = "http://localhost:3010",
    public_key: str = "pk-lf-test",
    secret_key: str = "sk-lf-test",
):
    """Build a mock SiteConfig with the four settings the function reads.

    ``get_secret`` is async, so it's an AsyncMock.
    """
    sc = MagicMock()
    sc.get_bool.return_value = enabled
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": host,
        "langfuse_public_key": public_key,
    }.get(key, default)
    sc.get_secret = AsyncMock(return_value=secret_key)
    return sc


@pytest.mark.asyncio
async def test_disabled_skips_registration_cleanly():
    """When ``langfuse_tracing_enabled=false``, the function returns
    False without touching ``litellm.success_callback`` or stamping
    env vars. This is the operator's kill switch.
    """
    sc = _fake_site_config(enabled=False)
    result = await configure_langfuse_callback(sc)
    assert result is False
    assert _litellm_stub.success_callback == []
    assert _litellm_stub.failure_callback == []
    assert "LANGFUSE_HOST" not in os.environ
    # Secret should not even be fetched when disabled.
    sc.get_secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_with_all_credentials_registers_callback():
    """Happy path — all three credentials present + tracing enabled.

    Verifies env vars get stamped (LiteLLM's Langfuse integration reads
    them on first callback fire) AND the success/failure callback lists
    get set to ``["langfuse"]``.
    """
    sc = _fake_site_config()
    result = await configure_langfuse_callback(sc)
    assert result is True
    assert _litellm_stub.success_callback == ["langfuse_otel"]
    assert _litellm_stub.failure_callback == ["langfuse_otel"]
    assert os.environ["LANGFUSE_HOST"] == "http://localhost:3010"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-test"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-lf-test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field,kwargs",
    [
        ("langfuse_host", {"host": ""}),
        ("langfuse_public_key", {"public_key": ""}),
        ("langfuse_secret_key", {"secret_key": ""}),
    ],
)
async def test_missing_credential_raises_loud_error(missing_field, kwargs):
    """Per ``feedback_no_silent_defaults``: a missing credential with
    tracing enabled MUST raise — never quietly skip registration.
    """
    sc = _fake_site_config(**kwargs)
    with pytest.raises(LangfuseConfigError, match=missing_field):
        await configure_langfuse_callback(sc)
    # Callback lists must remain untouched on failure.
    assert _litellm_stub.success_callback == []
    assert _litellm_stub.failure_callback == []


@pytest.mark.asyncio
async def test_idempotent_double_call():
    """Calling twice doesn't re-register, doesn't raise, returns True
    both times. Refreshes env vars on the second call so credential
    rotation propagates without a worker restart.
    """
    sc = _fake_site_config()
    first = await configure_langfuse_callback(sc)
    second = await configure_langfuse_callback(sc)
    assert first is True
    assert second is True
    assert _litellm_stub.success_callback == ["langfuse_otel"]
    # Should have been fetched twice (env-var refresh path).
    assert sc.get_secret.await_count == 2


@pytest.mark.asyncio
async def test_none_site_config_skips_silently():
    """When called outside the worker (CLI scripts, test harnesses
    that don't construct a SiteConfig), the function logs + returns
    False without raising. Keeps unit-test paths green.
    """
    result = await configure_langfuse_callback(None)
    assert result is False
    assert _litellm_stub.success_callback == []


@pytest.mark.asyncio
async def test_secret_fetch_exception_wrapped():
    """If ``site_config.get_secret`` itself raises (e.g. DB down), the
    error is wrapped in :class:`LangfuseConfigError` with the original
    chained — operator gets one clean error type to catch.
    """
    sc = _fake_site_config()
    sc.get_secret = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(LangfuseConfigError, match="langfuse_secret_key"):
        await configure_langfuse_callback(sc)


# ---------------------------------------------------------------------------
# LiteLLMProvider — model namespacing + completion/embedding normalization.
#
# The `configure_langfuse_callback` tests above cover the standalone observability
# wiring. The class itself owns: model-prefix resolution, per-call provider config,
# response normalization to the OpenAI shape, and embedding extraction across
# the three response variants LiteLLM hands back (pydantic model, plain dict,
# bare list). Each section below pins one of those contracts.
# ---------------------------------------------------------------------------


def _fake_choice(content: str, finish_reason: str = "stop"):
    """Build a SimpleNamespace mimicking a LiteLLM ``choices[i]`` entry.

    LiteLLM normalizes every backend to the OpenAI shape, so the provider
    code reads ``choice.message.content`` + ``choice.finish_reason``. We
    use SimpleNamespace rather than MagicMock so `hasattr` checks behave
    naturally (MagicMock auto-creates attributes which would defeat the
    "no choices" + "no usage" branches under test).
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        message=SimpleNamespace(content=content),
        finish_reason=finish_reason,
    )


def _fake_usage(prompt: int, completion: int, total: int):
    from types import SimpleNamespace
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def test_resolve_model_appends_default_prefix_to_bare_name():
    """Bare model names (no slash, no http) get the configured default
    provider prefix prepended. This is the migration ramp for callers
    that still pass legacy names like ``gemma3:27b`` without churn.
    """
    provider = LiteLLMProvider()
    assert provider._resolve_model("gemma3:27b") == "ollama/gemma3:27b"


def test_resolve_model_preserves_namespaced_model_string():
    """A model string with a provider namespace (``openai/gpt-4o-mini``)
    must be passed to LiteLLM verbatim — otherwise we'd double-prefix
    and ship ``ollama/openai/gpt-4o-mini`` which LiteLLM can't route.
    """
    provider = LiteLLMProvider()
    assert provider._resolve_model("openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert (
        provider._resolve_model("openrouter/anthropic/claude-haiku-4-5")
        == "openrouter/anthropic/claude-haiku-4-5"
    )


def test_resolve_model_preserves_http_endpoint_url():
    """Custom OpenAI-compatible endpoints (full http URLs) bypass the
    prefix rule. ``http://host:port/v1`` has a ``/`` so the namespace
    branch could miscompare — verify the http guard wins.
    """
    provider = LiteLLMProvider()
    url = "http://localhost:11434/v1"
    assert provider._resolve_model(url) == url


def test_configure_from_overrides_default_prefix_and_timeouts():
    """``_configure_from`` is the seam by which the dispatcher hands
    provider config to a freshly-created LiteLLMProvider. It mutates
    instance state on every call; the first call also stamps the
    process-wide LiteLLM knobs (``set_verbose`` / ``drop_params`` /
    ``api_base``) by flipping ``_configured`` to True.
    """
    provider = LiteLLMProvider()
    provider._configure_from({
        "default_prefix": "openrouter/",
        "api_base": "http://custom:9999",
        "timeout_seconds": 30,
        "drop_params": False,
    })
    assert provider._default_prefix == "openrouter/"
    assert provider._api_base == "http://custom:9999"
    assert provider._timeout == 30.0
    assert provider._drop_params is False
    # Bare name now picks up the new prefix.
    assert provider._resolve_model("foo:7b") == "openrouter/foo:7b"
    # The global config stamp ran exactly once.
    assert provider._configured is True
    assert _litellm_stub.drop_params is False
    assert _litellm_stub.api_base == "http://custom:9999"


@pytest.mark.asyncio
async def test_complete_normalizes_response_to_completion_dataclass(monkeypatch):
    """Happy path: LiteLLM returns its OpenAI-shaped response object,
    ``LiteLLMProvider.complete`` extracts text / finish_reason / token
    counts and returns the plugin-layer ``Completion`` dataclass.

    Verifies the API base + resolved model get passed through to
    ``litellm.acompletion`` so the downstream backend receives the
    right routing.
    """
    from types import SimpleNamespace

    response = SimpleNamespace(
        choices=[_fake_choice("hello world", finish_reason="length")],
        usage=_fake_usage(prompt=12, completion=3, total=15),
        _response_ms=42.0,
        response_cost=0.0007,
    )
    fake_acompletion = AsyncMock(return_value=response)
    monkeypatch.setattr(_litellm_stub, "acompletion", fake_acompletion)

    provider = LiteLLMProvider()
    result = await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="gemma3:27b",
        temperature=0.5,
        max_tokens=64,
        timeout_s=15,
        _provider_config={"api_base": "http://localhost:11434"},
    )

    assert result.text == "hello world"
    assert result.finish_reason == "length"
    assert result.model == "ollama/gemma3:27b"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (12, 3, 15)
    # response_cost surfaces in raw so cost_logs can use it without re-pricing.
    assert result.raw["response_cost"] == 0.0007
    assert result.raw["_response_ms"] == 42.0

    # Verify what we actually handed to LiteLLM — the api_base + resolved
    # model must reach the call site, kwargs not in the safelist get dropped.
    kwargs = fake_acompletion.call_args.kwargs
    assert kwargs["model"] == "ollama/gemma3:27b"
    assert kwargs["api_base"] == "http://localhost:11434"
    assert kwargs["timeout"] == 15.0
    assert kwargs["stream"] is False
    assert kwargs["temperature"] == 0.5
    assert kwargs["max_tokens"] == 64
    # Provider-config and timeout_s are consumed; they must NOT leak to LiteLLM.
    assert "_provider_config" not in kwargs
    assert "timeout_s" not in kwargs


@pytest.mark.asyncio
async def test_complete_handles_empty_choices_and_missing_usage(monkeypatch):
    """LiteLLM CAN return a response with no choices (e.g. some Ollama
    error paths surface as an empty list) and no usage block. The
    provider must not crash; it returns an empty-text Completion with
    zero token counts so downstream code can treat it as a failed
    generation rather than an exception.
    """
    from types import SimpleNamespace

    response = SimpleNamespace(choices=[], usage=None)
    monkeypatch.setattr(
        _litellm_stub, "acompletion", AsyncMock(return_value=response),
    )

    provider = LiteLLMProvider()
    result = await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/gemma3:27b",
    )

    assert result.text == ""
    assert result.finish_reason == ""
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.total_tokens == 0
    assert result.model == "ollama/gemma3:27b"


@pytest.mark.asyncio
async def test_complete_propagates_acompletion_exception(monkeypatch):
    """LiteLLM exceptions (rate limits, backend timeouts, auth errors)
    must bubble up unchanged so the dispatcher's retry/fallback layer
    sees the original error type. The provider only logs and re-raises.
    """
    monkeypatch.setattr(
        _litellm_stub,
        "acompletion",
        AsyncMock(side_effect=RuntimeError("backend exploded")),
    )

    provider = LiteLLMProvider()
    with pytest.raises(RuntimeError, match="backend exploded"):
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
        )


@pytest.mark.asyncio
async def test_complete_omits_optional_kwargs_when_not_supplied(monkeypatch):
    """The safelist (temperature/max_tokens/top_p) is opt-in: callers
    that don't pass these must NOT see them forwarded as ``None`` or
    default-stamped values to LiteLLM. Some Ollama backends reject
    unknown-key payloads even with drop_params=true depending on the
    chat-template path.
    """
    from types import SimpleNamespace

    response = SimpleNamespace(choices=[_fake_choice("ok")], usage=_fake_usage(1, 1, 2))
    fake_acompletion = AsyncMock(return_value=response)
    monkeypatch.setattr(_litellm_stub, "acompletion", fake_acompletion)

    provider = LiteLLMProvider()
    await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="ollama/gemma3:27b",
    )
    kwargs = fake_acompletion.call_args.kwargs
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs
    assert "top_p" not in kwargs


@pytest.mark.asyncio
async def test_embed_extracts_vector_from_object_response(monkeypatch):
    """LiteLLM's embedding response variant #1: a pydantic-style object
    with a ``data`` list whose entries expose an ``.embedding`` attr.
    This is what the OpenAI + Ollama backends return.
    """
    from types import SimpleNamespace

    entry = SimpleNamespace(embedding=[0.1, 0.2, 0.3])
    response = SimpleNamespace(data=[entry])
    monkeypatch.setattr(
        _litellm_stub, "aembedding", AsyncMock(return_value=response),
    )

    provider = LiteLLMProvider()
    vec = await provider.embed("hello", model="nomic-embed-text")
    assert vec == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_returns_empty_list_when_response_data_empty(monkeypatch):
    """If LiteLLM returns an empty ``data`` list (a real failure mode
    when the local Ollama embedding daemon is reachable but the model
    isn't pulled), ``embed`` must return ``[]`` rather than crash on
    an IndexError. Callers gate on the empty list themselves.
    """
    from types import SimpleNamespace

    response = SimpleNamespace(data=[])
    monkeypatch.setattr(
        _litellm_stub, "aembedding", AsyncMock(return_value=response),
    )

    provider = LiteLLMProvider()
    vec = await provider.embed("hello", model="ollama/nomic-embed-text")
    assert vec == []
