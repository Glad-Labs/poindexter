"""_budget_inputs resolves the gpu_vram_total_gb 'auto' sentinel via GPURegistry."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers import dispatcher
from services.site_config import SiteConfig


def _container(settings: dict, *, detected: float | None):
    c = MagicMock()
    c.site_config = SiteConfig(initial_config=settings)
    c.gpu_registry = MagicMock()
    c.gpu_registry.total_vram_gb = AsyncMock(return_value=detected)
    return c


@pytest.mark.asyncio
async def test_auto_uses_detected_pool():
    container = _container({"gpu_vram_total_gb": "auto"}, detected=55.8)
    with patch("services.container_registry.get_container", return_value=container):
        total, reserve, _kv = await dispatcher._budget_inputs({})
    assert total == pytest.approx(55.8)
    assert reserve == 3.0


@pytest.mark.asyncio
async def test_explicit_number_overrides_and_skips_detection():
    container = _container({"gpu_vram_total_gb": "48"}, detected=55.8)
    with patch("services.container_registry.get_container", return_value=container):
        total, _reserve, _kv = await dispatcher._budget_inputs({})
    assert total == 48.0
    container.gpu_registry.total_vram_gb.assert_not_awaited()


@pytest.mark.asyncio
async def test_detection_fail_uses_fallback_and_emits_finding():
    container = _container(
        {"gpu_vram_total_gb": "auto", "gpu_vram_autodetect_fallback_gb": "24"},
        detected=None,
    )
    with patch("services.container_registry.get_container", return_value=container), \
         patch("utils.findings.emit_finding") as mock_emit:
        total, _reserve, _kv = await dispatcher._budget_inputs({})
    assert total == 24.0
    mock_emit.assert_called_once()
    assert mock_emit.call_args.kwargs["kind"] == "vram_autodetect_failed"
