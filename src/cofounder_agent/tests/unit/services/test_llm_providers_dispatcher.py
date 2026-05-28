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
        return _FakeConn(
            self._pool._setting_value,
            executions=getattr(self._pool, "_executions", None),
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, setting_value: str | None, executions: list | None = None):
        self._setting_value = setting_value
        self._executions = executions

    async def fetchval(self, _query: str, _key: str):
        return self._setting_value

    async def execute(self, query: str, *args):
        if self._executions is not None:
            self._executions.append((query, args))


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
        # PR #615 made dispatch_embed symmetric with dispatch_complete —
        # both inject ``_provider_config`` so the paid-endpoint policy
        # (added in cycle-5 #251 for LiteLLM, cycle-4 #615 for openai_compat)
        # gates the embed path too. The empty dict is what _FakePool returns
        # when no plugin.llm_provider.<name>.config rows are seeded.
        provider.embed.assert_awaited_once_with(
            text="hello", model="nomic-embed-text", _provider_config={},
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


# ---------------------------------------------------------------------------
# dispatch_complete auto-logging to cost_logs
# ---------------------------------------------------------------------------


def _make_logging_pool(setting_value: str = "ollama_native") -> tuple[_FakePool, list]:
    """Pool that records every conn.execute(...) call in `executions`."""
    executions: list = []
    pool = _FakePool(setting_value=setting_value)
    pool._executions = executions  # noqa: SLF001 — test fixture
    return pool, executions


@pytest.mark.unit
class TestDispatchCompleteAutoLog:
    """The dispatcher auto-writes a cost_logs row for every call.

    Closes the cost-capture gap where 11 of 13 dispatch_complete callers
    (atoms, topic_ranking, media-script generators, etc.) never logged.
    Only cross_model_qa + generate_content explicitly logged before;
    everything else was invisible to cost dashboards.
    """

    async def test_happy_path_writes_cost_log_row(self):
        pool, executions = _make_logging_pool()
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult(
            prompt_tokens=12, completion_tokens=34,
        )
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
                task_id="task-abc",
                phase="atom.narrate_bundle",
            )
        # One INSERT INTO cost_logs row.
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1, executions
        query, args = cost_inserts[0]
        # Args: task_id, phase, model, provider, in, out, total, cost_usd,
        # cost_type, duration_ms, success, error
        assert args[0] == "task-abc"
        assert args[1] == "atom.narrate_bundle"
        assert args[2] == "gemma3:27b"
        assert args[3] == "ollama_native"
        assert args[4] == 12   # input_tokens
        assert args[5] == 34   # output_tokens
        assert args[7] == 0.0  # cost_usd (no response_cost on raw)
        assert args[8] == "inference"
        assert args[10] is True   # success
        assert args[11] is None   # error

    async def test_response_cost_from_raw_populates_cost_usd(self):
        pool, executions = _make_logging_pool(setting_value="litellm")
        provider = _FakeProvider(name="litellm")
        result = _FakeCompletionResult(prompt_tokens=100, completion_tokens=50)
        result.raw = {"response_cost": 0.000123}  # type: ignore[attr-defined]
        provider.complete.return_value = result
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            await dispatcher.dispatch_complete(
                pool, messages=[{"role": "user", "content": "hi"}],
                model="claude-3-haiku-20240307",
            )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1
        # cost_usd is at index 7.
        assert cost_inserts[0][1][7] == 0.000123
        assert cost_inserts[0][1][3] == "litellm"

    async def test_phase_defaults_to_dispatch_complete_when_not_supplied(self):
        pool, executions = _make_logging_pool()
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult()
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            await dispatcher.dispatch_complete(
                pool, messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
            )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1
        assert cost_inserts[0][1][1] == "dispatch_complete"
        assert cost_inserts[0][1][0] is None  # task_id

    async def test_failure_path_writes_failure_row_then_reraises(self):
        pool, executions = _make_logging_pool()
        provider = _FakeProvider(name="ollama_native")
        provider.complete.side_effect = RuntimeError("upstream timeout")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            with pytest.raises(RuntimeError, match="upstream timeout"):
                await dispatcher.dispatch_complete(
                    pool, messages=[{"role": "user", "content": "hi"}],
                    model="gemma3:27b", task_id="t1", phase="atom.X",
                )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1
        args = cost_inserts[0][1]
        assert args[10] is False  # success
        assert "upstream timeout" in (args[11] or "")  # error_message
        assert args[7] == 0.0  # cost_usd

    async def test_log_write_failure_does_not_break_call(self):
        # Pool whose execute() raises — auto-log must swallow it.
        pool = _FakePool(setting_value="ollama_native")
        # Acquire a conn whose execute throws.
        class _BrokenConn:
            async def fetchval(self, *_a, **_k):
                return "ollama_native"
            async def execute(self, *_a, **_k):
                raise RuntimeError("disk full")
        class _BrokenCM:
            async def __aenter__(self_inner):
                return _BrokenConn()
            async def __aexit__(self_inner, *_a):
                return False
        pool.acquire = lambda: _BrokenCM()  # type: ignore[method-assign]
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult(prompt_tokens=1)
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]):
            # The call must still return — auto-log failure is observability-only.
            result = await dispatcher.dispatch_complete(
                pool, messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
            )
        assert result.prompt_tokens == 1
