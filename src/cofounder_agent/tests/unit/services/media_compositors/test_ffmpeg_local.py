"""Unit tests for ``services.media_compositors.ffmpeg_local``.

Mocks ``_run_blocking`` and ``_ffprobe_blocking`` so the tests never
spawn ffmpeg. Covers Protocol conformance, the supports() truth table,
input-validation matrix, command-builder argv plumbing, ffprobe parser
edge cases, and compose() happy/failure paths.

Note: some features mentioned in the spec (``_is_still_image``,
``_build_ken_burns_filter``, configurable caption position/font_size)
are NOT in the current implementation — those tests are marked
``skip`` with a comment so they show up in the next pass.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from plugins.media_compositor import (
    CompositionRequest,
    CompositionResult,
    CompositionScene,
    MediaCompositor,
)
from services.media_compositors import ffmpeg_local as ffmpeg_mod
from services.media_compositors.ffmpeg_local import (
    FFmpegLocalCompositor,
    _build_burn_captions_cmd,
    _build_concat_cmd,
    _build_ken_burns_filter,
    _build_normalize_cmd,
    _is_still_image,
    _parse_probe,
    _validate_inputs,
)


class _StubSiteConfig:
    """Minimal site_config double — supports .get() with prefixed keys."""

    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        self._mapping = {
            f"plugin.media_compositor.ffmpeg_local.{k}": v
            for k, v in (mapping or {}).items()
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


def _make_compositor(mapping: dict[str, Any] | None = None) -> FFmpegLocalCompositor:
    return FFmpegLocalCompositor(site_config=_StubSiteConfig(mapping or {}))


def _make_scene(tmp_path, name: str = "clip", *, with_narration: bool = False) -> CompositionScene:
    clip = tmp_path / f"{name}.mp4"
    clip.write_bytes(b"\x00" * 32)
    narration = None
    if with_narration:
        narr = tmp_path / f"{name}-narr.wav"
        narr.write_bytes(b"\x00" * 32)
        narration = str(narr)
    return CompositionScene(
        clip_path=str(clip),
        narration_path=narration,
        duration_s=2.0,
    )


def _make_request(tmp_path, **overrides: Any) -> CompositionRequest:
    output = tmp_path / "output.mp4"
    base: dict[str, Any] = dict(
        scenes=[_make_scene(tmp_path)],
        output_path=str(output),
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        container="mp4",
    )
    base.update(overrides)
    return CompositionRequest(**base)


# ---------------------------------------------------------------------------
# Protocol conformance + supports() truth table
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_media_compositor_protocol(self):
        assert isinstance(FFmpegLocalCompositor(), MediaCompositor)

    def test_class_attributes(self):
        c = FFmpegLocalCompositor()
        assert c.name == "ffmpeg_local"
        assert c.supports_burned_captions is True
        # All five codecs the encoder map covers
        assert set(c.supported_codecs) == {"h264", "hevc", "av1", "vp9", "prores"}


class TestSupports:
    """Spot-check the supports() truth table — codec × container."""

    def test_mp4_h264_supported(self):
        assert _make_compositor().supports(codec="h264", container="mp4") is True

    def test_mp4_hevc_supported(self):
        assert _make_compositor().supports(codec="hevc", container="mp4") is True

    def test_mp4_av1_supported(self):
        assert _make_compositor().supports(codec="av1", container="mp4") is True

    def test_mp4_vp9_unsupported(self):
        # VP9 is webm/mkv only
        assert _make_compositor().supports(codec="vp9", container="mp4") is False

    def test_webm_vp9_supported(self):
        assert _make_compositor().supports(codec="vp9", container="webm") is True

    def test_webm_av1_supported(self):
        assert _make_compositor().supports(codec="av1", container="webm") is True

    def test_webm_h264_unsupported(self):
        assert _make_compositor().supports(codec="h264", container="webm") is False

    def test_mov_prores_supported(self):
        assert _make_compositor().supports(codec="prores", container="mov") is True

    def test_mov_av1_unsupported(self):
        assert _make_compositor().supports(codec="av1", container="mov") is False

    def test_mkv_vp9_supported(self):
        assert _make_compositor().supports(codec="vp9", container="mkv") is True

    def test_unknown_container_returns_false(self):
        # Outside the documented set
        assert _make_compositor().supports(codec="h264", container="avi") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Disabled gate
# ---------------------------------------------------------------------------


class TestDisabledGate:
    @pytest.mark.asyncio
    async def test_disabled_returns_failure(self, tmp_path):
        compositor = _make_compositor({"enabled": False})
        request = _make_request(tmp_path)
        result = await compositor.compose(request)
        assert isinstance(result, CompositionResult)
        assert result.success is False
        assert "disabled" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_disabled_does_not_spawn_subprocess(self, tmp_path):
        compositor = _make_compositor({"enabled": False})
        request = _make_request(tmp_path)
        with patch.object(ffmpeg_mod, "_run_blocking") as mock_run:
            await compositor.compose(request)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# _validate_inputs
# ---------------------------------------------------------------------------


class TestValidateInputs:
    def test_empty_scenes_rejected(self, tmp_path):
        request = CompositionRequest(scenes=[], output_path=str(tmp_path / "o.mp4"))
        err = _validate_inputs(request)
        assert err is not None
        assert "scenes is empty" in err

    def test_missing_output_path_rejected(self, tmp_path):
        request = CompositionRequest(
            scenes=[_make_scene(tmp_path)],
            output_path="",
        )
        err = _validate_inputs(request)
        assert err is not None
        assert "output_path" in err

    def test_missing_scene_clip_rejected(self, tmp_path):
        scene = CompositionScene(clip_path=str(tmp_path / "missing.mp4"))
        request = CompositionRequest(
            scenes=[scene],
            output_path=str(tmp_path / "o.mp4"),
        )
        err = _validate_inputs(request)
        assert err is not None
        assert "clip_path" in err
        assert "scene[0]" in err

    def test_missing_narration_rejected(self, tmp_path):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"\x00")
        scene = CompositionScene(
            clip_path=str(clip),
            narration_path=str(tmp_path / "missing-narr.wav"),
        )
        request = CompositionRequest(
            scenes=[scene],
            output_path=str(tmp_path / "o.mp4"),
        )
        err = _validate_inputs(request)
        assert err is not None
        assert "narration_path" in err

    def test_missing_soundtrack_rejected(self, tmp_path):
        request = _make_request(
            tmp_path,
            soundtrack_path=str(tmp_path / "missing.mp3"),
        )
        err = _validate_inputs(request)
        assert err is not None
        assert "soundtrack_path" in err

    def test_missing_caption_track_rejected(self, tmp_path):
        request = _make_request(
            tmp_path,
            caption_track_path=str(tmp_path / "missing.srt"),
        )
        err = _validate_inputs(request)
        assert err is not None
        assert "caption_track_path" in err

    def test_unsupported_codec_rejected(self, tmp_path):
        request = _make_request(tmp_path, codec="mpeg2")
        err = _validate_inputs(request)
        assert err is not None
        assert "codec" in err

    def test_valid_request_returns_none(self, tmp_path):
        request = _make_request(tmp_path)
        assert _validate_inputs(request) is None


# ---------------------------------------------------------------------------
# _build_normalize_cmd
# ---------------------------------------------------------------------------


class TestBuildNormalizeCmd:
    def _kwargs(self, scene: CompositionScene, **overrides):
        base = dict(
            binary="/usr/bin/ffmpeg",
            scene=scene,
            output_path="/tmp/scene_0000.mp4",
            width=1920,
            height=1080,
            fps=30,
            encoder="libx264",
            preset="medium",
            crf=20,
            audio_bitrate="192k",
            loglevel="error",
            hwaccel="",
        )
        base.update(overrides)
        return base

    def test_uses_anullsrc_for_silent_scenes(self, tmp_path):
        # No narration → ffmpeg synthesizes silence via lavfi
        scene = _make_scene(tmp_path, with_narration=False)
        cmd = _build_normalize_cmd(**self._kwargs(scene))
        assert "anullsrc=r=48000:cl=stereo" in cmd
        # lavfi flag must precede the synthetic input
        assert "lavfi" in cmd

    def test_uses_narration_when_provided(self, tmp_path):
        scene = _make_scene(tmp_path, with_narration=True)
        cmd = _build_normalize_cmd(**self._kwargs(scene))
        # No anullsrc when we have real narration
        assert all("anullsrc" not in x for x in cmd)
        # Two -i inputs: clip and narration
        assert cmd.count("-i") == 2

    def test_video_filtergraph_pins_dimensions_and_fps(self, tmp_path):
        scene = _make_scene(tmp_path)
        cmd = _build_normalize_cmd(**self._kwargs(scene, width=1280, height=720, fps=60))
        idx = cmd.index("-vf")
        vf = cmd[idx + 1]
        assert "scale=w=1280:h=720" in vf
        assert "pad=w=1280:h=720" in vf
        assert "fps=60" in vf
        # Aspect-ratio preserve flag
        assert "force_original_aspect_ratio=decrease" in vf

    def test_duration_pinned_when_set(self, tmp_path):
        scene = _make_scene(tmp_path)
        scene.duration_s = 3.5
        cmd = _build_normalize_cmd(**self._kwargs(scene))
        assert "-t" in cmd
        assert cmd[cmd.index("-t") + 1] == "3.500"

    def test_duration_defaults_when_zero(self, tmp_path):
        # Current behavior — duration_s=0 collapses to a frame, which is
        # never what we want, so the helper substitutes 5.0s.
        scene = _make_scene(tmp_path)
        scene.duration_s = 0.0
        cmd = _build_normalize_cmd(**self._kwargs(scene))
        assert "-t" in cmd
        assert cmd[cmd.index("-t") + 1] == "5.000"

    def test_hwaccel_added_when_set(self, tmp_path):
        scene = _make_scene(tmp_path)
        cmd = _build_normalize_cmd(**self._kwargs(scene, hwaccel="cuda"))
        assert "-hwaccel" in cmd
        assert cmd[cmd.index("-hwaccel") + 1] == "cuda"

    def test_hwaccel_absent_when_empty(self, tmp_path):
        scene = _make_scene(tmp_path)
        cmd = _build_normalize_cmd(**self._kwargs(scene, hwaccel=""))
        assert "-hwaccel" not in cmd

    def test_encoder_and_quality_flags_plumbed(self, tmp_path):
        scene = _make_scene(tmp_path)
        cmd = _build_normalize_cmd(**self._kwargs(
            scene, encoder="libx265", preset="slow", crf=18, audio_bitrate="256k",
        ))
        assert cmd[cmd.index("-c:v") + 1] == "libx265"
        assert cmd[cmd.index("-preset") + 1] == "slow"
        assert cmd[cmd.index("-crf") + 1] == "18"
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert cmd[cmd.index("-b:a") + 1] == "256k"

    def test_still_image_uses_loop_and_framerate(self, tmp_path):
        # Still inputs need `-loop 1 -framerate {fps}` BEFORE -i.
        still = tmp_path / "img.jpg"
        still.write_bytes(b"\xff\xd8\xff")
        scene = CompositionScene(
            clip_path=str(still),
            narration_path=None,
            duration_s=2.0,
        )
        cmd = _build_normalize_cmd(**self._kwargs(scene, fps=30))
        # Both flags must appear, both before the matching -i.
        assert "-loop" in cmd
        assert cmd[cmd.index("-loop") + 1] == "1"
        assert "-framerate" in cmd
        assert cmd[cmd.index("-framerate") + 1] == "30"
        # Loop flags precede the input.
        assert cmd.index("-loop") < cmd.index("-i")

    def test_video_clip_uses_stream_loop(self, tmp_path):
        # mp4 input — different loop semantics.
        scene = _make_scene(tmp_path)
        cmd = _build_normalize_cmd(**self._kwargs(scene))
        assert "-stream_loop" in cmd
        assert cmd[cmd.index("-stream_loop") + 1] == "-1"
        # Should NOT also have -loop 1 (those are for stills only).
        assert "-loop" not in cmd

    def test_ken_burns_vf_used_for_stills_when_enabled(self, tmp_path):
        still = tmp_path / "img.png"
        still.write_bytes(b"\x89PNG")
        scene = CompositionScene(clip_path=str(still), duration_s=4.0)
        cmd = _build_normalize_cmd(**self._kwargs(
            scene, ken_burns_enabled=True,
        ))
        vf = cmd[cmd.index("-vf") + 1]
        # Ken Burns filtergraph uses zoompan
        assert "zoompan=z=" in vf
        # 2× upsample for headroom
        assert "scale=w=3840:h=2160" in vf  # default 1920x1080 → 2× = 3840x2160

    def test_ken_burns_skipped_for_video_clips(self, tmp_path):
        # Even with ken_burns_enabled=True, real video clips keep the
        # static scale+pad graph (zoompan only fires on stills).
        scene = _make_scene(tmp_path)
        cmd = _build_normalize_cmd(**self._kwargs(
            scene, ken_burns_enabled=True,
        ))
        vf = cmd[cmd.index("-vf") + 1]
        assert "zoompan" not in vf
        assert "scale=w=1920:h=1080" in vf

    def test_ken_burns_skipped_for_stills_when_disabled(self, tmp_path):
        still = tmp_path / "img.webp"
        still.write_bytes(b"RIFF")
        scene = CompositionScene(clip_path=str(still), duration_s=2.0)
        cmd = _build_normalize_cmd(**self._kwargs(
            scene, ken_burns_enabled=False,
        ))
        vf = cmd[cmd.index("-vf") + 1]
        assert "zoompan" not in vf

    def test_ken_burns_variant_rotates_with_scene_idx(self, tmp_path):
        """Adjacent scenes get different start positions — visual variety."""
        still = tmp_path / "img.jpg"
        still.write_bytes(b"\xff\xd8\xff")
        scene = CompositionScene(clip_path=str(still), duration_s=2.0)

        seen_vfs: list[str] = []
        for idx in range(4):
            cmd = _build_normalize_cmd(**self._kwargs(
                scene, ken_burns_enabled=True, scene_idx=idx,
            ))
            seen_vfs.append(cmd[cmd.index("-vf") + 1])

        # Four variants are pre-defined in _KEN_BURNS_VARIANTS — one per
        # scene_idx mod 4. They MUST differ pairwise.
        assert len(set(seen_vfs)) == 4

        # Variant 5 wraps to variant 1 — same vf as scene_idx=1.
        cmd5 = _build_normalize_cmd(**self._kwargs(
            scene, ken_burns_enabled=True, scene_idx=5,
        ))
        assert cmd5[cmd5.index("-vf") + 1] == seen_vfs[1]


class TestIsStillImage:
    @pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"])
    def test_image_extensions_recognized(self, ext):
        assert _is_still_image(f"/path/to/file{ext}") is True
        # Case-insensitive
        assert _is_still_image(f"/path/to/file{ext.upper()}") is True

    @pytest.mark.parametrize("ext", [".mp4", ".mov", ".mkv", ".webm", ".avi"])
    def test_video_extensions_rejected(self, ext):
        assert _is_still_image(f"/path/to/clip{ext}") is False

    def test_unknown_extension_treated_as_video(self):
        assert _is_still_image("/path/to/file.xyz") is False

    def test_no_extension_treated_as_video(self):
        assert _is_still_image("/path/to/no_extension") is False


class TestBuildKenBurnsFilter:
    def test_zoom_expression_progresses_over_duration_frames(self):
        # zoom = 1 + (delta / duration_frames) * on
        # delta = 1.10 - 1.0 = 0.10
        # duration_frames = round(2.0 * 30) = 60
        vf = _build_ken_burns_filter(
            width=1920, height=1080, fps=30, duration_s=2.0,
            zoom_factor=1.10, variant_idx=0,
        )
        # Match coefficient/denominator structure
        assert "0.1000/60" in vf
        assert "*on" in vf

    def test_variant_idx_rotates_through_four_positions(self):
        # 4 variants pre-defined — index 0..3 each produce a distinct
        # start position; index 4 wraps back to 0.
        positions: list[str] = []
        for i in range(4):
            vf = _build_ken_burns_filter(
                width=640, height=360, fps=30, duration_s=1.0,
                zoom_factor=1.10, variant_idx=i,
            )
            # extract the "x='...':y='...'" segment
            x_start = vf.index("x='")
            positions.append(vf[x_start:x_start + 60])
        assert len(set(positions)) == 4

        wrapped = _build_ken_burns_filter(
            width=640, height=360, fps=30, duration_s=1.0,
            zoom_factor=1.10, variant_idx=4,
        )
        x_idx = wrapped.index("x='")
        assert wrapped[x_idx:x_idx + 60] == positions[0]

    def test_duration_frames_minimum_one(self):
        # Even at sub-frame durations, never produce duration_frames=0
        # (would be a division by zero in zoom_expr / d=0 in zoompan).
        vf = _build_ken_burns_filter(
            width=100, height=100, fps=30, duration_s=0.0,
            zoom_factor=1.10, variant_idx=0,
        )
        # d=1 in the zoompan args
        assert "d=1" in vf

    def test_output_size_in_zoompan(self):
        vf = _build_ken_burns_filter(
            width=1280, height=720, fps=30, duration_s=2.0,
            zoom_factor=1.10, variant_idx=0,
        )
        assert "s=1280x720" in vf
        assert "fps=30" in vf


# ---------------------------------------------------------------------------
# _build_concat_cmd
# ---------------------------------------------------------------------------


class TestBuildConcatCmd:
    def test_includes_concat_safe_and_copy(self):
        cmd = _build_concat_cmd(
            binary="/usr/bin/ffmpeg",
            list_path="/tmp/list.txt",
            output_path="/tmp/out.mp4",
            loglevel="error",
        )
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "concat"
        assert "-safe" in cmd
        assert cmd[cmd.index("-safe") + 1] == "0"
        # Stream-copy avoids re-encode at concat time
        assert "-c" in cmd
        assert cmd[cmd.index("-c") + 1] == "copy"
        # List file routed through -i
        assert cmd[cmd.index("-i") + 1] == "/tmp/list.txt"


# ---------------------------------------------------------------------------
# _build_burn_captions_cmd
# ---------------------------------------------------------------------------


class TestBuildBurnCaptionsCmd:
    def _kwargs(self, **overrides):
        base = dict(
            binary="/usr/bin/ffmpeg",
            video_in="/tmp/in.mp4",
            caption_path="/tmp/captions.srt",
            encoder="libx264",
            preset="medium",
            crf=20,
            loglevel="error",
            hwaccel="",
            output_path="/tmp/out.mp4",
        )
        base.update(overrides)
        return base

    def test_subtitles_filter_applied(self):
        cmd = _build_burn_captions_cmd(**self._kwargs())
        idx = cmd.index("-vf")
        vf = cmd[idx + 1]
        assert vf.startswith("subtitles='")
        assert "captions.srt" in vf

    def test_audio_stream_copied(self):
        cmd = _build_burn_captions_cmd(**self._kwargs())
        # Audio should be -c:a copy (no re-encode)
        assert cmd[cmd.index("-c:a") + 1] == "copy"

    def test_path_separators_normalized_for_filtergraph(self):
        # Windows-style backslashes must be flipped to forward slashes
        # for ffmpeg's subtitles filter — and any colons (from drive
        # letters) need backslash-escaping inside the filtergraph string.
        cmd = _build_burn_captions_cmd(**self._kwargs(
            caption_path=r"C:\videos\captions.srt",
        ))
        vf = cmd[cmd.index("-vf") + 1]
        # No raw backslashes (path separators flipped)
        assert r"\videos" not in vf
        # Drive-letter colon escaped
        assert r"C\:" in vf
        # Forward-slash path body preserved
        assert "/videos/captions.srt" in vf

    def test_force_style_includes_alignment_for_position(self):
        # caption_position="top" → libass numpad alignment 8.
        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="top"))
        vf = cmd[cmd.index("-vf") + 1]
        assert "force_style='" in vf
        assert "Alignment=8" in vf

        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="bottom"))
        vf = cmd[cmd.index("-vf") + 1]
        assert "Alignment=2" in vf

        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="middle"))
        vf = cmd[cmd.index("-vf") + 1]
        assert "Alignment=5" in vf

    def test_unknown_position_falls_back_to_middle(self):
        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="diagonal"))
        vf = cmd[cmd.index("-vf") + 1]
        # Any value not in the {top, middle, bottom} map → middle (5).
        assert "Alignment=5" in vf

    def test_force_style_includes_font_size(self):
        cmd = _build_burn_captions_cmd(**self._kwargs(caption_font_size=42))
        vf = cmd[cmd.index("-vf") + 1]
        assert "FontSize=42" in vf

    def test_force_style_default_font_size(self):
        # The default _DEFAULT_CAPTION_FONT_SIZE is 28.
        cmd = _build_burn_captions_cmd(**self._kwargs())
        vf = cmd[cmd.index("-vf") + 1]
        assert "FontSize=28" in vf


# ---------------------------------------------------------------------------
# _parse_probe
# ---------------------------------------------------------------------------


class TestParseProbe:
    def test_parses_full_payload(self):
        payload = """
        {
          "format": {
            "duration": "12.345",
            "size": "98765",
            "format_name": "mov,mp4,m4a"
          },
          "streams": [
            {
              "codec_type": "video",
              "codec_name": "h264",
              "width": 1920,
              "height": 1080,
              "r_frame_rate": "30000/1001"
            },
            {
              "codec_type": "audio",
              "codec_name": "aac"
            }
          ]
        }
        """
        out = _parse_probe(payload)
        assert out["duration_s"] == pytest.approx(12.345)
        assert out["file_size_bytes"] == 98765
        assert out["container"] == "mov"
        assert out["width"] == 1920
        assert out["height"] == 1080
        assert out["codec"] == "h264"
        # 30000 / 1001 ≈ 29.97
        assert out["fps"] == pytest.approx(30000 / 1001)

    def test_missing_format_block_yields_empty_format_fields(self):
        payload = '{"streams": [{"codec_type": "video", "width": 640, "height": 480}]}'
        out = _parse_probe(payload)
        assert "duration_s" not in out
        assert "file_size_bytes" not in out
        assert "container" not in out
        assert out["width"] == 640
        assert out["height"] == 480

    def test_missing_streams_yields_format_only(self):
        payload = '{"format": {"duration": "5.0", "size": "100"}}'
        out = _parse_probe(payload)
        assert out["duration_s"] == 5.0
        assert out["file_size_bytes"] == 100
        assert "width" not in out
        assert "height" not in out

    def test_invalid_json_returns_empty_dict(self):
        assert _parse_probe("not json at all {") == {}
        assert _parse_probe("") == {}

    def test_handles_zero_denominator_in_frame_rate(self):
        payload = """
        {
          "streams": [
            {"codec_type": "video", "r_frame_rate": "30/0", "width": 1, "height": 1, "codec_name": "x"}
          ]
        }
        """
        out = _parse_probe(payload)
        # fps falls back to 0.0 when the denominator is 0 — no crash,
        # no infinity, no division-by-zero exception.
        assert out.get("fps") == 0.0
        assert out["width"] == 1

    def test_skips_streams_without_video_type(self):
        payload = """
        {
          "streams": [
            {"codec_type": "audio", "codec_name": "aac"},
            {"codec_type": "subtitle", "codec_name": "mov_text"}
          ]
        }
        """
        out = _parse_probe(payload)
        assert "width" not in out
        assert "height" not in out
        assert "codec" not in out


# ---------------------------------------------------------------------------
# compose() — happy + failure paths
# ---------------------------------------------------------------------------


class TestComposeHappyPath:
    @pytest.mark.asyncio
    async def test_writes_output_and_marks_success(self, tmp_path):
        compositor = _make_compositor({"binary_path": "ffmpeg"})
        request = _make_request(tmp_path)

        # Patch shutil.which so binary resolution succeeds and ffprobe
        # is treated as present.
        def fake_which(name: str):
            return f"/usr/bin/{name}"

        # _run_blocking returns rc=0, no stderr — and we need the output
        # file to actually exist when compose() is done. The compositor
        # writes the final output via shutil.copyfile from the last
        # intermediate. Provide a side-effect that creates a fake output
        # whenever the command writes to the request.output_path.
        def fake_run(cmd: list[str]):
            # The output path is always the LAST argv. Touch it so the
            # compositor's `if os.path.exists(...)` checks pass.
            out_path = cmd[-1]
            try:
                # ffmpeg writes into a tempdir for intermediate steps,
                # so we always create the file at whatever path was
                # passed as the trailing arg.
                with open(out_path, "wb") as f:
                    f.write(b"\x00FAKE_MP4")
            except OSError:
                pass
            return (0, "", "")

        # ffprobe returns a minimal but valid JSON payload.
        ffprobe_payload = (
            '{"format": {"duration": "2.0", "size": "9", "format_name": "mp4"},'
            '"streams": [{"codec_type": "video", "codec_name": "h264",'
            '"width": 1920, "height": 1080, "r_frame_rate": "30/1"}]}'
        )

        with patch("services.media_compositors.ffmpeg_local.shutil.which", side_effect=fake_which):
            with patch.object(ffmpeg_mod, "_run_blocking", side_effect=fake_run):
                with patch.object(
                    ffmpeg_mod, "_ffprobe_blocking",
                    return_value=(0, ffprobe_payload, ""),
                ):
                    result = await compositor.compose(request)

        assert result.success is True, f"unexpected error: {result.error}"
        assert result.output_path == request.output_path
        assert result.error is None
        # Probe-derived attributes propagate
        assert result.width == 1920
        assert result.height == 1080
        assert result.codec == "h264"
        # local-only — always electricity, never dollars
        assert result.cost_usd == 0.0
        assert result.metadata["scene_count"] == 1
        assert result.metadata["encoder"] == "libx264"


class TestComposeFailurePath:
    @pytest.mark.asyncio
    async def test_normalize_failure_marks_step(self, tmp_path):
        compositor = _make_compositor({"binary_path": "ffmpeg"})
        request = _make_request(tmp_path)

        def fake_which(name: str):
            return f"/usr/bin/{name}"

        with patch("services.media_compositors.ffmpeg_local.shutil.which", side_effect=fake_which):
            # rc=1 with stderr — this is the FIRST call (normalize).
            with patch.object(
                ffmpeg_mod, "_run_blocking",
                return_value=(1, "", "Invalid data found when processing input"),
            ):
                with patch.object(
                    ffmpeg_mod, "_ffprobe_blocking",
                    return_value=(0, "{}", ""),
                ):
                    result = await compositor.compose(request)

        assert result.success is False
        assert "normalize step failed" in (result.error or "")
        assert result.output_path is None

    @pytest.mark.asyncio
    async def test_concat_failure_after_normalize_success(self, tmp_path):
        compositor = _make_compositor({"binary_path": "ffmpeg"})
        request = _make_request(tmp_path)

        def fake_which(name: str):
            return f"/usr/bin/{name}"

        # First call (normalize) succeeds; second (concat) fails.
        calls = {"n": 0}

        def fake_run(cmd: list[str]):
            calls["n"] += 1
            if calls["n"] == 1:
                # normalize — write the intermediate file so it exists
                out_path = cmd[-1]
                with open(out_path, "wb") as f:
                    f.write(b"\x00")
                return (0, "", "")
            return (1, "", "concat list invalid")

        with patch("services.media_compositors.ffmpeg_local.shutil.which", side_effect=fake_which):
            with patch.object(ffmpeg_mod, "_run_blocking", side_effect=fake_run):
                with patch.object(
                    ffmpeg_mod, "_ffprobe_blocking",
                    return_value=(0, "{}", ""),
                ):
                    result = await compositor.compose(request)

        assert result.success is False
        assert "concat step failed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_binary_returns_failure(self, tmp_path):
        compositor = _make_compositor({"binary_path": "no-such-ffmpeg"})
        request = _make_request(tmp_path)

        with patch("services.media_compositors.ffmpeg_local.shutil.which", return_value=None):
            result = await compositor.compose(request)

        assert result.success is False
        assert "binary not found" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


class TestProbe:
    @pytest.mark.asyncio
    async def test_missing_path_returns_empty_dict(self, tmp_path):
        compositor = _make_compositor()
        out = await compositor.probe(str(tmp_path / "missing.mp4"))
        assert out == {}

    @pytest.mark.asyncio
    async def test_empty_path_returns_empty_dict(self):
        compositor = _make_compositor()
        out = await compositor.probe("")
        assert out == {}

    @pytest.mark.asyncio
    async def test_ffprobe_failure_returns_empty_dict(self, tmp_path):
        media = tmp_path / "x.mp4"
        media.write_bytes(b"\x00" * 8)
        compositor = _make_compositor()
        with patch(
            "services.media_compositors.ffmpeg_local.shutil.which",
            return_value="/usr/bin/ffprobe",
        ):
            with patch.object(
                ffmpeg_mod, "_ffprobe_blocking",
                return_value=(1, "", "I/O error"),
            ):
                out = await compositor.probe(str(media))
        assert out == {}

    @pytest.mark.asyncio
    async def test_happy_path_returns_dict(self, tmp_path):
        media = tmp_path / "x.mp4"
        media.write_bytes(b"\x00" * 100)
        compositor = _make_compositor()

        ffprobe_payload = (
            '{"format": {"duration": "5.0", "size": "100", "format_name": "mp4"},'
            '"streams": [{"codec_type": "video", "codec_name": "h264",'
            '"width": 1280, "height": 720, "r_frame_rate": "24/1"}]}'
        )
        with patch(
            "services.media_compositors.ffmpeg_local.shutil.which",
            return_value="/usr/bin/ffprobe",
        ):
            with patch.object(
                ffmpeg_mod, "_ffprobe_blocking",
                return_value=(0, ffprobe_payload, ""),
            ):
                out = await compositor.probe(str(media))
        assert out["duration_s"] == 5.0
        assert out["width"] == 1280
        assert out["height"] == 720
        assert out["codec"] == "h264"
        # File-size injected by the wrapper, not by ffprobe
        assert out["file_size_bytes"] == 100

    @pytest.mark.asyncio
    async def test_no_ffprobe_binary_returns_empty(self, tmp_path):
        media = tmp_path / "x.mp4"
        media.write_bytes(b"\x00")
        compositor = _make_compositor()
        with patch(
            "services.media_compositors.ffmpeg_local.shutil.which",
            return_value=None,
        ):
            out = await compositor.probe(str(media))
        assert out == {}
