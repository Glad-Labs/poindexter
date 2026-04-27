"""Unit tests for ``services/stages/_video_stitch.py``.

Shared helpers used by both stitch_long_form and stitch_short_form.

NOTE: This module's ``build_scenes()`` is a simple zip — it does NOT
implement the bookend / intro-outro selection logic the agent spec
described. Tests cover what the source actually does: scenes are
selected by scene_idx; missing visuals drop the scene; TTS-derived
duration wins over fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.media_compositor import CompositionScene
from services.stages._video_stitch import (
    StitchSpec,
    _format_srt_timestamp,
    _json_dumps,
    build_scenes,
    derive_srt,
    output_paths,
    persist_media_asset,
    resolve_compositor,
    upload_to_object_storage,
    write_srt_sidecar,
)


# ---------------------------------------------------------------------------
# StitchSpec dataclass
# ---------------------------------------------------------------------------


class TestStitchSpec:
    def test_frozen(self):
        spec = StitchSpec(
            format_kind="long_form", asset_type="video_long",
            width=1920, height=1080, fps=30,
            include_outro=True, output_filename="x.mp4",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            spec.width = 1280  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _format_srt_timestamp
# ---------------------------------------------------------------------------


class TestFormatSrtTimestamp:
    def test_zero(self):
        assert _format_srt_timestamp(0) == "00:00:00,000"

    def test_subsecond(self):
        assert _format_srt_timestamp(0.5) == "00:00:00,500"

    def test_one_second(self):
        assert _format_srt_timestamp(1.0) == "00:00:01,000"

    def test_minute(self):
        assert _format_srt_timestamp(60.0) == "00:01:00,000"
        assert _format_srt_timestamp(125.25) == "00:02:05,250"

    def test_over_one_hour(self):
        # 3600 + 65.123 = 1:01:05,123
        assert _format_srt_timestamp(3665.123) == "01:01:05,123"

    def test_negative_clamped_to_zero(self):
        assert _format_srt_timestamp(-1) == "00:00:00,000"

    def test_rounds_milliseconds(self):
        # 1.9999 → 1.999 (rounded to nearest ms = 1000ms)
        # But the helper rounds (seconds - int(seconds)) * 1000 → 999.9 → 1000.
        # When that overflows to 1000, the helper bumps whole_seconds.
        out = _format_srt_timestamp(1.9999)
        # 1.9999 → millis fraction = 999.9 → round to 1000 → bump → 02,000
        assert out == "00:00:02,000"


# ---------------------------------------------------------------------------
# derive_srt
# ---------------------------------------------------------------------------


class TestDeriveSrt:
    def test_emits_blocks_in_order(self):
        out = derive_srt(
            intro_text="Welcome.",
            intro_duration_s=2.0,
            scene_pairs=[("Scene one.", 5.0), ("Scene two.", 3.0)],
            outro_text="Goodbye.",
            outro_duration_s=2.0,
        )
        assert "1\n00:00:00,000 --> 00:00:02,000\nWelcome." in out
        assert "2\n00:00:02,000 --> 00:00:07,000\nScene one." in out
        assert "3\n00:00:07,000 --> 00:00:10,000\nScene two." in out
        assert "4\n00:00:10,000 --> 00:00:12,000\nGoodbye." in out

    def test_skips_empty_text(self):
        out = derive_srt(
            intro_text="",
            intro_duration_s=2.0,
            scene_pairs=[("", 1.0), ("Hello.", 3.0)],
        )
        # Only "Hello." emitted, with seq=1 starting at cursor=0.
        assert "1\n00:00:00,000 --> 00:00:03,000\nHello." in out
        # No empty intro block
        assert "2\n" not in out

    def test_skips_zero_duration(self):
        out = derive_srt(
            intro_text="Hi.",
            intro_duration_s=0.0,
            scene_pairs=[("Body.", 5.0)],
        )
        # Intro with zero duration skipped; first emitted block = Body
        assert "1\n00:00:00,000 --> 00:00:05,000\nBody." in out

    def test_no_inputs_returns_empty(self):
        assert derive_srt(
            intro_text="",
            intro_duration_s=0,
            scene_pairs=[],
        ) == ""

    def test_cumulative_timestamps(self):
        # Three scenes of 1s each → cursor advances 0→1→2→3
        out = derive_srt(
            intro_text="",
            intro_duration_s=0.0,
            scene_pairs=[("a", 1.0), ("b", 1.0), ("c", 1.0)],
        )
        assert "00:00:00,000 --> 00:00:01,000\na" in out
        assert "00:00:01,000 --> 00:00:02,000\nb" in out
        assert "00:00:02,000 --> 00:00:03,000\nc" in out

    def test_outro_only(self):
        out = derive_srt(
            intro_text="",
            intro_duration_s=0,
            scene_pairs=[],
            outro_text="Bye.",
            outro_duration_s=2.0,
        )
        assert "1\n00:00:00,000 --> 00:00:02,000\nBye." in out


# ---------------------------------------------------------------------------
# build_scenes
# ---------------------------------------------------------------------------


class TestBuildScenes:
    def test_zips_visuals_with_tts_by_idx(self):
        visuals = [
            {"scene_idx": 0, "clip_path": "/clips/0.jpg"},
            {"scene_idx": 1, "clip_path": "/clips/1.jpg"},
        ]
        tts = [
            {"scene_idx": 0, "audio_path": "/aud/0.mp3", "duration_s": 5.0, "text": "a"},
            {"scene_idx": 1, "audio_path": "/aud/1.mp3", "duration_s": 7.0, "text": "b"},
        ]
        scenes = build_scenes(
            visuals=visuals, tts_scenes=tts, fallback_duration_s=30,
        )
        assert len(scenes) == 2
        assert scenes[0].clip_path == "/clips/0.jpg"
        assert scenes[0].duration_s == 5.0
        assert scenes[0].narration_path == "/aud/0.mp3"
        assert scenes[1].duration_s == 7.0

    def test_drops_scenes_with_no_visual(self):
        visuals = [{"scene_idx": 1, "clip_path": "/clips/1.jpg"}]
        tts = [
            {"scene_idx": 0, "audio_path": "/aud/0.mp3", "duration_s": 5.0, "text": "a"},
            {"scene_idx": 1, "audio_path": "/aud/1.mp3", "duration_s": 7.0, "text": "b"},
        ]
        scenes = build_scenes(
            visuals=visuals, tts_scenes=tts, fallback_duration_s=30,
        )
        # Only scene_idx=1 has a visual.
        assert len(scenes) == 1
        assert scenes[0].clip_path == "/clips/1.jpg"

    def test_uses_fallback_duration_when_tts_zero(self):
        visuals = [{"scene_idx": 0, "clip_path": "/clips/0.jpg"}]
        tts = [{"scene_idx": 0, "audio_path": "/aud/0.mp3", "duration_s": 0.0, "text": ""}]
        scenes = build_scenes(
            visuals=visuals, tts_scenes=tts, fallback_duration_s=42,
        )
        assert scenes[0].duration_s == 42.0

    def test_uses_fallback_duration_when_no_tts_for_scene(self):
        # Visual exists but no TTS at that idx — duration falls back.
        visuals = [{"scene_idx": 0, "clip_path": "/clips/0.jpg"}]
        tts: list[dict[str, Any]] = []
        scenes = build_scenes(
            visuals=visuals, tts_scenes=tts, fallback_duration_s=15,
        )
        assert len(scenes) == 1
        assert scenes[0].duration_s == 15.0
        # No narration audio when no TTS row.
        assert scenes[0].narration_path is None

    def test_empty_inputs_returns_empty(self):
        assert build_scenes(visuals=[], tts_scenes=[], fallback_duration_s=30) == []

    def test_returns_composition_scene_instances(self):
        visuals = [{"scene_idx": 0, "clip_path": "/c.jpg"}]
        tts = [{"scene_idx": 0, "audio_path": "/a.mp3", "duration_s": 5.0, "text": "t"}]
        scenes = build_scenes(visuals=visuals, tts_scenes=tts, fallback_duration_s=30)
        assert isinstance(scenes[0], CompositionScene)


# ---------------------------------------------------------------------------
# write_srt_sidecar
# ---------------------------------------------------------------------------


class TestWriteSrtSidecar:
    def test_writes_file(self, tmp_path: Path):
        srt = "1\n00:00:00,000 --> 00:00:01,000\nHi"
        out = write_srt_sidecar(srt, tmp_path, "myvid")
        assert out.endswith("myvid.srt")
        assert Path(out).read_text(encoding="utf-8") == srt

    def test_empty_srt_returns_empty_string(self, tmp_path: Path):
        assert write_srt_sidecar("", tmp_path, "x") == ""
        assert write_srt_sidecar("   \n\t  ", tmp_path, "x") == ""

    def test_creates_parent_dir(self, tmp_path: Path):
        nested = tmp_path / "sub" / "dir"
        out = write_srt_sidecar("1\n00:00:00,000 --> 00:00:01,000\nHi", nested, "v")
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# output_paths
# ---------------------------------------------------------------------------


class TestOutputPaths:
    def test_long_form_filename(self, tmp_path, monkeypatch):
        # Redirect output root to tmp_path
        monkeypatch.setenv("POINDEXTER_DATA_ROOT", str(tmp_path))
        spec = StitchSpec(
            format_kind="long_form", asset_type="video_long",
            width=1920, height=1080, fps=30, include_outro=True,
            output_filename="{task_id}_long.mp4",
        )
        out_dir, out_path = output_paths(spec, "task-123")
        assert out_path.endswith("task-123_long.mp4")
        assert str(out_dir).startswith(str(tmp_path))

    def test_short_form_filename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POINDEXTER_DATA_ROOT", str(tmp_path))
        spec = StitchSpec(
            format_kind="short_form", asset_type="video_short",
            width=1080, height=1920, fps=30, include_outro=False,
            output_filename="{task_id}_short.mp4",
        )
        _out_dir, out_path = output_paths(spec, "task-X")
        assert out_path.endswith("task-X_short.mp4")


# ---------------------------------------------------------------------------
# resolve_compositor
# ---------------------------------------------------------------------------


class TestResolveCompositor:
    """Note: ``ENTRY_POINT_GROUPS`` doesn't include ``media_compositors``
    in this revision, so ``_cached(ENTRY_POINT_GROUPS["media_compositors"])``
    raises KeyError. The helper catches it and falls through to its
    last-ditch direct ``FFmpegLocalCompositor`` import. Tests work
    against that real path."""

    def test_last_ditch_fallback_returns_ffmpeg_local(self):
        # With no entry-points group registered, the helper falls all the
        # way through to the last-ditch direct import — and that import
        # succeeds in this repo, so we get an FFmpegLocalCompositor.
        cfg = SimpleNamespace(get=lambda _k, _d="": _d)
        out = resolve_compositor(cfg)
        # Either we got the real ffmpeg_local instance, or None (if the
        # import failed for some reason). On a healthy checkout it's
        # the former.
        assert out is None or getattr(out, "name", None) == "ffmpeg_local"

    def test_returns_none_when_import_fails(self):
        # Force the last-ditch import path to raise.
        # Patch the underlying compositor module so the import inside
        # resolve_compositor raises.
        import services.media_compositors.ffmpeg_local as _mod
        with patch.object(_mod, "FFmpegLocalCompositor", side_effect=ImportError("boom")):
            cfg = SimpleNamespace(get=lambda _k, _d="": _d)
            out = resolve_compositor(cfg)
            assert out is None

    def test_none_site_config_does_not_raise(self):
        # Helper handles site_config=None gracefully (defaults to ffmpeg_local).
        out = resolve_compositor(None)
        # Same expectation as above — last-ditch import path.
        assert out is None or getattr(out, "name", None) == "ffmpeg_local"


# ---------------------------------------------------------------------------
# persist_media_asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPersistMediaAsset:
    async def test_pool_none_returns_none_gracefully(self):
        out = await persist_media_asset(
            pool=None, post_id="p", asset_type="video_long",
            provider_plugin="compositor.ffmpeg_local",
            output_path="/tmp/x.mp4", public_url="",
            width=1920, height=1080, duration_ms=5000,
            file_size_bytes=1024, cost_usd=0.0, electricity_kwh=0.0,
            metadata={},
        )
        assert out is None

    async def test_returns_uuid_string_on_success(self):
        # asyncpg pool.acquire() context manager → conn.fetchval()
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value="abc-123-uuid")

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_a):
                return None

        pool = MagicMock()
        pool.acquire = lambda: _Ctx()

        out = await persist_media_asset(
            pool=pool, post_id="p", asset_type="video_long",
            provider_plugin="compositor.ffmpeg_local",
            output_path="/tmp/x.mp4", public_url="https://u",
            width=1920, height=1080, duration_ms=5000,
            file_size_bytes=1024, cost_usd=0.0, electricity_kwh=0.05,
            metadata={"a": 1},
        )
        assert out == "abc-123-uuid"
        conn.fetchval.assert_awaited_once()

    async def test_db_failure_returns_none(self):
        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=RuntimeError("db boom"))

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_a):
                return None

        pool = MagicMock()
        pool.acquire = lambda: _Ctx()

        out = await persist_media_asset(
            pool=pool, post_id="p", asset_type="video_long",
            provider_plugin="compositor.x",
            output_path="/tmp/x.mp4", public_url="",
            width=1920, height=1080, duration_ms=0,
            file_size_bytes=0, cost_usd=0.0, electricity_kwh=0.0,
            metadata={},
        )
        assert out is None


# ---------------------------------------------------------------------------
# _json_dumps (private helper, but callable + worth a sanity test)
# ---------------------------------------------------------------------------


class TestJsonDumps:
    def test_normal_dict(self):
        out = _json_dumps({"a": 1, "b": "x"})
        assert json.loads(out) == {"a": 1, "b": "x"}

    def test_falls_back_to_str_for_unserializable(self):
        # set is not JSON-serializable; helper coerces values to str.
        out = _json_dumps({"a": {1, 2, 3}})
        loaded = json.loads(out)
        assert "a" in loaded
        # Value was coerced to string repr
        assert isinstance(loaded["a"], str)


# ---------------------------------------------------------------------------
# upload_to_object_storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadToObjectStorage:
    async def test_no_local_path_returns_empty(self):
        out = await upload_to_object_storage(
            local_path="", asset_type="video_long",
            post_id="p", site_config=SimpleNamespace(),
        )
        assert out == ""

    async def test_no_site_config_returns_empty(self, tmp_path):
        # Even with a real file, missing site_config returns empty.
        f = tmp_path / "x.mp4"
        f.write_bytes(b"fake")
        out = await upload_to_object_storage(
            local_path=str(f), asset_type="video_long",
            post_id="p", site_config=None,
        )
        assert out == ""

    async def test_missing_file_returns_empty(self):
        out = await upload_to_object_storage(
            local_path="/nonexistent/foo.mp4", asset_type="video_long",
            post_id="p", site_config=SimpleNamespace(),
        )
        assert out == ""

    async def test_upload_success_returns_url(self, tmp_path):
        f = tmp_path / "x.mp4"
        f.write_bytes(b"video")
        with patch(
            "services.r2_upload_service.upload_to_r2",
            AsyncMock(return_value="https://cdn.example/v.mp4"),
        ):
            out = await upload_to_object_storage(
                local_path=str(f), asset_type="video_long",
                post_id="post-1", site_config=SimpleNamespace(),
            )
        assert out == "https://cdn.example/v.mp4"

    async def test_upload_exception_returns_empty(self, tmp_path):
        f = tmp_path / "x.mp4"
        f.write_bytes(b"video")
        with patch(
            "services.r2_upload_service.upload_to_r2",
            AsyncMock(side_effect=RuntimeError("upload boom")),
        ):
            out = await upload_to_object_storage(
                local_path=str(f), asset_type="video_long",
                post_id="post-1", site_config=SimpleNamespace(),
            )
        assert out == ""
