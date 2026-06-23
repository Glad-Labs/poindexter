"""Unit tests for services/vram_budget.py — the deterministic single-GPU
VRAM footprint calculator.

The tests assert *relationships* (linearity in context, halving with q8 KV,
fit/headroom arithmetic) rather than absolute GB, so they don't depend on the
illustrative ModelArch shape below — the real arch is read live off
Ollama /api/show by read_model_arch at runtime.
"""

import pytest

from services.vram_budget import (
    ModelArch,
    estimate_kv_cache_gb,
    estimate_model_vram_gb,
    fits,
    kv_bytes_per_elem,
    max_safe_num_ctx,
)

# gemma-4-31B-class shape (illustrative): 48 layers, 8 KV heads, head_dim 128.
_ARCH = ModelArch(n_layers=48, n_kv_heads=8, head_dim=128, weight_bytes=18 * 1024**3)


def test_kv_bytes_per_elem_maps_dtype():
    assert kv_bytes_per_elem("f16") == 2.0
    assert kv_bytes_per_elem("q8_0") == 1.0
    assert kv_bytes_per_elem("q4_0") == 0.5
    assert kv_bytes_per_elem("") == 2.0  # unset sentinel -> safe f16


def test_kv_cache_grows_linearly_with_context():
    a = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=1.0)
    b = estimate_kv_cache_gb(_ARCH, num_ctx=16384, kv_bytes_per_elem=1.0)
    assert b == pytest.approx(2 * a, rel=1e-6)


def test_q8_halves_kv_vs_f16():
    f16 = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=2.0)
    q8 = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=1.0)
    assert q8 == pytest.approx(f16 / 2, rel=1e-6)


def test_fits_reports_headroom():
    ok, headroom = fits(footprint_gb=25.0, total_gb=32.0, desktop_reserve_gb=3.0)
    assert ok is True
    assert headroom == pytest.approx(4.0)
    bad, deficit = fits(footprint_gb=31.0, total_gb=32.0, desktop_reserve_gb=3.0)
    assert bad is False
    assert deficit == pytest.approx(-2.0)


def test_max_safe_num_ctx_fits_within_budget():
    n = max_safe_num_ctx(_ARCH, total_gb=32.0, desktop_reserve_gb=3.0, kv_bytes_per_elem=1.0)
    foot = estimate_model_vram_gb(_ARCH, estimate_kv_cache_gb(_ARCH, n, 1.0))
    ok, _ = fits(foot, 32.0, 3.0)
    assert ok is True
    assert n > 0
