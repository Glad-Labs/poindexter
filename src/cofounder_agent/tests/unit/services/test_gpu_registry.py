"""Unit tests for services/gpu_registry.py — VRAM pool auto-detection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gpu_registry import GPURegistry
from services.site_config import SiteConfig


def _sc() -> SiteConfig:
    return SiteConfig(initial_config={"gpu_metrics_prometheus_url": "http://prometheus:9090"})


def _mock_client(*, value: str | None = None, status: int = 200, raise_exc: Exception | None = None):
    """Fake httpx.AsyncClient whose .get returns a Prometheus instant-vector."""
    resp = MagicMock()
    resp.status_code = status
    if value is None:
        resp.json = MagicMock(return_value={"data": {"result": []}})
    else:
        resp.json = MagicMock(return_value={"data": {"result": [{"value": [1782600000.0, value]}]}})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=raise_exc) if raise_exc else AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_sums_and_converts_mib_to_gb():
    # 32607 + 24576 MiB summed by Prometheus = 57183 MiB -> /1024 = 55.84 GB
    client = _mock_client(value="57183")
    with patch("httpx.AsyncClient", return_value=client):
        total = await GPURegistry(site_config=_sc()).total_vram_gb()
    assert total == pytest.approx(57183 / 1024.0, abs=0.01)


@pytest.mark.asyncio
async def test_memoizes_first_success_no_requery():
    client = _mock_client(value="57183")
    reg = GPURegistry(site_config=_sc())
    with patch("httpx.AsyncClient", return_value=client):
        first = await reg.total_vram_gb()
        second = await reg.total_vram_gb()
    assert first == second
    assert client.get.await_count == 1  # cached; second call did not re-query


@pytest.mark.asyncio
async def test_empty_result_returns_none():
    client = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_http_error_returns_none():
    client = _mock_client(value="57183", status=503)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_exception_returns_none():
    client = _mock_client(raise_exc=RuntimeError("boom"))
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_retries_after_failure_then_caches():
    reg = GPURegistry(site_config=_sc())
    fail = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=fail):
        assert await reg.total_vram_gb() is None  # not cached
    ok = _mock_client(value="57183")
    with patch("httpx.AsyncClient", return_value=ok):
        assert await reg.total_vram_gb() == pytest.approx(57183 / 1024.0, abs=0.01)
