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
    # ``_extract_response_cost`` falls back to ``litellm.completion_cost()``
    # when a response has no ``_hidden_params`` cost. Default that to None so
    # a bare MagicMock doesn't return ``float(MagicMock())`` == 1.0 and stamp
    # a phantom price — tests that exercise the fallback set their own value.
    fake.completion_cost = MagicMock(return_value=None)
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
        mock_litellm.api_base = None
        p._configure_from(
            {"api_base": "http://localhost:11434", "drop_params": False}
        )
        assert p._configured is True
        assert mock_litellm.set_verbose is False
        assert mock_litellm.drop_params is False
        # litellm.api_base must stay untouched: the global beats the
        # per-call kwarg in litellm's ollama branch, which silently
        # defeats model_api_base_overrides (glad-labs-stack#2051).
        assert mock_litellm.api_base is None
        assert p._api_base == "http://localhost:11434"

    def test_configure_from_idempotent_on_repeat(self, mock_litellm):
        p = _provider_instance()
        p._configure_from({"api_base": "http://first/", "drop_params": True})
        # mutate the mock to detect a second apply
        mock_litellm.drop_params = "SHOULD_NOT_BE_OVERWRITTEN"
        p._configure_from({"api_base": "http://second/", "drop_params": False})
        # _apply_global_litellm_config only runs the first time —
        # second call updates instance state but doesn't touch the global
        assert mock_litellm.drop_params == "SHOULD_NOT_BE_OVERWRITTEN"
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
    # LiteLLM exposes the computed cost on ``_hidden_params``, NOT as a
    # top-level ``response.response_cost`` attribute (the naive hasattr read
    # logged $0 for every paid call — glad-labs-stack #2183). Model that: a
    # priced call carries the value in _hidden_params; an unpriced one has an
    # empty dict so ``.get("response_cost")`` misses and the extractor falls
    # back to ``litellm.completion_cost`` (stubbed to None by the fixture).
    if response_cost is not None:
        resp._hidden_params = {"response_cost": response_cost}
    else:
        resp._hidden_params = {}
    return resp


class TestLiteLLMProviderComplete:
    @pytest.mark.asyncio
    async def test_complete_returns_litellm_response_shape(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        # ``anthropic/`` is a paid prefix; the cycle-5 paid-base-url gate
        # refuses it unless the test explicitly opts in. The test is
        # exercising response-shape mapping (not the paid-vendor
        # integration policy), so granting opt-in keeps it focused on
        # what it's actually testing.
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-haiku-4-5",
            _provider_config={"allow_paid_base_url": "true"},
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
        # response_cost is only attached by LiteLLM on cloud calls, so this
        # test specifically needs a paid-vendor model. Opt in to the gate
        # — the test is checking the cost-surfacing seam, not the policy.
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-haiku-4-5",
            _provider_config={"allow_paid_base_url": "true"},
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
                _provider_config={"allow_paid_base_url": "true"},
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
        resp._hidden_params = {}
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
        # ``my-vllm`` + ``other-host`` aren't in is_local_base_url's
        # localhost/127.0.0.1/host.docker.internal allowlist (custom
        # hostnames are a real prod shape — k8s ClusterIP DNS, tailnet,
        # etc.), so the cycle-5 paid-base-url gate refuses them by
        # default. This test is about api_base/http-URL handling, not
        # the policy, so opt in.
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="http://my-vllm:8080/v1",
            _provider_config={
                "api_base": "http://other-host/",
                "allow_paid_base_url": "true",
            },
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


# --------------------------------------------------------------------------- #
# Anthropic prompt caching — inject a ``cache_control: ephemeral`` breakpoint
# on the system prefix so reused writer prompts cache on the Anthropic path.
# Gated by ``anthropic_prompt_caching`` (folded from the flat
# ``plugin.llm_provider.litellm.anthropic_prompt_caching`` row). LiteLLM only
# forwards the breakpoint for the ``anthropic/`` prefix; local + other-vendor
# targets must be left untouched.
# --------------------------------------------------------------------------- #


class TestLiteLLMProviderAnthropicPromptCaching:
    @pytest.mark.asyncio
    async def test_annotates_system_prefix_on_anthropic_by_default(
        self, mock_litellm,
    ):
        """The whole point of the fix: an ``anthropic/`` call rewrites the
        system turn into a content-block carrying the ephemeral breakpoint,
        with no explicit config (caching defaults on).
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[
                {"role": "system", "content": "You are a writer."},
                {"role": "user", "content": "Write about bees."},
            ],
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        )
        sent = mock_litellm.acompletion.await_args.kwargs["messages"]
        assert sent[0] == {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "You are a writer.",
                    "cache_control": {"type": "ephemeral"},
                },
            ],
        }
        # User turn is untouched — the breakpoint sits at the end of the
        # stable system prefix, not on the volatile per-post content.
        assert sent[1] == {"role": "user", "content": "Write about bees."}

    @pytest.mark.asyncio
    async def test_no_cache_control_on_local_model(self, mock_litellm):
        """Ollama has no cache_control concept — a local call must send the
        system turn as a plain string so litellm's ollama transform isn't
        handed an Anthropic-only shape.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[
                {"role": "system", "content": "You are a writer."},
                {"role": "user", "content": "Write about bees."},
            ],
            model="ollama/gemma-4-31b",
        )
        sent = mock_litellm.acompletion.await_args.kwargs["messages"]
        assert sent[0] == {"role": "system", "content": "You are a writer."}

    @pytest.mark.asyncio
    async def test_gate_off_leaves_anthropic_system_untouched(self, mock_litellm):
        """``anthropic_prompt_caching=false`` is the operator kill switch —
        an anthropic call then sends the plain-string system turn.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[
                {"role": "system", "content": "You are a writer."},
                {"role": "user", "content": "Write about bees."},
            ],
            model="anthropic/claude-sonnet-5",
            _provider_config={
                "allow_paid_base_url": "true",
                "anthropic_prompt_caching": "false",
            },
        )
        sent = mock_litellm.acompletion.await_args.kwargs["messages"]
        assert sent[0] == {"role": "system", "content": "You are a writer."}

    @pytest.mark.asyncio
    async def test_does_not_mutate_caller_messages(self, mock_litellm):
        """The transform must return a copy — the caller (writer atom) may
        log or replay its own list, so an in-place rewrite would leak the
        Anthropic-only block shape back into the pipeline.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        original = [
            {"role": "system", "content": "You are a writer."},
            {"role": "user", "content": "Write about bees."},
        ]
        await p.complete(
            messages=original,
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        )
        assert original[0] == {"role": "system", "content": "You are a writer."}

    @pytest.mark.asyncio
    async def test_no_system_turn_is_a_noop(self, mock_litellm):
        """A user-only conversation has no stable prefix to cache — the
        messages must pass through unchanged rather than crash.
        """
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "Write about bees."}],
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        )
        sent = mock_litellm.acompletion.await_args.kwargs["messages"]
        assert sent == [{"role": "user", "content": "Write about bees."}]

    @pytest.mark.asyncio
    async def test_surfaces_cache_tokens_into_raw(self, mock_litellm):
        """Close the telemetry gap: Anthropic's cache hit/miss token counts
        must land on ``Completion.raw`` so cost_logs / Langfuse can see
        whether the breakpoint is actually landing.
        """
        msg = SimpleNamespace(content="drafted")
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        usage = SimpleNamespace(
            prompt_tokens=8000,
            completion_tokens=1200,
            total_tokens=9200,
            cache_read_input_tokens=7000,
            cache_creation_input_tokens=500,
        )
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = usage
        resp.model_dump = MagicMock(return_value={"id": "fake"})
        resp._hidden_params = {"response_cost": 0.01}
        mock_litellm.acompletion = AsyncMock(return_value=resp)
        p = _provider_instance()
        out = await p.complete(
            messages=[
                {"role": "system", "content": "You are a writer."},
                {"role": "user", "content": "Write about bees."},
            ],
            model="anthropic/claude-sonnet-5",
            _provider_config={"allow_paid_base_url": "true"},
        )
        assert out.raw["cache_read_input_tokens"] == 7000
        assert out.raw["cache_creation_input_tokens"] == 500


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


# --------------------------------------------------------------------------- #
# Tool calling (poindexter#947) — ``tools=`` forwarding + ``tool_calls``
# extraction for the Cofounder chat agent loop. Catches: the params
# allowlist silently dropping ``tools`` (the model would answer in prose
# and the agent would never act), and the reasoning-content fallback
# fabricating an assistant answer alongside tool calls.
# --------------------------------------------------------------------------- #


def _tool_call_response(*, content=None, with_reasoning=None):
    fn = SimpleNamespace(name="list_tasks", arguments='{"limit": 3}')
    tc = SimpleNamespace(id="call_1", function=fn)
    msg = SimpleNamespace(content=content, tool_calls=[tc])
    if with_reasoning is not None:
        msg.reasoning_content = with_reasoning
    choice = SimpleNamespace(message=msg, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    resp.model_dump = MagicMock(return_value={"id": "fake-id"})
    resp._hidden_params = {}
    return resp


class TestLiteLLMProviderToolCalling:
    def test_supports_tools_attribute(self, mock_litellm):
        assert _provider_instance().supports_tools is True

    @pytest.mark.asyncio
    async def test_tools_kwarg_is_forwarded(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        p = _provider_instance()
        await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen2.5:7b", tools=tools, tool_choice="auto",
        )
        kwargs = mock_litellm.acompletion.await_args.kwargs
        assert kwargs["tools"] == tools
        assert kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_tool_calls_extracted_and_normalized(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(return_value=_tool_call_response())
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen2.5:7b",
            tools=[{"type": "function", "function": {"name": "list_tasks"}}],
        )
        assert out.tool_calls == [
            {"id": "call_1", "name": "list_tasks", "arguments": '{"limit": 3}'},
        ]
        assert out.text == ""

    @pytest.mark.asyncio
    async def test_no_tools_means_tool_calls_none(self, mock_litellm):
        mock_litellm.acompletion = AsyncMock(
            return_value=_shaped_completion_response(),
        )
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}], model="qwen2.5:7b",
        )
        assert out.tool_calls is None

    @pytest.mark.asyncio
    async def test_reasoning_recovery_skipped_when_tool_calls_present(
        self, mock_litellm,
    ):
        """Empty content + tool_calls is the NORMAL tool-turn shape — the
        reasoning-content fallback must not fabricate an answer from the
        thinking channel next to the calls."""
        mock_litellm.acompletion = AsyncMock(
            return_value=_tool_call_response(
                content="", with_reasoning="I should call list_tasks…",
            ),
        )
        p = _provider_instance()
        out = await p.complete(
            messages=[{"role": "user", "content": "hi"}], model="qwen2.5:7b",
        )
        assert out.text == ""
        assert out.tool_calls and out.tool_calls[0]["name"] == "list_tasks"
