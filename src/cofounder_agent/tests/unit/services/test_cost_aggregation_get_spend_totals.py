"""Unit tests for ``cost_aggregation_service.get_spend_totals``.

As of cost-control P1 (the cost_ledger reroute) this no longer issues raw
``SUM(cost_usd)`` SQL — it delegates to ``cost_ledger.get_spend`` per window and
returns a backward-compatible superset: the blended ``*_total_usd`` keys the MCP
``get_budget`` tool already serialized, PLUS the honest api/electricity split and
the electricity source. So the tests stub ``cost_ledger.get_spend`` rather than
``pool.fetchval``. (Date-window-correctness lives in ``test_cost_ledger.py`` now.)
"""

from __future__ import annotations

import pytest

from services import cost_aggregation_service as cas
from services.cost_ledger import SpendBreakdown


def _patch_get_spend(monkeypatch, *, month: SpendBreakdown, day: SpendBreakdown):
    async def fake_get_spend(pool, *, window="day", strict=False, site_config=None):
        return month if window == "month" else day

    monkeypatch.setattr(cas.cost_ledger, "get_spend", fake_get_spend)


class TestGetSpendTotals:
    async def test_returns_split_superset(self, monkeypatch):
        _patch_get_spend(
            monkeypatch,
            month=SpendBreakdown(
                api_usd=0.0, electricity_usd=34.25, total_usd=34.25,
                electricity_source="measured",
            ),
            day=SpendBreakdown(
                api_usd=0.0, electricity_usd=1.10, total_usd=1.10,
                electricity_source="measured",
            ),
        )
        out = await cas.get_spend_totals(object())
        # Backcompat keys (the MCP get_budget tool already serialized these).
        assert out["monthly_total_usd"] == pytest.approx(34.25)
        assert out["daily_total_usd"] == pytest.approx(1.10)
        # New honest split.
        assert out["monthly_api_usd"] == 0.0
        assert out["monthly_electricity_usd"] == pytest.approx(34.25)
        assert out["daily_api_usd"] == 0.0
        assert out["daily_electricity_usd"] == pytest.approx(1.10)
        assert out["electricity_source"] == "measured"

    async def test_api_axis_carries_paid_cloud_spend(self, monkeypatch):
        # When real cloud spend exists it lands on the api axis, not blended in.
        _patch_get_spend(
            monkeypatch,
            month=SpendBreakdown(
                api_usd=3.50, electricity_usd=30.0, total_usd=33.50,
                electricity_source="measured",
            ),
            day=SpendBreakdown(
                api_usd=0.25, electricity_usd=1.0, total_usd=1.25,
                electricity_source="estimated",
            ),
        )
        out = await cas.get_spend_totals(object())
        assert out["monthly_api_usd"] == pytest.approx(3.50)
        assert out["monthly_total_usd"] == pytest.approx(33.50)
        assert out["daily_api_usd"] == pytest.approx(0.25)

    async def test_zero_spend_is_all_zeros(self, monkeypatch):
        _patch_get_spend(monkeypatch, month=SpendBreakdown(), day=SpendBreakdown())
        out = await cas.get_spend_totals(object())
        assert out["monthly_total_usd"] == 0.0
        assert out["daily_total_usd"] == 0.0
        assert out["electricity_source"] == "none"

    async def test_threads_site_config_to_ledger(self, monkeypatch):
        # The optional site_config (electricity rate/coverage knobs) reaches both
        # windows so the ledger's estimate fallback uses the real rate, not 0.16.
        seen = {}

        async def fake_get_spend(pool, *, window="day", strict=False, site_config=None):
            seen[window] = site_config
            return SpendBreakdown()

        monkeypatch.setattr(cas.cost_ledger, "get_spend", fake_get_spend)
        sentinel = object()
        await cas.get_spend_totals(object(), site_config=sentinel)
        assert seen["month"] is sentinel
        assert seen["day"] is sentinel
