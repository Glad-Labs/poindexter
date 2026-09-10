"""Deterministic single-GPU VRAM footprint math (no I/O except read_model_arch).

Estimates a model's VRAM footprint = weights + KV cache + fixed overhead, and
answers "does it fit within (total - desktop_reserve)?" so the dispatch path can
clamp context before the NVIDIA driver would spill into system RAM (which freezes
the WDDM desktop). See docs/superpowers/specs/2026-06-22-single-gpu-vram-budget-stability-design.md.

**Calibration (poindexter#942, 2026-07-31).** The original model was wrong in two
large, opposite directions and only looked healthy because they cancelled:

- ``weight_bytes`` was read as ``model_info["size"]`` from ``/api/show``, which
  has no ``size`` key **anywhere** — so weights, the dominant term, were always
  ``0``. Now sourced from ``/api/tags``.
- KV was charged at full ``num_ctx`` on every layer, ignoring sliding-window
  attention. gemma-4-31B (``sliding_window=1024``) was over-charged ~500× on the
  8k→16k step: predicted +9.84 GB, measured **+0.02 GB**.

Fixing only the first would have pushed the writer's estimate to ~38 GB against a
29 GB budget and started clamping it for real, so both land together here.

Measured ground truth on the operator box (Ollama ``/api/ps`` ``size_vram``, GPU
otherwise idle, full offload) — these are the numbers ``tests/unit/services/
test_vram_budget_calibration.py`` pins against:

===================  =========  ===========  ============  ===============
model                weights    @8192        @16384        sliding_window
===================  =========  ===========  ============  ===============
gemma-4-31B-it-qat   17.22 GB   18.15 GB     18.17 GB      1024
phi4:14b              8.43 GB    9.98 GB     11.56 GB      131072 (≈none)
qwen3-vl:30b         18.25 GB   18.31 GB     --            (absent)
===================  =========  ===========  ============  ===============

The estimator is deliberately **conservative** (over-estimates by roughly the
fixed overhead constant) rather than exact. Two known sources of slack, both
safe-direction and both forced by metadata Ollama does not expose:

- gemma-4 publishes no ``attention.head_count_kv``, so ``n_kv_heads`` falls back
  to ``head_count`` (32) and over-counts KV by the GQA ratio.
- No arch publishes ``sliding_window_pattern``, so when a window is present we
  charge **every** layer at the window rather than splitting global vs local.
  Real gemma has a few global-attention layers whose KV does scale with context;
  charging all layers at the window slightly under-counts those while the
  head-count fallback over-counts everything, and the measured totals above
  confirm the net lands conservative.

Do not "tighten" this into an exact model without re-measuring — the lesson of
#942 is that computing this got it wrong by 500× and measuring got it right.
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

# Returned by max_safe_num_ctx when KV has saturated (a sliding-window model
# whose cache stops growing with context) and the footprint still fits: no
# context is unsafe, so the caller's clamp must be a no-op. Deliberately larger
# than any real num_ctx rather than unbounded, so arithmetic downstream stays
# finite.
_CTX_EFFECTIVELY_UNBOUNDED = 1 << 20

# read_model_arch cache — arch is immutable per model tag.
_ARCH_CACHE: dict[str, ModelArch] = {}


@dataclass(frozen=True)
class ModelArch:
    n_layers: int
    n_kv_heads: int
    head_dim: int
    weight_bytes: int
    # Sliding-window size in tokens; 0 means dense attention (KV scales with the
    # full context). Defaulted so existing constructions stay valid.
    sliding_window: int = 0


def kv_bytes_per_elem(kv_cache_type: str) -> float:
    """Map an Ollama KV cache dtype to bytes/element; unset/'' -> safe f16."""
    return _KV_BYTES.get(kv_cache_type or "f16", 2.0)


def effective_kv_ctx(arch: ModelArch, num_ctx: int) -> int:
    """Tokens actually held in the KV cache at ``num_ctx``.

    A sliding-window layer evicts beyond its window, so its cache stops growing
    once ``num_ctx`` passes ``sliding_window``. This is the whole reason gemma-4
    costs +0.02 GB for an 8k→16k bump while phi4 costs +1.58 GB: gemma's window
    is 1024, phi4's is 131072 (i.e. larger than any context we run, so dense).
    """
    if arch.sliding_window and arch.sliding_window > 0:
        return min(num_ctx, arch.sliding_window)
    return num_ctx


def estimate_kv_cache_gb(
    arch: ModelArch, num_ctx: int, kv_bytes_per_elem: float, batch: int = 1,
) -> float:
    """KV cache = 2 (K+V) * layers * kv_heads * head_dim * ctx * batch * bytes.

    ``ctx`` is the *effective* cached-token count, not the requested context —
    see :func:`effective_kv_ctx`.
    """
    ctx = effective_kv_ctx(arch, num_ctx)
    elems = 2 * arch.n_layers * arch.n_kv_heads * arch.head_dim * ctx * batch
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
    already exceed it).

    Two regimes, because KV is piecewise-linear in context once a sliding window
    is involved:

    - **Saturated** — the model has a window and its full-window KV fits. KV
      cannot grow further no matter the context, so every context is safe and we
      return :data:`_CTX_EFFECTIVELY_UNBOUNDED` (the caller's clamp no-ops).
      Without this branch the closed-form below would divide by a per-token cost
      that stops applying past the window and clamp a model that never needed it.
    - **Linear** — dense attention, or a window larger than the affordable
      context. Solve fits() for num_ctx and floor to a 256-token multiple, then
      cap at the window (past it the extra context buys no KV, so reporting more
      would overstate what was verified).
    """
    budget = total_gb - desktop_reserve_gb
    non_kv = arch.weight_bytes / (1024 ** 3) + _DEFAULT_OVERHEAD_GB
    kv_budget_gb = budget - non_kv
    if kv_budget_gb <= 0:
        return 0
    window = arch.sliding_window if arch.sliding_window > 0 else 0
    if window:
        # Cost at full saturation — if that fits, context is free from here on.
        saturated_gb = estimate_kv_cache_gb(
            arch, num_ctx=window, kv_bytes_per_elem=kv_bytes_per_elem,
        )
        if saturated_gb <= kv_budget_gb:
            return _CTX_EFFECTIVELY_UNBOUNDED
    # Per-token cost, measured below the window so effective_kv_ctx is identity.
    per_ctx_gb = estimate_kv_cache_gb(
        arch, num_ctx=1, kv_bytes_per_elem=kv_bytes_per_elem,
    )
    if per_ctx_gb <= 0:
        return 0
    raw = int(kv_budget_gb / per_ctx_gb)
    if window:
        raw = min(raw, window)
    return max(0, (raw // 256) * 256)


async def _read_weight_bytes(
    tag: str, base_url: str, client: httpx.AsyncClient,
) -> int | None:
    """On-disk size of ``tag`` in bytes, from Ollama ``/api/tags``.

    **Not** ``/api/show`` — that response carries ``modelfile`` / ``model_info`` /
    ``details`` and no size field at all, which is how ``weight_bytes`` silently
    stayed 0 (poindexter#942). ``/api/tags`` lists every local model with its
    size and needs no load.

    Returns None when the listing is unreachable or the tag is absent, which the
    caller turns into "no arch" so the guard fails open loudly rather than
    guarding with the dominant term missing.
    """
    try:
        resp = await client.get(f"{base_url}/api/tags", timeout=10)
        resp.raise_for_status()
        models = resp.json().get("models", []) or []
    except Exception as exc:
        logger.warning("[vram_budget] /api/tags failed for %s: %s", tag, exc)
        return None
    for entry in models:
        if str(entry.get("name", "")) == tag:
            size = entry.get("size")
            if isinstance(size, int) and size > 0:
                return size
    logger.warning(
        "[vram_budget] %s absent from /api/tags (%d models listed) — cannot size "
        "weights", tag, len(models),
    )
    return None


async def read_model_arch(
    model: str, base_url: str, client: httpx.AsyncClient,
) -> ModelArch | None:
    """Read layer/head geometry + sliding window + weight size for ``model``.

    Geometry and the window come from ``/api/show``'s ``model_info``; the weight
    size comes from ``/api/tags`` (see :func:`_read_weight_bytes`). Cached per
    model tag. Returns None — caller fails open with a finding — when either
    source is unusable, because a footprint missing its dominant term is worse
    than no guard: it reads as "plenty of room" and clamps nothing.
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
    # gemma-4 publishes no head_count_kv; the head_count fallback over-counts KV
    # by the GQA ratio, which is the safe direction for a guard. See module docs.
    n_kv_heads = _find(".attention.head_count_kv") or _find(".attention.head_count")
    emb = _find(".embedding_length")
    n_heads = _find(".attention.head_count")
    head_dim = (emb // n_heads) if (emb and n_heads) else None
    # 0/absent -> dense. A window >= any context we run (phi4 ships 131072) is
    # dense in practice too, and effective_kv_ctx's min() handles that naturally.
    sliding_window = _find(".attention.sliding_window") or 0
    if not (n_layers and n_kv_heads and head_dim):
        logger.warning("[vram_budget] incomplete model_info for %s: %s", tag, list(info)[:8])
        return None
    weight_bytes = await _read_weight_bytes(tag, base_url, client)
    if weight_bytes is None:
        return None
    arch = ModelArch(
        n_layers, n_kv_heads, head_dim, weight_bytes, sliding_window,
    )
    _ARCH_CACHE[model] = arch
    return arch
