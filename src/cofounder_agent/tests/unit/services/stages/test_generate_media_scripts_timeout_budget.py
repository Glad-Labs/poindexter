"""generate_media_scripts' node timeout must contain its own work.

The static 300 s could not: two LLM calls plus the ambient bed, whose
stable-audio render cold-loads its model (~125 s) whenever the GPU scheduler
has hard-unloaded the sidecar. atom_runs (30 days): ok runs 210-280 s, and the
stage errored at exactly 300 s on 08-27, 09-06, 09-08 and twice on 09-15 — each
time no podcast_script reached the director and the post shipped with no shot
list (poindexter#1001).

The ambient bed now also waits, bounded by gpu_sched_media_max_wait_s, for the
render-GPU lock before it renders. That wait sits in front of the render the
floor protects, so it is a term of the floor, read from the same setting the
lock's budget is.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from poindexter.modules.content.stages import generate_media_scripts as gms
from poindexter.services.site_config import SiteConfig


class _SC:
    def __init__(self, **v):
        self.v = v

    def get_int(self, key, default=None):
        return int(self.v.get(key, default))

    def get(self, key, default=None):
        return self.v.get(key, default)


def test_default_floor_is_720_seconds():
    assert gms.resolve_stage_timeout_seconds(_SC()) == 2 * 120 + 180 + 150 + 120 + 30 == 720


def test_floor_tracks_the_audio_render_timeout_setting():
    assert gms.resolve_stage_timeout_seconds(_SC(audio_render_timeout_seconds=300)) == 840


def test_every_term_is_a_setting():
    sc = _SC(media_scripts_llm_call_budget_seconds=90, media_scripts_llm_calls=3,
             audio_render_timeout_seconds=100, audio_gen_cold_load_allowance_seconds=0,
             gpu_sched_media_max_wait_s="45", media_scripts_stage_overhead_seconds=10)
    assert gms.resolve_stage_timeout_seconds(sc) == 3 * 90 + 100 + 0 + 45 + 10


def test_no_site_config_falls_back_to_defaults():
    assert gms.resolve_stage_timeout_seconds(None) == 720


def test_stage_exposes_the_hook_the_runner_floors_on():
    stage = gms.GenerateMediaScriptsStage()
    assert stage.timeout_seconds == 300
    assert stage.resolve_timeout_seconds(_SC()) == 720


def test_floor_tracks_the_media_wait_budget_setting():
    """app_settings stores the value as text, so the floor must parse it the
    way the lock's budget does."""
    assert gms.resolve_stage_timeout_seconds(_SC(gpu_sched_media_max_wait_s="240")) == 840


@pytest.mark.parametrize("raw", ["0", "-5", 0])
def test_legacy_unbounded_budget_adds_no_wait_term(raw):
    """<= 0 restores the unbounded legacy wait, which no finite floor can
    contain. It must not be read as a negative term that shrinks the floor
    below the render it protects."""
    assert gms.resolve_stage_timeout_seconds(_SC(gpu_sched_media_max_wait_s=raw)) == 600


def test_fractional_budget_rounds_up():
    assert gms._render_lock_wait_seconds(_SC(gpu_sched_media_max_wait_s="120.5")) == 121


@pytest.mark.parametrize("raw", ["soon", None, "inf"])
def test_unreadable_budget_falls_back_to_the_default(raw):
    assert (
        gms._render_lock_wait_seconds(_SC(gpu_sched_media_max_wait_s=raw))
        == gms.DEFAULT_RENDER_LOCK_WAIT_SECONDS
    )


@pytest.mark.parametrize("cfg", [{}, {"gpu_sched_media_max_wait_s": "240"}])
def test_wait_term_is_the_budget_the_lock_is_given(cfg):
    """Derived, not hand-listed: whatever media_wait_budget_s() hands the
    render lock, the floor must allow for exactly that much wait. A default
    that drifted in either place would let the wrapper kill the render again.
    """
    from poindexter.services.gpu_scheduler import media_wait_budget_s

    sc = SiteConfig(initial_config=cfg)
    with patch("poindexter.services.gpu_scheduler._sc", return_value=sc):
        budget = media_wait_budget_s()

    assert budget is not None
    assert gms._render_lock_wait_seconds(sc) == budget
