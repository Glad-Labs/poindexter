"""Unit tests for services.llm_providers.dispatcher.

Covers ``get_provider_name``, ``get_provider``, ``get_provider_config``,
``dispatch_complete`` and ``dispatch_embed``. Lifts the module from
0% to ~95% coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers import dispatcher


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal asyncpg-pool stand-in.

    ``setting_value`` is what ``fetchval`` returns. ``raise_on_acquire``
    triggers the warning path inside ``get_provider_name``.
    """

    def __init__(self, setting_value: str | None = None, raise_on_acquire: bool = False):
        self._setting_value = setting_value
        self._raise = raise_on_acquire

    def acquire(self):
        return _FakeAcquireCM(self)

    async def fetchval(self, _query: str, _key: str):
        # Used by PluginConfig.load (called via get_provider_config).
        return None


class _FakeAcquireCM:
    def __init__(self, pool: _FakePool):
        self._pool = pool

    async def __aenter__(self):
        if self._pool._raise:
            raise RuntimeError("connection pool exploded")
        return _FakeConn(self._pool._setting_value)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, setting_value: str | None):
        self._setting_value = setting_value

    async def fetchval(self, _query: str, _key: str):
        return self._setting_value


class _FakeProvider:
    """Stand-in for an LLMProvider instance."""

    def __init__(self, name: str = "ollama_native"):
        self.name = name
        self.complete = AsyncMock()
        self.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])


class _FakeCompletionResult:
    def __init__(self, prompt_tokens=0, completion_tokens=0, finish_reason=""):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.finish_reason = finish_reason


# ---------------------------------------------------------------------------
# get_provider_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderName:
    async def test_returns_configured_value(self):
        pool = _FakePool(setting_value="openai_compat")
        name = await dispatcher.get_provider_name(pool, "standard")
        assert name == "openai_compat"

    async def test_strips_whitespace_around_value(self):
        pool = _FakePool(setting_value="   anthropic   ")
        name = await dispatcher.get_provider_name(pool, "premium")
        assert name == "anthropic"

    async def test_falls_back_to_default_when_value_missing(self):
        pool = _FakePool(setting_value=None)
        name = await dispatcher.get_provider_name(pool, "standard")
        assert name == "ollama_native"

    async def test_falls_back_to_default_when_value_empty_string(self):
        pool = _FakePool(setting_value="")
        name = await dispatcher.get_provider_name(pool, "free")
        assert name == "ollama_native"

    async def test_unknown_tier_falls_back_to_ollama_native(self):
        pool = _FakePool(setting_value=None)
        name = await dispatcher.get_provider_name(pool, "made_up_tier")
        assert name == "ollama_native"

    async def test_swallows_db_error_and_returns_default(self):
        pool = _FakePool(raise_on_acquire=True)
        # Should NOT raise — function logs a warning + uses default.
        name = await dispatcher.get_provider_name(pool, "standard")
        assert name == "ollama_native"


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProvider:
    async def test_returns_named_provider_when_registered(self):
        pool = _FakePool(setting_value="openai_compat")
        provider = _FakeProvider(name="openai_compat")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            result = await dispatcher.get_provider(pool, tier="standard")
        assert result is provider

    async def test_falls_back_to_ollama_native_when_configured_missing(self):
        pool = _FakePool(setting_value="weird_provider_that_isnt_installed")
        ollama = _FakeProvider(name="ollama_native")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[ollama]):
            result = await dispatcher.get_provider(pool, tier="standard")
        assert result is ollama

    async def test_raises_when_neither_configured_nor_fallback_registered(self):
        pool = _FakePool(setting_value="missing_one")
        # Registry has neither the configured name nor ollama_native.
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[]):
            with pytest.raises(RuntimeError, match="No LLMProvider"):
                await dispatcher.get_provider(pool, tier="standard")

    async def test_default_tier_is_standard(self):
        pool = _FakePool(setting_value="ollama_native")
        ollama = _FakeProvider(name="ollama_native")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[ollama]):
            result = await dispatcher.get_provider(pool)  # no tier arg
        assert result.name == "ollama_native"


# ---------------------------------------------------------------------------
# get_provider_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderConfig:
    async def test_returns_config_dict(self):
        pool = _FakePool()
        # PluginConfig.load returns a PluginConfig with config={} when
        # fetchval returns None — the dispatcher just unwraps `.config`.
        cfg = await dispatcher.get_provider_config(pool, "ollama_native")
        assert cfg == {}


# ---------------------------------------------------------------------------
# dispatch_complete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatchComplete:
    async def test_happy_path_passes_through_to_provider(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult(
            prompt_tokens=5, completion_tokens=7, finish_reason="stop",
        )
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            result = await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
                tier="standard",
            )
        assert result.prompt_tokens == 5
        provider.complete.assert_awaited_once()
        # `_provider_config` should have been seeded into kwargs.
        kwargs = provider.complete.await_args.kwargs
        assert "_provider_config" in kwargs
        assert kwargs["model"] == "gemma3:27b"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    async def test_caller_supplied_provider_config_is_not_overwritten(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult()
        custom_cfg = {"base_url": "http://my-vllm.local"}
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
                _provider_config=custom_cfg,
            )
        # setdefault must not clobber a caller-provided value.
        assert provider.complete.await_args.kwargs["_provider_config"] is custom_cfg

    async def test_handles_completion_with_missing_token_attrs(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        # Plain object with no token attrs — getattr defaults kick in.
        provider.complete.return_value = object()
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            result = await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
            )
        assert result is not None  # didn't crash on the missing attrs

    async def test_propagates_provider_exception(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.complete.side_effect = RuntimeError("provider down")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            with pytest.raises(RuntimeError, match="provider down"):
                await dispatcher.dispatch_complete(
                    pool,
                    messages=[{"role": "user", "content": "hi"}],
                    model="gemma3:27b",
                )


# ---------------------------------------------------------------------------
# dispatch_embed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatchEmbed:
    async def test_happy_path_returns_vector(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.embed.return_value = [0.4, 0.5, 0.6]
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            vec = await dispatcher.dispatch_embed(
                pool, text="hello", model="nomic-embed-text",
            )
        assert vec == [0.4, 0.5, 0.6]
        provider.embed.assert_awaited_once_with(
            text="hello", model="nomic-embed-text",
        )

    async def test_default_tier_is_free(self):
        pool = MagicMock()
        # acquire returns a context manager whose __aenter__ produces a
        # connection that records the key it was asked for.
        captured = {}

        class _Conn:
            async def fetchval(self_inner, _q, key):
                captured["key"] = key
                return "ollama_native"

        class _CM:
            async def __aenter__(self_inner):
                return _Conn()

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        pool.acquire = lambda: _CM()
        # PluginConfig.load also calls fetchval directly on the pool.
        pool.fetchval = AsyncMock(return_value=None)

        provider = _FakeProvider(name="ollama_native")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            await dispatcher.dispatch_embed(pool, text="hi", model="m")
        assert captured["key"] == "plugin.llm_provider.primary.free"

    async def test_propagates_provider_exception(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.embed.side_effect = RuntimeError("embed down")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            with pytest.raises(RuntimeError, match="embed down"):
                await dispatcher.dispatch_embed(pool, text="hello", model="m")
