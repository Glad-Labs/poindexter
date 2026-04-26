"""Unit tests for services.cost_guard.

Reinstated alongside the OpenAICompatProvider plugin
(Glad-Labs/poindexter#132). The earlier cost_guard module was deleted
in commit 5eb26b51 because it had no live callers; the new plugin gives
it a real consumer so the module + tests come back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.cost_guard import (
    CostEstimate,
    CostGuard,
    CostGuardExhausted,
    is_local_base_url,
)

# ---------------------------------------------------------------------------
# is_local_base_url helper
# ---------------------------------------------------------------------------


class TestIsLocalBaseUrl:
    @pytest.mark.parametrize("url", [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080/v1",
        "http://host.docker.internal:9999/v1",
        "http://0.0.0.0:11434",
        "HTTP://LOCALHOST/v1",  # case-insensitive
    ])
    def test_returns_true_for_local(self, url: str) -> None:
        assert is_local_base_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://api.together.xyz/v1",
        "http://my-vllm-cluster.example.com/v1",
    ])
    def test_returns_false_for_cloud(self, url: str) -> None:
        assert is_local_base_url(url) is False

    def test_handles_none(self) -> None:
        assert is_local_base_url(None) is False
        assert is_local_base_url("") is False


# ---------------------------------------------------------------------------
# CostGuard.estimate
# ---------------------------------------------------------------------------


class TestCostGuardEstimate:
    def test_local_backend_is_zero(self) -> None:
        guard = CostGuard()
        est = guard.estimate(
            provider="openai_compat",
            model="gpt-4o",  # would otherwise be expensive
            base_url="http://localhost:11434/v1",
            prompt_tokens=10_000,
            completion_tokens=10_000,
        )
        assert est.is_local is True
        assert est.estimated_usd == 0.0

    def test_cloud_uses_rate_table(self) -> None:
        guard = CostGuard()
        est = guard.estimate(
            provider="openai_compat",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            prompt_tokens=1000,
            completion_tokens=1000,
            rate_table={"gpt-4o": {"input": 0.0025, "output": 0.010}},
        )
        # 1000/1000 * 0.0025 + 1000/1000 * 0.010 = 0.0125
        assert est.is_local is False
        assert est.estimated_usd == pytest.approx(0.0125, rel=1e-6)

    def test_unknown_model_uses_fallback_rate(self) -> None:
        """Conservative fallback rate for unrecognized cloud models."""
        guard = CostGuard()
        est = guard.estimate(
            provider="openai_compat",
            model="some/never-seen-model",
            base_url="https://gateway.example.com/v1",
            prompt_tokens=1000,
            completion_tokens=1000,
        )
        # _FALLBACK_RATE_PER_1K = {"input": 0.0005, "output": 0.0015}
        assert est.estimated_usd == pytest.approx(0.0020, rel=1e-6)


# ---------------------------------------------------------------------------
# CostGuard.preflight
# ---------------------------------------------------------------------------


def _make_guard(*, daily: float, monthly: float,
                daily_limit: float = 2.0,
                monthly_limit: float = 100.0) -> CostGuard:
    """Build a CostGuard whose spend lookups are mocked deterministically."""
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default=None: {
        "daily_spend_limit_usd": daily_limit,
        "monthly_spend_limit_usd": monthly_limit,
        "cost_alert_threshold_pct": 80.0,
    }.get(key, default))
    guard = CostGuard(site_config=sc, pool=None)
    guard.get_daily_spend = AsyncMock(return_value=daily)  # type: ignore[method-assign]
    guard.get_monthly_spend = AsyncMock(return_value=monthly)  # type: ignore[method-assign]
    return guard


class TestCostGuardPreflight:
    @pytest.mark.asyncio
    async def test_local_short_circuits(self) -> None:
        guard = _make_guard(daily=999.0, monthly=999.0)
        # Local estimate must never raise even when the budget is blown.
        await guard.preflight(CostEstimate(
            estimated_usd=0.0, is_local=True, model="x", provider="x",
        ))

    @pytest.mark.asyncio
    async def test_cloud_within_budget_passes(self) -> None:
        guard = _make_guard(daily=0.5, monthly=10.0)
        await guard.preflight(CostEstimate(
            estimated_usd=0.10, is_local=False, model="gpt-4o-mini",
            provider="openai_compat",
        ))

    @pytest.mark.asyncio
    async def test_daily_cap_raises_typed_exception(self) -> None:
        guard = _make_guard(daily=1.99, monthly=10.0, daily_limit=2.0)
        with pytest.raises(CostGuardExhausted) as excinfo:
            await guard.preflight(CostEstimate(
                estimated_usd=0.50, is_local=False, model="x", provider="x",
            ))
        assert excinfo.value.scope == "daily"
        assert excinfo.value.spent_usd == pytest.approx(1.99)
        assert excinfo.value.limit_usd == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_monthly_cap_takes_precedence(self) -> None:
        """Monthly check happens before daily — monthly cap exhaustion
        should raise with scope='monthly' even if daily would also fail.
        """
        guard = _make_guard(daily=1.99, monthly=99.99,
                            daily_limit=2.0, monthly_limit=100.0)
        with pytest.raises(CostGuardExhausted) as excinfo:
            await guard.preflight(CostEstimate(
                estimated_usd=0.50, is_local=False, model="x", provider="x",
            ))
        assert excinfo.value.scope == "monthly"


# ---------------------------------------------------------------------------
# CostGuard.record
# ---------------------------------------------------------------------------


class TestCostGuardRecord:
    @pytest.mark.asyncio
    async def test_record_writes_to_cost_logs(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        guard = CostGuard(pool=pool)
        await guard.record(
            provider="openai_compat",
            model="gpt-4o-mini",
            cost_usd=0.0042,
            prompt_tokens=100,
            completion_tokens=50,
            phase="openai_compat.complete",
        )
        pool.execute.assert_awaited_once()
        call = pool.execute.await_args
        # Verify the SQL is an INSERT into cost_logs.
        assert "INSERT INTO cost_logs" in call.args[0]

    @pytest.mark.asyncio
    async def test_record_swallows_db_errors(self) -> None:
        """A failing INSERT must not bubble out of the call path."""
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        guard = CostGuard(pool=pool)
        # Should not raise.
        await guard.record(
            provider="openai_compat", model="gpt-4o-mini", cost_usd=0.01,
        )

    @pytest.mark.asyncio
    async def test_record_no_op_without_pool(self) -> None:
        guard = CostGuard(pool=None)
        # Should silently no-op.
        await guard.record(provider="x", model="y", cost_usd=0.01)


# ---------------------------------------------------------------------------
# CostGuardExhausted exception shape
# ---------------------------------------------------------------------------


class TestCostGuardExhausted:
    def test_is_runtime_error(self) -> None:
        # Subclass of RuntimeError so generic ``except Exception`` paths
        # still catch it without losing the type.
        e = CostGuardExhausted("over budget")
        assert isinstance(e, RuntimeError)

    def test_carries_budget_snapshot(self) -> None:
        e = CostGuardExhausted(
            "over", scope="daily", spent_usd=1.99, limit_usd=2.0,
        )
        assert e.scope == "daily"
        assert e.spent_usd == 1.99
        assert e.limit_usd == 2.0
        assert "over" in str(e)
