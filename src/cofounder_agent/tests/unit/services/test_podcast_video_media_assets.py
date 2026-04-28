"""Tests for the legacy podcast + video producer hooks (GH#161).

Verifies that ``PodcastService.generate_episode`` and the legacy
``video_service.generate_video_for_post`` / ``generate_short_video_for_post``
record ``media_assets`` rows after producing the file.

The recorder itself is mocked. We only assert the call shape (asset
type, post_id, provider_plugin, mime_type) and that the call happens
on the success path. Failures should NOT call the recorder.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.podcast_service import EpisodeResult, PodcastService


def _fake_site_config(pool: Any | None = None, **overrides: Any):
    base: dict[str, Any] = {
        "podcast_tts_engine": "edge_tts",
        "ollama_base_url": "http://localhost:11434",
        "default_ollama_model": "gemma3:27b",
        "podcast_name": "Test Pod",
        "site_domain": "test.example",
    }
    base.update(overrides)

    return SimpleNamespace(
        get=lambda k, d="": base.get(k, d if d is not None else ""),
        get_int=lambda _k, d=0: d,
        get_float=lambda _k, d=0.0: d,
        get_bool=lambda _k, d=False: d,
        get_secret=AsyncMock(return_value=""),
        _pool=pool,
    )


# ---------------------------------------------------------------------------
# PodcastService.generate_episode records on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPodcastServiceRecordsAsset:
    async def test_success_records_media_asset(self, tmp_path: Path):
        pool = MagicMock()
        sc = _fake_site_config(pool=pool)
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        # Mock the TTS render — write the file inside the mock so the
        # "episode already exists" short-circuit doesn't fire.
        async def _gen_with_voice(script: str, voice: str, output_path: Path):
            output_path.write_bytes(b"fake-mp3" * 1000)
            return EpisodeResult(
                success=True,
                file_path=str(output_path),
                duration_seconds=300,
                file_size_bytes=output_path.stat().st_size,
            )

        recorder = AsyncMock(return_value="asset-uuid")
        with patch.object(svc, "_generate_with_voice", _gen_with_voice), \
             patch(
                 "services.media_asset_recorder.record_media_asset", recorder,
             ):
            result = await svc.generate_episode(
                post_id="post-1",
                title="Test Title",
                content="Body.",
                pre_generated_script="A long enough script. " * 30,
            )

        assert result.success is True
        recorder.assert_awaited_once()
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "podcast"
        assert kwargs["post_id"] == "post-1"
        assert kwargs["mime_type"] == "audio/mpeg"
        assert kwargs["provider_plugin"] == "tts.edge_tts"
        assert kwargs["pool"] is pool
        # File size + duration carried through
        assert kwargs["duration_ms"] == 300_000
        assert kwargs["file_size_bytes"] > 0

    async def test_failure_does_not_record(self, tmp_path: Path):
        sc = _fake_site_config(pool=MagicMock())
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        async def _gen_with_voice(script: str, voice: str, output_path: Path):
            return EpisodeResult(success=False, error="all voices failed")

        recorder = AsyncMock()
        with patch.object(svc, "_generate_with_voice", _gen_with_voice), \
             patch(
                 "services.media_asset_recorder.record_media_asset", recorder,
             ):
            result = await svc.generate_episode(
                post_id="post-1",
                title="T",
                content="Body.",
                pre_generated_script="A long enough script. " * 30,
            )

        assert result.success is False
        recorder.assert_not_awaited()

    async def test_skip_recording_when_episode_already_existed(
        self, tmp_path: Path,
    ):
        # When the episode mp3 already exists on disk, generate_episode
        # short-circuits and returns the existing result. That path
        # does NOT need to land a row — backfill catches it. Verify
        # the recorder is not called on this short-circuit path.
        sc = _fake_site_config(pool=MagicMock())
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        fake_mp3 = tmp_path / "post-1.mp3"
        fake_mp3.write_bytes(b"bytes" * 1000)

        recorder = AsyncMock()
        with patch(
            "services.media_asset_recorder.record_media_asset", recorder,
        ):
            result = await svc.generate_episode(
                post_id="post-1",
                title="T",
                content="Body.",
                pre_generated_script="A long enough script. " * 30,
            )

        assert result.success is True
        # Existing-file short-circuit doesn't re-record.
        recorder.assert_not_awaited()


# ---------------------------------------------------------------------------
# video_service.generate_video_for_post records on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestVideoServiceRecordsAsset:
    async def test_long_form_success_records_media_asset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # Force VIDEO_DIR + PODCAST_DIR to tmp_path — the video service
        # uses these module constants to find inputs / write outputs.
        from services import video_service as vs
        monkeypatch.setenv("POINDEXTER_DATA_ROOT", str(tmp_path))
        # The constants were captured at import time, so re-patch.
        monkeypatch.setattr(vs, "VIDEO_DIR", tmp_path / "video")
        (tmp_path / "video").mkdir(parents=True, exist_ok=True)
        (tmp_path / "podcast").mkdir(parents=True, exist_ok=True)

        # Stub a podcast file so the video function gets past the input
        # check.
        podcast_path = tmp_path / "podcast" / "post-1.mp3"
        podcast_path.write_bytes(b"fake-podcast")

        # Stub the image-extraction + image-generation calls so we don't
        # need an SDXL server. Return a single fake image path.
        fake_img = tmp_path / "img.jpg"
        fake_img.write_bytes(b"img-bytes")

        # Stub the HTTP call to the video server to return a fake mp4.
        fake_mp4_bytes = b"fake-mp4-bytes" * 100
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "60",
            "X-Elapsed-Seconds": "30",
        }
        mock_resp.content = fake_mp4_bytes

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_ctx = MagicMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        sc = _fake_site_config(pool=MagicMock(), host_home="/host")
        recorder = AsyncMock(return_value="asset-uuid")

        with patch(
            "services.video_service._extract_images_from_content",
            AsyncMock(return_value=[str(fake_img)]),
        ), patch(
            "services.video_service._generate_images_for_video",
            AsyncMock(return_value=[]),
        ), patch(
            "services.video_service._generate_images_from_scenes",
            AsyncMock(return_value=[]),
        ), patch(
            "services.video_service._maybe_generate_ambient_bed",
            AsyncMock(return_value=None),
        ), patch(
            "services.video_service._video_server_url",
            return_value="http://localhost:9837",
        ), patch(
            "httpx.AsyncClient", return_value=mock_client_ctx,
        ), patch(
            "services.media_asset_recorder.record_media_asset", recorder,
        ):
            result = await vs.generate_video_for_post(
                post_id="post-1",
                title="Test Video",
                content="Body content",
                podcast_path=str(podcast_path),
                site_config=sc,
            )

        assert result.success is True
        recorder.assert_awaited_once()
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "video"
        assert kwargs["post_id"] == "post-1"
        assert kwargs["mime_type"] == "video/mp4"
        assert kwargs["provider_plugin"] == "video.ken_burns_slideshow"
        assert kwargs["duration_ms"] == 60_000
        assert kwargs["file_size_bytes"] > 0
