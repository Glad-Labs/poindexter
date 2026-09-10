"""Live free-VRAM read for the render GPU (``pipeline_gpu_index``) via Prometheus.

Why this exists (2026-07-12 desktop-lockup fix): the Wan 2.2 TI2V-5B render
loads ~24 GB onto ``pipeline_gpu_index`` — the RTX 5090 that also drives the
desktop. When stale WSL2/Docker GPU retention + the desktop already hold
~18 GB, the render's 24 GB pushes past the card's 32 GB → CUDA OOM + WDDM
spill to shared system RAM → the desktop freezes. The media dispatch gate
uses this reading to defer a render unless the card has room.

Kept separate from ``gpu_scheduler._query_prometheus_scalar`` (a bound method
with finding side-effects) so the gate can read free VRAM as a pure, testable
function. Reuses the same instant-query shape and the same
``gpu_metrics_prometheus_url`` seam.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_PROM_URL = "http://prometheus:9090"


def _prometheus_url(site_config: Any) -> str:
    return (
        site_config.get("gpu_metrics_prometheus_url", _DEFAULT_PROM_URL)
        or _DEFAULT_PROM_URL
    ).rstrip("/")


async def render_gpu_free_vram_gb(
    site_config: Any,
    *,
    http_client_factory: Any = None,
) -> float | None:
    """Free VRAM (GB) on ``pipeline_gpu_index``, or ``None`` when unreadable.

    ``None`` means "the reading is unavailable" (Prometheus unreachable, no
    recent scrape, or a malformed response) — the caller treats that as
    fail-closed (defer the render) rather than blindly rendering. Computed as
    ``nvidia_gpu_memory_total_mib - nvidia_gpu_memory_used_mib`` for the render
    GPU, divided by 1024.
    """
    if site_config is None:
        return None

    try:
        idx = int(site_config.get("pipeline_gpu_index", "0") or "0")
    except (TypeError, ValueError):
        idx = 0

    expr = (
        f'nvidia_gpu_memory_total_mib{{gpu="{idx}"}}'
        f' - nvidia_gpu_memory_used_mib{{gpu="{idx}"}}'
    )
    factory = http_client_factory or httpx.AsyncClient
    url = f"{_prometheus_url(site_config)}/api/v1/query"

    try:
        async with factory(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(url, params={"query": expr})
            resp.raise_for_status()
            result = (resp.json().get("data", {}) or {}).get("result", []) or []
            if not result:
                logger.warning(
                    "[render_vram] no series for gpu=%s (no recent nvidia-smi "
                    "scrape) — reading unavailable",
                    idx,
                )
                return None
            free_mib = float(result[0]["value"][1])
            return free_mib / 1024.0
    except Exception as exc:  # noqa: BLE001 — unreadable IS the signal; caller fails closed
        logger.warning(
            "[render_vram] Prometheus read failed (%s): %s: %s",
            url, type(exc).__name__, exc,
        )
        return None


__all__ = ["render_gpu_free_vram_gb"]
