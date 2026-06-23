"""Deterministic single-GPU VRAM footprint math (no I/O except read_model_arch).

Estimates a model's VRAM footprint = weights + KV cache + fixed overhead, and
answers "does it fit within (total - desktop_reserve)?" so the dispatch path can
clamp context before the NVIDIA driver would spill into system RAM (which freezes
the WDDM desktop). See docs/superpowers/specs/2026-06-22-single-gpu-vram-budget-stability-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from services.logger_config import get_logger

if TYPE_CHECKING:
    import httpx

logger = get_logger(__name__)

# Per-element KV-cache byte cost by Ollama OLLAMA_KV_CACHE_TYPE.
_KV_BYTES = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5}
_DEFAULT_OVERHEAD_GB = 1.5  # CUDA context + activations, conservative.

# read_model_arch cache — arch is immutable per model tag.
_ARCH_CACHE: dict[str, ModelArch] = {}


@dataclass(frozen=True)
class ModelArch:
    n_layers: int
    n_kv_heads: int
    head_dim: int
    weight_bytes: int


def kv_bytes_per_elem(kv_cache_type: str) -> float:
    """Map an Ollama KV cache dtype to bytes/element; unset/'' -> safe f16."""
    return _KV_BYTES.get(kv_cache_type or "f16", 2.0)


def estimate_kv_cache_gb(
    arch: ModelArch, num_ctx: int, kv_bytes_per_elem: float, batch: int = 1,
) -> float:
    """KV cache = 2 (K+V) * layers * kv_heads * head_dim * ctx * batch * bytes."""
    elems = 2 * arch.n_layers * arch.n_kv_heads * arch.head_dim * num_ctx * batch
    return (elems * kv_bytes_per_elem) / (1024 ** 3)


def estimate_model_vram_gb(
    arch: ModelArch, kv_cache_gb: float, overhead_gb: float = _DEFAULT_OVERHEAD_GB,
) -> float:
    return arch.weight_bytes / (1024 ** 3) + kv_cache_gb + overhead_gb


def fits(
    footprint_gb: float, total_gb: float, desktop_reserve_gb: float,
) -> tuple[bool, float]:
    """Return (fits, headroom_gb). headroom is negative (the deficit) when over."""
    budget = total_gb - desktop_reserve_gb
    headroom = budget - footprint_gb
    return headroom >= 0, headroom


def max_safe_num_ctx(
    arch: ModelArch, total_gb: float, desktop_reserve_gb: float,
    kv_bytes_per_elem: float,
) -> int:
    """Largest num_ctx whose footprint still fits the budget (0 if weights alone
    already exceed it). Closed-form: solve fits() for num_ctx, floor to a
    256-token multiple."""
    budget = total_gb - desktop_reserve_gb
    non_kv = arch.weight_bytes / (1024 ** 3) + _DEFAULT_OVERHEAD_GB
    kv_budget_gb = budget - non_kv
    if kv_budget_gb <= 0:
        return 0
    per_ctx_gb = estimate_kv_cache_gb(arch, num_ctx=1, kv_bytes_per_elem=kv_bytes_per_elem)
    if per_ctx_gb <= 0:
        return 0
    raw = int(kv_budget_gb / per_ctx_gb)
    return max(0, (raw // 256) * 256)


async def read_model_arch(
    model: str, base_url: str, client: httpx.AsyncClient,
) -> ModelArch | None:
    """Read n_layers/n_kv_heads/head_dim/weight_bytes from Ollama /api/show.

    Cached per model tag. Returns None (caller fails open with a finding) when
    /api/show is unreachable or the model_info keys are absent. ``client`` is a
    shared httpx.AsyncClient.
    """
    if model in _ARCH_CACHE:
        return _ARCH_CACHE[model]
    tag = model.split("/", 1)[-1]  # strip any "ollama/" prefix
    try:
        resp = await client.post(f"{base_url}/api/show", json={"model": tag}, timeout=10)
        resp.raise_for_status()
        info = resp.json().get("model_info", {}) or {}
    except Exception as exc:
        logger.warning("[vram_budget] /api/show failed for %s: %s", tag, exc)
        return None
    # Ollama model_info keys are architecture-prefixed, e.g. "gemma3.block_count".
    def _find(suffix: str) -> int | None:
        for k, v in info.items():
            if k.endswith(suffix) and isinstance(v, int):
                return v
        return None
    n_layers = _find(".block_count")
    n_kv_heads = _find(".attention.head_count_kv") or _find(".attention.head_count")
    emb = _find(".embedding_length")
    n_heads = _find(".attention.head_count")
    head_dim = (emb // n_heads) if (emb and n_heads) else None
    size_bytes = info.get("size") or 0
    if not (n_layers and n_kv_heads and head_dim):
        logger.warning("[vram_budget] incomplete model_info for %s: %s", tag, list(info)[:8])
        return None
    arch = ModelArch(n_layers, n_kv_heads, head_dim, int(size_bytes))
    _ARCH_CACHE[model] = arch
    return arch
