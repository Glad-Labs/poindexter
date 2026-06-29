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
    _build_soundtrack_mix_cmd,
    _compute_narration_pad_s,
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

    def test_missing_narration_track_rejected(self, tmp_path):
        request = _make_request(
            tmp_path,
            narration_track_path=str(tmp_path / "missing-narr.mp3"),
        )
        err = _validate_inputs(request)
        assert err is not None
        assert "narration_track_path" in err

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
# _compute_narration_pad_s  (short-video narration cutoff fix)
# ---------------------------------------------------------------------------


class TestComputeNarrationPadS:
    def test_zero_when_narration_fits_within_visuals(self):
        # Long-form: the assembled scenes already cover the voiceover, so no
        # padding is needed (legacy duration=first overlay applies).
        assert _compute_narration_pad_s(
            video_dur_s=120.0, narration_dur_s=110.0, tail_pad_s=0.5,
        ) == 0.0

    def test_zero_when_durations_equal(self):
        assert _compute_narration_pad_s(
            video_dur_s=60.0, narration_dur_s=60.0, tail_pad_s=0.5,
        ) == 0.0

    def test_covers_overhang_plus_tail_when_narration_longer(self):
        # Short profile: 48s of visuals, ~58s narration → pad the 10s
        # overhang plus a short tail hold so the final syllable isn't clipped.
        assert _compute_narration_pad_s(
            video_dur_s=48.0, narration_dur_s=58.0, tail_pad_s=0.5,
        ) == pytest.approx(10.5)

    def test_zero_on_nonpositive_durations(self):
        # Missing/unprobeable durations must not produce a bogus pad.
        assert _compute_narration_pad_s(
            video_dur_s=0.0, narration_dur_s=58.0, tail_pad_s=0.5,
        ) == 0.0
        assert _compute_narration_pad_s(
            video_dur_s=48.0, narration_dur_s=0.0, tail_pad_s=0.5,
        ) == 0.0

    def test_negative_tail_clamped_to_zero(self):
        # A misconfigured negative tail never shortens the overhang pad.
        assert _compute_narration_pad_s(
            video_dur_s=48.0, narration_dur_s=58.0, tail_pad_s=-5.0,
        ) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# _build_soundtrack_mix_cmd  (#media-render-fixes: narration overlay)
# ---------------------------------------------------------------------------


class TestBuildSoundtrackMixCmd:
    def _kwargs(self, **overrides):
        base = dict(
            binary="/usr/bin/ffmpeg",
            video_in="/tmp/concat.mp4",
            soundtrack_path="/tmp/narration.mp3",
            soundtrack_dbfs=0.0,
            encoder="libx264",
            preset="medium",
            crf=20,
            audio_bitrate="192k",
            loglevel="error",
            hwaccel="",
        )
        base.update(overrides)
        return base

    def test_two_inputs_video_then_audio(self):
        cmd = _build_soundtrack_mix_cmd(**self._kwargs())
        # First -i is the composed video, second is the audio to overlay.
        first = cmd.index("-i")
        second = cmd.index("-i", first + 1)
        assert cmd[first + 1] == "/tmp/concat.mp4"
        assert cmd[second + 1] == "/tmp/narration.mp3"

    def test_normalize_false_sums_keeps_voice_full_volume(self):
        # The narration overlay sums voice over a silent concat — amix
        # normalize=0 keeps the voice at 100% instead of halving it.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(normalize=False))
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "normalize=0" in af

    def test_normalize_true_averages(self):
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(normalize=True))
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "normalize=1" in af

    def test_default_normalize_is_true(self):
        # Backcompat: the default behaviour (no normalize kwarg) averages.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs())
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "normalize=1" in af

    def test_full_volume_voiceover_uses_zero_db(self):
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(soundtrack_dbfs=0.0))
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "volume=0.0dB" in af

    def test_ambient_bed_uses_requested_dbfs(self):
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(soundtrack_dbfs=-18.0))
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "volume=-18.0dB" in af

    def test_duration_follows_video_not_audio(self):
        # No pad (video_pad_s=0, the long-form case where the narration
        # already fits the visuals): duration=first tracks the video, so a
        # short narration plays out and the video continues silent after.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs())
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "duration=first" in af

    def test_no_pad_maps_video_stream_directly(self):
        # Backcompat: the no-pad path maps the raw video stream (0:v:0),
        # not a filtered label.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs())
        map_idxs = [i for i, tok in enumerate(cmd) if tok == "-map"]
        mapped = {cmd[i + 1] for i in map_idxs}
        assert "0:v:0" in mapped

    def test_pad_holds_final_frame_when_narration_longer(self):
        # When the narration runs longer than the assembled visuals, the
        # video is extended by holding (cloning) its final frame for the
        # overhang so the speaker is never cut off mid-sentence.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(video_pad_s=12.5))
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "tpad=stop_mode=clone:stop_duration=12.500" in af

    def test_pad_uses_duration_longest_so_narration_not_truncated(self):
        # With a pad, the (now-longest) narration must drive the mixed-audio
        # length — duration=longest — instead of being clipped to the
        # shorter silent concat track.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(video_pad_s=12.5))
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "duration=longest" in af
        assert "duration=first" not in af

    def test_pad_maps_padded_video_label(self):
        # The padded path maps the filtered [vpad] label, not the raw stream.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs(video_pad_s=12.5))
        map_idxs = [i for i, tok in enumerate(cmd) if tok == "-map"]
        mapped = {cmd[i + 1] for i in map_idxs}
        assert "[vpad]" in mapped
        assert "0:v:0" not in mapped

    def test_default_video_pad_is_zero_no_regression(self):
        # Omitting video_pad_s preserves the legacy duration=first overlay.
        cmd = _build_soundtrack_mix_cmd(**self._kwargs())
        af = cmd[cmd.index("-filter_complex") + 1]
        assert "tpad" not in af
        assert "duration=first" in af


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
        # force_style uses the OLD SSA alignment convention (not ASS v4+ numpad).
        # In old SSA: 2=bottom-center, 6=top-center, 10=middle-center.
        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="top"))
        vf = cmd[cmd.index("-vf") + 1]
        assert "force_style='" in vf
        assert "Alignment=6" in vf

        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="bottom"))
        vf = cmd[cmd.index("-vf") + 1]
        assert "Alignment=2" in vf

        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="middle"))
        vf = cmd[cmd.index("-vf") + 1]
        assert "Alignment=10" in vf

    def test_unknown_position_falls_back_to_middle(self):
        cmd = _build_burn_captions_cmd(**self._kwargs(caption_position="diagonal"))
        vf = cmd[cmd.index("-vf") + 1]
        # Any value not in the {top, middle, bottom} map → middle (10 in old SSA).
        assert "Alignment=10" in vf

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

    @pytest.mark.asyncio
    async def test_narration_track_overlaid_over_whole_concat(self, tmp_path):
        """#media-render-fixes: a full-length narration_track_path is mixed
        over the whole concat (not bound to scene 0) at full volume — the
        overlay command must reference the narration file and sum (normalize=0)
        so the voice spans every scene."""
        compositor = _make_compositor({"binary_path": "ffmpeg"})
        narr = tmp_path / "narration.mp3"
        narr.write_bytes(b"\x00" * 64)
        request = _make_request(
            tmp_path,
            scenes=[_make_scene(tmp_path, "a"), _make_scene(tmp_path, "b")],
            narration_track_path=str(narr),
        )

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str]):
            captured_cmds.append(list(cmd))
            out_path = cmd[-1]
            try:
                with open(out_path, "wb") as f:
                    f.write(b"\x00FAKE_MP4")
            except OSError:
                pass
            return (0, "", "")

        ffprobe_payload = (
            '{"format": {"duration": "4.0", "size": "9", "format_name": "mp4"},'
            '"streams": [{"codec_type": "video", "codec_name": "h264",'
            '"width": 1920, "height": 1080, "r_frame_rate": "30/1"}]}'
        )

        with patch(
            "services.media_compositors.ffmpeg_local.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            with patch.object(ffmpeg_mod, "_run_blocking", side_effect=fake_run):
                with patch.object(
                    ffmpeg_mod, "_ffprobe_blocking",
                    return_value=(0, ffprobe_payload, ""),
                ):
                    result = await compositor.compose(request)

        assert result.success is True, f"unexpected error: {result.error}"

        # Exactly one overlay command must reference the narration file via
        # -filter_complex and sum it (normalize=0) at full volume.
        overlay_cmds = [
            c for c in captured_cmds
            if "-filter_complex" in c and str(narr) in c
        ]
        assert len(overlay_cmds) == 1, (
            "expected one narration overlay mix referencing the narration "
            f"file; got {len(overlay_cmds)}"
        )
        af = overlay_cmds[0][overlay_cmds[0].index("-filter_complex") + 1]
        assert "normalize=0" in af  # sum → voice stays at full volume
        assert "volume=0.0dB" in af  # full-volume voiceover

    @staticmethod
    def _probe_payload(duration_s: float) -> str:
        return (
            f'{{"format": {{"duration": "{duration_s}", "size": "9",'
            ' "format_name": "mp4"}, "streams": [{"codec_type": "video",'
            ' "codec_name": "h264", "width": 1080, "height": 1920,'
            ' "r_frame_rate": "30/1"}]}'
        )

    @pytest.mark.asyncio
    async def test_narration_longer_than_visuals_pads_video(self, tmp_path):
        """Short-video cutoff fix: when the narration runs longer than the
        assembled scenes, the overlay holds the final frame (tpad) and mixes
        duration=longest so the voiceover plays to completion instead of
        being clipped to the shorter visual track."""
        compositor = _make_compositor({"binary_path": "ffmpeg"})
        narr = tmp_path / "narration.mp3"
        narr.write_bytes(b"\x00" * 64)
        request = _make_request(
            tmp_path,
            scenes=[_make_scene(tmp_path, "a"), _make_scene(tmp_path, "b")],
            narration_track_path=str(narr),
        )

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str]):
            captured_cmds.append(list(cmd))
            out_path = cmd[-1]
            try:
                with open(out_path, "wb") as f:
                    f.write(b"\x00FAKE_MP4")
            except OSError:
                pass
            return (0, "", "")

        def fake_probe(_probe_bin: str, media_path: str):
            # Narration (58s) outruns the assembled concat (48s).
            if media_path == str(narr):
                return (0, self._probe_payload(58.0), "")
            return (0, self._probe_payload(48.0), "")

        with patch(
            "services.media_compositors.ffmpeg_local.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            with patch.object(ffmpeg_mod, "_run_blocking", side_effect=fake_run):
                with patch.object(
                    ffmpeg_mod, "_ffprobe_blocking", side_effect=fake_probe,
                ):
                    result = await compositor.compose(request)

        assert result.success is True, f"unexpected error: {result.error}"
        overlay_cmds = [
            c for c in captured_cmds
            if "-filter_complex" in c and str(narr) in c
        ]
        assert len(overlay_cmds) == 1
        af = overlay_cmds[0][overlay_cmds[0].index("-filter_complex") + 1]
        # 58s narration - 48s visuals + 0.5s tail = 10.5s held final frame.
        assert "tpad=stop_mode=clone:stop_duration=10.500" in af
        assert "duration=longest" in af

    @pytest.mark.asyncio
    async def test_narration_shorter_than_visuals_no_pad(self, tmp_path):
        """Long-form: when the visuals already cover the narration, the
        overlay stays on the legacy duration=first path (no held frame)."""
        compositor = _make_compositor({"binary_path": "ffmpeg"})
        narr = tmp_path / "narration.mp3"
        narr.write_bytes(b"\x00" * 64)
        request = _make_request(
            tmp_path,
            scenes=[_make_scene(tmp_path, "a"), _make_scene(tmp_path, "b")],
            narration_track_path=str(narr),
        )

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str]):
            captured_cmds.append(list(cmd))
            out_path = cmd[-1]
            try:
                with open(out_path, "wb") as f:
                    f.write(b"\x00FAKE_MP4")
            except OSError:
                pass
            return (0, "", "")

        def fake_probe(_probe_bin: str, media_path: str):
            # Narration (20s) fits inside the assembled concat (48s).
            if media_path == str(narr):
                return (0, self._probe_payload(20.0), "")
            return (0, self._probe_payload(48.0), "")

        with patch(
            "services.media_compositors.ffmpeg_local.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            with patch.object(ffmpeg_mod, "_run_blocking", side_effect=fake_run):
                with patch.object(
                    ffmpeg_mod, "_ffprobe_blocking", side_effect=fake_probe,
                ):
                    result = await compositor.compose(request)

        assert result.success is True, f"unexpected error: {result.error}"
        overlay_cmds = [
            c for c in captured_cmds
            if "-filter_complex" in c and str(narr) in c
        ]
        assert len(overlay_cmds) == 1
        af = overlay_cmds[0][overlay_cmds[0].index("-filter_complex") + 1]
        assert "tpad" not in af
        assert "duration=first" in af


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
