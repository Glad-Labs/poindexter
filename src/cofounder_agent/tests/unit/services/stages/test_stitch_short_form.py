"""Unit tests for ``services/stages/stitch_short_form.py``.

Mostly mirrors test_stitch_long_form, but adds coverage of the
60-second short-form duration cap (``_enforce_duration_cap``) which is
the key short-form-specific behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.media_compositor import CompositionResult, CompositionScene
from plugins.stage import Stage
from services.stages.stitch_short_form import (
    _SHORT_FORM_MAX_DURATION_S,
    StitchShortFormStage,
    _enforce_duration_cap,
)


def _make_site_config(pool: Any = None):
    return SimpleNamespace(
        get=lambda _k, _d="": _d,
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=pool,
    )


def _full_context(tmp_path, scene_count: int = 2):
    """Build a fully-populated context for short-form."""
    visuals = []
    audios = []
    tts_scenes = []
    visual_lookup: list[str] = []
    for i in range(scene_count):
        visual_path = tmp_path / f"vis_{i}.jpg"
        visual_path.write_bytes(b"img")
        audio_path = tmp_path / f"aud_{i}.mp3"
        audio_path.write_bytes(b"aud")
        visual_lookup.append(str(visual_path))
        visuals.append({"scene_idx": i, "clip_path": str(visual_path), "url": "u"})
        audios.append(str(audio_path))
        tts_scenes.append({
            "scene_idx": i,
            "audio_path": str(audio_path),
            "duration_s": 10.0,
            "text": f"text {i}",
        })

    return {
        "task_id": "t-test",
        "post_id": "post-1",
        "site_config": _make_site_config(),
        "video_script": {
            "long_form": {"scenes": []},
            "short_form": {
                "intro_hook": "Hook!",
                "scenes": [
                    {"narration_text": f"text {i}", "visual_prompt": "v",
                     "duration_s_hint": 13}
                    for i in range(scene_count)
                ],
            },
        },
        "video_scene_visuals": {
            "long_form": [],
            "short_form": visuals,
        },
        "video_tts": {
            "long_form": {"scenes": []},
            "short_form": {
                "intro_audio_path": audios[0],
                "intro_duration_s": 1.5,
                "scenes": tts_scenes,
                "total_duration_s": float(scene_count) * 10.0,
            },
        },
    }


def _success_compose_result(output_path: str):
    return CompositionResult(
        success=True,
        output_path=output_path,
        duration_s=20.0,
        width=1080,
        height=1920,
        fps=30,
        codec="h264",
        file_size_bytes=2048,
        cost_usd=0.0,
        electricity_kwh=0.005,
        metadata={},
    )


# ---------------------------------------------------------------------------
# _enforce_duration_cap — short-form's signature behavior
# ---------------------------------------------------------------------------


class TestEnforceDurationCap:
    def test_under_cap_unchanged(self):
        scenes = [
            CompositionScene(clip_path=f"/c/{i}", duration_s=10.0)
            for i in range(5)  # 50s total, under 60s cap
        ]
        out = _enforce_duration_cap(scenes)
        assert len(out) == 5

    def test_at_cap_unchanged(self):
        scenes = [
            CompositionScene(clip_path=f"/c/{i}", duration_s=15.0)
            for i in range(4)  # exactly 60s
        ]
        out = _enforce_duration_cap(scenes)
        assert len(out) == 4

    def test_over_cap_drops_trailing(self):
        # 7 × 10s = 70s; trim to ≤60s → drop 1 trailing scene.
        scenes = [
            CompositionScene(clip_path=f"/c/{i}", duration_s=10.0)
            for i in range(7)
        ]
        out = _enforce_duration_cap(scenes)
        # Total should be ≤ 60.0
        total = sum(s.duration_s for s in out)
        assert total <= _SHORT_FORM_MAX_DURATION_S
        # Trimmed from the END — first scene preserved
        assert out[0].clip_path == "/c/0"
        assert len(out) < 7

    def test_far_over_cap(self):
        # 12 × 10s = 120s → trim down significantly
        scenes = [
            CompositionScene(clip_path=f"/c/{i}", duration_s=10.0)
            for i in range(12)
        ]
        out = _enforce_duration_cap(scenes)
        total = sum(s.duration_s for s in out)
        assert total <= _SHORT_FORM_MAX_DURATION_S
        assert out[0].clip_path == "/c/0"  # head preserved

    def test_empty_list(self):
        assert _enforce_duration_cap([]) == []

    def test_single_giant_scene_returns_empty_or_one(self):
        # A single 90s scene: helper drops it (loop continues until total ≤ cap).
        scenes = [CompositionScene(clip_path="/c", duration_s=90.0)]
        out = _enforce_duration_cap(scenes)
        # After popping the 90s scene, total = 0 → loop terminates.
        # Helper allows empty result.
        assert isinstance(out, list)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(StitchShortFormStage(), Stage)

    def test_metadata(self):
        s = StitchShortFormStage()
        assert s.name == "video.stitch_short_form"
        assert s.halts_on_failure is False


# ---------------------------------------------------------------------------
# Stage.execute — early returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteEarlyReturns:
    async def test_missing_site_config_returns_not_ok(self):
        result = await StitchShortFormStage().execute({}, {})
        assert result.ok is False
        assert "site_config" in result.detail

    async def test_missing_script_scenes_returns_not_ok(self):
        ctx: dict[str, Any] = {
            "site_config": _make_site_config(),
            "video_script": {"short_form": {"scenes": []}},
            "video_tts": {"short_form": {"scenes": []}},
        }
        result = await StitchShortFormStage().execute(ctx, {})
        assert result.ok is False
        assert "missing scenes" in result.detail

    async def test_no_compositor_returns_not_ok(self, tmp_path):
        ctx = _full_context(tmp_path)
        with patch(
            "services.stages.stitch_short_form.resolve_compositor",
            return_value=None,
        ):
            result = await StitchShortFormStage().execute(ctx, {})
        assert result.ok is False
        assert "MediaCompositor" in result.detail

    async def test_no_buildable_scenes_returns_not_ok(self, tmp_path):
        ctx = _full_context(tmp_path, scene_count=1)
        ctx["video_scene_visuals"]["short_form"] = [
            {"scene_idx": 0, "clip_path": "", "url": ""},
        ]
        compositor = SimpleNamespace(name="ffmpeg_local", compose=AsyncMock())
        with patch(
            "services.stages.stitch_short_form.resolve_compositor",
            return_value=compositor,
        ):
            result = await StitchShortFormStage().execute(ctx, {})
        assert result.ok is False
        assert "no usable scenes" in result.detail


# ---------------------------------------------------------------------------
# Stage.execute — happy path + duration cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteHappyPath:
    async def test_produces_short_form_output(self, tmp_path):
        ctx = _full_context(tmp_path, scene_count=2)
        out_video = tmp_path / "task_short.mp4"
        out_video.write_bytes(b"video")

        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=_success_compose_result(str(out_video))),
        )
        with patch(
            "services.stages.stitch_short_form.resolve_compositor",
            return_value=compositor,
        ), patch(
            "services.stages.stitch_short_form.upload_to_object_storage",
            AsyncMock(return_value="https://cdn.example/short.mp4"),
        ), patch(
            "services.stages.stitch_short_form.persist_media_asset",
            AsyncMock(return_value="asset-2"),
        ):
            result = await StitchShortFormStage().execute(ctx, {})

        assert result.ok is True
        sf = result.context_updates["video_outputs"]["short_form"]
        assert sf["output_path"] == str(out_video)
        assert sf["public_url"] == "https://cdn.example/short.mp4"
        assert sf["media_asset_id"] == "asset-2"

    async def test_calls_compositor_with_vertical_dimensions(self, tmp_path):
        ctx = _full_context(tmp_path, scene_count=1)
        out_video = tmp_path / "task_short.mp4"
        out_video.write_bytes(b"v")
        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=_success_compose_result(str(out_video))),
        )
        with patch(
            "services.stages.stitch_short_form.resolve_compositor",
            return_value=compositor,
        ), patch(
            "services.stages.stitch_short_form.upload_to_object_storage",
            AsyncMock(return_value=""),
        ), patch(
            "services.stages.stitch_short_form.persist_media_asset",
            AsyncMock(return_value=None),
        ):
            await StitchShortFormStage().execute(ctx, {})

        request = compositor.compose.await_args.args[0]
        # Vertical 9:16
        assert request.width == 1080
        assert request.height == 1920

    async def test_60s_cap_trims_trailing_scenes(self, tmp_path):
        # Build 8 scenes of 10s each = 80s total → exceeds cap → trimmed.
        ctx = _full_context(tmp_path, scene_count=8)
        out_video = tmp_path / "task_short.mp4"
        out_video.write_bytes(b"v")
        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=_success_compose_result(str(out_video))),
        )
        with patch(
            "services.stages.stitch_short_form.resolve_compositor",
            return_value=compositor,
        ), patch(
            "services.stages.stitch_short_form.upload_to_object_storage",
            AsyncMock(return_value=""),
        ), patch(
            "services.stages.stitch_short_form.persist_media_asset",
            AsyncMock(return_value=None),
        ):
            result = await StitchShortFormStage().execute(ctx, {})

        assert result.ok is True
        # Verify the compositor saw fewer scenes than the original 8.
        request = compositor.compose.await_args.args[0]
        total_dur = sum(s.duration_s for s in request.scenes)
        assert total_dur <= _SHORT_FORM_MAX_DURATION_S
        assert len(request.scenes) < 8
        # Head preserved (scene_idx=0's clip path)
        first_clip = request.scenes[0].clip_path
        assert first_clip.endswith("vis_0.jpg")

    async def test_compositor_failure_returns_not_ok(self, tmp_path):
        ctx = _full_context(tmp_path, scene_count=1)
        bad_result = CompositionResult(
            success=False, output_path=None, error="oom",
            metadata={"duration_ms": 100},
        )
        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=bad_result),
        )
        with patch(
            "services.stages.stitch_short_form.resolve_compositor",
            return_value=compositor,
        ):
            result = await StitchShortFormStage().execute(ctx, {})
        assert result.ok is False
        assert "compositor failed" in result.detail
