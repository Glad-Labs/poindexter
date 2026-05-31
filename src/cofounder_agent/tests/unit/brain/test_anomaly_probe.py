"""Unit tests for ``brain/anomaly_probe.py`` (Glad-Labs/poindexter#440).

The statistical core (``_zscore`` / ``_is_anomaly`` / ``_evaluate``) is pure
and tested directly — no DB mock needed, so these pin the actual math. The
``probe_anomaly`` tests use a fake pool that dispatches on the SQL (settings
vs series) and raises on an unexpected shape, per the CONTRIBUTING strict-
fake convention.
"""

from __future__ import annotations

import math

import pytest

from brain.anomaly_probe import (
    METRICS,
    MetricSpec,
    _evaluate,
    _is_anomaly,
    _zscore,
    probe_anomaly,
)


# --------------------------------------------------------------------------
# _zscore
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_zscore_normal_distribution():
    z, mean, std = _zscore([10, 12, 11, 9, 13, 10, 11], 11)
    assert mean == pytest.approx(10.857, abs=0.01)
    assert std > 0
    assert abs(z) < 1  # 11 is right near the mean


@pytest.mark.unit
def test_zscore_flatline_baseline_with_change_is_infinite():
    # std==0 baseline, current differs → signed inf (a flatline broke).
    z_up, mean, std = _zscore([5, 5, 5, 5, 5], 20)
    assert std == 0
    assert z_up == math.inf
    z_down, _, _ = _zscore([5, 5, 5, 5, 5], 0)
    assert z_down == -math.inf


@pytest.mark.unit
def test_zscore_flatline_baseline_no_change_is_zero():
    z, mean, std = _zscore([5, 5, 5, 5, 5], 5)
    assert (z, mean, std) == (0.0, 5.0, 0.0)


# --------------------------------------------------------------------------
# _is_anomaly — direction awareness
# --------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "z,direction,sigma,expected",
    [
        (4.0, "high", 3.0, True),    # spike, high-bad → anomaly
        (-4.0, "high", 3.0, False),  # dip, high-bad → fine
        (-4.0, "low", 3.0, True),    # drop, low-bad → anomaly
        (4.0, "low", 3.0, False),    # surge, low-bad → fine
        (4.0, "both", 3.0, True),
        (-4.0, "both", 3.0, True),
        (2.0, "high", 3.0, False),   # within envelope
        (math.inf, "high", 3.0, True),
        (-math.inf, "low", 3.0, True),
    ],
)
def test_is_anomaly_direction(z, direction, sigma, expected):
    assert _is_anomaly(z, direction, sigma) is expected


# --------------------------------------------------------------------------
# _evaluate
# --------------------------------------------------------------------------

_SPEC_HIGH = MetricSpec("x", "high", "t", "ts", "COUNT(*)")
_SPEC_LOW = MetricSpec("y", "low", "t", "ts", "COUNT(*)")


@pytest.mark.unit
def test_evaluate_insufficient_data():
    out = _evaluate([5.0], _SPEC_HIGH, sigma=3.0, min_samples=5)
    assert out["status"] == "insufficient_data"
    assert out["anomaly"] is False


@pytest.mark.unit
def test_evaluate_insufficient_history_guards_young_system():
    # 8 points but only 2 non-zero baseline days < min_samples=5 → skip.
    series = [0, 0, 0, 0, 0, 1, 2, 9]
    out = _evaluate([float(v) for v in series], _SPEC_HIGH, sigma=3.0, min_samples=5)
    assert out["status"] == "insufficient_history"
    assert out["anomaly"] is False


@pytest.mark.unit
def test_evaluate_within_envelope_is_not_anomaly():
    series = [10, 11, 9, 10, 12, 8, 11, 10]
    out = _evaluate([float(v) for v in series], _SPEC_HIGH, sigma=3.0, min_samples=5)
    assert out["status"] == "evaluated"
    assert out["anomaly"] is False


@pytest.mark.unit
def test_evaluate_high_spike_flagged():
    series = [1.0, 1.2, 0.9, 1.1, 1.0, 1.3, 0.8, 10.0]
    out = _evaluate(series, _SPEC_HIGH, sigma=3.0, min_samples=5)
    assert out["anomaly"] is True
    assert out["current"] == 10.0


@pytest.mark.unit
def test_evaluate_high_drop_not_flagged():
    # A drop on a high-bad metric is good news — must not flag.
    series = [10.0, 11.0, 9.0, 10.5, 10.0, 11.0, 9.5, 1.0]
    out = _evaluate(series, _SPEC_HIGH, sigma=3.0, min_samples=5)
    assert out["anomaly"] is False


@pytest.mark.unit
def test_evaluate_low_drop_flagged_the_flatline_case():
    # The #524 pipeline-flatline scenario: throughput drops to 0.
    series = [3.0, 4.0, 3.0, 5.0, 4.0, 3.0, 4.0, 0.0]
    out = _evaluate(series, _SPEC_LOW, sigma=3.0, min_samples=5)
    assert out["anomaly"] is True
    assert out["current"] == 0.0


# --------------------------------------------------------------------------
# probe_anomaly — fake pool
# --------------------------------------------------------------------------

class _FakePool:
    """Dispatches fetch() on SQL content: settings query vs series query.
    Series are returned in METRICS order; an Exception entry is raised to
    exercise per-metric error isolation."""

    def __init__(self, settings: dict, series_in_order: list):
        self._settings_rows = [{"key": k, "value": v} for k, v in settings.items()]
        self._series = series_in_order
        self._idx = 0

    async def fetch(self, query: str, *args):
        if "app_settings" in query:
            return self._settings_rows
        if "generate_series" not in query:
            raise AssertionError(f"unexpected query: {query[:60]}")
        data = self._series[self._idx]
        self._idx += 1
        if isinstance(data, Exception):
            raise data
        return [{"d": i, "v": float(v)} for i, v in enumerate(data)]


_ENABLED_SETTINGS = {
    "anomaly_probe_enabled": "true",
    "anomaly_sigma_threshold": "3.0",
    "anomaly_baseline_days": "7",
    "anomaly_min_samples": "5",
}

# 8-point series (7 baseline + current). One per metric, METRICS order:
# post_throughput, llm_cost, pipeline_failures, audit_errors.
_ALL_NORMAL = [
    [3, 4, 3, 5, 4, 3, 4, 4],        # throughput steady
    [1.0, 1.2, 0.9, 1.1, 1.0, 1.3, 0.8, 1.0],  # cost steady
    [0, 1, 0, 0, 1, 0, 0, 0],        # failures low/steady
    [2, 1, 3, 2, 1, 2, 3, 2],        # errors steady
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_disabled_returns_ok():
    pool = _FakePool({"anomaly_probe_enabled": "false"}, [])
    out = await probe_anomaly(pool)
    assert out["ok"] is True
    assert out["status"] == "disabled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_all_within_envelope_is_ok():
    pool = _FakePool(_ENABLED_SETTINGS, [list(s) for s in _ALL_NORMAL])
    out = await probe_anomaly(pool)
    assert out["ok"] is True
    assert out["anomalies"] == []
    assert set(out["metrics"]) == {m.name for m in METRICS}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_cost_spike_trips_only_cost():
    series = [list(s) for s in _ALL_NORMAL]
    series[1] = [1.0, 1.2, 0.9, 1.1, 1.0, 1.3, 0.8, 50.0]  # llm_cost spike
    pool = _FakePool(_ENABLED_SETTINGS, series)
    out = await probe_anomaly(pool)
    assert out["ok"] is False
    assert out["anomalies"] == ["llm_cost"]
    assert out["severity"] == "warning"
    assert "ANOMALY" in out["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_metric_query_error_degrades_not_crashes():
    series = [list(s) for s in _ALL_NORMAL]
    series[3] = RuntimeError("boom")  # audit_errors query fails
    pool = _FakePool(_ENABLED_SETTINGS, series)
    out = await probe_anomaly(pool)
    assert out["ok"] is False
    assert out["metrics"]["audit_errors"]["status"] == "error"
    # The other three still evaluated — one bad metric doesn't blind the rest.
    assert out["metrics"]["post_throughput"]["status"] == "evaluated"
