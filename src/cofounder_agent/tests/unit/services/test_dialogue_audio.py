"""Creative studio phase 3 (poindexter#1137): the multi-voice dialogue track.

The timeline is the contract the shot director cuts lip-sync windows from, so
these pin its arithmetic exactly (line / scene / action-beat pauses on measured
clip durations), that each speaker is voiced by their own persona, and that a
line that will not synthesize stops the run instead of being skipped or
re-voiced.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.modules.content.atoms import media_render_dialogue as atom
from poindexter.services import dialogue_audio as da
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true", "persona.dex.voice_id": "am_puck",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true", "persona.nova.voice_id": "af_nova",
        "persona.host.display_name": "Host", "persona.host.enabled": "true",
        "persona.off.display_name": "Off", "persona.off.enabled": "false",
        "story_dialogue_gap_seconds": "0.35",
        "story_scene_gap_seconds": "0.8",
        "story_action_beat_seconds": "1.5",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


SCREENPLAY = {
    "title": "t",
    "scenes": [
        {"scene_id": "s1", "characters": ["dex", "nova"], "beats": [
            {"kind": "line", "speaker": "dex", "text": "two seconds"},
            {"kind": "line", "speaker": "nova", "text": "three seconds"},
            {"kind": "action", "text": "Nova taps the GPU", "characters": ["nova"]},
        ]},
        {"scene_id": "s2", "characters": ["dex"], "beats": [
            {"kind": "line", "speaker": "narrator", "text": "one second"},
        ]},
    ],
}
SECONDS = {"two seconds": 2.0, "three seconds": 3.0, "one second": 1.0}


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Stub the four ffmpeg/ffprobe calls; durations come from the file name."""
    lengths: dict[str, float] = {}

    async def _normalize(src, dst):
        Path(dst).write_bytes(b"WAV")
        lengths[dst] = lengths[src]

    async def _silence(seconds, dst):
        Path(dst).write_bytes(b"WAV")
        lengths[dst] = seconds

    async def _concat(parts, dst, work):
        Path(dst).write_bytes(b"WAV")
        lengths[dst] = sum(lengths[p] for p in parts)

    async def _probe(path):
        return lengths[path]

    monkeypatch.setattr(da, "_normalize", _normalize)
    monkeypatch.setattr(da, "_silence", _silence)
    monkeypatch.setattr(da, "_concat", _concat)
    monkeypatch.setattr(da, "probe_seconds", _probe)
    return lengths


def _synth(lengths, calls):
    async def synthesize(text, persona, path):
        calls.append((text, getattr(persona, "slug", None)))
        Path(path).write_bytes(b"MP3")
        lengths[path] = SECONDS[text]
        return path
    return synthesize


class TestTimeline:
    @pytest.mark.asyncio
    async def test_measured_lines_and_every_pause_kind(self, fake_ffmpeg, tmp_path):
        calls: list = []
        out = await da.render_dialogue(
            SCREENPLAY, site_config=_sc(), output_path=str(tmp_path / "d.wav"),
            narrator_persona="host", synthesize=_synth(fake_ffmpeg, calls),
        )
        spans = [(b["kind"], b["speaker"], b["start_s"], b["end_s"]) for b in out["timeline"]]
        assert spans == [
            ("line", "dex", 0.0, 2.0),
            ("line", "nova", 2.35, 5.35),        # + line gap
            ("action", None, 5.35, 6.85),        # action beat holds 1.5 s, no line gap before it
            ("line", "narrator", 7.65, 8.65),    # + scene gap
        ]
        assert out["duration_s"] == 8.65
        assert out["spoken_script"] == "two seconds\nthree seconds\none second"
        assert [b["beat_idx"] for b in out["timeline"]] == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_each_speaker_uses_their_own_persona(self, fake_ffmpeg, tmp_path):
        calls: list = []
        await da.render_dialogue(
            SCREENPLAY, site_config=_sc(), output_path=str(tmp_path / "d.wav"),
            narrator_persona="host", synthesize=_synth(fake_ffmpeg, calls),
        )
        assert [slug for _, slug in calls] == ["dex", "nova", "host"]

    @pytest.mark.asyncio
    async def test_narrator_defaults_to_the_setting_then_the_house_voice(self, fake_ffmpeg, tmp_path):
        calls: list = []
        await da.render_dialogue(
            SCREENPLAY, site_config=_sc(story_default_narrator_persona="host"),
            output_path=str(tmp_path / "d.wav"), synthesize=_synth(fake_ffmpeg, calls),
        )
        assert calls[-1][1] == "host"
        calls.clear()
        await da.render_dialogue(
            SCREENPLAY, site_config=_sc(), output_path=str(tmp_path / "e.wav"),
            synthesize=_synth(fake_ffmpeg, calls),
        )
        assert calls[-1][1] is None  # house voice

    @pytest.mark.asyncio
    async def test_the_screenplays_narrator_persona_is_used(self, fake_ffmpeg, tmp_path):
        calls: list = []
        await da.render_dialogue(
            {**SCREENPLAY, "narrator_persona": "host"}, site_config=_sc(),
            output_path=str(tmp_path / "d.wav"), synthesize=_synth(fake_ffmpeg, calls),
        )
        assert calls[-1][1] == "host"


class TestVoices:
    def test_unknown_or_disabled_speaker_raises(self):
        with pytest.raises(da.DialogueAudioError, match="ghost"):
            da.resolve_voice(_sc(), "ghost", "")
        with pytest.raises(da.DialogueAudioError, match="off"):
            da.resolve_voice(_sc(), "off", "")

    def test_a_bad_narrator_persona_degrades_to_the_house_voice(self):
        assert da.resolve_voice(_sc(), "narrator", "off") is None
        assert da.resolve_voice(_sc(), "narrator", "") is None


class TestFailures:
    @pytest.mark.asyncio
    async def test_one_retry_then_success(self, fake_ffmpeg, tmp_path):
        attempts = {"n": 0}
        good = _synth(fake_ffmpeg, [])

        async def flaky(text, persona, path):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("speaches 503")
            return await good(text, persona, path)

        out = await da.render_dialogue(
            SCREENPLAY, site_config=_sc(), output_path=str(tmp_path / "d.wav"), synthesize=flaky,
        )
        assert len([b for b in out["timeline"] if b["kind"] == "line"]) == 3

    @pytest.mark.asyncio
    async def test_a_line_that_fails_twice_stops_the_run(self, fake_ffmpeg, tmp_path):
        with pytest.raises(da.DialogueAudioError, match="failed to synthesize twice: RuntimeError: down"):
            await da.render_dialogue(
                SCREENPLAY, site_config=_sc(), output_path=str(tmp_path / "d.wav"),
                synthesize=AsyncMock(side_effect=RuntimeError("down")),
            )

    @pytest.mark.asyncio
    async def test_no_lines_raises(self, fake_ffmpeg, tmp_path):
        only_action = {"scenes": [{"scene_id": "s1", "beats": [{"kind": "action", "text": "x"}]}]}
        with pytest.raises(da.DialogueAudioError, match="no line beats"):
            await da.render_dialogue(
                only_action, site_config=_sc(), output_path=str(tmp_path / "d.wav"),
                synthesize=_synth(fake_ffmpeg, []),
            )


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.asyncio
async def test_real_ffmpeg_track_matches_its_timeline(tmp_path):
    """No stubs below the TTS seam: the joined file's length equals the
    timeline's last end, so lip-sync windows land on the right words."""
    async def tone(text, persona, path):
        seconds = SECONDS[text]
        rc, out = await da._run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={seconds}", path,
        ])
        assert rc == 0, out
        return path

    out = await da.render_dialogue(
        SCREENPLAY, site_config=_sc(), output_path=str(tmp_path / "d.wav"), synthesize=tone,
    )
    assert os.path.getsize(out["audio_path"]) > 0
    assert abs(out["duration_s"] - out["timeline"][-1]["end_s"]) < 0.1


class TestAtom:
    def test_contract(self):
        assert atom.ATOM_META.requires == ("screenplay",)
        assert set(atom.ATOM_META.produces) == {
            "long_narration_audio_path", "video_long_script", "dialogue_timeline"}

    @pytest.mark.asyncio
    async def test_outputs_land_on_the_video_chain_channels(self, monkeypatch):
        seen = {}

        async def fake(screenplay, **kw):
            seen.update(kw)
            return {"audio_path": kw["output_path"], "timeline": [{"kind": "line"}],
                    "spoken_script": "hi", "duration_s": 1.0}

        monkeypatch.setattr(da, "render_dialogue", fake)
        out = await atom.run({"screenplay": SCREENPLAY, "site_config": _sc(), "task_id": "abc12345",
                              "dialogue_narrator_persona": "host"})
        assert out["long_narration_audio_path"].endswith("dialogue.wav")
        assert out["video_long_script"] == "hi" and out["dialogue_timeline"] == [{"kind": "line"}]
        assert seen["narrator_persona"] == "host"

    @pytest.mark.asyncio
    async def test_missing_screenplay_raises(self):
        with pytest.raises(RuntimeError, match="no screenplay"):
            await atom.run({"site_config": _sc()})


class TestSynthesizeLine:
    @pytest.mark.asyncio
    async def test_persona_voice_and_no_fallback(self, monkeypatch, tmp_path):
        from poindexter.services import podcast_service as pod
        from poindexter.services.persona_service import get_persona

        sc = _sc(podcast_tts_voice="bf_emma")
        svc = pod.PodcastService(site_config=sc)
        seen = {}

        async def gen(text, voice, path, *, persona=None):
            seen.update(voice=voice, persona=persona)
            Path(path).write_bytes(b"MP3")
            return pod.EpisodeResult(success=True, file_path=str(path))

        monkeypatch.setattr(svc, "_generate_with_voice", gen)
        out = await svc.synthesize_line("hi", get_persona(sc, "dex"), tmp_path / "x.mp3")
        assert out.endswith("x.mp3") and seen["voice"] == "am_puck"
        await svc.synthesize_line("hi", None, tmp_path / "y.mp3")
        assert seen["voice"] == "bf_emma" and seen["persona"] is None

        monkeypatch.setattr(svc, "_generate_with_voice",
                            AsyncMock(return_value=pod.EpisodeResult(success=False, error="engine down")))
        with pytest.raises(RuntimeError, match="engine down"):
            await svc.synthesize_line("hi", get_persona(sc, "dex"), tmp_path / "z.mp3")
