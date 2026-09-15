"""generate_media_scripts' node timeout must contain its own work.

The static 300 s could not: two LLM calls plus the ambient bed, whose
stable-audio render cold-loads its model (~125 s) whenever the GPU scheduler
has hard-unloaded the sidecar. atom_runs (30 days): ok runs 210-280 s, and the
stage errored at exactly 300 s on 08-27, 09-06, 09-08 and twice on 09-15 — each
time no podcast_script reached the director and the post shipped with no shot
list (poindexter#1001).
"""
from __future__ import annotations

from poindexter.modules.content.stages import generate_media_scripts as gms


class _SC:
    def __init__(self, **v):
        self.v = v

    def get_int(self, key, default=None):
        return int(self.v.get(key, default))

    def get(self, key, default=None):
        return self.v.get(key, default)


def test_default_floor_is_600_seconds():
    assert gms.resolve_stage_timeout_seconds(_SC()) == 2 * 120 + 180 + 150 + 30 == 600


def test_floor_tracks_the_audio_render_timeout_setting():
    assert gms.resolve_stage_timeout_seconds(_SC(audio_render_timeout_seconds=300)) == 720


def test_every_term_is_a_setting():
    sc = _SC(media_scripts_llm_call_budget_seconds=90, media_scripts_llm_calls=3,
             audio_render_timeout_seconds=100, audio_gen_cold_load_allowance_seconds=0,
             media_scripts_stage_overhead_seconds=10)
    assert gms.resolve_stage_timeout_seconds(sc) == 3 * 90 + 100 + 0 + 10


def test_no_site_config_falls_back_to_defaults():
    assert gms.resolve_stage_timeout_seconds(None) == 600


def test_stage_exposes_the_hook_the_runner_floors_on():
    stage = gms.GenerateMediaScriptsStage()
    assert stage.timeout_seconds == 300
    assert stage.resolve_timeout_seconds(_SC()) == 600
