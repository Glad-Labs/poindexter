"""Unit tests for ``services/render_vram.py``.

Reads live free VRAM on the render GPU (``pipeline_gpu_index``) from Prometheus
so the media dispatch gate can defer a render that would oversubscribe the
display GPU (2026-07-12 desktop-lockup fix). All network is mocked via the
``http_client_factory`` seam.
"""

from __future__ import annotations

import pytest

from services.render_vram import render_gpu_free_vram_gb
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {
        "pipeline_gpu_index": "0",
        "gpu_metrics_prometheus_url": "http://prometheus:9090",
    }
    base.update(overrides)
    return SiteConfig(initial_config=base)


def _factory(*, result=None, status=200, raise_exc=None):
    """Fake ``httpx.AsyncClient`` factory returning a Prometheus instant-query
    body, an HTTP error, or a raised connection exception."""

    class _Resp:
        status_code = status

        def json(self):
            return {
                "status": "success",
                "data": {"resultType": "vector", "result": result or []},
            }

        def raise_for_status(self):
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

    class _FakeClient:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            if raise_exc is not None:
                raise raise_exc
            return _Resp()

    return _FakeClient


@pytest.mark.unit
class TestRenderGpuFreeVramGb:

    @pytest.mark.asyncio
    async def test_parses_free_vram_to_gb(self):
        # 20480 MiB free -> 20.0 GB
        result = [{"metric": {"gpu": "0"}, "value": [0, "20480"]}]
        free = await render_gpu_free_vram_gb(
            _sc(), http_client_factory=_factory(result=result)
        )
        assert free == pytest.approx(20.0, abs=0.05)

    @pytest.mark.asyncio
    async def test_none_when_no_series(self):
        # Prometheus up but no recent scrape of the metric -> unreadable.
        free = await render_gpu_free_vram_gb(
            _sc(), http_client_factory=_factory(result=[])
        )
        assert free is None

    @pytest.mark.asyncio
    async def test_none_when_prometheus_unreachable(self):
        free = await render_gpu_free_vram_gb(
            _sc(), http_client_factory=_factory(raise_exc=ConnectionError("refused"))
        )
        assert free is None

    @pytest.mark.asyncio
    async def test_none_when_http_error(self):
        free = await render_gpu_free_vram_gb(
            _sc(), http_client_factory=_factory(status=503)
        )
        assert free is None

    @pytest.mark.asyncio
    async def test_none_when_site_config_missing(self):
        assert await render_gpu_free_vram_gb(None) is None
