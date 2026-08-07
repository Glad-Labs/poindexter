"""Podcast sting mixer (poindexter#690 finish) — filtergraph + fail-soft.

The mixer is polish, never load-bearing: any failure returns None and the
episode ships dry. The filtergraph tests pin the sound design (sting solo
→ fade under the voice → mirrored outro) as config-driven math.
"""
from __future__ import annotations

import asyncio

import pytest

from services import podcast_sting_mixer as m


class _Cfg:
    def __init__(self, **over):
        self.v = {k: str(v) for k, v in over.items()}

    def get(self, key, default=None):
        return self.v.get(key, default)


@pytest.mark.unit
class TestResolveConfig:
    def test_defaults(self):
        cfg = m.resolve_config(_Cfg())
        assert cfg.solo_s == 2.5 and cfg.fade_s == 3.0
        assert cfg.outro_s == 6.0 and cfg.gain_db == -7.0

    def test_settings_override_and_clamp(self):
        cfg = m.resolve_config(_Cfg(
            podcast_sting_solo_seconds="4",
            podcast_sting_gain_db="-99",       # clamped to -30
            podcast_sting_outro_seconds="999",  # clamped to 30
        ))
        assert cfg.solo_s == 4.0
        assert cfg.gain_db == -30.0
        assert cfg.outro_s == 30.0

    def test_garbage_falls_back(self):
        cfg = m.resolve_config(_Cfg(podcast_sting_solo_seconds="loud"))
        assert cfg.solo_s == 2.5

    def test_none_site_config(self):
        assert m.resolve_config(None).fade_s == 3.0


@pytest.mark.unit
class TestFiltergraph:
    def test_structure_and_timing(self):
        cfg = m.resolve_config(_Cfg())
        g = m.build_mix_filtergraph(narration_s=218.0, cfg=cfg)
        # Voice delayed by the solo window (2.5s → 2500ms).
        assert "adelay=2500|2500[voice]" in g
        # Intro trims to solo+fade and fades out across the fade window.
        assert "atrim=0:5.500" in g
        assert "afade=t=out:st=2.500:d=3.000" in g
        # Outro lands at solo + narration - 1s overlap = 219.5s.
        assert "adelay=219500|219500[outro]" in g
        # Voice level untouched; only stings gained.
        assert "amix=inputs=3:duration=longest:normalize=0" in g
        assert g.count("volume=-7.0dB") == 2

    def test_short_narration_keeps_overlap_sane(self):
        cfg = m.resolve_config(_Cfg())
        g = m.build_mix_filtergraph(narration_s=0.5, cfg=cfg)
        # overlap clamps to the narration length → outro at solo point.
        assert "adelay=2500|2500[outro]" in g


@pytest.mark.unit
class TestMixFailSoft:
    def test_missing_inputs_return_none(self, tmp_path):
        out = asyncio.run(m.mix_intro_outro(
            str(tmp_path / "nope.mp3"), str(tmp_path / "sting.wav"),
            site_config=_Cfg(),
        ))
        assert out is None

    def test_unprobeable_narration_returns_none(self, tmp_path, monkeypatch):
        narr = tmp_path / "n.mp3"
        narr.write_bytes(b"x" * 10)
        sting = tmp_path / "s.wav"
        sting.write_bytes(b"x" * 10)

        async def no_dur(path):
            return None

        monkeypatch.setattr(m, "_probe_duration_s", no_dur)
        out = asyncio.run(m.mix_intro_outro(
            str(narr), str(sting), site_config=_Cfg(),
        ))
        assert out is None

    def test_ffmpeg_failure_cleans_up_and_returns_none(
        self, tmp_path, monkeypatch,
    ):
        narr = tmp_path / "n.mp3"
        narr.write_bytes(b"x" * 10)
        sting = tmp_path / "s.wav"
        sting.write_bytes(b"x" * 10)

        async def dur(path):
            return 60.0

        class FakeProc:
            returncode = 1

            async def communicate(self):
                return b"", b"boom"

        async def fake_exec(*cmd, **kw):
            return FakeProc()

        monkeypatch.setattr(m, "_probe_duration_s", dur)
        monkeypatch.setattr(m.asyncio, "create_subprocess_exec", fake_exec)
        out = asyncio.run(m.mix_intro_outro(
            str(narr), str(sting), site_config=_Cfg(),
        ))
        assert out is None
