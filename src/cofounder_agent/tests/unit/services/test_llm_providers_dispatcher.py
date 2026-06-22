"""Unit tests for services.llm_providers.dispatcher.

Covers ``get_provider_name``, ``get_provider``, ``get_provider_config``,
``dispatch_complete`` and ``dispatch_embed``. Lifts the module from
0% to ~95% coverage.
"""

from __future__ import annotations

import contextlib
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
        guard = MagicMock()
        guard.estimate_local_kwh.return_value = 0.001
        guard.kwh_to_usd.return_value = 0.00016
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch("services.cost_guard.CostGuard", return_value=guard):
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
        # cost_type, duration_ms, success, electricity_kwh, error_message
        assert args[0] == "task-abc"
        assert args[1] == "atom.narrate_bundle"
        assert args[2] == "gemma3:27b"
        assert args[3] == "ollama_native"
        assert args[4] == 12    # input_tokens
        assert args[5] == 34    # output_tokens
        assert args[7] == 0.0  # API cost is $0 for local (electricity not billed)
        assert args[8] == "inference"
        assert args[10] is True   # success
        assert args[11] == pytest.approx(0.001)   # electricity_kwh
        assert args[12] is None   # error_message

    async def test_local_phantom_response_cost_is_zeroed_to_electricity(self):
        """A LOCAL call must NOT record LiteLLM's phantom hosted price.

        Regression for the 2026-06-21 incident: triage/alerting moved to
        ``llama3.2:3b``, a bare local Ollama model. LiteLLM's ``model_cost``
        table carries a *hosted* llama3.2 price, so ``response_cost`` came
        back ~$0.0135/call for free local inference — 311 calls logged $4.16
        and tripped DailySpendOverBudget. A local call (``_is_paid_llm_call``
        is False) must discard that phantom price and record $0 API cost,
        staying consistent with the budget gate which already treats the same
        call as free. Electricity is attribution-only (electricity_kwh), never
        billed onto cost_usd (P1 invariant).
        """
        pool, executions = _make_logging_pool(setting_value="litellm")
        provider = _FakeProvider(name="litellm")
        result = _FakeCompletionResult(prompt_tokens=100, completion_tokens=50)
        result.raw = {"response_cost": 4.16}  # type: ignore[attr-defined]  # phantom hosted price
        provider.complete.return_value = result
        guard = MagicMock()
        guard.estimate_local_kwh.return_value = 0.001
        guard.kwh_to_usd.return_value = 0.00016
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch("services.cost_guard.CostGuard", return_value=guard):
            await dispatcher.dispatch_complete(
                pool, messages=[{"role": "user", "content": "hi"}],
                model="llama3.2:3b", phase="ollama_chat_text",
            )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1
        args = cost_inserts[0][1]
        # Phantom $4.16 must NOT be recorded; API cost stays $0 for local
        # (electricity is attribution-only via electricity_kwh, not billed here).
        assert args[7] == 0.0
        assert args[3] == "litellm"
        assert args[11] == pytest.approx(0.001)   # electricity_kwh populated
        assert args[12] is None  # error_message
        guard.estimate_local_kwh.assert_called_once()

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
        guard = MagicMock()
        guard.estimate_local_kwh.return_value = 0.0005
        guard.kwh_to_usd.return_value = 0.00008
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch("services.cost_guard.CostGuard", return_value=guard):
            with pytest.raises(RuntimeError, match="upstream timeout"):
                await dispatcher.dispatch_complete(
                    pool, messages=[{"role": "user", "content": "hi"}],
                    model="gemma3:27b", task_id="t1", phase="atom.X",
                )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1
        args = cost_inserts[0][1]
        assert args[10] is False  # success
        assert args[11] == pytest.approx(0.0005)  # electricity_kwh
        assert "upstream timeout" in (args[12] or "")  # error_message
        assert args[7] == 0.0  # API cost is $0 for local (electricity not billed)

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


# ---------------------------------------------------------------------------
# Spend-cap enforcement on the primary dispatch path (audit H2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsPaidLlmCall:
    """Local vs paid classification — must be CONSERVATIVE toward local so a
    misclassification can never block a free Ollama call."""

    def test_bare_model_is_local(self):
        assert dispatcher._is_paid_llm_call("gemma3:27b", {}) is False

    def test_ollama_prefix_is_local(self):
        assert dispatcher._is_paid_llm_call("ollama/gemma3:27b", {}) is False

    def test_vllm_prefix_is_local(self):
        assert dispatcher._is_paid_llm_call("vllm/some-model", None) is False

    def test_openai_prefix_is_paid(self):
        assert dispatcher._is_paid_llm_call("openai/gpt-4o-mini", {}) is True

    def test_anthropic_prefix_is_paid(self):
        assert dispatcher._is_paid_llm_call("anthropic/claude-haiku-4-5", None) is True

    def test_local_api_base_keeps_call_local(self):
        assert dispatcher._is_paid_llm_call(
            "gemma3:27b", {"api_base": "http://localhost:11434"}
        ) is False

    def test_remote_api_base_is_paid(self):
        assert dispatcher._is_paid_llm_call(
            "gpt-4o", {"api_base": "https://api.openai.com/v1"}
        ) is True

    def test_inline_remote_http_model_is_paid(self):
        assert dispatcher._is_paid_llm_call("https://api.openai.com/v1", {}) is True

    def test_inline_local_http_model_is_local(self):
        assert dispatcher._is_paid_llm_call("http://localhost:8080/v1", {}) is False


@pytest.mark.unit
class TestEnforceBudgetIfPaid:
    async def test_local_call_skips_cost_guard(self):
        """A local model must NOT even construct a CostGuard — zero overhead,
        zero risk to the 99.99% local path."""
        with patch("services.cost_guard.CostGuard") as CG:
            await dispatcher._enforce_budget_if_paid(
                pool=MagicMock(), provider=_FakeProvider("ollama_native"),
                model="gemma3:27b", provider_config={},
            )
        CG.assert_not_called()

    async def test_paid_call_enforces_budget(self):
        guard = MagicMock()
        guard.check_budget = AsyncMock()
        with patch("services.cost_guard.CostGuard", return_value=guard):
            await dispatcher._enforce_budget_if_paid(
                pool=MagicMock(), provider=_FakeProvider("litellm"),
                model="openai/gpt-4o-mini", provider_config={},
            )
        guard.check_budget.assert_awaited_once()

    async def test_paid_call_over_budget_raises(self):
        from services.cost_guard import CostGuardExhausted

        guard = MagicMock()
        guard.check_budget = AsyncMock(
            side_effect=CostGuardExhausted("over budget", scope="daily")
        )
        with patch("services.cost_guard.CostGuard", return_value=guard):
            with pytest.raises(CostGuardExhausted):
                await dispatcher._enforce_budget_if_paid(
                    pool=MagicMock(), provider=_FakeProvider("litellm"),
                    model="openai/gpt-4o-mini", provider_config={},
                )


@pytest.mark.unit
class TestDispatchCompleteBudgetGate:
    async def test_paid_call_over_budget_blocks_before_provider(self):
        """End-to-end: dispatch_complete enforces the spend cap BEFORE calling
        the provider, so an over-budget paid call never fires (audit H2)."""
        from services.cost_guard import CostGuardExhausted

        pool = _FakePool(setting_value="litellm")
        provider = _FakeProvider(name="litellm")
        guard = MagicMock()
        guard.check_budget = AsyncMock(
            side_effect=CostGuardExhausted("over budget", scope="daily")
        )
        with patch.object(
            dispatcher, "get_all_llm_providers", return_value=[provider]
        ), patch(
            "services.cost_guard.CostGuard", return_value=guard
        ), patch.object(
            dispatcher, "get_provider_config", AsyncMock(return_value={})
        ):
            with pytest.raises(CostGuardExhausted):
                await dispatcher.dispatch_complete(
                    pool, messages=[{"role": "user", "content": "hi"}],
                    model="openai/gpt-4o-mini", tier="premium",
                )
        provider.complete.assert_not_awaited()

    async def test_local_call_not_gated(self):
        """A local call dispatches normally — the budget gate is a no-op."""
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.complete = AsyncMock(return_value=_FakeCompletionResult(prompt_tokens=2))
        guard = MagicMock()
        guard.estimate_local_kwh.return_value = 0.001
        guard.kwh_to_usd.return_value = 0.00016
        with patch.object(
            dispatcher, "get_all_llm_providers", return_value=[provider]
        ), patch.object(
            dispatcher, "get_provider_config", AsyncMock(return_value={})
        ), patch("services.cost_guard.CostGuard", return_value=guard):
            result = await dispatcher.dispatch_complete(
                pool, messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
            )
        # Budget gate (check_budget) must NOT have been called for a local model.
        guard.check_budget.assert_not_called()
        provider.complete.assert_awaited_once()
        assert result.prompt_tokens == 2


# ---------------------------------------------------------------------------
# Local-model electricity attribution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLocalElectricityAttribution:
    """Local Ollama calls get electricity_kwh populated and cost_usd derived
    from GPU power × duration — not left at $0.0 / NULL."""

    async def test_local_model_writes_zero_api_cost_keeps_electricity_kwh(self):
        """Phantom canary: a LOCAL call records cost_usd=0 on the API axis but
        keeps electricity_kwh for attribution. Guards against a bare local tag
        (e.g. 'glm-4.7-5090:latest') re-acquiring a hosted price — the
        2026-06-21 phantom bug — and against billing per-call electricity onto
        the API axis (cost-control attribution spec, P1 invariant)."""
        pool, executions = _make_logging_pool()
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult(
            prompt_tokens=50, completion_tokens=100,
        )
        guard = MagicMock()
        guard.estimate_local_kwh.return_value = 0.002
        guard.kwh_to_usd.return_value = 0.00032
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch("services.cost_guard.CostGuard", return_value=guard):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="glm-4.7-5090:latest",
                task_id="task-123",
                phase="generate_content",
            )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        assert len(cost_inserts) == 1
        args = cost_inserts[0][1]
        assert args[7] == 0.0                      # API cost is $0 for local
        assert args[11] == pytest.approx(0.002)    # electricity_kwh kept (attribution)
        assert args[12] is None                    # error_message
        guard.estimate_local_kwh.assert_called_once()
        guard.kwh_to_usd.assert_not_called()       # electricity NOT billed to cost_usd

    async def test_cloud_model_with_nonzero_response_cost_skips_electricity_path(self):
        """If LiteLLM returns a real price, electricity estimation must not run."""
        pool, executions = _make_logging_pool(setting_value="litellm")
        provider = _FakeProvider(name="litellm")
        result = _FakeCompletionResult(prompt_tokens=200, completion_tokens=80)
        result.raw = {"response_cost": 0.0045}  # type: ignore[attr-defined]
        provider.complete.return_value = result
        guard = MagicMock()
        # _enforce_budget_if_paid also uses CostGuard.check_budget — must be async.
        guard.check_budget = AsyncMock()
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch("services.cost_guard.CostGuard", return_value=guard):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="anthropic/claude-haiku-4-5",
            )
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        args = cost_inserts[0][1]
        assert args[7] == pytest.approx(0.0045)  # cloud price preserved
        assert args[11] is None                   # electricity_kwh not touched
        guard.estimate_local_kwh.assert_not_called()

    async def test_electricity_estimation_failure_falls_back_to_zero(self):
        """If CostGuard raises, cost_usd stays 0.0 — best-effort only."""
        pool, executions = _make_logging_pool()
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult()
        guard = MagicMock()
        guard.estimate_local_kwh.side_effect = RuntimeError("gpu metrics unavailable")
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch("services.cost_guard.CostGuard", return_value=guard):
            # Must not raise.
            result = await dispatcher.dispatch_complete(
                pool, messages=[{"role": "user", "content": "hi"}], model="gemma3:27b",
            )
        assert result is not None
        cost_inserts = [(q, a) for (q, a) in executions if "cost_logs" in q]
        args = cost_inserts[0][1]
        assert args[7] == 0.0   # fallback
        assert args[11] is None  # electricity_kwh not set


# ---------------------------------------------------------------------------
# GPU serialization at the dispatch chokepoint
# ---------------------------------------------------------------------------


class _GpuLockTracker:
    """Records gpu.lock(owner, **kwargs) entries; yields a no-op context."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    @contextlib.asynccontextmanager
    async def lock(self, owner, **kwargs):
        self.calls.append((owner, kwargs))
        yield


@pytest.mark.unit
class TestGpuSerializeLocalDispatch:
    """`_gpu_serialize_local_dispatch` decides whether a dispatch holds the GPU
    lock: local calls do (default on), paid/cloud calls never, flag can disable."""

    def test_paid_call_never_serializes(self):
        assert dispatcher._gpu_serialize_local_dispatch("openai/gpt-4o", {}) is False

    def test_local_call_serializes_by_default(self):
        with patch("services.container_registry.get_container", return_value=None):
            assert dispatcher._gpu_serialize_local_dispatch("gemma3:27b", {}) is True

    def test_flag_off_disables_for_local(self):
        container = MagicMock()
        container.site_config.get_bool.return_value = False
        with patch("services.container_registry.get_container", return_value=container):
            assert dispatcher._gpu_serialize_local_dispatch("gemma3:27b", {}) is False
        container.site_config.get_bool.assert_called_once_with(
            "gpu_serialize_llm_dispatch", True,
        )


@pytest.mark.unit
class TestDispatchCompleteGpuSerialization:
    """dispatch_complete wraps a LOCAL provider.complete in gpu.lock('ollama')
    so a concurrent media render can't be undercut; cloud calls skip the lock."""

    async def test_local_call_acquires_ollama_lock(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult(prompt_tokens=1)
        tracker = _GpuLockTracker()
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch.object(dispatcher, "_record_dispatch_cost", AsyncMock()), \
             patch.object(dispatcher, "gpu", tracker):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
                task_id="t1",
                phase="generate_content",
            )
        assert len(tracker.calls) == 1
        owner, kwargs = tracker.calls[0]
        assert owner == "ollama"
        assert kwargs["model"] == "gemma3:27b"
        assert kwargs["task_id"] == "t1"
        assert kwargs["phase"] == "generate_content"
        provider.complete.assert_awaited_once()

    async def test_cloud_call_does_not_acquire_lock(self):
        pool = _FakePool(setting_value="litellm")
        provider = _FakeProvider(name="litellm")
        provider.complete.return_value = _FakeCompletionResult()
        tracker = _GpuLockTracker()
        guard = MagicMock()
        guard.check_budget = AsyncMock()
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch.object(dispatcher, "get_provider_config", AsyncMock(return_value={})), \
             patch.object(dispatcher, "_record_dispatch_cost", AsyncMock()), \
             patch("services.cost_guard.CostGuard", return_value=guard), \
             patch.object(dispatcher, "gpu", tracker):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="anthropic/claude-haiku-4-5",
                tier="premium",
            )
        assert tracker.calls == []
        provider.complete.assert_awaited_once()

    async def test_flag_off_skips_lock_for_local(self):
        pool = _FakePool(setting_value="ollama_native")
        provider = _FakeProvider(name="ollama_native")
        provider.complete.return_value = _FakeCompletionResult()
        tracker = _GpuLockTracker()
        with patch.object(dispatcher, "get_all_llm_providers", return_value=[provider]), \
             patch.object(dispatcher, "_record_dispatch_cost", AsyncMock()), \
             patch.object(dispatcher, "_gpu_serialize_local_dispatch", return_value=False), \
             patch.object(dispatcher, "gpu", tracker):
            await dispatcher.dispatch_complete(
                pool,
                messages=[{"role": "user", "content": "hi"}],
                model="gemma3:27b",
            )
        assert tracker.calls == []
        provider.complete.assert_awaited_once()
