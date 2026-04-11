"""
Tests for video service — narrated slideshow video generation from posts.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from services.video_service import (
    VideoResult,
    _extract_images_from_content,
    _generate_images_for_video,
    _generate_images_from_scenes,
    _generate_short_summary_audio,
    generate_short_video_for_post,
    generate_video_episode,
    generate_video_for_post,
)

# ---------------------------------------------------------------------------
# VideoResult dataclass
# ---------------------------------------------------------------------------

class TestVideoResult:
    """VideoResult dataclass structure."""

    def test_defaults(self):
        r = VideoResult(success=True)
        assert r.success is True
        assert r.file_path is None
        assert r.duration_seconds == 0
        assert r.file_size_bytes == 0
        assert r.images_used == 0
        assert r.error is None

    def test_failure_with_error(self):
        r = VideoResult(success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"

    def test_full_success(self):
        r = VideoResult(
            success=True,
            file_path="/tmp/video.mp4",
            duration_seconds=120,
            file_size_bytes=5000,
            images_used=4,
        )
        assert r.file_path == "/tmp/video.mp4"
        assert r.duration_seconds == 120
        assert r.images_used == 4


# ---------------------------------------------------------------------------
# generate_video_for_post
# ---------------------------------------------------------------------------

class TestGenerateVideoForPost:
    """Core generate_video_for_post behavior."""

    @pytest.mark.asyncio
    async def test_skips_if_already_exists(self, tmp_path):
        """Should return early if video file already exists and force=False."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        existing = video_dir / "post123.mp4"
        existing.write_bytes(b"fake video content")

        with patch("services.video_service.VIDEO_DIR", video_dir):
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                content="body",
            )

        assert result.success is True
        assert result.file_path == str(existing)
        assert result.file_size_bytes == len(b"fake video content")

    @pytest.mark.asyncio
    async def test_force_regenerates_existing(self, tmp_path):
        """With force=True, should not skip even if file exists."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        existing = video_dir / "post123.mp4"
        existing.write_bytes(b"old video")

        podcast_dir = tmp_path / "podcast"
        podcast_dir.mkdir()
        podcast_file = podcast_dir / "post123.mp3"
        podcast_file.write_bytes(b"fake mp3")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "90",
            "X-Elapsed-Seconds": "10",
        }
        mock_resp.content = b"new video bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_gen.return_value = ["/root/.poindexter/video/frames/frame_00.png"]
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                content="body",
                podcast_path=str(podcast_file),
                force=True,
            )

        assert result.success is True
        assert result.duration_seconds == 90
        assert result.images_used == 1

    @pytest.mark.asyncio
    async def test_podcast_not_found(self, tmp_path):
        """Should fail if podcast file does not exist."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()

        with patch("services.video_service.VIDEO_DIR", video_dir):
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                content="body",
                podcast_path="/nonexistent/podcast.mp3",
            )

        assert result.success is False
        assert "Podcast not found" in result.error

    @pytest.mark.asyncio
    async def test_no_images_generated(self, tmp_path):
        """Should fail if image generation returns empty list."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast_file = tmp_path / "podcast.mp3"
        podcast_file.write_bytes(b"fake audio")

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = []
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                content="body",
                podcast_path=str(podcast_file),
            )

        assert result.success is False
        assert "No images" in result.error

    @pytest.mark.asyncio
    async def test_successful_generation(self, tmp_path):
        """Full happy path: podcast exists, images generated, video rendered."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast_file = tmp_path / "podcast.mp3"
        podcast_file.write_bytes(b"fake mp3 data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "180",
            "X-Elapsed-Seconds": "25",
        }
        mock_resp.content = b"rendered-video-bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_gen.return_value = [
                "/root/.poindexter/video/frames/frame_00.png",
                "/root/.poindexter/video/frames/frame_01.png",
            ]
            result = await generate_video_for_post(
                post_id="post123",
                title="Test Post",
                content="Some content",
                podcast_path=str(podcast_file),
            )

        assert result.success is True
        assert result.duration_seconds == 180
        assert result.images_used == 2
        assert result.file_size_bytes == len(b"rendered-video-bytes")
        # Verify the mp4 was written to disk
        assert (video_dir / "post123.mp4").exists()

    @pytest.mark.asyncio
    async def test_video_server_error_status(self, tmp_path):
        """Non-200 from the video server returns failure result."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast_file = tmp_path / "podcast.mp3"
        podcast_file.write_bytes(b"audio")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"error": "GPU OOM"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_gen.return_value = ["/root/.poindexter/video/frames/frame_00.png"]
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                podcast_path=str(podcast_file),
            )

        assert result.success is False
        assert "GPU OOM" in result.error

    @pytest.mark.asyncio
    async def test_video_server_exception(self, tmp_path):
        """Network error to video server returns failure result."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast_file = tmp_path / "podcast.mp3"
        podcast_file.write_bytes(b"audio")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_gen.return_value = ["/root/.poindexter/video/frames/frame_00.png"]
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                podcast_path=str(podcast_file),
            )

        assert result.success is False
        assert "refused" in result.error

    @pytest.mark.asyncio
    async def test_default_podcast_path_lookup(self, tmp_path):
        """When podcast_path is None, looks up from PODCAST_DIR."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()

        podcast_dir = tmp_path / "podcast"
        podcast_dir.mkdir()
        podcast_file = podcast_dir / "post123.mp3"
        podcast_file.write_bytes(b"audio")

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.podcast_service.PODCAST_DIR", podcast_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = []
            result = await generate_video_for_post(
                post_id="post123",
                title="Test",
                content="body",
                # podcast_path intentionally omitted
            )

        # It should find the podcast but fail on images
        assert result.success is False
        assert "No images" in result.error

    @pytest.mark.asyncio
    async def test_host_path_conversion(self, tmp_path):
        """Container paths should be converted to host paths for the video server."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast_file = tmp_path / "podcast.mp3"
        podcast_file.write_bytes(b"audio")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "60",
            "X-Elapsed-Seconds": "5",
        }
        mock_resp.content = b"video"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client), \
             patch.dict(os.environ, {"HOST_HOME": "/host/home"}), \
             patch("services.video_service.os.path.exists", return_value=True):
            mock_gen.return_value = [
                "/root/.poindexter/video/frames/frame_00.png",
                "/root/.poindexter/video/frames/frame_01.png",
            ]
            await generate_video_for_post(
                post_id="post123",
                title="Test",
                podcast_path="/root/.poindexter/podcast/post123.mp3",
            )

        # Inspect the JSON payload sent to the video server
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["audio_path"] == "/host/home/.poindexter/podcast/post123.mp3"
        assert payload["image_paths"] == [
            "/host/home/.poindexter/video/frames/frame_00.png",
            "/host/home/.poindexter/video/frames/frame_01.png",
        ]


# ---------------------------------------------------------------------------
# _generate_images_for_video
# ---------------------------------------------------------------------------

class TestGenerateImagesForVideo:
    """Image generation pipeline (Ollama prompts + SDXL rendering)."""

    @pytest.mark.asyncio
    async def test_ollama_prompt_generation_and_sdxl(self, tmp_path):
        """Ollama generates prompts, SDXL generates images from them."""
        ollama_resp = MagicMock()
        ollama_resp.status_code = 200
        ollama_resp.raise_for_status = MagicMock()
        ollama_resp.json.return_value = {
            "response": (
                "1. A futuristic server room with blue neon lights and rows of GPUs\n"
                "2. Cinematic landscape of data flowing through fiber optic cables\n"
            )
        }

        sdxl_resp = MagicMock()
        sdxl_resp.status_code = 200
        sdxl_resp.headers = {"content-type": "image/png"}
        sdxl_resp.content = b"\x89PNG fake image data"

        # We need two separate client instances (two `async with` blocks)
        mock_client_ollama = AsyncMock()
        mock_client_ollama.__aenter__ = AsyncMock(return_value=mock_client_ollama)
        mock_client_ollama.__aexit__ = AsyncMock(return_value=False)
        mock_client_ollama.post = AsyncMock(return_value=ollama_resp)

        mock_client_sdxl = AsyncMock()
        mock_client_sdxl.__aenter__ = AsyncMock(return_value=mock_client_sdxl)
        mock_client_sdxl.__aexit__ = AsyncMock(return_value=False)
        mock_client_sdxl.post = AsyncMock(return_value=sdxl_resp)

        frames_dir = tmp_path / "video" / "frames"

        with patch("services.video_service.VIDEO_DIR", tmp_path / "video"), \
             patch("services.video_service.httpx.AsyncClient", side_effect=[
                 mock_client_ollama, mock_client_sdxl,
             ]):
            paths = await _generate_images_for_video("GPU Computing", "content", num_images=2)

        assert len(paths) == 2
        # Ollama was called once, SDXL called twice (one per prompt)
        mock_client_ollama.post.assert_called_once()
        assert mock_client_sdxl.post.call_count == 2
        # Files should exist
        for p in paths:
            assert Path(p).exists()

    @pytest.mark.asyncio
    async def test_fallback_prompts_on_ollama_failure(self, tmp_path):
        """When Ollama fails, uses hardcoded fallback prompts for SDXL."""
        mock_client_ollama = AsyncMock()
        mock_client_ollama.__aenter__ = AsyncMock(return_value=mock_client_ollama)
        mock_client_ollama.__aexit__ = AsyncMock(return_value=False)
        mock_client_ollama.post = AsyncMock(side_effect=Exception("Ollama down"))

        sdxl_resp = MagicMock()
        sdxl_resp.status_code = 200
        sdxl_resp.headers = {"content-type": "image/png"}
        sdxl_resp.content = b"\x89PNG fallback image"

        mock_client_sdxl = AsyncMock()
        mock_client_sdxl.__aenter__ = AsyncMock(return_value=mock_client_sdxl)
        mock_client_sdxl.__aexit__ = AsyncMock(return_value=False)
        mock_client_sdxl.post = AsyncMock(return_value=sdxl_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path / "video"), \
             patch("services.video_service.httpx.AsyncClient", side_effect=[
                 mock_client_ollama, mock_client_sdxl,
             ]):
            paths = await _generate_images_for_video("AI Tools", "content about AI", num_images=3)

        # Should get 3 images from fallback prompts
        assert len(paths) == 3
        assert mock_client_sdxl.post.call_count == 3

    @pytest.mark.asyncio
    async def test_sdxl_failure_skips_frame(self, tmp_path):
        """When SDXL fails for one frame, that frame is skipped."""
        ollama_resp = MagicMock()
        ollama_resp.status_code = 200
        ollama_resp.raise_for_status = MagicMock()
        ollama_resp.json.return_value = {
            "response": (
                "1. A cinematic scene of a modern AI research laboratory\n"
                "2. Futuristic quantum computing hardware with glowing circuits\n"
            )
        }

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.headers = {"content-type": "image/png"}
        success_resp.content = b"\x89PNG good image"

        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.headers = {"content-type": "application/json"}
        fail_resp.text = "Internal Server Error"

        mock_client_ollama = AsyncMock()
        mock_client_ollama.__aenter__ = AsyncMock(return_value=mock_client_ollama)
        mock_client_ollama.__aexit__ = AsyncMock(return_value=False)
        mock_client_ollama.post = AsyncMock(return_value=ollama_resp)

        mock_client_sdxl = AsyncMock()
        mock_client_sdxl.__aenter__ = AsyncMock(return_value=mock_client_sdxl)
        mock_client_sdxl.__aexit__ = AsyncMock(return_value=False)
        # First SDXL call succeeds, second fails
        mock_client_sdxl.post = AsyncMock(side_effect=[success_resp, fail_resp])

        with patch("services.video_service.VIDEO_DIR", tmp_path / "video"), \
             patch("services.video_service.httpx.AsyncClient", side_effect=[
                 mock_client_ollama, mock_client_sdxl,
             ]):
            paths = await _generate_images_for_video("Quantum AI", "content", num_images=2)

        # Only the first image should be in the list
        assert len(paths) == 1

    @pytest.mark.asyncio
    async def test_sdxl_exception_skips_frame(self, tmp_path):
        """Network exception during SDXL call skips that frame gracefully."""
        ollama_resp = MagicMock()
        ollama_resp.status_code = 200
        ollama_resp.raise_for_status = MagicMock()
        ollama_resp.json.return_value = {
            "response": "1. A beautiful cinematic scene of neural network visualization\n"
        }

        mock_client_ollama = AsyncMock()
        mock_client_ollama.__aenter__ = AsyncMock(return_value=mock_client_ollama)
        mock_client_ollama.__aexit__ = AsyncMock(return_value=False)
        mock_client_ollama.post = AsyncMock(return_value=ollama_resp)

        mock_client_sdxl = AsyncMock()
        mock_client_sdxl.__aenter__ = AsyncMock(return_value=mock_client_sdxl)
        mock_client_sdxl.__aexit__ = AsyncMock(return_value=False)
        mock_client_sdxl.post = AsyncMock(side_effect=Exception("SDXL timeout"))

        with patch("services.video_service.VIDEO_DIR", tmp_path / "video"), \
             patch("services.video_service.httpx.AsyncClient", side_effect=[
                 mock_client_ollama, mock_client_sdxl,
             ]):
            paths = await _generate_images_for_video("Test", "content", num_images=1)

        assert len(paths) == 0

    @pytest.mark.asyncio
    async def test_short_prompts_filtered_out(self, tmp_path):
        """Ollama lines shorter than 20 chars are ignored."""
        ollama_resp = MagicMock()
        ollama_resp.status_code = 200
        ollama_resp.raise_for_status = MagicMock()
        ollama_resp.json.return_value = {
            "response": (
                "1. short\n"
                "2. A very detailed cinematic photorealistic scene of neural networks\n"
            )
        }

        sdxl_resp = MagicMock()
        sdxl_resp.status_code = 200
        sdxl_resp.headers = {"content-type": "image/png"}
        sdxl_resp.content = b"\x89PNG image"

        mock_client_ollama = AsyncMock()
        mock_client_ollama.__aenter__ = AsyncMock(return_value=mock_client_ollama)
        mock_client_ollama.__aexit__ = AsyncMock(return_value=False)
        mock_client_ollama.post = AsyncMock(return_value=ollama_resp)

        mock_client_sdxl = AsyncMock()
        mock_client_sdxl.__aenter__ = AsyncMock(return_value=mock_client_sdxl)
        mock_client_sdxl.__aexit__ = AsyncMock(return_value=False)
        mock_client_sdxl.post = AsyncMock(return_value=sdxl_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path / "video"), \
             patch("services.video_service.httpx.AsyncClient", side_effect=[
                 mock_client_ollama, mock_client_sdxl,
             ]):
            paths = await _generate_images_for_video("Test", "content", num_images=2)

        # Only 1 prompt passes the 20-char filter
        assert mock_client_sdxl.post.call_count == 1
        assert len(paths) == 1


# ---------------------------------------------------------------------------
# generate_video_episode
# ---------------------------------------------------------------------------

class TestGenerateVideoEpisode:
    """Fire-and-forget wrapper."""

    @pytest.mark.asyncio
    async def test_calls_generate_video_for_post(self):
        """Should delegate to generate_video_for_post."""
        with patch("services.video_service.generate_video_for_post", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = VideoResult(success=True, file_path="/tmp/v.mp4")
            await generate_video_episode("post1", "Title", "Content")

        mock_gen.assert_awaited_once_with("post1", "Title", "Content", pre_generated_scenes=None)

    @pytest.mark.asyncio
    async def test_logs_failure_without_raising(self):
        """Failed result should be logged, not raised."""
        with patch("services.video_service.generate_video_for_post", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = VideoResult(success=False, error="no images")
            # Should not raise
            await generate_video_episode("post1", "Title", "Content")

    @pytest.mark.asyncio
    async def test_catches_unexpected_exception(self):
        """Unexpected exception should be caught, not propagated."""
        with patch("services.video_service.generate_video_for_post", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = RuntimeError("unexpected crash")
            # Should not raise
            await generate_video_episode("post1", "Title", "Content")


# ---------------------------------------------------------------------------
# _to_host_path (tested indirectly via generate_video_for_post)
# ---------------------------------------------------------------------------

class TestToHostPath:
    """Path conversion from container to host (tested via integration)."""

    @pytest.mark.asyncio
    async def test_default_host_home(self, tmp_path):
        """Without HOST_HOME env var, uses default C:/Users/mattm."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast = tmp_path / "podcast.mp3"
        podcast.write_bytes(b"audio")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "10",
            "X-Elapsed-Seconds": "1",
        }
        mock_resp.content = b"vid"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        env = {k: v for k, v in os.environ.items() if k != "HOST_HOME"}

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client), \
             patch.dict(os.environ, env, clear=True), \
             patch("services.video_service.os.path.exists", return_value=True):
            mock_gen.return_value = ["/root/.poindexter/video/frames/f.png"]
            await generate_video_for_post(
                post_id="p1",
                title="T",
                podcast_path="/root/.poindexter/podcast/p1.mp3",
            )

        payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1]["json"]
        assert payload["audio_path"] == "C:/Users/mattm/.poindexter/podcast/p1.mp3"

    @pytest.mark.asyncio
    async def test_non_gladlabs_paths_unchanged(self, tmp_path):
        """Paths that don't contain /root/.poindexter remain unchanged."""
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        podcast = tmp_path / "podcast.mp3"
        podcast.write_bytes(b"audio")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "10",
            "X-Elapsed-Seconds": "1",
        }
        mock_resp.content = b"vid"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_images_for_video", new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client), \
             patch("services.video_service.os.path.exists", return_value=True):
            # Image path that does NOT contain /root/.poindexter
            mock_gen.return_value = ["/tmp/some/other/frame.png"]
            await generate_video_for_post(
                post_id="p2",
                title="T",
                podcast_path="/tmp/other/podcast.mp3",
            )

        payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1]["json"]
        # Paths without /root/.poindexter should pass through unmodified
        assert payload["image_paths"] == ["/tmp/some/other/frame.png"]
        assert payload["audio_path"] == "/tmp/other/podcast.mp3"


# ---------------------------------------------------------------------------
# _extract_images_from_content
# ---------------------------------------------------------------------------

class TestExtractImagesFromContent:
    """URL extraction + download from post content."""

    @pytest.mark.asyncio
    async def test_no_images_returns_empty(self, tmp_path):
        with patch("services.video_service.VIDEO_DIR", tmp_path):
            result = await _extract_images_from_content("just text, no images here.")
        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_markdown_image_urls(self, tmp_path):
        content = "![alt one](https://cdn.example.com/img1.png) ![alt two](https://cdn.example.com/img2.jpg)"
        # Make a fake response with enough bytes (>5000)
        big_bytes = b"x" * 6000
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = big_bytes

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert len(result) == 2
        # Both files were written to the frames dir
        for path in result:
            assert os.path.exists(path)
            assert os.path.getsize(path) == 6000

    @pytest.mark.asyncio
    async def test_extracts_html_img_tags(self, tmp_path):
        content = '<img src="https://cdn.example.com/foo.png" alt="x">'
        big_bytes = b"y" * 6000
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = big_bytes

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_deduplicates_urls(self, tmp_path):
        # Same URL referenced twice → should download once
        content = (
            "![a](https://cdn.example.com/same.png) "
            "![b](https://cdn.example.com/same.png)"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"x" * 6000

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert len(result) == 1
        assert mock_client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_skips_small_responses(self, tmp_path):
        """Responses under 5000 bytes are treated as failures (likely error pages)."""
        content = "![a](https://cdn.example.com/tiny.png)"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"too small"  # well under 5000

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_404(self, tmp_path):
        content = "![a](https://cdn.example.com/missing.png)"
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.content = b"x" * 6000

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert result == []

    @pytest.mark.asyncio
    async def test_jpeg_content_type_uses_jpg_extension(self, tmp_path):
        content = "![a](https://cdn.example.com/photo.jpg)"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/jpeg"}
        mock_resp.content = b"j" * 6000

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert len(result) == 1
        assert result[0].endswith(".jpg")

    @pytest.mark.asyncio
    async def test_network_exception_skipped(self, tmp_path):
        content = "![a](https://cdn.example.com/will-fail.png)"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection lost"))

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _extract_images_from_content(content)

        assert result == []


# ---------------------------------------------------------------------------
# _generate_images_from_scenes
# ---------------------------------------------------------------------------

class TestGenerateImagesFromScenes:
    """SDXL image generation from pre-written scene descriptions."""

    @pytest.mark.asyncio
    async def test_empty_scenes_returns_empty(self, tmp_path):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock()
        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _generate_images_from_scenes([])
        assert result == []
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generates_one_image_per_scene(self, tmp_path):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}
        mock_resp.content = b"\x89PNG fake image bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        scenes = [
            "cinematic data center with flowing light",
            "abstract neural network in space",
            "futuristic gpu cluster, blue lighting",
        ]
        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _generate_images_from_scenes(scenes)

        assert len(result) == 3
        assert mock_client.post.await_count == 3
        for path in result:
            assert os.path.exists(path)

    @pytest.mark.asyncio
    async def test_sdxl_failure_skips_frame(self, tmp_path):
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {"content-type": "image/png"}
        ok_resp.content = b"good png"

        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.headers = {"content-type": "application/json"}
        fail_resp.text = '{"error": "OOM"}'

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[ok_resp, fail_resp])

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _generate_images_from_scenes(["scene a", "scene b"])

        # Only the successful one is in the result
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_sdxl_exception_skips_frame(self, tmp_path):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("sdxl down"))

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _generate_images_from_scenes(["scene a"])

        assert result == []


# ---------------------------------------------------------------------------
# _generate_short_summary_audio
# ---------------------------------------------------------------------------

class TestGenerateShortSummaryAudio:
    """Ollama summary script + edge_tts narration generation."""

    @pytest.mark.asyncio
    async def test_too_short_returns_none(self, tmp_path):
        ollama_resp = MagicMock()
        ollama_resp.json.return_value = {"response": "tiny."}
        ollama_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=ollama_resp)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _generate_short_summary_audio("p1", "Title", "Some content body here")

        assert result is None

    @pytest.mark.asyncio
    async def test_ollama_failure_returns_none(self, tmp_path):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("ollama down"))

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            result = await _generate_short_summary_audio("p1", "Title", "Body")

        assert result is None

    @pytest.mark.asyncio
    async def test_happy_path_returns_audio_path(self, tmp_path):
        long_script = "x" * 200  # >50 chars to pass the gate
        ollama_resp = MagicMock()
        ollama_resp.json.return_value = {"response": long_script}
        ollama_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=ollama_resp)

        # Fake edge_tts.Communicate that writes the file when .save() is called
        fake_communicate = MagicMock()
        fake_communicate.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"\x49\x44\x33" + b"x" * 2000))

        fake_edge_tts = MagicMock()
        fake_edge_tts.Communicate = MagicMock(return_value=fake_communicate)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {"edge_tts": fake_edge_tts}):
            result = await _generate_short_summary_audio("p1", "Title", "Body" * 100)

        assert result is not None
        assert "p1-short-audio.mp3" in result
        assert os.path.exists(result)

    @pytest.mark.asyncio
    async def test_audio_file_too_small_returns_none(self, tmp_path):
        long_script = "x" * 200
        ollama_resp = MagicMock()
        ollama_resp.json.return_value = {"response": long_script}
        ollama_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=ollama_resp)

        # Edge TTS produces a tiny (<=1000 byte) file
        fake_communicate = MagicMock()
        fake_communicate.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"tiny"))

        fake_edge_tts = MagicMock()
        fake_edge_tts.Communicate = MagicMock(return_value=fake_communicate)

        with patch("services.video_service.VIDEO_DIR", tmp_path), \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {"edge_tts": fake_edge_tts}):
            result = await _generate_short_summary_audio("p1", "T", "Body" * 100)

        assert result is None


# ---------------------------------------------------------------------------
# generate_short_video_for_post
# ---------------------------------------------------------------------------

class TestGenerateShortVideoForPost:
    """Top-level short video generation orchestration."""

    @pytest.mark.asyncio
    async def test_skips_if_already_exists(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        existing = video_dir / "p1-short.mp4"
        existing.write_bytes(b"existing short")

        with patch("services.video_service.VIDEO_DIR", video_dir):
            result = await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
            )

        assert result.success is True
        assert result.file_path == str(existing)

    @pytest.mark.asyncio
    async def test_no_audio_no_podcast_returns_failure(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_short_summary_audio",
                   new_callable=AsyncMock) as mock_summary, \
             patch("services.video_service.os.path.exists", return_value=False):
            mock_summary.return_value = None
            result = await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
            )

        assert result.success is False
        assert "audio" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_no_images_returns_failure(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        # Audio file path that exists
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"x" * 2000)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_short_summary_audio",
                   new_callable=AsyncMock) as mock_summary, \
             patch("services.video_service._extract_images_from_content",
                   new_callable=AsyncMock) as mock_extract, \
             patch("services.video_service._generate_images_for_video",
                   new_callable=AsyncMock) as mock_gen:
            mock_summary.return_value = str(audio_path)
            mock_extract.return_value = []
            mock_gen.return_value = []
            result = await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
            )

        assert result.success is False
        assert "images" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_happy_path_renders_short(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"x" * 2000)

        # Successful video server response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-type": "video/mp4",
            "X-Duration-Seconds": "55",
        }
        mock_resp.content = b"short video bytes"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_short_summary_audio",
                   new_callable=AsyncMock) as mock_summary, \
             patch("services.video_service._extract_images_from_content",
                   new_callable=AsyncMock) as mock_extract, \
             patch("services.video_service._generate_images_for_video",
                   new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_summary.return_value = str(audio_path)
            mock_extract.return_value = []
            mock_gen.return_value = ["/root/.poindexter/video/frames/frame_00.png"]
            result = await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
            )

        assert result.success is True
        assert result.duration_seconds == 55
        assert result.file_size_bytes > 0
        # Verify it called the /generate-short endpoint, not /generate
        call_url = mock_client.post.call_args.args[0]
        assert "generate-short" in call_url

    @pytest.mark.asyncio
    async def test_video_server_error_returns_failure(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"x" * 2000)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"error": "ffmpeg crashed"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_short_summary_audio",
                   new_callable=AsyncMock) as mock_summary, \
             patch("services.video_service._extract_images_from_content",
                   new_callable=AsyncMock) as mock_extract, \
             patch("services.video_service._generate_images_for_video",
                   new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_summary.return_value = str(audio_path)
            mock_extract.return_value = []
            mock_gen.return_value = ["/root/.poindexter/video/frames/frame_00.png"]
            result = await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
            )

        assert result.success is False
        assert "ffmpeg" in (result.error or "")

    @pytest.mark.asyncio
    async def test_uses_pre_generated_scenes_when_provided(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"x" * 2000)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "video/mp4", "X-Duration-Seconds": "30"}
        mock_resp.content = b"v"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_short_summary_audio",
                   new_callable=AsyncMock) as mock_summary, \
             patch("services.video_service._extract_images_from_content",
                   new_callable=AsyncMock) as mock_extract, \
             patch("services.video_service._generate_images_from_scenes",
                   new_callable=AsyncMock) as mock_scenes, \
             patch("services.video_service._generate_images_for_video",
                   new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client):
            mock_summary.return_value = str(audio_path)
            mock_extract.return_value = []
            mock_scenes.return_value = ["/root/.poindexter/video/frames/scene_00.png"]
            mock_gen.return_value = []
            await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
                pre_generated_scenes=["scene a", "scene b"],
            )

        # When pre_generated_scenes is supplied, _generate_images_from_scenes
        # is called, not _generate_images_for_video
        mock_scenes.assert_awaited_once()
        mock_gen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_full_podcast_when_summary_audio_fails(self, tmp_path):
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        # Pre-existing podcast file at the default location
        podcast_dir = tmp_path / "podcast"
        podcast_dir.mkdir()
        podcast_file = podcast_dir / "p1.mp3"
        podcast_file.write_bytes(b"podcast bytes")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "video/mp4", "X-Duration-Seconds": "60"}
        mock_resp.content = b"v"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        # podcast_service.PODCAST_DIR is imported inside the function
        fake_podcast_module = MagicMock()
        fake_podcast_module.PODCAST_DIR = podcast_dir

        with patch("services.video_service.VIDEO_DIR", video_dir), \
             patch("services.video_service._generate_short_summary_audio",
                   new_callable=AsyncMock) as mock_summary, \
             patch("services.video_service._extract_images_from_content",
                   new_callable=AsyncMock) as mock_extract, \
             patch("services.video_service._generate_images_for_video",
                   new_callable=AsyncMock) as mock_gen, \
             patch("services.video_service.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {"services.podcast_service": fake_podcast_module}):
            mock_summary.return_value = None  # short summary fails
            mock_extract.return_value = []
            mock_gen.return_value = ["/root/.poindexter/video/frames/frame_00.png"]
            result = await generate_short_video_for_post(
                post_id="p1", title="T", content="body",
            )

        assert result.success is True
        # The audio path passed to the video server should be the full podcast
        payload = mock_client.post.call_args.kwargs.get("json")
        assert "p1.mp3" in payload["audio_path"]
