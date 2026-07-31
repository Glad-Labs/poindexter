"""Calibrate the VRAM estimator against MEASURED footprints (poindexter#942).

The estimator's job is to answer "will loading this model at this context spill
into system RAM?" It was answering with two large errors that happened to
cancel: ``weight_bytes`` was always 0 (read from a ``/api/show`` field that does
not exist), and KV was charged at full context on every layer, ignoring sliding
windows. Net effect for gemma-4-31B at 16384: estimate 21.19 GB vs actual
~18.2 GB — plausible-looking, and arrived at by adding ~+10 GB of phantom KV to
~-17 GB of missing weights.

The trap that made this urgent: fixing weights ALONE pushes gemma to ~38 GB
against a 29 GB budget, so the clamp would start cutting the writer's context
for real. Both halves have to be right at once, which is what these tests pin.

Ground truth was measured on the operator box via Ollama ``/api/ps``
``size_vram`` with the GPU otherwise idle and full offload confirmed
(``size_vram == size``). The numbers are hardware-independent — they are
properties of the model files, not the card — so they are safe to assert on in
CI even though CI has no GPU.

These tests deliberately assert a BAND, not a point. The estimator is meant to
be conservative (never under-estimate, or the guard lets a spill through) but
not wildly so (or it clamps contexts that were fine). Asserting exactness would
break on any reasonable refinement; asserting only "> measured" would let the
500× regression back in.
"""

from __future__ import annotations

import pytest

from services.vram_budget import (
    ModelArch,
    effective_kv_ctx,
    estimate_kv_cache_gb,
    estimate_model_vram_gb,
    fits,
    kv_bytes_per_elem,
    max_safe_num_ctx,
)

_GB = 1024 ** 3
_F16 = kv_bytes_per_elem("f16")

# Geometry from Ollama /api/show model_info; weights from /api/tags; measured
# footprints from /api/ps size_vram. See the module docstring for provenance.
GEMMA = ModelArch(
    n_layers=60, n_kv_heads=32, head_dim=168,
    weight_bytes=int(17.22 * _GB), sliding_window=1024,
)
PHI4 = ModelArch(
    n_layers=40, n_kv_heads=10, head_dim=128,
    weight_bytes=int(8.43 * _GB), sliding_window=131072,
)
QWEN_VL = ModelArch(
    n_layers=48, n_kv_heads=4, head_dim=64,
    weight_bytes=int(18.25 * _GB), sliding_window=0,
)

# (arch, num_ctx, measured_total_vram_gb)
MEASURED = [
    pytest.param(GEMMA, 8192, 18.15, id="gemma-8k"),
    pytest.param(GEMMA, 16384, 18.17, id="gemma-16k"),
    pytest.param(PHI4, 8192, 9.98, id="phi4-8k"),
    pytest.param(PHI4, 16384, 11.56, id="phi4-16k"),
    pytest.param(QWEN_VL, 8192, 18.31, id="qwen3vl-8k"),
]


@pytest.mark.parametrize(("arch", "num_ctx", "measured_gb"), MEASURED)
def test_estimate_is_conservative_but_not_absurd(arch, num_ctx, measured_gb):
    """Estimate must sit in [measured, measured + 3 GB].

    Lower bound: under-estimating is the dangerous direction — the guard would
    wave through a load that spills to system RAM and freezes the desktop.
    Upper bound: the pre-fix estimator cleared measured+3 by a mile on gemma
    (21.19 vs 18.15 came from ~+10 GB of phantom KV); 3 GB is roughly the fixed
    overhead constant plus the documented GQA/window slack, so this fails if
    either error class comes back.
    """
    kv = estimate_kv_cache_gb(arch, num_ctx, _F16)
    estimate = estimate_model_vram_gb(arch, kv)
    assert estimate >= measured_gb, (
        f"UNDER-estimate: {estimate:.2f} GB < measured {measured_gb:.2f} GB — "
        "the guard would allow a load that spills into system RAM"
    )
    assert estimate <= measured_gb + 3.0, (
        f"OVER-estimate: {estimate:.2f} GB vs measured {measured_gb:.2f} GB — "
        "this is how the clamp starts cutting contexts that were fine"
    )


def test_sliding_window_kv_barely_grows_across_the_8k_16k_step():
    """gemma measured +0.02 GB for 8k->16k; the pre-fix math said +9.84 GB.

    This is the single number that most exposed the old model, so pin it
    directly rather than relying on the band check above.
    """
    kv8 = estimate_kv_cache_gb(GEMMA, 8192, _F16)
    kv16 = estimate_kv_cache_gb(GEMMA, 16384, _F16)
    assert kv16 - kv8 == pytest.approx(0.0, abs=0.01), (
        f"sliding-window KV must saturate at the window: got +{kv16 - kv8:.2f} GB"
    )


def test_dense_model_kv_still_doubles_across_the_8k_16k_step():
    """The window fix must not flatten DENSE models — phi4 really does pay.

    Measured 9.98 -> 11.56 GB (+1.58). If a refactor made every model look
    windowed, this catches it.
    """
    kv8 = estimate_kv_cache_gb(PHI4, 8192, _F16)
    kv16 = estimate_kv_cache_gb(PHI4, 16384, _F16)
    assert kv16 == pytest.approx(2 * kv8, rel=0.01)
    assert (kv16 - kv8) == pytest.approx(1.58, abs=0.10)


def test_effective_ctx_clamps_only_windowed_models():
    assert effective_kv_ctx(GEMMA, 16384) == 1024          # windowed
    assert effective_kv_ctx(PHI4, 16384) == 16384          # window > ctx
    assert effective_kv_ctx(PHI4, 200000) == 131072        # window < ctx
    assert effective_kv_ctx(QWEN_VL, 16384) == 16384       # dense


def test_weights_are_the_dominant_term():
    """Regression guard for the actual #942 bug: weight_bytes == 0.

    With weights zeroed, every footprint collapses to KV + overhead and the
    guard reads as "plenty of room" for an 18 GB model.
    """
    for arch in (GEMMA, PHI4, QWEN_VL):
        kv = estimate_kv_cache_gb(arch, 8192, _F16)
        total = estimate_model_vram_gb(arch, kv)
        weights_gb = arch.weight_bytes / _GB
        assert weights_gb > 0, "weight_bytes must not be zero"
        assert weights_gb / total > 0.5, (
            "weights should dominate the footprint for these models; a "
            f"{weights_gb:.1f}/{total:.1f} split means KV is being over-charged"
        )


def test_writer_at_16k_still_fits_the_5090_budget():
    """The exact scenario the half-fix would have broken.

    gemma at 16384 on a 32 GB card with a 3 GB desktop reserve must fit —
    otherwise the clamp silently undoes the per-phase num_ctx raises
    (stack#2895 / #2910). Weights-only-fixed math gives ~38 GB and fails here.
    """
    kv = estimate_kv_cache_gb(GEMMA, 16384, _F16)
    footprint = estimate_model_vram_gb(GEMMA, kv)
    ok, headroom = fits(footprint, total_gb=32.0, desktop_reserve_gb=3.0)
    assert ok, (
        f"writer at 16384 estimated {footprint:.2f} GB, does not fit 29 GB "
        f"budget (headroom {headroom:.2f}) — the clamp would cut article length"
    )


def test_max_safe_ctx_is_unbounded_once_a_window_saturates():
    """A windowed model that fits at full window fits at ANY context.

    The old closed-form divided by a per-token cost that stops applying past the
    window, so it would clamp gemma to a finite context for no reason.
    """
    safe = max_safe_num_ctx(GEMMA, 32.0, 3.0, _F16)
    assert safe >= 1 << 20


def test_max_safe_ctx_stays_finite_for_a_dense_model():
    """phi4 genuinely pays per token, so the budget really does bound it."""
    safe = max_safe_num_ctx(PHI4, 32.0, 3.0, _F16)
    assert 0 < safe < 1 << 20
    assert safe % 256 == 0
    # The returned ctx must actually fit when re-estimated.
    kv = estimate_kv_cache_gb(PHI4, safe, _F16)
    ok, _ = fits(estimate_model_vram_gb(PHI4, kv), 32.0, 3.0)
    assert ok


def test_max_safe_ctx_is_zero_when_weights_alone_bust_the_budget():
    ok_arch = ModelArch(
        n_layers=60, n_kv_heads=32, head_dim=168,
        weight_bytes=int(40 * _GB), sliding_window=1024,
    )
    assert max_safe_num_ctx(ok_arch, 32.0, 3.0, _F16) == 0
