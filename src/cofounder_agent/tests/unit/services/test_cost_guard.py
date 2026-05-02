"""Unit tests for services.cost_guard.

Reinstated alongside the OpenAICompatProvider plugin
(Glad-Labs/poindexter#132). The earlier cost_guard module was deleted
in commit 5eb26b51 because it had no live callers; the new plugin gives
it a real consumer so the module + tests come back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Round-2 fills: previously-uncovered surface area
# ---------------------------------------------------------------------------


class TestLimitLookup:
    """``CostGuard._limit`` reads numeric settings from site_config (lines 196-209)."""

    def test_returns_default_when_no_site_config(self) -> None:
        guard = CostGuard()  # no site_config
        assert guard._limit("daily_spend_limit_usd", 2.0) == 2.0

    def test_returns_value_from_site_config(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(return_value="5.50")
        guard = CostGuard(site_config=sc)
        assert guard._limit("daily_spend_limit_usd", 2.0) == 5.5

    def test_falls_back_on_non_numeric_setting(self) -> None:
        """Malformed value falls through to default rather than crashing."""
        sc = MagicMock()
        sc.get = MagicMock(return_value="not-a-number")
        guard = CostGuard(site_config=sc)
        assert guard._limit("daily_spend_limit_usd", 2.0) == 2.0


class TestSpendLookups:
    """``get_daily_spend`` and ``get_monthly_spend`` query cost_logs (lines 215-244)."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_pool(self) -> None:
        guard = CostGuard(pool=None)
        assert await guard.get_daily_spend() == 0.0
        assert await guard.get_monthly_spend() == 0.0

    @pytest.mark.asyncio
    async def test_get_daily_spend_returns_db_total(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"total": 1.234})
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == pytest.approx(1.234)

    @pytest.mark.asyncio
    async def test_get_monthly_spend_returns_db_total(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"total": 42.5})
        guard = CostGuard(pool=pool)
        assert await guard.get_monthly_spend() == 42.5

    @pytest.mark.asyncio
    async def test_db_error_returns_zero(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("conn lost"))
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == 0.0

    @pytest.mark.asyncio
    async def test_db_returns_none_row(self) -> None:
        """Empty result set -> 0.0."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == 0.0

    @pytest.mark.asyncio
    async def test_uses_correct_window_sql(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"total": 0.0})
        guard = CostGuard(pool=pool)
        await guard.get_daily_spend()
        sql = pool.fetchrow.await_args.args[0]
        assert "date_trunc('day'" in sql
        await guard.get_monthly_spend()
        sql2 = pool.fetchrow.await_args.args[0]
        assert "date_trunc('month'" in sql2


class TestPreflightAlertPath:
    """Soft alert path logs a warning at >=alert_threshold_pct (line 333)."""

    @pytest.mark.asyncio
    async def test_alert_fires_at_threshold(self, caplog) -> None:
        # daily=1.6, est=0.0, daily_limit=2.0, threshold=80% -> trip
        guard = _make_guard(daily=1.6, monthly=0.0,
                            daily_limit=2.0, monthly_limit=100.0)
        with caplog.at_level("WARNING", logger="services.cost_guard"):
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))
        # The warning text starts with [COST_GUARD] approaching daily cap
        assert any("approaching daily cap" in r.message for r in caplog.records)


class TestRecordAuditFallback:
    """When cost_logs INSERT fails, audit_log_bg is called as a fallback (lines 396-415)."""

    @pytest.mark.asyncio
    async def test_audit_log_called_on_insert_failure(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        guard = CostGuard(pool=pool)

        with patch("services.audit_log.audit_log_bg") as audit_mock:
            await guard.record(provider="openai", model="gpt-4o", cost_usd=0.1)
        audit_mock.assert_called_once()
        kwargs = audit_mock.call_args.kwargs
        assert kwargs.get("severity") == "error"

    @pytest.mark.asyncio
    async def test_audit_log_failure_swallowed(self) -> None:
        """Audit logger blowing up too should not surface — best-effort."""
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        guard = CostGuard(pool=pool)

        with patch("services.audit_log.audit_log_bg",
                   side_effect=RuntimeError("audit also broken")):
            # Should not raise.
            await guard.record(provider="openai", model="gpt-4o", cost_usd=0.1)


class TestGetRate:
    """``CostGuard._get_rate`` resolves cost-per-1K-tokens (lines 433-477)."""

    def test_unknown_direction_returns_zero(self) -> None:
        guard = CostGuard()
        assert guard._get_rate("openai", "gpt-4o", "sideways") == 0.0

    def test_unknown_provider_returns_zero(self) -> None:
        """Misclassified Ollama call shouldn't trip the budget."""
        guard = CostGuard()
        assert guard._get_rate("ollama", "llama3", "input") == 0.0

    def test_known_provider_uses_fallback_rate(self) -> None:
        guard = CostGuard()
        # _FALLBACK_RATE_PER_1K input = 0.0005
        assert guard._get_rate("openai", "unknown-model", "input") == 0.0005

    def test_per_model_override_wins(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "plugin.llm_provider.openai.model.gpt-4o.cost_per_1k_input_usd": "0.0030",
        }.get(key, default))
        guard = CostGuard(site_config=sc)
        assert guard._get_rate("openai", "gpt-4o", "input") == 0.0030

    def test_provider_default_used_when_no_per_model(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "plugin.llm_provider.openai.cost_per_1k_input_usd": "0.0007",
        }.get(key, default))
        guard = CostGuard(site_config=sc)
        assert guard._get_rate("openai", "any-model", "input") == 0.0007

    def test_non_numeric_setting_skipped(self) -> None:
        """Bad rate row falls through to fallback."""
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "plugin.llm_provider.openai.model.gpt-4o.cost_per_1k_input_usd": "not-numeric",
        }.get(key, default))
        guard = CostGuard(site_config=sc)
        # Falls back to _FALLBACK_RATE_PER_1K input
        assert guard._get_rate("openai", "gpt-4o", "input") == 0.0005

    def test_site_config_get_exception_swallowed(self) -> None:
        """sc.get blowing up shouldn't crash the rate lookup — fall through."""
        sc = MagicMock()
        sc.get = MagicMock(side_effect=RuntimeError("settings unavailable"))
        guard = CostGuard(site_config=sc)
        # Falls through to _FALLBACK_RATE_PER_1K for known cloud provider
        assert guard._get_rate("openai", "gpt-4o", "input") == 0.0005


class TestEstimateCost:
    """``estimate_cost`` is the high-level entry that chains _get_rate (lines 479-496)."""

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_zero(self) -> None:
        guard = CostGuard()
        result = await guard.estimate_cost(
            provider="ollama", model="llama3",
            prompt_tokens=1000, completion_tokens=500,
        )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_known_provider_with_fallback_rate(self) -> None:
        guard = CostGuard()
        # 1000 input @ 0.0005 + 1000 output @ 0.0015 = 0.002
        result = await guard.estimate_cost(
            provider="openai", model="unknown-model",
            prompt_tokens=1000, completion_tokens=1000,
        )
        assert result == pytest.approx(0.002, rel=1e-6)

    @pytest.mark.asyncio
    async def test_negative_tokens_clamped_to_zero(self) -> None:
        guard = CostGuard()
        result = await guard.estimate_cost(
            provider="openai", model="gpt-4o",
            prompt_tokens=-5, completion_tokens=-5,
        )
        assert result == 0.0


class TestEnergyAndKwh:
    """Energy estimation paths (lines 516-572 + 582-599)."""

    def test_get_energy_unknown_provider_returns_zero(self) -> None:
        guard = CostGuard()
        assert guard._get_energy_per_1k_wh("ollama", "llama3") == 0.0

    def test_get_energy_uses_per_model_default(self) -> None:
        guard = CostGuard()
        # _DEFAULT_CLOUD_ENERGY_WH_PER_1K[gemini][gemini-2.5-flash] = 0.3
        assert guard._get_energy_per_1k_wh("gemini", "gemini-2.5-flash") == 0.3

    def test_get_energy_falls_back_to_provider_constant(self) -> None:
        guard = CostGuard()
        # _FALLBACK_ENERGY_WH_PER_1K = 1.0 for known provider, unknown model
        assert guard._get_energy_per_1k_wh("openai", "totally-new-model") == 1.0

    def test_get_energy_per_model_override_from_settings(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "plugin.llm_provider.openai.model.gpt-4o.energy_per_1k_wh": "2.5",
        }.get(key, default))
        guard = CostGuard(site_config=sc)
        assert guard._get_energy_per_1k_wh("openai", "gpt-4o") == 2.5

    def test_get_energy_provider_default_override(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "plugin.llm_provider.openai.energy_per_1k_wh": "3.0",
        }.get(key, default))
        guard = CostGuard(site_config=sc)
        assert guard._get_energy_per_1k_wh("openai", "anything") == 3.0

    def test_get_energy_non_numeric_falls_back(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "plugin.llm_provider.openai.energy_per_1k_wh": "garbage",
        }.get(key, default))
        guard = CostGuard(site_config=sc)
        # falls through to fallback (1.0 for known provider)
        assert guard._get_energy_per_1k_wh("openai", "x") == 1.0

    def test_get_energy_site_config_get_exception_swallowed(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=RuntimeError("settings down"))
        guard = CostGuard(site_config=sc)
        # falls back to provider default (gemini-2.5-flash = 0.3)
        assert guard._get_energy_per_1k_wh("gemini", "gemini-2.5-flash") == 0.3

    @pytest.mark.asyncio
    async def test_estimate_cloud_kwh(self) -> None:
        guard = CostGuard()
        # gemini-2.5-flash = 0.3 Wh/1K. 2000 tokens -> 0.6 Wh -> 0.0006 kWh
        result = await guard.estimate_cloud_kwh(
            provider="gemini", model="gemini-2.5-flash",
            prompt_tokens=1000, completion_tokens=1000,
        )
        assert result == pytest.approx(0.0006, rel=1e-6)

    def test_estimate_local_kwh_with_zero_duration(self) -> None:
        guard = CostGuard()
        assert guard.estimate_local_kwh(duration_ms=None) == 0.0
        assert guard.estimate_local_kwh(duration_ms=0) == 0.0

    def test_estimate_local_kwh_default_watts(self) -> None:
        guard = CostGuard()
        # 1 second @ 450W default = 450 Joules / 3.6e6 = 0.000125 kWh
        result = guard.estimate_local_kwh(duration_ms=1000)
        assert result == pytest.approx(0.000125, rel=1e-3)

    def test_estimate_local_kwh_custom_watts(self) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "gpu_power_watts": "300",
        }.get(key, str(default)))
        guard = CostGuard(site_config=sc)
        # 1 second @ 300W
        result = guard.estimate_local_kwh(duration_ms=1000)
        assert result == pytest.approx(300.0 / 3_600_000, rel=1e-3)

    def test_kwh_to_usd_uses_default_rate(self) -> None:
        guard = CostGuard()
        # default electricity_rate_kwh = 0.16
        assert guard.kwh_to_usd(1.0) == pytest.approx(0.16)
        assert guard.kwh_to_usd(2.5) == pytest.approx(0.40)


class TestCheckBudget:
    """``check_budget`` is the high-level cap check (lines 601-672)."""

    @pytest.mark.asyncio
    async def test_zero_estimate_short_circuits(self) -> None:
        guard = _make_guard(daily=0.0, monthly=0.0)
        # Should not raise — zero or negative estimate skips all checks.
        await guard.check_budget(provider="x", model="y", estimated_cost_usd=0.0)
        await guard.check_budget(provider="x", model="y", estimated_cost_usd=-0.5)

    @pytest.mark.asyncio
    async def test_estimate_alone_exceeds_daily_cap(self) -> None:
        """A single call larger than the daily cap is refused even on $0 spend."""
        guard = _make_guard(daily=0.0, monthly=0.0,
                            daily_limit=2.0, monthly_limit=100.0)
        with pytest.raises(CostGuardExhausted) as exc:
            await guard.check_budget(
                provider="openai", model="gpt-4o",
                estimated_cost_usd=5.0,
            )
        assert exc.value.scope == "daily_estimate"
        assert exc.value.provider == "openai"
        assert exc.value.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_daily_cap_exceeded(self) -> None:
        guard = _make_guard(daily=1.95, monthly=10.0, daily_limit=2.0)
        with pytest.raises(CostGuardExhausted) as exc:
            await guard.check_budget(
                provider="openai", model="gpt-4o",
                estimated_cost_usd=0.10,
            )
        assert exc.value.scope == "daily"

    @pytest.mark.asyncio
    async def test_monthly_cap_exceeded(self) -> None:
        guard = _make_guard(daily=0.5, monthly=99.95,
                            daily_limit=2.0, monthly_limit=100.0)
        with pytest.raises(CostGuardExhausted) as exc:
            await guard.check_budget(
                provider="openai", model="gpt-4o",
                estimated_cost_usd=0.10,
            )
        assert exc.value.scope == "monthly"

    @pytest.mark.asyncio
    async def test_within_budget_passes(self) -> None:
        guard = _make_guard(daily=0.5, monthly=10.0,
                            daily_limit=2.0, monthly_limit=100.0)
        # Should not raise
        await guard.check_budget(
            provider="openai", model="gpt-4o",
            estimated_cost_usd=0.05,
        )

    @pytest.mark.asyncio
    async def test_alert_warning_emitted_near_threshold(self, caplog) -> None:
        guard = _make_guard(daily=1.6, monthly=10.0,
                            daily_limit=2.0, monthly_limit=100.0)
        with caplog.at_level("WARNING", logger="services.cost_guard"):
            await guard.check_budget(
                provider="openai", model="gpt-4o",
                estimated_cost_usd=0.0001,
            )
        assert any("approaching daily cap" in r.message for r in caplog.records)


class TestRecordUsage:
    """``record_usage`` auto-fills cost + electricity_kwh (lines 705-739)."""

    @pytest.mark.asyncio
    async def test_local_provider_auto_calculates_via_electricity(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        guard = CostGuard(pool=pool)
        cost = await guard.record_usage(
            provider="ollama", model="llama3",
            prompt_tokens=500, completion_tokens=500,
            duration_ms=2000, is_local=True,
        )
        # local cost = kwh_to_usd(estimate_local_kwh(2000ms))
        # 2s @ 450W = 900J / 3.6e6 = 0.00025 kWh; * 0.16 = 0.00004
        assert cost == pytest.approx(0.16 * 900 / 3_600_000, rel=1e-3)
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cloud_provider_auto_calculates_via_token_rates(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        guard = CostGuard(pool=pool)
        cost = await guard.record_usage(
            provider="openai", model="unknown-model",
            prompt_tokens=1000, completion_tokens=1000,
            is_local=False,
        )
        # 0.0005 + 0.0015 = 0.002
        assert cost == pytest.approx(0.002, rel=1e-6)

    @pytest.mark.asyncio
    async def test_explicit_cost_usd_used_as_is(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        guard = CostGuard(pool=pool)
        cost = await guard.record_usage(
            provider="openai", model="gpt-4o",
            cost_usd=0.50, electricity_kwh=0.001,
            prompt_tokens=10, completion_tokens=10,
        )
        assert cost == 0.50

    @pytest.mark.asyncio
    async def test_returns_persisted_cost(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        guard = CostGuard(pool=pool)
        cost = await guard.record_usage(
            provider="openai", model="gpt-4o",
            cost_usd=1.234, electricity_kwh=0.0,
            prompt_tokens=0, completion_tokens=0,
        )
        assert cost == 1.234
