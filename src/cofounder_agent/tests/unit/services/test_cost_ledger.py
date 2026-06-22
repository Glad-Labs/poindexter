"""Unit tests for the cost_ledger read seam (P1 attribution).

Stubs route by SQL fragment: COUNT(*) → measured-sample count, SUM(electricity_kwh)
→ estimate kWh, NOT LIKE 'electricity%' → api $, LIKE 'electricity%' → measured $.
"""
import pytest

from services import cost_ledger


class _FakeConfig:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class _Pool:
    def __init__(self, *, api=0.0, measured=0.0, samples=0, est_kwh=0.0, by_type=None):
        self._api = api
        self._measured = measured
        self._samples = samples
        self._est_kwh = est_kwh
        self._by_type = by_type or []

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql:
            return self._samples
        if "SUM(electricity_kwh)" in sql:
            return self._est_kwh
        if "NOT LIKE 'electricity%'" in sql:
            return self._api
        if "LIKE 'electricity%'" in sql:
            return self._measured
        return 0.0

    async def fetch(self, sql, *args):
        return self._by_type


class _BadPool:
    async def fetchval(self, sql, *args):
        raise RuntimeError("db down")

    async def fetch(self, sql, *args):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_get_spend_splits_axes_measured():
    # day window: expected = 1440/15 = 96 samples; 200 samples => 100% coverage.
    pool = _Pool(api=0.0, measured=34.25, samples=200)
    b = await cost_ledger.get_spend(pool, window="day")
    assert b.api_usd == 0.0
    assert b.electricity_usd == 34.25
    assert b.total_usd == 34.25
    assert b.electricity_source == "measured"
    assert b.electricity_coverage_pct == 100.0


@pytest.mark.asyncio
async def test_get_spend_strict_raises_on_db_error():
    with pytest.raises(RuntimeError):
        await cost_ledger.get_spend(_BadPool(), window="day", strict=True)


@pytest.mark.asyncio
async def test_get_spend_swallows_db_error_when_not_strict():
    b = await cost_ledger.get_spend(_BadPool(), window="day", strict=False)
    assert b.api_usd == 0.0
    assert b.electricity_usd == 0.0
    assert b.electricity_source == "none"


@pytest.mark.asyncio
async def test_electricity_falls_back_to_estimate_when_measured_sparse():
    # No measured samples (0% coverage) but 10 kWh attributed on local rows.
    pool = _Pool(api=0.0, measured=0.0, samples=0, est_kwh=10.0)
    cfg = _FakeConfig({
        "electricity_rate_kwh": "0.2579",
        "electricity_measured_min_coverage_pct": "80",
        "electricity_source_gap_minutes": "15",
    })
    b = await cost_ledger.get_spend(pool, window="day", site_config=cfg)
    assert b.electricity_source == "estimated"
    assert round(b.electricity_usd, 4) == round(10.0 * 0.2579, 4)


@pytest.mark.asyncio
async def test_api_axis_is_paid_only_when_local_is_zero():
    # Post-invariant: local rows are $0, so the api sum (which the stub returns
    # via the NOT LIKE branch) reflects only genuinely-paid cloud spend.
    pool = _Pool(api=3.50, measured=34.25, samples=200)  # day: 200 > 96 => measured
    b = await cost_ledger.get_spend(pool, window="day")
    assert b.api_usd == 3.50
    assert b.total_usd == pytest.approx(37.75)
