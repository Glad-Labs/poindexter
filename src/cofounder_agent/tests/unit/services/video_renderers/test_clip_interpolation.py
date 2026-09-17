"""Generative clips are conformed to the timeline's frame rate by MOTION
INTERPOLATION at their native geometry, not by frame duplication at 1080p.

Wan renders at 16 fps (S2V, hero i2v via ComfyUI) and the compositor assembles
at 30, so every other frame of a talking head was a duplicate — visibly
stuttery next to the 30 fps stock scenes (operator feedback 2026-09-17).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr


def _sc(**over):
    base = {"video_clip_interpolation_enabled": "true"}
    base.update(over)
    return SiteConfig(initial_config=base)


class _Proc:
    def __init__(self, rc=0, err=b"", write=None):
        self.returncode = rc
        self._err = err
        self._write = write

    async def communicate(self):
        if self._write:
            self._write()
        return b"", self._err


def _fake_exec(recorder, *, rc=0, err=b"", write_output=True):
    async def fake(*argv, **_kw):
        recorder.append(list(argv))
        out = argv[-1]

        def _w():
            if write_output:
                with open(out, "wb") as fh:
                    fh.write(b"interpolated")

        return _Proc(rc=rc, err=err, write=_w)

    return fake


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sixteen_fps_clip_is_interpolated_in_place(tmp_path, monkeypatch):
    clip = tmp_path / "presenter_0.mp4"
    clip.write_bytes(b"native16")
    monkeypatch.setattr(slr, "_probe_fps", AsyncMock(return_value=16.0))
    calls: list[list[str]] = []
    with patch.object(slr.asyncio, "create_subprocess_exec", _fake_exec(calls)):
        changed, detail = await slr._interpolate_clip_fps(str(clip), site_config=_sc())

    assert changed is True and detail == "16 -> 30 fps"
    assert clip.read_bytes() == b"interpolated", "the interpolated file must replace the original"
    assert not (tmp_path / "presenter_0.mp4.interp.mp4").exists()
    argv = calls[0]
    assert argv[0] == "ffmpeg" and argv[argv.index("-i") + 1] == str(clip)
    vf = argv[argv.index("-vf") + 1]
    assert vf.startswith("minterpolate=fps=30:") and "mi_mode=mci" in vf
    assert argv[argv.index("-c:a") + 1] == "copy", "the presenter's speech track rides along untouched"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clip_already_at_target_is_left_alone(tmp_path, monkeypatch):
    clip = tmp_path / "stock.mp4"
    clip.write_bytes(b"30fps")
    monkeypatch.setattr(slr, "_probe_fps", AsyncMock(return_value=29.97))
    calls: list[list[str]] = []
    with patch.object(slr.asyncio, "create_subprocess_exec", _fake_exec(calls)):
        changed, detail = await slr._interpolate_clip_fps(str(clip), site_config=_sc())
    assert changed is False and "already" in detail and calls == []
    assert clip.read_bytes() == b"30fps"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_and_missing_config_are_noops(tmp_path, monkeypatch):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    probe = AsyncMock(return_value=16.0)
    monkeypatch.setattr(slr, "_probe_fps", probe)
    assert (await slr._interpolate_clip_fps(str(clip), site_config=_sc(video_clip_interpolation_enabled="false")))[0] is False
    assert (await slr._interpolate_clip_fps(str(clip), site_config=None))[0] is False
    probe.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ffmpeg_failure_keeps_the_native_clip(tmp_path, monkeypatch):
    clip = tmp_path / "hero.mp4"
    clip.write_bytes(b"native16")
    monkeypatch.setattr(slr, "_probe_fps", AsyncMock(return_value=16.0))
    calls: list[list[str]] = []
    with patch.object(slr.asyncio, "create_subprocess_exec", _fake_exec(calls, rc=1, err=b"boom", write_output=False)):
        changed, detail = await slr._interpolate_clip_fps(str(clip), site_config=_sc())
    assert changed is False and "rc=1" in detail
    assert clip.read_bytes() == b"native16"
    assert not (tmp_path / "hero.mp4.interp.mp4").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_operator_filter_and_target_are_honoured(tmp_path, monkeypatch):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(slr, "_probe_fps", AsyncMock(return_value=24.0))
    calls: list[list[str]] = []
    sc = _sc(video_clip_interpolation_target_fps="60", video_clip_interpolation_filter="framerate=fps={fps}")
    with patch.object(slr.asyncio, "create_subprocess_exec", _fake_exec(calls)):
        changed, _ = await slr._interpolate_clip_fps(str(clip), site_config=sc)
    assert changed is True
    assert calls[0][calls[0].index("-vf") + 1] == "framerate=fps=60"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generative_render_interpolates_the_clip_it_produced(tmp_path):
    """The hook sits in the one function every generative clip (hero i2v and
    presenter S2V) passes through, after the provider's file is on disk."""
    out = tmp_path / "shot.mp4"

    class _Result:
        file_path = str(out)

    class _Provider:
        last_error = ""

        async def fetch(self, *a, **kw):
            out.write_bytes(b"mp4")
            return [_Result()]

    interp = AsyncMock(return_value=(True, "16 -> 30 fps"))
    with patch("poindexter.services.video_providers.wan2_1.Wan21Provider", _Provider), \
         patch.object(slr, "_clear_image_gen_for_hero", AsyncMock()), \
         patch.object(slr, "_interpolate_clip_fps", interp):
        ok, err = await slr._render_generative_clip(
            prompt="a glowing cube", output_path=str(out), image_path=None,
            duration_s=5, site_config=_sc(video_hero_unload_settle_seconds="0"),
        )
    assert (ok, err) == (True, "")
    interp.assert_awaited_once()
    assert interp.await_args.args[0] == str(out)


@pytest.mark.unit
def test_probe_fps_parses_a_rational():
    async def fake(*argv, **_kw):
        class P:
            async def communicate(self):
                return b"16/1\n", b""
        return P()

    with patch.object(slr.asyncio, "create_subprocess_exec", fake):
        assert asyncio.run(slr._probe_fps("/x.mp4")) == 16.0
