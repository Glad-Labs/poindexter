"""Unit tests for services.cost_guard.

Reinstated alongside the OpenAICompatProvider plugin
(Glad-Labs/poindexter#132). The earlier cost_guard module was deleted
in commit 5eb26b51 because it had no live callers; the new plugin gives
it a real consumer so the module + tests come back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import cost_ledger
from services.cost_guard import (
    CostEstimate,
    CostGuard,
    CostGuardExhausted,
    is_local_base_url,
)
from services.cost_ledger import SpendBreakdown

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
                monthly_limit: float = 100.0,
                daily_electricity: float = 0.0,
                total_budget: float = 3.0) -> CostGuard:
    """Build a CostGuard whose spend lookups are mocked deterministically.

    The gate reads the ledger's ``SpendBreakdown`` once per window (P2): the
    hard cap keys on ``api_usd`` and the soft alert keys on ``total_usd``. We
    mock the ``_daily_breakdown`` / ``_monthly_breakdown`` seam — the single
    ``cost_ledger.get_spend`` boundary — so ``daily`` / ``monthly`` are the
    paid-API axis and ``daily_electricity`` stacks onto the daily total to
    drive the both-axes alert without any paid spend.

    Two ceilings, one per axis, and they are NOT interchangeable:
    ``daily_limit`` / ``monthly_limit`` (``daily_spend_limit_usd`` /
    ``monthly_spend_limit_usd``) bound the API axis and are what the hard cap
    enforces; ``total_budget`` (``cost_throttle_daily_budget_usd``) bounds the
    total axis and is what the soft alert trips on.
    """
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default=None: {
        "daily_spend_limit_usd": daily_limit,
        "monthly_spend_limit_usd": monthly_limit,
        "cost_throttle_daily_budget_usd": total_budget,
        "cost_alert_threshold_pct": 80.0,
    }.get(key, default))
    guard = CostGuard(site_config=sc, pool=None)
    guard._daily_breakdown = AsyncMock(return_value=SpendBreakdown(  # type: ignore[method-assign]
        api_usd=daily,
        electricity_usd=daily_electricity,
        total_usd=daily + daily_electricity,
    ))
    guard._monthly_breakdown = AsyncMock(return_value=SpendBreakdown(  # type: ignore[method-assign]
        api_usd=monthly, total_usd=monthly,
    ))
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

    @pytest.mark.asyncio
    async def test_unreadable_spend_fails_closed(self) -> None:
        """#611 — if cost_logs can't be read on the enforcement path,
        preflight raises CostGuardExhausted(scope='unknown') rather than
        admitting the call on a silent $0 read (the fail-open bug).

        Post-P2 the spend read goes through ``cost_ledger.get_spend`` (which
        uses ``fetchval``), so a failing ``fetchval`` is the ledger read
        blowing up — ``get_spend(strict=True)`` re-raises and preflight fails
        closed."""
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default=None: {
            "daily_spend_limit_usd": 2.0,
            "monthly_spend_limit_usd": 100.0,
        }.get(key, default))
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("cost_logs down"))
        guard = CostGuard(site_config=sc, pool=pool)
        with pytest.raises(CostGuardExhausted) as excinfo:
            await guard.preflight(CostEstimate(
                estimated_usd=0.10, is_local=False, model="x", provider="x",
            ))
        assert excinfo.value.scope == "unknown"


class TestCheckBudgetFailsClosed:
    """``check_budget`` is the helper the cloud providers + dispatcher use.
    It must fail CLOSED on a cost_logs read error, like ``preflight`` already
    does (audit M4) — previously it read spend NON-strict, so a transient DB
    error read as $0 and ADMITTED the call, silently disabling the spend cap."""

    @pytest.mark.asyncio
    async def test_check_budget_fails_closed_on_unreadable_daily_spend(self, monkeypatch) -> None:
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default=None: {
            "daily_spend_limit_usd": 2.0,
            "monthly_spend_limit_usd": 10.0,
        }.get(key, default))

        async def _boom(pool, *, window="day", strict=False, site_config=None):
            # The enforcement path reads strict=True, so a ledger failure must
            # propagate (fail closed) rather than swallow to $0 (fail open).
            if strict:
                raise RuntimeError("cost_logs down")
            return SpendBreakdown()

        monkeypatch.setattr(cost_ledger, "get_spend", _boom)
        guard = CostGuard(site_config=sc, pool=MagicMock())
        with pytest.raises(CostGuardExhausted):
            await guard.check_budget(
                provider="gemini", model="gemini-2.0-flash",
                estimated_cost_usd=0.10,
            )

    @pytest.mark.asyncio
    async def test_check_budget_admits_when_spend_readable_and_under_cap(self, monkeypatch) -> None:
        """Sanity: a healthy read under the cap does NOT raise (no over-zealous
        blocking of legitimate paid calls)."""
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default=None: {
            "daily_spend_limit_usd": 2.0,
            "monthly_spend_limit_usd": 10.0,
        }.get(key, default))

        async def _fake(pool, *, window="day", strict=False, site_config=None):
            return SpendBreakdown(api_usd=0.10, total_usd=0.10)

        monkeypatch.setattr(cost_ledger, "get_spend", _fake)
        guard = CostGuard(site_config=sc, pool=MagicMock())
        # 0.10 spent + 0.05 estimate < 2.0 daily → no raise.
        await guard.check_budget(
            provider="gemini", model="gemini-2.0-flash", estimated_cost_usd=0.05,
        )


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
    """``get_daily_spend`` / ``get_monthly_spend`` rent the ``cost_ledger`` read
    seam (P2): each delegates to ``cost_ledger.get_spend(window=...)`` and
    returns its ``api_usd`` (genuinely-paid cloud spend) — no hand-rolled inline
    ``SUM(cost_usd)``. The one meter the cap and the dashboards share.
    """

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_pool(self) -> None:
        guard = CostGuard(pool=None)
        assert await guard.get_daily_spend() == 0.0
        assert await guard.get_monthly_spend() == 0.0

    @pytest.mark.asyncio
    async def test_get_daily_spend_returns_ledger_api_axis(self, monkeypatch) -> None:
        # api=1.234 with a fat electricity axis; the cap must read the api axis
        # only, never the blended total.
        async def _fake(pool, *, window="day", strict=False, site_config=None):
            return SpendBreakdown(api_usd=1.234, electricity_usd=9.0, total_usd=10.234)

        monkeypatch.setattr(cost_ledger, "get_spend", _fake)
        guard = CostGuard(pool=MagicMock())
        assert await guard.get_daily_spend() == pytest.approx(1.234)

    @pytest.mark.asyncio
    async def test_get_monthly_spend_returns_ledger_api_axis(self, monkeypatch) -> None:
        async def _fake(pool, *, window="day", strict=False, site_config=None):
            return SpendBreakdown(api_usd=42.5, total_usd=42.5)

        monkeypatch.setattr(cost_ledger, "get_spend", _fake)
        guard = CostGuard(pool=MagicMock())
        assert await guard.get_monthly_spend() == 42.5

    @pytest.mark.asyncio
    async def test_delegates_to_ledger_with_correct_window(self, monkeypatch) -> None:
        seen: list[str] = []

        async def _spy(pool, *, window="day", strict=False, site_config=None):
            seen.append(window)
            return SpendBreakdown()

        monkeypatch.setattr(cost_ledger, "get_spend", _spy)
        guard = CostGuard(pool=MagicMock())
        await guard.get_daily_spend()
        await guard.get_monthly_spend()
        assert seen == ["day", "month"]

    @pytest.mark.asyncio
    async def test_passes_strict_and_site_config_through(self, monkeypatch) -> None:
        captured: dict = {}

        async def _spy(pool, *, window="day", strict=False, site_config=None):
            captured["strict"] = strict
            captured["site_config"] = site_config
            return SpendBreakdown()

        monkeypatch.setattr(cost_ledger, "get_spend", _spy)
        sc = MagicMock()
        guard = CostGuard(site_config=sc, pool=MagicMock())
        await guard.get_daily_spend(strict=True)
        assert captured["strict"] is True
        assert captured["site_config"] is sc

    @pytest.mark.asyncio
    async def test_db_error_returns_zero_when_not_strict(self) -> None:
        # Real guard→ledger→pool stack: a failing fetchval, non-strict, is
        # swallowed by the ledger to a zeroed breakdown → $0.
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("conn lost"))
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == 0.0

    @pytest.mark.asyncio
    async def test_db_error_raises_when_strict(self) -> None:
        """#611 — the enforcement path reads strict=True, so a cost_logs failure
        raises (fail closed) instead of silently reading $0 (fail open). The
        ledger's ``get_spend(strict=True)`` re-raises; the guard propagates."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("conn lost"))
        guard = CostGuard(pool=pool)
        with pytest.raises(RuntimeError):
            await guard.get_daily_spend(strict=True)
        with pytest.raises(RuntimeError):
            await guard.get_monthly_spend(strict=True)

    @pytest.mark.asyncio
    async def test_null_sums_read_as_zero(self) -> None:
        """Empty windows (fetchval → None) read as $0, not a crash."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[])
        guard = CostGuard(pool=pool)
        assert await guard.get_daily_spend() == 0.0

    @pytest.mark.asyncio
    async def test_gate_reads_spend_only_via_the_ledger_seam(self, monkeypatch) -> None:
        """The cap no longer hand-rolls a ``SUM(cost_usd)`` — it reads spend
        exclusively through ``cost_ledger.get_spend`` (which owns the single
        api-axis definition, guarded end-to-end by ``test_cost_ledger`` + the
        cross-consumer source scan in ``test_cost_logs_local_predicate``). This
        pins the P2 delegation: the guard issues no ``fetchrow`` SQL of its own,
        so the stale router-blind provider-name denylist that leaked local rows
        into the paid set (the 2026-06-21 phantom-regression) can never return.
        """
        called = {"n": 0}

        async def _spy(pool, *, window="day", strict=False, site_config=None):
            called["n"] += 1
            return SpendBreakdown(api_usd=0.0, total_usd=0.0)

        monkeypatch.setattr(cost_ledger, "get_spend", _spy)
        pool = MagicMock()
        guard = CostGuard(pool=pool)
        await guard.get_daily_spend()
        assert called["n"] == 1              # delegated to the ledger seam
        pool.fetchrow.assert_not_called()    # no hand-rolled inline SQL


class TestPreflightAlertPath:
    """Soft alert path logs a warning at >=alert_threshold_pct."""

    @pytest.mark.asyncio
    async def test_alert_fires_at_threshold(self, caplog) -> None:
        # total=2.5, est=0.0, total_budget=3.0, threshold=80% (=$2.40) -> trip
        guard = _make_guard(daily=0.5, monthly=0.0, daily_electricity=2.0,
                            daily_limit=2.0, monthly_limit=100.0,
                            total_budget=3.0)
        with caplog.at_level("WARNING", logger="services.cost_guard"):
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))
        # The warning names the throttle budget, not the API cap.
        assert any(
            "approaching daily THROTTLE budget" in r.message
            for r in caplog.records
        )


class TestSoftAlertBothAxes:
    """The soft alert fires on the **total** axis (api + electricity) and emits
    an advisory finding, but NEVER blocks. The hard cap stays on ``api_usd``.

    Each axis is measured against its OWN ceiling: the total against
    ``cost_throttle_daily_budget_usd`` (what ``spend_throttle`` defers new work
    at), the API axis against ``daily_spend_limit_usd`` (the hard cap).
    """

    @pytest.mark.asyncio
    async def test_preflight_alert_keys_on_total_not_api(self) -> None:
        # api=0.5 is nowhere near its $2 cap (25%), but api+electricity=2.5 is
        # 83% of the $3 throttle budget → the total-axis alert must fire even
        # though paid spend is low.
        guard = _make_guard(daily=0.5, monthly=1.0, daily_limit=2.0,
                            daily_electricity=2.0, total_budget=3.0)
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        assert kwargs["severity"] == "warn"        # routine → Discord, not a page
        assert kwargs["source"] == "cost_guard"
        assert kwargs.get("dedup_key")             # cooldown-able so it can't spam
        assert kwargs["extra"]["daily_total_usd"] == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_preflight_no_finding_below_threshold(self) -> None:
        # api+electricity=0.7 = 23% of the $3 throttle budget → below 80%.
        guard = _make_guard(daily=0.2, monthly=1.0, daily_limit=2.0,
                            daily_electricity=0.5, total_budget=3.0)
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))
        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_preflight_alert_never_blocks(self) -> None:
        # Total over the alert threshold but api under the hard cap → advisory
        # only: preflight returns without raising.
        guard = _make_guard(daily=0.5, monthly=1.0, daily_limit=2.0,
                            daily_electricity=2.4, total_budget=3.0)
        with patch("services.cost_guard.emit_finding"):
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))  # no raise

    @pytest.mark.asyncio
    async def test_check_budget_alert_keys_on_total(self) -> None:
        guard = _make_guard(daily=0.5, monthly=1.0, daily_limit=2.0,
                            daily_electricity=2.0, total_budget=3.0)
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.check_budget(
                provider="openai", model="gpt-4o", estimated_cost_usd=0.05,
            )
        emit.assert_called_once()
        assert emit.call_args.kwargs["severity"] == "warn"

    @pytest.mark.asyncio
    async def test_check_budget_no_finding_below_threshold(self) -> None:
        guard = _make_guard(daily=0.2, monthly=1.0, daily_limit=2.0,
                            daily_electricity=0.3, total_budget=3.0)
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.check_budget(
                provider="openai", model="gpt-4o", estimated_cost_usd=0.05,
            )
        emit.assert_not_called()


class TestSoftAlertMeasuresTotalAgainstThrottleBudget:
    """Glad-Labs/poindexter#912 — the total axis is measured against the
    total-axis ceiling (``cost_throttle_daily_budget_usd``), never against the
    API-only hard cap (``daily_spend_limit_usd``).

    Keying the total against the API cap made this alert fire on essentially
    any day with a paid call: measured electricity alone runs $1.5-1.9/day
    against a $2 API cap, so the 80% threshold ($1.60) was already consumed
    before any inference happened. Live audit_log showed 1 finding on ordinary
    draft days and 72 on 2026-07-14.
    """

    @pytest.mark.asyncio
    async def test_electricity_alone_does_not_trip_against_api_cap(self) -> None:
        # The exact production shape on 2026-07-26: electricity $1.50, API
        # $0.41. Total $1.91 is 96% of the OLD (wrong) $2 ceiling but only 64%
        # of the correct $3 throttle budget → must stay silent.
        guard = _make_guard(daily=0.4135, monthly=2.0, daily_limit=2.0,
                            daily_electricity=1.5003, total_budget=3.0)
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.check_budget(
                provider="anthropic", model="claude-sonnet-5",
                estimated_cost_usd=0.01,
            )
        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_raising_api_cap_alone_does_not_move_the_threshold(self) -> None:
        """The trip point tracks the throttle budget, not the API cap.

        Same spend either way; only the API cap differs. If the alert were
        still keyed on the API cap, widening it from $2 to $10 would silence a
        firing alert — proving which key is in play.
        """
        for api_cap in (2.0, 10.0):
            guard = _make_guard(daily=0.5, monthly=1.0, daily_limit=api_cap,
                                daily_electricity=2.0, total_budget=3.0)
            with patch("services.cost_guard.emit_finding") as emit:
                await guard.check_budget(
                    provider="openai", model="gpt-4o", estimated_cost_usd=0.05,
                )
            assert emit.call_count == 1, f"alert did not fire at api_cap={api_cap}"

    @pytest.mark.asyncio
    async def test_zero_throttle_budget_disables_the_alert(self) -> None:
        """``<= 0`` disables the axis — the escape-hatch convention
        ``spend_throttle`` uses for the same key."""
        guard = _make_guard(daily=0.5, monthly=1.0, daily_limit=2.0,
                            daily_electricity=99.0, total_budget=0.0)
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))
        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_names_both_axes_explicitly(self) -> None:
        """A reader must be able to tell which axis moved without opening the
        code — so both figures and both ceilings appear in the finding."""
        guard = _make_guard(daily=0.5, monthly=1.0, daily_limit=2.0,
                            daily_electricity=2.0, total_budget=3.0)
        with patch("services.cost_guard.emit_finding") as emit:
            # Non-zero: check_budget short-circuits on a $0 estimate before it
            # ever reaches the alert.
            await guard.check_budget(
                provider="openai", model="gpt-4o", estimated_cost_usd=0.0001,
            )
        kwargs = emit.call_args.kwargs
        extra = kwargs["extra"]
        assert extra["daily_api_usd"] == pytest.approx(0.5)
        assert extra["api_limit_usd"] == pytest.approx(2.0)
        assert extra["daily_total_usd"] == pytest.approx(2.5)
        assert extra["total_budget_usd"] == pytest.approx(3.0)
        # 2.5/3.0 on the total axis; 0.5/2.0 on the API axis.
        assert extra["pct_of_total_budget"] == pytest.approx(83.3, abs=0.1)
        assert extra["pct_of_api_cap"] == pytest.approx(25.0, abs=0.1)
        # Title and body name both ceilings so the Discord card is readable
        # on its own.
        assert "throttle budget" in kwargs["title"]
        assert "API" in kwargs["title"]
        body = kwargs["body"]
        assert "cost_throttle_daily_budget_usd" in body
        assert "daily_spend_limit_usd" in body

    @pytest.mark.asyncio
    async def test_default_total_budget_is_the_throttle_default(self) -> None:
        """Unset key → $3.00, matching ``spend_throttle``'s own default. A
        divergent default here would re-create the axis mismatch on any
        install that never tuned the key.
        """
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda key, default=None: {
            "daily_spend_limit_usd": 2.0,
            "monthly_spend_limit_usd": 100.0,
            "cost_alert_threshold_pct": 80.0,
        }.get(key, default))  # cost_throttle_daily_budget_usd deliberately unset
        guard = CostGuard(site_config=sc, pool=None)
        guard._daily_breakdown = AsyncMock(return_value=SpendBreakdown(  # type: ignore[method-assign]
            api_usd=0.2, electricity_usd=2.3, total_usd=2.5,
        ))
        guard._monthly_breakdown = AsyncMock(return_value=SpendBreakdown(  # type: ignore[method-assign]
            api_usd=1.0, total_usd=1.0,
        ))
        # 2.5 is 83% of the $3.00 default → trips. Against a $4 default it
        # would be 62% and stay silent, and the emitted ceiling below pins the
        # exact figure.
        with patch("services.cost_guard.emit_finding") as emit:
            await guard.preflight(CostEstimate(
                estimated_usd=0.0, is_local=False, model="x", provider="x",
            ))
        emit.assert_called_once()
        assert emit.call_args.kwargs["extra"]["total_budget_usd"] == pytest.approx(3.0)


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
        # total=2.4 (api 0.4 + electricity 2.0) = 80% of the $3 throttle budget.
        guard = _make_guard(daily=0.4, monthly=10.0, daily_electricity=2.0,
                            daily_limit=2.0, monthly_limit=100.0,
                            total_budget=3.0)
        with caplog.at_level("WARNING", logger="services.cost_guard"):
            await guard.check_budget(
                provider="openai", model="gpt-4o",
                estimated_cost_usd=0.0001,
            )
        assert any(
            "approaching daily THROTTLE budget" in r.message
            for r in caplog.records
        )
        # The log line carries both axes so a grep of worker logs shows which
        # one actually moved.
        line = next(r.message for r in caplog.records if "THROTTLE" in r.message)
        assert "API axis" in line


class TestRecordUsage:
    """``record_usage`` auto-fills cost + electricity_kwh (lines 705-739)."""

    @pytest.mark.asyncio
    async def test_local_provider_records_zero_api_cost(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        guard = CostGuard(pool=pool)
        cost = await guard.record_usage(
            provider="ollama", model="llama3",
            prompt_tokens=500, completion_tokens=500,
            duration_ms=2000, is_local=True,
        )
        # Local API cost is $0 (P1 invariant); electricity is attribution-only,
        # tracked via electricity_kwh + the brain's measured rows, not billed
        # onto cost_usd.
        assert cost == 0.0
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
