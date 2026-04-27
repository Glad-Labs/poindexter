"""Unit tests for ``services/stages/stitch_long_form.py``.

The Stage drives a MediaCompositor + R2 upload + DB write, all of
which are mocked. Tests cover Protocol conformance, the missing-
upstream early returns, and the happy path through to
``video_outputs.long_form``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.media_compositor import CompositionResult
from plugins.stage import Stage
from services.stages.stitch_long_form import StitchLongFormStage


def _make_site_config(pool: Any = None):
    return SimpleNamespace(
        get=lambda _k, _d="": _d,
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=pool,
    )


def _full_context(tmp_path):
    """Build a fully-populated context with an existing visual file."""
    visual_path = tmp_path / "visual_0.jpg"
    visual_path.write_bytes(b"fake-image")
    audio_path = tmp_path / "audio_0.mp3"
    audio_path.write_bytes(b"fake-audio")

    return {
        "task_id": "t-test",
        "post_id": "post-1",
        "site_config": _make_site_config(),
        "video_script": {
            "long_form": {
                "intro_hook": "Welcome.",
                "outro_cta": "Bye.",
                "scenes": [
                    {"narration_text": "n", "visual_prompt": "v", "duration_s_hint": 30},
                ],
            },
            "short_form": {"scenes": []},
        },
        "video_scene_visuals": {
            "long_form": [
                {"scene_idx": 0, "clip_path": str(visual_path), "url": "u"},
            ],
            "short_form": [],
        },
        "video_tts": {
            "long_form": {
                "intro_audio_path": str(audio_path),
                "intro_duration_s": 2.0,
                "outro_audio_path": str(audio_path),
                "outro_duration_s": 1.5,
                "scenes": [{
                    "scene_idx": 0,
                    "audio_path": str(audio_path),
                    "duration_s": 5.0,
                    "text": "n",
                }],
                "total_duration_s": 8.5,
            },
            "short_form": {"scenes": []},
        },
    }


def _success_compose_result(output_path: str):
    return CompositionResult(
        success=True,
        output_path=output_path,
        duration_s=8.5,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        file_size_bytes=2048,
        cost_usd=0.0,
        electricity_kwh=0.01,
        metadata={"encoder": "ffmpeg"},
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(StitchLongFormStage(), Stage)

    def test_metadata(self):
        s = StitchLongFormStage()
        assert s.name == "video.stitch_long_form"
        assert s.halts_on_failure is False


# ---------------------------------------------------------------------------
# Stage.execute — early returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteEarlyReturns:
    async def test_missing_site_config_returns_not_ok(self):
        result = await StitchLongFormStage().execute({}, {})
        assert result.ok is False
        assert "site_config" in result.detail

    async def test_missing_script_scenes_returns_not_ok(self):
        ctx: dict[str, Any] = {
            "site_config": _make_site_config(),
            "video_script": {"long_form": {"scenes": []}},
            "video_tts": {"long_form": {"scenes": []}},
        }
        result = await StitchLongFormStage().execute(ctx, {})
        assert result.ok is False
        assert "missing scenes" in result.detail

    async def test_missing_tts_scenes_returns_not_ok(self):
        ctx: dict[str, Any] = {
            "site_config": _make_site_config(),
            "video_script": {"long_form": {"scenes": [{"narration_text": "n"}]}},
            "video_tts": {"long_form": {"scenes": []}},
        }
        result = await StitchLongFormStage().execute(ctx, {})
        assert result.ok is False

    async def test_no_compositor_returns_not_ok(self, tmp_path):
        ctx = _full_context(tmp_path)
        with patch(
            "services.stages.stitch_long_form.resolve_compositor",
            return_value=None,
        ):
            result = await StitchLongFormStage().execute(ctx, {})
        assert result.ok is False
        assert "MediaCompositor" in result.detail

    async def test_no_buildable_scenes_returns_not_ok(self, tmp_path):
        # Visual without clip_path → build_scenes returns []
        ctx = _full_context(tmp_path)
        ctx["video_scene_visuals"]["long_form"] = [
            {"scene_idx": 0, "clip_path": "", "url": ""},
        ]
        with patch(
            "services.stages.stitch_long_form.resolve_compositor",
            return_value=SimpleNamespace(name="ffmpeg_local", compose=AsyncMock()),
        ):
            result = await StitchLongFormStage().execute(ctx, {})
        assert result.ok is False
        assert "no usable scenes" in result.detail


# ---------------------------------------------------------------------------
# Stage.execute — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteHappyPath:
    async def test_produces_video_outputs(self, tmp_path):
        ctx = _full_context(tmp_path)
        out_video = tmp_path / "task_long.mp4"
        out_video.write_bytes(b"video-bytes")

        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=_success_compose_result(str(out_video))),
        )
        with patch(
            "services.stages.stitch_long_form.resolve_compositor",
            return_value=compositor,
        ), patch(
            "services.stages.stitch_long_form.upload_to_object_storage",
            AsyncMock(return_value="https://cdn.example/v.mp4"),
        ), patch(
            "services.stages.stitch_long_form.persist_media_asset",
            AsyncMock(return_value="asset-uuid"),
        ):
            result = await StitchLongFormStage().execute(ctx, {})

        assert result.ok is True
        outputs = result.context_updates["video_outputs"]
        assert "long_form" in outputs
        lf = outputs["long_form"]
        assert lf["output_path"] == str(out_video)
        assert lf["public_url"] == "https://cdn.example/v.mp4"
        assert lf["media_asset_id"] == "asset-uuid"
        assert lf["width"] == 1920
        assert lf["height"] == 1080
        # Stages flag set
        stages = result.context_updates["stages"]
        assert stages["video.stitch_long_form"] is True
        # Metrics
        assert result.metrics["uploaded"] is True

    async def test_compositor_failure_returns_not_ok(self, tmp_path):
        ctx = _full_context(tmp_path)
        bad_result = CompositionResult(
            success=False, output_path=None, error="ffmpeg failed",
            metadata={"duration_ms": 1234},
        )
        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=bad_result),
        )
        with patch(
            "services.stages.stitch_long_form.resolve_compositor",
            return_value=compositor,
        ):
            result = await StitchLongFormStage().execute(ctx, {})

        assert result.ok is False
        assert "compositor failed" in result.detail
        assert "ffmpeg failed" in result.detail

    async def test_upload_failure_does_not_kill_stage(self, tmp_path):
        # upload_to_object_storage returns "" → public_url empty,
        # but stage still ok since the file was rendered.
        ctx = _full_context(tmp_path)
        out_video = tmp_path / "task_long.mp4"
        out_video.write_bytes(b"video-bytes")
        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=_success_compose_result(str(out_video))),
        )
        with patch(
            "services.stages.stitch_long_form.resolve_compositor",
            return_value=compositor,
        ), patch(
            "services.stages.stitch_long_form.upload_to_object_storage",
            AsyncMock(return_value=""),
        ), patch(
            "services.stages.stitch_long_form.persist_media_asset",
            AsyncMock(return_value=None),
        ):
            result = await StitchLongFormStage().execute(ctx, {})

        assert result.ok is True
        lf = result.context_updates["video_outputs"]["long_form"]
        assert lf["public_url"] == ""
        assert lf["media_asset_id"] is None
        assert result.metrics["uploaded"] is False

    async def test_calls_compositor_with_landscape_dimensions(self, tmp_path):
        ctx = _full_context(tmp_path)
        out_video = tmp_path / "task_long.mp4"
        out_video.write_bytes(b"v")
        compositor = SimpleNamespace(
            name="ffmpeg_local",
            compose=AsyncMock(return_value=_success_compose_result(str(out_video))),
        )
        with patch(
            "services.stages.stitch_long_form.resolve_compositor",
            return_value=compositor,
        ), patch(
            "services.stages.stitch_long_form.upload_to_object_storage",
            AsyncMock(return_value=""),
        ), patch(
            "services.stages.stitch_long_form.persist_media_asset",
            AsyncMock(return_value=None),
        ):
            await StitchLongFormStage().execute(ctx, {})

        # Assert compose() received a CompositionRequest with landscape dims.
        request = compositor.compose.await_args.args[0]
        assert request.width == 1920
        assert request.height == 1080
        assert request.fps == 30
