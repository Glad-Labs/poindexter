"""The word budget must not outrun the seconds cap at the REAL speaking rate.

Eight of 38 rendered shorts breached `video_short_max_seconds` (60 s), the
worst at 72.5 s, while every one passed the word-count check — because the cap
is enforced in WORDS via an assumed 2.5 words/second and nothing downstream
measures the rendered audio. Chatterbox actually runs 2.11 wps (whisper over 9
shorts, 1085 words / 515 s, 2026-09-21), so 60 x 2.5 = 150 words speaks for
71 s and the cap could never bite.
"""
from __future__ import annotations

import pytest

from poindexter.services.site_config import SiteConfig

# Measured, and the reason this file exists. If the voice changes, re-measure
# and move BOTH this and the default together.
MEASURED_WPS = 2.109
TOLERANCE = 1.02  # allow 2% over the cap before calling it a breach


def _shotlist_wps(cfg):
    from poindexter.modules.content.stages.generate_video_shot_list import (
        _words_per_second,
    )
    return _words_per_second(cfg)


def _scripts_wps(cfg):
    from poindexter.modules.content.stages.generate_media_scripts import (
        _words_per_second,
    )
    return _words_per_second(cfg)


def test_default_word_budget_fits_the_seconds_cap_at_the_real_rate():
    """60 s x default wps, spoken at the measured rate, must stay under 60 s."""
    cfg = SiteConfig(initial_config={})
    for wps in (_shotlist_wps(cfg), _scripts_wps(cfg)):
        words = round(60 * wps)
        spoken = words / MEASURED_WPS
        assert spoken <= 60 * TOLERANCE, (
            f"{words} words at {MEASURED_WPS} wps speaks for {spoken:.1f}s, "
            f"over the 60s cap — the budget rate {wps} is too optimistic"
        )


def test_the_old_assumed_rate_would_fail_this_test():
    """Guards the guard: 2.5 must actually be caught, or the test proves nothing."""
    words = round(60 * 2.5)
    assert words / MEASURED_WPS > 60 * TOLERANCE


def test_both_stages_agree_on_the_rate():
    """A short's word target and its shot-list clamp must share one rate (#867)."""
    cfg = SiteConfig(initial_config={})
    assert _shotlist_wps(cfg) == _scripts_wps(cfg)


def test_setting_overrides_the_default_in_both_stages():
    cfg = SiteConfig(initial_config={"media_narration_words_per_second": "1.8"})
    assert _shotlist_wps(cfg) == pytest.approx(1.8)
    assert _scripts_wps(cfg) == pytest.approx(1.8)


@pytest.mark.parametrize("bad", ["0", "-1", "", "abc", None])
def test_a_nonsense_rate_falls_back_rather_than_dividing_by_zero(bad):
    """<=0 would emit a zero-word script or divide by zero — reject, don't honour."""
    cfg = SiteConfig(initial_config={"media_narration_words_per_second": bad})
    for wps in (_shotlist_wps(cfg), _scripts_wps(cfg)):
        assert wps > 0


def test_short_duration_estimate_uses_the_supplied_rate():
    from poindexter.modules.content.stages.generate_video_shot_list import (
        _estimate_short_duration,
    )
    script = " ".join(["word"] * 100)
    slow = _estimate_short_duration(script, 120.0, words_per_second=2.0)
    fast = _estimate_short_duration(script, 120.0, words_per_second=4.0)
    assert slow == pytest.approx(50.0)
    assert fast == pytest.approx(25.0)
