"""Auto-detect GPU VRAM facts across the box (totals, per-card free, evictable).

Reads the nvidia-smi exporter's ``nvidia_gpu_*`` series via Prometheus (the
dispatcher runs in a GPU-less container and can't call nvidia-smi directly —
it reads through the same telemetry path the GPU scheduler uses for
util/power). Three read surfaces:

- :meth:`GPURegistry.total_vram_gb` — box-wide total. A static hardware
  constant for a process's lifetime, so the first success is memoized
  permanently; while detection has not yet succeeded each call retries, so a
  startup Prometheus blip self-heals on a later call.
- :meth:`GPURegistry.free_gb` — per-card free VRAM (total − used), memoized
  ≤15s (Prometheus itself scrapes every 15s, so a shorter TTL buys nothing).
  ``None`` on any failure — the admission fit gate fails OPEN on None
  (poindexter#914 P1).
- :meth:`GPURegistry.evictable_ollama_gb` — per-card VRAM held by the primary
  Ollama runner (the ``nvidia_gpu_process_memory_mib`` per-process series,
  filtered client-side by card index + process-name pattern). ``0.0`` on any
  failure or when the metric is absent — no phantom eviction credit. Per the
  design's per-card mandate this NEVER derives from Ollama ``/api/ps``, whose
  size field is a cross-card total that would overstate the reclaimable share
  on gpu0 whenever a model has spilled to the second card.
"""
from __future__ import annotations

import logging
import time

import httpx

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

_MIB_PER_GB = 1024.0
_DEFAULT_PROM_URL = "http://prometheus:9090"
_PROM_TIMEOUT_SEC = 5.0
_VRAM_TOTAL_QUERY = "sum(nvidia_gpu_memory_total_mib)"
_PROCESS_MEMORY_METRIC = "nvidia_gpu_process_memory_mib"
_FREE_MEMO_TTL_SEC = 15.0
_DEFAULT_EVICTABLE_PROCESS_PATTERN = "ollama"


class GPURegistry:
    """Detects + memoizes VRAM facts (total pool, per-card free, evictable)."""

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config
        self._cached_total_gb: float | None = None
        # Per-card free-VRAM memo: gpu_index -> (monotonic_ts, free_gb).
        self._free_memo: dict[int, tuple[float, float]] = {}

    async def total_vram_gb(self) -> float | None:
        """Total VRAM across all GPUs in GB, or None if not yet detectable.

        Cached permanently after the first success; retries while still None.
        """
        if self._cached_total_gb is not None:
            return self._cached_total_gb
        value = await self._instant_scalar(_VRAM_TOTAL_QUERY)
        if value is None or value <= 0:
            return None
        detected = value / _MIB_PER_GB
        self._cached_total_gb = detected
        return detected

    async def free_gb(self, gpu_index: int) -> float | None:
        """Free VRAM (GB) on one card, or None when telemetry is unavailable.

        Memoized for ≤15s per card. Feeds the admission fit gate's fail-open
        ``free_gpu0_gb`` input — a None here means "skip the fit check", never
        "assume empty" and never "assume full".
        """
        now = time.monotonic()
        hit = self._free_memo.get(gpu_index)
        if hit is not None and (now - hit[0]) <= _FREE_MEMO_TTL_SEC:
            return hit[1]
        query = (
            f'(nvidia_gpu_memory_total_mib{{gpu="{gpu_index}"}} '
            f'- nvidia_gpu_memory_used_mib{{gpu="{gpu_index}"}}) / {_MIB_PER_GB}'
        )
        value = await self._instant_scalar(query)
        if value is None:
            return None
        self._free_memo[gpu_index] = (now, value)
        return value

    async def evictable_ollama_gb(self, gpu_index: int) -> float:
        """VRAM (GB) the primary Ollama runner holds ON THIS CARD; 0.0 unknown.

        Sums the exporter's per-process rows (``nvidia_gpu_process_memory_mib``)
        whose ``gpu`` label matches the card and whose ``process`` label
        contains ``gpu_evictable_process_pattern`` (case-insensitive substring,
        default "ollama"). The metric is queried unfiltered and matched
        client-side so a model spilled across both cards contributes only its
        gpu0 share — the per-card mandate that rules out the ``/api/ps``
        cross-card total. Absent metric, empty result, or any failure → 0.0
        (conservative: admission then sees no eviction credit).
        """
        pattern = (
            self._site_config.get("gpu_evictable_process_pattern", "")
            or _DEFAULT_EVICTABLE_PROCESS_PATTERN
        ).lower()
        rows = await self._instant_query(_PROCESS_MEMORY_METRIC)
        if not rows:
            return 0.0
        total_mib = 0.0
        for row in rows:
            labels = row.get("metric") or {}
            if labels.get("gpu") != str(gpu_index):
                continue
            if pattern not in str(labels.get("process", "")).lower():
                continue
            try:
                total_mib += float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return total_mib / _MIB_PER_GB

    def _prometheus_url(self) -> str:
        return self._site_config.get("gpu_metrics_prometheus_url", "") or _DEFAULT_PROM_URL

    async def _instant_query(self, query: str) -> list[dict]:
        """Prometheus instant query → raw result list ([] on any failure)."""
        url = f"{self._prometheus_url()}/api/v1/query"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url, params={"query": query}, timeout=_PROM_TIMEOUT_SEC
                )
            if resp.status_code != 200:
                logger.warning(
                    "[gpu_registry] Prometheus HTTP %s for %r", resp.status_code, query
                )
                return []
            return (resp.json().get("data") or {}).get("result") or []
        except Exception as exc:  # detection is best-effort; callers fall back
            logger.warning(
                "[gpu_registry] Prometheus query failed (%r): %s: %s",
                query, type(exc).__name__, exc,
            )
            return []

    async def _instant_scalar(self, query: str) -> float | None:
        """First sample's value from an instant query, or None."""
        result = await self._instant_query(query)
        if not result:
            logger.debug("[gpu_registry] no series for %r yet", query)
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
