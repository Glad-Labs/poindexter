"""Unit tests for ``LiteLLMProvider`` — production cutover provider (#372).

Covers the LLM-call surface: ``complete()``, ``stream()``, ``embed()``,
``_resolve_model()``, ``_configure_from()``. The Langfuse-callback
helper has its own test file (``test_litellm_langfuse_callback.py``);
this file focuses on the provider class itself.

Heavy reliance on a mocked ``litellm`` so these tests don't need a live
backend. Mirrors the style of ``test_llm_providers.py`` (mocked
``httpx.AsyncClient``) — same fake-the-network-layer pattern.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins import LLMProvider
from plugins.llm_provider import Completion, Token


# --------------------------------------------------------------------------- #
# Fixture: install a mock ``litellm`` in sys.modules for the duration of the
# test. Scoped via monkeypatch so it auto-restores on teardown — no leakage
# into downstream test files (the bug that prompted this audit).
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_litellm(monkeypatch):
    """Install a fresh mock ``litellm`` module for one test."""
    fake = MagicMock(name="litellm")
    fake.success_callback = []
    fake.failure_callback = []
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


def _provider_instance():
    """Construct a fresh LiteLLMProvider — re-imports so the test sees
    the mocked litellm, not the real one cached at module-load time.
    """
    from services.llm_providers.litellm_provider import LiteLLMProvider
    return LiteLLMProvider()


# --------------------------------------------------------------------------- #
# Protocol conformance — uniform with the OllamaNative + OpenAICompat tests
# --------------------------------------------------------------------------- #


class TestLiteLLMProviderProtocol:
    def test_conforms_to_llm_provider(self, mock_litellm):
        assert isinstance(_provider_instance(), LLMProvider)

    def test_has_required_attributes(self, mock_litellm):
        p = _provider_instance()
        assert p.name == "litellm"
        assert p.supports_streaming is True
        assert p.supports_embeddings is True


# --------------------------------------------------------------------------- #
# Model resolution — bare names get the default prefix; namespaced + URL
# names pass through. Catches: regression where ``ollama/`` gets prepended
# twice or where an HTTP base URL gets mangled with a default prefix.
# --------------------------------------------------------------------------- #


class TestLiteLLMProviderResolveModel:
    def test_bare_name_gets_default_ollama_prefix(self, mock_litellm):
        p = _provider_instance()
        assert p._resolve_model("gemma3:27b") == "ollama/gemma3:27b"

    def test_namespaced_name_passes_through_unchanged(self, mock_litellm):
        p = _provider_instance()
        assert (
            p._resolve_model("anthropic/claude-haiku-4-5")
            == "anthropic/claude-haiku-4-5"
        )

    def test_http_url_passes_through_unchanged(self, mock_litellm):
        p = _provider_instance()
        url = "http://vllm-host:8080/v1"
        assert p._resolve_model(url) == url

    def test_custom_default_prefix_from_provider_config(self, mock_litellm):
        p = _provider_instance()
        p._configure_from({"default_prefix": "openrouter/"})
        # bare model now gets the configured prefix, not "ollama/"
        assert p._resolve_model("foo-9000") == "openrouter/foo-9000"


# --------------------------------------------------------------------------- #
# _configure_from + _apply_global_litellm_config — verify provider config
# threads through to the global litellm knobs exactly once. Catches:
# regression where a per-call config override mutates litellm.api_base on
# every request and races other concurrent callers.
# --------------------------------------------------------------------------- #


class TestLiteLLMProviderConfigure:
    def test_configure_from_applies_global_knobs_once(self, mock_litellm):
        p = _provider_instance()
        p._configure_from(
            {"api_base": "http://localhost:11434", "drop_params": False}
        )
        assert p._configured is True
        assert mock_litellm.set_verbose is False
        assert mock_litellm.drop_params is False
        assert mock_litellm.api_base == "http://localhost:11434"

    def test_configure_from_idempotent_on_repeat(self, mock_litellm):
        p = _provider_instance()
        p._configure_from({"api_base": "http://first/"})
        # mutate the mock to detect a second apply
        mock_litellm.api_base = "SHOULD_NOT_BE_OVERWRITTEN"
        p._configure_from({"api_base": "http://second/"})
        # _apply_global_litellm_config only runs the first time —
        # second call updates instance state but doesn't touch the global
        assert mock_litellm.api_base == "SHOULD_NOT_BE_OVERWRITTEN"
        assert p._api_base == "http://second/"

    def test_configure_from_swallows_global_apply_errors(self, mock_litellm):
        """If litellm itself misbehaves (e.g. a stale package), per-instance
        state still updates — provider degrades to defaults rather than
        refusing to load.
        """
        type(mock_litellm).api_base = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        p = _provider_instance()
        # Should NOT raise even though setting api_base raises
        p._configure_from({"api_base": "http://x/"})
        assert p._api_base == "http://x/"


# --------------------------------------------------------------------------- #
# complete() — the main LLM call path. Verifies token attribution +
# response_cost surfacing. Catches: regression where `Completion.raw` drops
# `response_cost` and cost tracking goes silently to zero on cloud calls.
# --------------------------------------------------------------------------- #


def _shaped_completion_response(
    *,
    text: str = "hello",
    finish_reason: str = "stop",
    prompt_tokens: int = 5,
    completion_tokens: int = 3,
    response_cost: float | None = 0.00012,
):
    """Build a LiteLLM-shaped response object (dict-like + attrs)."""
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    resp.model_dump = MagicMock(return_value={"id": "fake-id"})
    if response_cost is not None:
        resp.response_cost = response_cost
    else:
        # explicit deletion: ensure hasattr() returns False
        del resp.response_cost
    return resp


class TestLiteLLMProviderComplete:
    @pytest.mark.asyncio
    async def test_complete_returns_litellm_response_shape(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-haiku-4-5",
        )
        assert isinstance(out, Completion)
        assert out.text == "hello"
        assert out.prompt_tokens == 5
        assert out.completion_tokens == 3
        assert out.total_tokens == 8
        assert out.finish_reason == "stop"
        assert out.model == "anthropic/claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_complete_resolves_bare_model_with_ollama_prefix(self, mock_litellm):
        """A bare model name must pick up ``ollama/`` so the local-cost
        path triggers in cost_lookup. Without this, every bare-model
        call would fall through to the cloud-default-rate fallback.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gemma3:27b",
        )
        kwargs = mock_litellm.acompletion.await_args.kwargs
        assert kwargs["model"] == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_complete_surfaces_response_cost_into_raw_for_cost_logs(
        self, mock_litellm,
    ):
        """Regression guard: ``Completion.raw["response_cost"]`` is what
        ``cost_logs`` reads to avoid re-deriving the price. Drop this
        and cloud-cost tracking silently zeros out.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(response_cost=0.00042),
        )
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-haiku-4-5",
        )
        assert out.raw["response_cost"] == 0.00042

    @pytest.mark.asyncio
    async def test_complete_omits_response_cost_when_litellm_did_not_set_it(
        self, mock_litellm,
    ):
        """Local Ollama calls don't get a response_cost from LiteLLM —
        the field should be absent from raw, not zero. Zero would
        misleadingly imply LiteLLM said the call cost $0.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(response_cost=None),
        )
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
        )
        assert "response_cost" not in out.raw

    @pytest.mark.asyncio
    async def test_complete_reraises_on_acompletion_exception(self, mock_litellm):
        """Per ``feedback_no_silent_defaults``: an LLM-call failure must
        propagate so the dispatcher can record the failure + decide
        whether to retry or surface to the operator. Silent fallback to
        an empty Completion would let bad runs land in cost_logs as
        ``status=ok``.
        """
        mock_litellm.acompletion = AsyncMock(
            side_effect=RuntimeError("upstream 500"),
        )
        p = _provider_instance()
        with pytest.raises(RuntimeError, match="upstream 500"):
            await p.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="anthropic/claude-haiku-4-5",
            )

    @pytest.mark.asyncio
    async def test_complete_threads_temperature_max_tokens_top_p(self, mock_litellm):
        """The whitelist of sampler kwargs is intentional — passing
        unknown kwargs through to ``acompletion`` would let callers
        accidentally enable provider-specific features that other
        backends silently drop. Verify only the documented three are
        forwarded.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
            temperature=0.7,
            max_tokens=512,
            top_p=0.9,
            random_kwarg="should_be_dropped",
        )
        kwargs = mock_litellm.acompletion.await_args.kwargs
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 512
        assert kwargs["top_p"] == 0.9
        assert "random_kwarg" not in kwargs

    @pytest.mark.asyncio
    async def test_complete_per_call_timeout_kwarg_overrides_default(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
            timeout_s=5.0,
        )
        kwargs = mock_litellm.acompletion.await_args.kwargs
        assert kwargs["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_complete_handles_empty_choices_without_crashing(self, mock_litellm):
        """If LiteLLM returns choices=[] (rare; provider hiccup), we
        return an empty Completion rather than crashing the run.
        """
        resp = MagicMock()
        resp.choices = []
        resp.usage = SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
        )
        resp.model_dump = MagicMock(return_value={})
        del resp.response_cost
        mock_litellm.acompletion = AsyncMock(return_value=resp)
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
        )
        assert out.text == ""
        assert out.finish_reason == ""

    @pytest.mark.asyncio
    async def test_complete_passes_api_base_when_resolved_model_not_url(
        self, mock_litellm,
    ):
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
            _provider_config={"api_base": "http://localhost:11434"},
        )
        kwargs = mock_litellm.acompletion.await_args.kwargs
        assert kwargs["api_base"] == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_complete_omits_api_base_when_model_is_full_url(
        self, mock_litellm,
    ):
        """When the caller passes a full URL as the model (rare; OpenAI-
        compat-via-url shape), the api_base is part of the URL — passing
        it again would confuse LiteLLM.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="http://my-vllm:8080/v1",
            _provider_config={"api_base": "http://other-host/"},
        )
        kwargs = mock_litellm.acompletion.await_args.kwargs
        assert "api_base" not in kwargs


# --------------------------------------------------------------------------- #
# stream() — yields Token chunks. Catches: regression where streaming
# breaks token attribution (writer pipeline shows zero tokens because
# ``delta.content`` returns None and we don't fall through).
# --------------------------------------------------------------------------- #


def _stream_chunk(text: str | None, finish_reason: str | None = None):
    delta = SimpleNamespace(content=text)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _AsyncStream:
    """Minimal async iterator over chunks."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


class TestLiteLLMProviderStream:
    @pytest.mark.asyncio
    async def test_stream_yields_token_per_chunk_with_text(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(
            return_value=_AsyncStream([
                _stream_chunk("Hel"),
                _stream_chunk("lo"),
                _stream_chunk("", finish_reason="stop"),
            ]),
        )
        p = _provider_instance()
        tokens: list[Token] = []
        async for tok in p.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
        ):
            tokens.append(tok)
        assert [t.text for t in tokens] == ["Hel", "lo", ""]
        assert tokens[-1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_stream_skips_chunks_with_no_choices(self, mock_litellm):
        """LiteLLM occasionally emits keepalive-style chunks with empty
        ``choices``. The writer can't do anything with them but they
        must not crash the stream loop.
        """
        keepalive = SimpleNamespace(choices=[])
        mock_litellm.acompletion = AsyncMock(
            return_value=_AsyncStream([
                _stream_chunk("a"),
                keepalive,
                _stream_chunk("b", finish_reason="stop"),
            ]),
        )
        p = _provider_instance()
        tokens = [t async for t in p.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
        )]
        assert [t.text for t in tokens] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_stream_treats_none_delta_content_as_empty_string(
        self, mock_litellm,
    ):
        """delta.content can be None on the terminal chunk for some
        backends. Yielding None as text would break the writer's
        string-concat assumption.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_AsyncStream([
                _stream_chunk(None, finish_reason="stop"),
            ]),
        )
        p = _provider_instance()
        tokens = [t async for t in p.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="ollama/gemma3:27b",
        )]
        assert tokens[-1].text == ""
        assert tokens[-1].finish_reason == "stop"


# --------------------------------------------------------------------------- #
# embed() — both attribute-style + dict-style response shapes. LiteLLM
# normalizes most providers but the embedding path returns a Pydantic
# Embedding object on cloud + a plain dict on Ollama.
# --------------------------------------------------------------------------- #


class TestLiteLLMProviderEmbed:
    @pytest.mark.asyncio
    async def test_embed_unpacks_attribute_style_response(self, mock_litellm):
        emb_obj = SimpleNamespace(embedding=[0.1, 0.2, 0.3])
        resp = SimpleNamespace(data=[emb_obj])
        mock_litellm.aembedding = AsyncMock(return_value=resp)
        p = _provider_instance()
        v = await p.embed("the quick brown fox", model="ollama/nomic-embed-text")
        assert v == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_embed_unpacks_dict_style_response(self, mock_litellm):
        resp = SimpleNamespace(data=[{"embedding": [0.4, 0.5, 0.6]}])
        mock_litellm.aembedding = AsyncMock(return_value=resp)
        p = _provider_instance()
        v = await p.embed("hi", model="ollama/nomic-embed-text")
        assert v == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_embed_returns_empty_list_on_empty_data(self, mock_litellm):
        """If a provider returns empty data, callers should get [] not
        a crash. Empty embeddings are filtered upstream by the embedding
        store; here we just don't break.
        """
        resp = SimpleNamespace(data=[])
        mock_litellm.aembedding = AsyncMock(return_value=resp)
        p = _provider_instance()
        v = await p.embed("hi", model="ollama/nomic-embed-text")
        assert v == []

    @pytest.mark.asyncio
    async def test_embed_resolves_bare_model_to_ollama(self, mock_litellm):
        emb_obj = SimpleNamespace(embedding=[0.0])
        resp = SimpleNamespace(data=[emb_obj])
        mock_litellm.aembedding = AsyncMock(return_value=resp)
        p = _provider_instance()
        await p.embed("x", model="nomic-embed-text")
        kwargs = mock_litellm.aembedding.await_args.kwargs
        assert kwargs["model"] == "ollama/nomic-embed-text"
