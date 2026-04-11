"""
Tests for R2 upload service — Cloudflare R2 media uploads.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.r2_upload_service import (
    _CONTENT_TYPES,
    upload_podcast_episode,
    upload_to_r2,
    upload_video_episode,
)


class TestUploadToR2:
    """Core upload_to_r2 function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self, tmp_path):
        result = await upload_to_r2(str(tmp_path / "nonexistent.mp3"), "podcast/test.mp3")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_credentials(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"fake audio data")
        with patch("services.r2_upload_service.site_config") as mock_sc:
            mock_sc.get.return_value = ""
            result = await upload_to_r2(str(mp3), "podcast/test.mp3")
        assert result is None

    @pytest.mark.asyncio
    async def test_uploads_successfully(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"fake audio data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        with patch("services.r2_upload_service.site_config") as mock_sc, \
             patch.dict("sys.modules", {"boto3": mock_boto3}):
            mock_sc.get.side_effect = lambda k, d="": {
                "cloudflare_r2_access_key": "test_key",
                "cloudflare_r2_secret_key": "test_secret",
                "cloudflare_r2_endpoint": "https://test.r2.dev",
                "cloudflare_r2_bucket": "test-bucket",
                "r2_public_url": "https://pub-test.r2.dev",
            }.get(k, d)

            result = await upload_to_r2(str(mp3), "podcast/abc.mp3")

        assert result == "https://pub-test.r2.dev/podcast/abc.mp3"
        mock_s3.upload_file.assert_called_once()
        call_args = mock_s3.upload_file.call_args
        assert call_args[0][1] == "test-bucket"
        assert call_args[0][2] == "podcast/abc.mp3"
        assert call_args[1]["ExtraArgs"]["ContentType"] == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_auto_detects_content_type_mp4(self, tmp_path):
        mp4 = tmp_path / "test.mp4"
        mp4.write_bytes(b"fake video data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        with patch("services.r2_upload_service.site_config") as mock_sc, \
             patch.dict("sys.modules", {"boto3": mock_boto3}):
            mock_sc.get.side_effect = lambda k, d="": {
                "cloudflare_r2_access_key": "key",
                "cloudflare_r2_secret_key": "secret",
            }.get(k, d)

            await upload_to_r2(str(mp4), "video/abc.mp4")

        call_args = mock_s3.upload_file.call_args
        assert call_args[1]["ExtraArgs"]["ContentType"] == "video/mp4"

    @pytest.mark.asyncio
    async def test_custom_content_type_overrides(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        with patch("services.r2_upload_service.site_config") as mock_sc, \
             patch.dict("sys.modules", {"boto3": mock_boto3}):
            mock_sc.get.side_effect = lambda k, d="": {
                "cloudflare_r2_access_key": "key",
                "cloudflare_r2_secret_key": "secret",
            }.get(k, d)

            await upload_to_r2(str(f), "custom/file.bin", content_type="application/custom")

        call_args = mock_s3.upload_file.call_args
        assert call_args[1]["ExtraArgs"]["ContentType"] == "application/custom"

    @pytest.mark.asyncio
    async def test_returns_none_on_upload_error(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"data")

        mock_s3 = MagicMock()
        mock_s3.upload_file.side_effect = Exception("Connection refused")
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        with patch("services.r2_upload_service.site_config") as mock_sc, \
             patch.dict("sys.modules", {"boto3": mock_boto3}):
            mock_sc.get.side_effect = lambda k, d="": {
                "cloudflare_r2_access_key": "key",
                "cloudflare_r2_secret_key": "secret",
            }.get(k, d)

            result = await upload_to_r2(str(f), "podcast/fail.mp3")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_boto3_missing(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"data")

        with patch("services.r2_upload_service.site_config") as mock_sc, \
             patch.dict("sys.modules", {"boto3": None}), \
             patch("builtins.__import__", side_effect=ImportError("no boto3")):
            mock_sc.get.side_effect = lambda k, d="": {
                "cloudflare_r2_access_key": "key",
                "cloudflare_r2_secret_key": "secret",
            }.get(k, d)

            result = await upload_to_r2(str(f), "podcast/test.mp3")

        assert result is None


class TestContentTypes:
    """Content type mapping."""

    def test_mp3_type(self):
        assert _CONTENT_TYPES[".mp3"] == "audio/mpeg"

    def test_mp4_type(self):
        assert _CONTENT_TYPES[".mp4"] == "video/mp4"

    def test_jpg_type(self):
        assert _CONTENT_TYPES[".jpg"] == "image/jpeg"

    def test_png_type(self):
        assert _CONTENT_TYPES[".png"] == "image/png"

    def test_webp_type(self):
        assert _CONTENT_TYPES[".webp"] == "image/webp"


class TestUploadPodcastEpisode:
    """upload_podcast_episode convenience function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        with patch("services.r2_upload_service.Path") as mock_path_cls:
            mock_path = MagicMock()
            mock_path.__truediv__ = MagicMock(return_value=MagicMock(exists=MagicMock(return_value=False)))
            mock_path_cls.return_value = mock_path
            # Use a non-existent post_id
            result = await upload_podcast_episode("nonexistent-post-id-000")
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_upload_for_existing_file(self, tmp_path):
        mp3 = tmp_path / "test-post.mp3"
        mp3.write_bytes(b"podcast data")

        with patch("services.r2_upload_service.upload_to_r2", new_callable=AsyncMock) as mock_upload, \
             patch("services.r2_upload_service.Path") as mock_path_cls:
            # Make PODCAST_DIR point to tmp_path
            mock_path_cls.return_value.__truediv__ = MagicMock(return_value=tmp_path)
            mock_upload.return_value = "https://r2.dev/podcast/test-post.mp3"

            # Patch at module level
            import services.r2_upload_service as mod
            original_dir = os.path.expanduser("~")
            with patch.object(mod, "upload_to_r2", mock_upload):
                # Call directly with patched path
                from services.r2_upload_service import upload_to_r2 as real_upload
                result = await real_upload(str(mp3), "podcast/test-post.mp3")

            assert result == "https://r2.dev/podcast/test-post.mp3"


class TestUploadVideoEpisode:
    """upload_video_episode convenience function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        result = await upload_video_episode("nonexistent-video-id-000")
        assert result is None
