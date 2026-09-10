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
# Comma-separated substrings. "llama-server" first because that is what stock
# Ollama actually names its runner on Linux (/usr/local/lib/ollama/llama-server);
# the bare "ollama" that shipped as the default matched nothing on this host, so
# the eviction credit was silently 0.0 for the entire P1 soak.
_DEFAULT_EVICTABLE_PROCESS_PATTERN = "llama-server,ollama"
# Per-card VRAM that `nvidia_gpu_process_memory_mib` may leave unattributed
# before the process list is treated as stale (poindexter#914). Driver/context
# overhead is never charged to a PID, so a small gap is normal; a multi-GB gap
# means a loaded model has not been scraped yet. Sized well under the smallest
# model in the fleet (~3.5GB) and well above observed overhead (~0.3GB).
_DEFAULT_UNATTRIBUTED_TOLERANCE_GB = 2.0


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

    async def evictable_ollama_gb(self, gpu_index: int) -> float | None:
        """VRAM (GB) the evictable LLM runner holds ON THIS CARD.

        Returns ``None`` when the answer is UNKNOWN and a float when it is
        known — including a genuine ``0.0`` for "nothing evictable is loaded".
        Mirrors :meth:`free_gb`'s contract so admission can fail open on
        unknown instead of treating ignorance as absence.

        Sums the exporter's per-process rows (``nvidia_gpu_process_memory_mib``)
        whose ``gpu`` label matches the card and whose ``process`` label
        contains ANY entry of ``gpu_evictable_process_pattern`` (comma-separated,
        case-insensitive substring). The metric is queried unfiltered and matched
        client-side so a model spilled across both cards contributes only its
        gpu0 share — the per-card mandate that rules out the ``/api/ps``
        cross-card total.

        **Why 0.0-means-unknown had to die (poindexter#914, 2026-07-31).** This
        returned 0.0 for every failure mode, and 0.0 is also the legitimate
        "nothing is loaded" answer, so the two were indistinguishable. Measured
        on prod: Prometheus was missing a live ``llama-server`` holding ~21 GB on
        gpu0 that ``nvidia-smi`` saw at the same instant — pure scrape lag (10s
        exporter refresh + 30s Prometheus interval ≈ 40s worst case, against a
        45s rail budget). Admission read the 0.0 as "nothing to evict", answered
        ``no_fit``, and the QA rail degraded and **passed open** — so the
        judgement silently did not happen. Over one 66-minute window that was
        105 rejections and a 0% success rate on ``qa_deepeval_judge``.

        Rejecting on incomplete telemetry is the wrong default here: a rejected
        rail means NO QA at all, while an over-granted one merely thrashes.

        **Staleness detection.** An empty/failed query is unknown outright. When
        rows do come back, the per-process sum for this card is corroborated
        against ``nvidia_gpu_memory_used_mib`` — a large unattributed remainder
        means the process list has not caught up with a load that already
        happened, so the honest answer is ``None``, not ``0.0``. Small gaps are
        expected (driver/context overhead is never attributed to a PID), hence
        the tolerance rather than an equality check.

        **Why a LIST (2026-07-29).** The setting shipped as a single substring
        defaulting to ``"ollama"`` — which matched nothing on this host, so the
        credit was silently 0.0 on every card and the fit gate ran blind for the
        whole P1 soak. Ollama does not name its runner "ollama": the real
        process is ``/usr/local/lib/ollama/llama-server`` → label
        ``llama-server``, and ``"ollama" not in "llama-server"``. Matching a list
        keeps that from recurring the next time a vendor renames a binary, and
        single-value configs still work unchanged.
        """
        raw = (
            self._site_config.get("gpu_evictable_process_pattern", "")
            or _DEFAULT_EVICTABLE_PROCESS_PATTERN
        )
        patterns = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
        if not patterns:
            # Operator explicitly cleared the pattern: nothing is DECLARED
            # evictable. That is a real answer, not missing telemetry.
            return 0.0
        rows = await self._instant_query(_PROCESS_MEMORY_METRIC)
        if not rows:
            return None  # metric absent / query failed — unknown, not zero

        matched_mib = 0.0
        attributed_mib = 0.0
        saw_card = False
        for row in rows:
            labels = row.get("metric") or {}
            if labels.get("gpu") != str(gpu_index):
                continue
            saw_card = True
            try:
                value_mib = float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            attributed_mib += value_mib
            process = str(labels.get("process", "")).lower()
            if any(p in process for p in patterns):
                matched_mib += value_mib

        if not saw_card:
            # The metric exists but carries no rows for THIS card — the
            # exporter may not have enumerated it yet. Unknown.
            return None

        used_mib = await self._instant_scalar(
            f'nvidia_gpu_memory_used_mib{{gpu="{gpu_index}"}}'
        )
        if used_mib is None:
            # Can't corroborate. The process list alone cannot distinguish
            # "nothing loaded" from "load not scraped yet".
            return None
        tolerance_gb = self._site_config.get_float(
            "gpu_evictable_unattributed_tolerance_gb",
            _DEFAULT_UNATTRIBUTED_TOLERANCE_GB,
        )
        unattributed_gb = (used_mib - attributed_mib) / _MIB_PER_GB
        if unattributed_gb > tolerance_gb:
            logger.debug(
                "[gpu_registry] gpu%d has %.1fGB unattributed VRAM (used=%.1fGB, "
                "per-process=%.1fGB) — process list is stale, eviction credit unknown",
                gpu_index, unattributed_gb, used_mib / _MIB_PER_GB,
                attributed_mib / _MIB_PER_GB,
            )
            return None

        return matched_mib / _MIB_PER_GB

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
