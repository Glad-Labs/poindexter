"""Tests for the podcast producer hooks (GH#161).

Verifies that ``PodcastService.generate_episode`` records ``media_assets``
rows after producing the file.

The recorder itself is mocked. We only assert the call shape (asset
type, post_id, provider_plugin, mime_type) and that the call happens
on the success path. Failures should NOT call the recorder.
"""

from __future__ import annotations

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
    async def test_success_records_media_asset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        pool = MagicMock()
        sc = _fake_site_config(pool=pool)
        # #272 Phase-2f: PodcastService requires an injected site_config
        # (module global deleted). Thread the fake — its ``_pool`` is what
        # the recorder reads for the ``pool`` kwarg asserted below.
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

    async def test_seo_metadata_threaded_into_podcast_asset(
        self, tmp_path: Path,
    ):
        """#539 — the post's stored SEO description + keywords land in the
        podcast media_assets row, reused (not regenerated)."""
        pool = MagicMock()
        sc = _fake_site_config(pool=pool)
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

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
            await svc.generate_episode(
                post_id="post-1",
                title="Test Title",
                content="Body.",
                pre_generated_script="A long enough script. " * 30,
                seo_description="A crisp SEO meta description.",
                seo_keywords="local llm, ollama, gpu",
            )

        kwargs = recorder.await_args.kwargs
        meta = kwargs["metadata"]
        assert meta["seo_description"] == "A crisp SEO meta description."
        assert meta["seo_keywords"] == "local llm, ollama, gpu"

    async def test_seo_metadata_defaults_to_empty_strings(
        self, tmp_path: Path,
    ):
        """#539 — omitting SEO args stores empty-string sentinels (the
        '' = unset convention), never None / a missing key."""
        sc = _fake_site_config(pool=MagicMock())
        svc = PodcastService(output_dir=tmp_path, site_config=sc)

        async def _gen_with_voice(script: str, voice: str, output_path: Path):
            output_path.write_bytes(b"fake-mp3" * 1000)
            return EpisodeResult(
                success=True,
                file_path=str(output_path),
                duration_seconds=120,
                file_size_bytes=output_path.stat().st_size,
            )

        recorder = AsyncMock(return_value="asset-uuid")
        with patch.object(svc, "_generate_with_voice", _gen_with_voice), \
             patch(
                 "services.media_asset_recorder.record_media_asset", recorder,
             ):
            await svc.generate_episode(
                post_id="post-1",
                title="Test Title",
                content="Body.",
                pre_generated_script="A long enough script. " * 30,
            )

        meta = recorder.await_args.kwargs["metadata"]
        assert meta["seo_description"] == ""
        assert meta["seo_keywords"] == ""

    async def test_failure_does_not_record(self, tmp_path: Path):
        sc = _fake_site_config(pool=MagicMock())
        # #272 Phase-2f: PodcastService requires an injected site_config.
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
        # #272 Phase-2f: PodcastService requires an injected site_config.
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
