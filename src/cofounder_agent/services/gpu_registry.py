"""Auto-detect total GPU VRAM across the box.

Sums the nvidia-smi exporter's per-GPU ``nvidia_gpu_memory_total_mib`` via
Prometheus (the dispatcher runs in a GPU-less container and can't call
nvidia-smi directly — it reads totals through the same telemetry path the GPU
scheduler uses for util/power). Total VRAM is a static hardware constant for a
process's lifetime, so the first successful detection is memoized permanently;
while detection has not yet succeeded the cache stays empty and each call
retries, so a startup Prometheus blip self-heals on a later call.
"""
from __future__ import annotations

import logging

import httpx

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

_MIB_PER_GB = 1024.0
_DEFAULT_PROM_URL = "http://prometheus:9090"
_PROM_TIMEOUT_SEC = 5.0
_VRAM_TOTAL_QUERY = "sum(nvidia_gpu_memory_total_mib)"


class GPURegistry:
    """Detects + memoizes the total VRAM pool (GB) across all GPUs."""

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config
        self._cached_total_gb: float | None = None

    async def total_vram_gb(self) -> float | None:
        """Total VRAM across all GPUs in GB, or None if not yet detectable.

        Cached permanently after the first success; retries while still None.
        """
        if self._cached_total_gb is not None:
            return self._cached_total_gb
        detected = await self._detect()
        if detected is not None:
            self._cached_total_gb = detected
        return detected

    def _prometheus_url(self) -> str:
        return self._site_config.get("gpu_metrics_prometheus_url", "") or _DEFAULT_PROM_URL

    async def _detect(self) -> float | None:
        url = f"{self._prometheus_url()}/api/v1/query"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url, params={"query": _VRAM_TOTAL_QUERY}, timeout=_PROM_TIMEOUT_SEC
                )
            if resp.status_code != 200:
                logger.warning(
                    "[gpu_registry] Prometheus HTTP %s reading total VRAM", resp.status_code
                )
                return None
            result = (resp.json().get("data") or {}).get("result") or []
            if not result:
                logger.debug("[gpu_registry] no nvidia_gpu_memory_total_mib series yet")
                return None
            total_mib = float(result[0]["value"][1])
            if total_mib <= 0:
                return None
            return total_mib / _MIB_PER_GB
        except Exception as exc:  # detection is best-effort; caller falls back
            logger.warning(
                "[gpu_registry] VRAM detect failed: %s: %s", type(exc).__name__, exc
            )
            return None
