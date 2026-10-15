"""Cartoon scores (services/score.py, chat drafting phase 6).

Music per scene and sound effects on the actions worth hearing, planned by
the writer, made by Stable Audio and laid on the dialogue's own timeline as
one soundtrack. Best-effort throughout: no score renders voices-only.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.services import score
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config={"audio_gen_engine": "stable-audio-open-1.0", **extra})


TIMELINE = [
    {"beat_idx": 0, "scene_id": "s1", "kind": "line", "speaker": "fox", "text": "Hello.", "start_s": 0.4, "end_s": 2.0},
    {"beat_idx": 1, "scene_id": "s1", "kind": "action", "text": "A door slams.", "start_s": 2.4, "end_s": 3.9},
    {"beat_idx": 2, "scene_id": "s2", "kind": "line", "speaker": "crow", "text": "Caw.", "start_s": 4.7, "end_s": 6.0},
]
SCRIPT = {"title": "The Fox and the Crow", "logline": "A fox flatters a crow.",
          "scenes": [{"scene_id": "s1", "setting": "a wood", "summary": "fox arrives", "turn": "the crow listens"},
                     {"scene_id": "s2", "setting": "a branch", "summary": "crow sings", "turn": "cheese falls"}]}


class TestTimeline:
    def test_scenes_tile_the_whole_track(self):
        assert score.scene_spans(TIMELINE, 7.0) == [("s1", 0.0, 4.7), ("s2", 4.7, 7.0)]

    def test_action_beats(self):
        assert [b["beat_idx"] for b in score.action_beats(TIMELINE)] == [1]


class TestCheck:
    def test_a_good_plan(self):
        plan = {"music": [{"scene_id": "s1", "prompt": "m"}, {"scene_id": "s2", "prompt": "m"}],
                "sounds": [{"beat": 1, "prompt": "door slam"}]}
        assert score.check_plan(plan, scene_ids={"s1", "s2"}, beat_ids={1}) == []

    def test_each_problem_is_named(self):
        plan = {"music": [{"scene_id": "s9", "prompt": ""}], "sounds": [{"beat": 7, "prompt": "x"}]}
        errors = score.check_plan(plan, scene_ids={"s1", "s2"}, beat_ids={1})
        text = " ".join(errors)
        assert "'s9' is not one of" in text and '"prompt"' in text and "missing ['s1', 's2']" in text
        assert "no action 7" in text

    def test_music_is_required(self):
        assert score.check_plan({}, scene_ids={"s1"}, beat_ids=set())[0].startswith('FIX: add "music"')


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text
    import poindexter.services.screenplay as sp

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    return mock


class TestPlan:
    @pytest.mark.asyncio
    async def test_the_writer_sees_scenes_and_actions_and_sounds_are_capped(self, chat):
        chat.side_effect = [json.dumps({
            "music": [{"scene_id": "s1", "prompt": "soft pizzicato"}, {"scene_id": "s2", "prompt": "playful oboe"}],
            "sounds": [{"beat": 1, "prompt": "door slam"}, {"beat": 1, "prompt": "echo"}],
        })]
        plan = await score.plan_score(SCRIPT, TIMELINE, style="ink", site_config=_sc(story_score_max_sounds="5"))
        # One sound per beat: two on beat 1 piled into noise.
        assert plan["sounds"] == [{"beat": 1, "prompt": "door slam"}] and plan["music"][1]["prompt"] == "playful oboe"
        prompt = chat.await_args.args[0]
        assert "s1 | 5s | a wood" in prompt and "1 | s1 | A door slams." in prompt
        assert chat.await_args.kwargs["phase"] == "story_score"


class TestMix:
    def test_every_piece_is_levelled_and_the_mix_is_limited(self):
        """The Midnight Patch score hit -1.4 dBFS on one action beat: a raw
        sound effect far louder than the music around it (2026-10-14)."""
        graph, sources = score.mix_graph(
            [{"path": "m", "kind": "music", "start": 0.0, "end": 4.7, "clip_s": 30.0},
             {"path": "s", "kind": "sfx", "start": 2.4, "end": 5.4}],
            duration_s=7.0, fade_s=1.5, sfx_gain_db=9.0, music_lufs=-24.0,
        )
        assert sources == ["m", "s"]
        assert "loudnorm=I=-24.0:TP=-3" in graph and "loudnorm=I=-15.0:TP=-3" in graph
        assert "adelay=2400|2400[p1]" in graph
        assert graph.endswith("amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.700:level=disabled,"
                              "atrim=0:7.000[out]")

    def test_a_sound_starts_on_its_cut_not_after_its_own_lead_in(self):
        """Generated effects often open quiet; the Battery Drop's sounds (2026-10-15)
        came in after the picture they belonged to."""
        graph, _ = score.mix_graph([{"path": "s", "kind": "sfx", "start": 2.4, "end": 5.4}],
                                   duration_s=7.0, fade_s=1.5, sfx_gain_db=9.0)
        trim = graph.index("silenceremove=start_periods=1")
        assert trim < graph.index("atrim=0:3.000") < graph.index("adelay=2400|2400")

    def test_long_music_crossfades_its_steady_part_from_separate_inputs(self):
        """Generated music fades out at its end, so only the first part is used,
        looped; asplit cannot feed an acrossfade, so each repeat is an input."""
        graph, sources = score.mix_graph(
            [{"path": "m", "kind": "music", "start": 0.0, "end": 43.0, "clip_s": 45.0}],
            duration_s=43.0, fade_s=1.5, sfx_gain_db=9.0, keep_fraction=0.6,
        )
        assert sources == ["m", "m"]
        assert "[0:a]atrim=0:27.000" in graph and "[1:a]atrim=0:27.000" in graph
        assert "acrossfade=d=3.00" in graph and "asplit" not in graph


@pytest.fixture
def studio(monkeypatch, tmp_path):
    made: list = []

    async def fake_generate(prompt, kind, seconds, stem, out_dir, site_config):
        made.append((kind, prompt, round(seconds, 1)))
        path = Path(out_dir) / f"{stem}.wav"
        path.write_bytes(b"wav")
        return str(path)

    async def fake_ffmpeg(*args):
        Path(args[-1]).write_bytes(b"score")
        return True

    import poindexter.services.dialogue_audio as da

    monkeypatch.setattr(score, "_generate", fake_generate)
    monkeypatch.setattr(score, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(da, "probe_seconds", AsyncMock(return_value=30.0))
    return made


PLAN = {"music": [{"scene_id": "s1", "prompt": "soft pizzicato"}, {"scene_id": "s2", "prompt": "playful oboe"}],
        "sounds": [{"beat": 1, "prompt": "door slam"}]}


class TestBuild:
    @pytest.mark.asyncio
    async def test_each_piece_is_made_for_its_moment(self, studio, tmp_path):
        out = await score.build_score(PLAN, TIMELINE, duration_s=7.0, site_config=_sc(),
                                      output_path=str(tmp_path / "t_score.wav"))
        assert out == {"path": str(tmp_path / "t_score.wav"), "music": 2, "sounds": 1}
        # Music is made as long as the engine allows; only its steady part is used.
        assert studio[0] == ("ambient", "soft pizzicato, instrumental music, no vocals, clean mix", 47.0)
        assert studio[2][0] == "sfx" and studio[2][1].startswith("sound effect: door slam")

    @pytest.mark.asyncio
    async def test_nothing_made_is_no_score(self, monkeypatch, tmp_path):
        monkeypatch.setattr(score, "_generate", AsyncMock(return_value=None))
        out = await score.build_score(PLAN, TIMELINE, duration_s=7.0, site_config=_sc(),
                                      output_path=str(tmp_path / "t.wav"))
        assert out["path"] is None


class TestScoreScreenplay:
    @pytest.mark.asyncio
    async def test_plans_then_builds_under_the_render_lock(self, studio, chat, monkeypatch, tmp_path):
        from poindexter.services.gpu_scheduler import gpu

        locks = []

        @contextlib.asynccontextmanager
        async def fake_lock(owner, **kw):
            locks.append((owner, kw.get("phase")))
            yield

        monkeypatch.setattr(gpu, "lock", fake_lock)
        chat.side_effect = [json.dumps(PLAN)]
        out = await score.score_screenplay(SCRIPT, TIMELINE, duration_s=7.0, style="ink", site_config=_sc(),
                                           task_id="t1", output_dir=str(tmp_path))
        assert out["path"] and out["music"] == 2 and out["sounds"] == 1
        assert locks == [("video", "video_score")]

    @pytest.mark.asyncio
    async def test_off_or_no_engine_or_no_timeline_is_skipped(self, chat):
        for sc, timeline in ((_sc(story_score_enabled="false"), TIMELINE), (_sc(audio_gen_engine=""), TIMELINE),
                             (_sc(), [])):
            out = await score.score_screenplay(SCRIPT, timeline, duration_s=7.0, style="", site_config=sc)
            assert out["path"] is None and out["skipped"]
        assert chat.await_count == 0


class TestAtom:
    @pytest.mark.asyncio
    async def test_the_score_becomes_the_soundtrack(self, monkeypatch):
        import poindexter.services.dialogue_audio as da
        from poindexter.modules.content.atoms import media_score_screenplay as atom

        monkeypatch.setattr(da, "probe_seconds", AsyncMock(return_value=7.0))
        monkeypatch.setattr(score, "score_screenplay", AsyncMock(return_value={"path": "/v/score.wav"}))
        out = await atom.run({"site_config": _sc(), "screenplay": SCRIPT, "dialogue_timeline": TIMELINE,
                              "long_narration_audio_path": "/t.wav"})
        assert out == {"video_ambient_audio_path": "/v/score.wav"}

    @pytest.mark.asyncio
    async def test_a_failure_renders_voices_only(self, monkeypatch):
        import poindexter.services.dialogue_audio as da
        from poindexter.modules.content.atoms import media_score_screenplay as atom

        monkeypatch.setattr(da, "probe_seconds", AsyncMock(return_value=7.0))
        monkeypatch.setattr(score, "score_screenplay", AsyncMock(side_effect=RuntimeError("writer down")))
        assert await atom.run({"site_config": _sc(), "screenplay": SCRIPT, "dialogue_timeline": TIMELINE,
                               "long_narration_audio_path": "/t.wav"}) == {}

    def test_outputs_are_declared_channels(self):
        from poindexter.modules.content.atoms import media_score_screenplay as atom
        from poindexter.services.template_runner import PipelineState

        assert set(atom.ATOM_META.produces) <= set(PipelineState.__annotations__)
