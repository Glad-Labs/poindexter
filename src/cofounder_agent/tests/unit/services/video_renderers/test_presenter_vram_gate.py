"""The presenter VRAM gate must admit a render the card can actually do.

Measured on the 5090, 2026-09-21, sampling nvidia-smi through real S2V renders
of the production graph:

    1 chunk  -> 20.2 GB attributable
    3 chunks -> 20.5 GB   (~+0.17 GB per extra chunk; chunks chain latents)

At the `video_comfyui_s2v_max_chunks` ceiling of 7 that is ~21.2 GB.

The gate was 26 GB, and the recorded refusals read "only 25.6 GB free" and
"only 25.9 GB free" — the presenter was blocked by 100-400 MB on a card with
ample room. The reclaim ladder in front of it was already correct; only the
number was wrong, which is why fixing the reclaim did not make presenter
shots appear.
"""
from __future__ import annotations

import pytest

from poindexter.services.settings_defaults import DEFAULTS
from poindexter.services.video_renderers.shot_list_renderer import (
    _PRESENTER_MIN_FREE_VRAM_GB,
)

MEASURED_ONE_CHUNK_GB = 20.2
PER_EXTRA_CHUNK_GB = 0.17
# The refusals that motivated this, in GB usable.
OBSERVED_REFUSALS_GB = (25.6, 25.9)


def _worst_case_gb() -> float:
    max_chunks = int(DEFAULTS.get("video_comfyui_s2v_max_chunks", 7))
    return MEASURED_ONE_CHUNK_GB + PER_EXTRA_CHUNK_GB * max(0, max_chunks - 1)


def test_gate_covers_the_worst_case_chunk_count():
    """Never admit a render that would OOM at the configured chunk ceiling."""
    assert _PRESENTER_MIN_FREE_VRAM_GB >= _worst_case_gb(), (
        f"gate {_PRESENTER_MIN_FREE_VRAM_GB} GB is below the measured "
        f"{_worst_case_gb():.1f} GB needed at max chunks — a render could OOM"
    )


def test_gate_is_not_needlessly_conservative():
    """…but not so high that a capable card is refused, which is the bug."""
    assert _PRESENTER_MIN_FREE_VRAM_GB <= _worst_case_gb() + 3.0, (
        f"gate {_PRESENTER_MIN_FREE_VRAM_GB} GB exceeds the measured need "
        f"({_worst_case_gb():.1f} GB) by more than 3 GB of headroom"
    )


@pytest.mark.parametrize("observed", OBSERVED_REFUSALS_GB)
def test_the_real_world_refusals_would_now_pass(observed):
    """Each recorded presenter_render_fallback had enough VRAM all along."""
    assert observed > _PRESENTER_MIN_FREE_VRAM_GB
    assert observed > _worst_case_gb()


def test_seeded_default_and_code_fallback_agree():
    """A settings read that fails must not change the verdict."""
    assert float(DEFAULTS["video_presenter_min_free_vram_gb"]) == pytest.approx(
        _PRESENTER_MIN_FREE_VRAM_GB)


def test_old_threshold_would_fail_these_checks():
    """Guards the guard — 26 must actually be caught, or this proves nothing."""
    assert 26.0 > _worst_case_gb() + 3.0
    assert all(obs < 26.0 for obs in OBSERVED_REFUSALS_GB)
