"""Tests for services/energy_bench.py — the cost/energy eval helpers (#530).

Covers the pure math helpers (joules_per_token, tokens_per_second) and the
Prometheus-backed measure_gpu_watts (httpx mocked with AsyncMock).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services import energy_bench

# ---------------------------------------------------------------------------
# joules_per_token
# ---------------------------------------------------------------------------


def test_joules_per_token():
    # 500 W × 2 s / 1000 tokens = 1.0 J/token
    assert energy_bench.joules_per_token(500.0, duration_ms=2000, total_tokens=1000) == pytest.approx(1.0)


def test_joules_per_token_zero_tokens_returns_none():
    assert energy_bench.joules_per_token(500.0, duration_ms=2000, total_tokens=0) is None


def test_joules_per_token_none_watts_returns_none():
    assert energy_bench.joules_per_token(None, duration_ms=2000, total_tokens=1000) is None


# ---------------------------------------------------------------------------
# tokens_per_second
# ---------------------------------------------------------------------------


def test_tokens_per_second():
    # 1500 tokens / 3 s = 500 tok/s
    assert energy_bench.tokens_per_second(1500, duration_ms=3000) == pytest.approx(500.0)


def test_tokens_per_second_zero_duration_returns_none():
    assert energy_bench.tokens_per_second(1500, duration_ms=0) is None


# ---------------------------------------------------------------------------
# measure_gpu_watts (httpx mocked)
# ---------------------------------------------------------------------------


def _mock_client(*, json_payload=None, raise_exc=None):
    """Build an AsyncClient context-manager mock with a stubbed .get()."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_payload)

    client = MagicMock()
    if raise_exc is not None:
        client.get = AsyncMock(side_effect=raise_exc)
    else:
        client.get = AsyncMock(return_value=resp)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_measure_gpu_watts_parses_prometheus_response():
    payload = {
        "status": "success",
        "data": {"result": [{"metric": {}, "value": [1733600000.0, "450.5"]}]},
    }
    with patch.object(httpx, "AsyncClient", return_value=_mock_client(json_payload=payload)):
        watts = await energy_bench.measure_gpu_watts(
            "http://localhost:9091", start_ts=100.0, end_ts=102.0,
        )
    assert watts == pytest.approx(450.5)


async def test_measure_gpu_watts_unreachable_returns_none():
    with patch.object(
        httpx, "AsyncClient",
        return_value=_mock_client(raise_exc=httpx.ConnectError("refused")),
    ):
        watts = await energy_bench.measure_gpu_watts(
            "http://localhost:9091", start_ts=100.0, end_ts=102.0,
        )
    assert watts is None


async def test_measure_gpu_watts_empty_result_returns_none():
    payload = {"status": "success", "data": {"result": []}}
    with patch.object(httpx, "AsyncClient", return_value=_mock_client(json_payload=payload)):
        watts = await energy_bench.measure_gpu_watts(
            "http://localhost:9091", start_ts=100.0, end_ts=102.0,
        )
    assert watts is None


async def test_measure_gpu_watts_status_error_returns_none():
    payload = {"status": "error", "errorType": "bad_data", "error": "boom"}
    with patch.object(httpx, "AsyncClient", return_value=_mock_client(json_payload=payload)):
        watts = await energy_bench.measure_gpu_watts(
            "http://localhost:9091", start_ts=100.0, end_ts=102.0,
        )
    assert watts is None


async def test_measure_gpu_watts_empty_url_returns_none():
    # Short-circuits before any HTTP call.
    watts = await energy_bench.measure_gpu_watts("", start_ts=100.0, end_ts=102.0)
    assert watts is None
