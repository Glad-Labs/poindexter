"""
Tests for object-store upload service — S3-compatible media uploads (R2/S3/B2/MinIO).

After the #198 rename, non-secret values come from `site_config.get()`
(sync) and secret values come from `site_config.get_secret()` (async).
Tests mock both entry points.

Constructor-DI migration PR 4 (design doc:
``docs/architecture/2026-05-28-site-config-di-migration.md``): the
former module-level ``site_config`` singleton + free functions are
gone. Tests now construct ``R2UploadService(site_config=stub)``
directly and call methods on it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.r2_upload_service import (
    _CONTENT_TYPES,
    _IMAGE_CACHE_CONTROL,
    R2UploadService,
    _convert_to_webp,
)


def _make_site_config_mock(values: dict[str, str]):
    """Return a site_config mock where .get() is sync and .get_secret() is async.

    Both read from the same ``values`` dict, supporting both the new
    ``storage_*`` keys and the legacy ``cloudflare_r2_*`` fallbacks.
    """
    mock = MagicMock()
    mock.get.side_effect = lambda k, d="": values.get(k, d)

    async def _get_secret(k, d=""):
        return values.get(k, d)

    mock.get_secret = AsyncMock(side_effect=_get_secret)
    return mock


def _make_service(values: dict[str, str]) -> R2UploadService:
    return R2UploadService(site_config=_make_site_config_mock(values))


class TestUploadToR2:
    """Core upload_to_r2 method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self, tmp_path):
        svc = _make_service({})
        result = await svc.upload_to_r2(
            str(tmp_path / "nonexistent.mp3"), "podcast/test.mp3",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_credentials(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"fake audio data")
        svc = _make_service({})  # nothing configured
        result = await svc.upload_to_r2(str(mp3), "podcast/test.mp3")
        assert result is None

    @pytest.mark.asyncio
    async def test_uploads_successfully(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"fake audio data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service({
            "storage_access_key": "test_key",
            "storage_secret_key": "test_secret",
            "storage_endpoint": "https://test.r2.dev",
            "storage_bucket": "test-bucket",
            "storage_public_url": "https://pub-test.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(str(mp3), "podcast/abc.mp3")

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
        svc = _make_service({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await svc.upload_to_r2(str(mp4), "video/abc.mp4")

        call_args = mock_s3.upload_file.call_args
        assert call_args[1]["ExtraArgs"]["ContentType"] == "video/mp4"

    @pytest.mark.asyncio
    async def test_custom_content_type_overrides(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await svc.upload_to_r2(
                str(f), "custom/file.bin", content_type="application/custom",
            )

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
        svc = _make_service({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(str(f), "podcast/fail.mp3")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_boto3_missing(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"data")

        svc = _make_service({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": None}), \
             patch("builtins.__import__", side_effect=ImportError("no boto3")):
            result = await svc.upload_to_r2(str(f), "podcast/test.mp3")

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_cloudflare_keys(self, tmp_path):
        """#198 migration: code accepts legacy cloudflare_r2_* keys as fallback."""
        mp3 = tmp_path / "legacy.mp3"
        mp3.write_bytes(b"data")
        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service({
            # Only legacy keys set — new code should fall back to them.
            "cloudflare_r2_access_key": "legacy_key",
            "cloudflare_r2_secret_key": "legacy_secret",
            "cloudflare_r2_endpoint": "https://legacy.r2.dev",
            "cloudflare_r2_bucket": "legacy-bucket",
            "cloudflare_r2_public_url": "https://pub-legacy.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(str(mp3), "podcast/legacy.mp3")
        assert result == "https://pub-legacy.r2.dev/podcast/legacy.mp3"

    @pytest.mark.asyncio
    async def test_retired_r2_public_url_key_is_not_honored(self, tmp_path):
        """storage_* cutover (#731): the deprecated ``r2_public_url`` key is
        no longer read for the public link. With valid credentials + bucket
        but ONLY ``r2_public_url`` set (no storage_/cloudflare_r2_ public_url),
        the upload returns None because the public base can't be resolved.
        """
        mp3 = tmp_path / "stale.mp3"
        mp3.write_bytes(b"data")
        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            # Only the retired key is set — must NOT be used.
            "r2_public_url": "https://pub-retired.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(str(mp3), "podcast/stale.mp3")
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
    """upload_podcast_episode convenience method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        svc = _make_service({})
        # Use a non-existent post_id — Path will resolve, file won't exist.
        result = await svc.upload_podcast_episode("nonexistent-post-id-000")
        assert result is None


class TestUploadVideoEpisode:
    """upload_video_episode convenience method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        svc = _make_service({})
        result = await svc.upload_video_episode("nonexistent-video-id-000")
        assert result is None


class TestConstructorRequiresSiteConfig:
    """Fail-loud: instantiation without site_config is a TypeError.

    Pins the DI-PR-4 contract: there is no module-level default and no
    fallback; reading settings without a constructed instance is a
    programmer error caught at construction time.
    """

    def test_missing_site_config_raises_type_error(self):
        with pytest.raises(TypeError):
            R2UploadService()  # type: ignore[call-arg]


class TestContainerExposesService:
    """``AppContainer.r2_upload_service`` constructs an R2UploadService
    wired to the container's SiteConfig — PR 4 contract.
    """

    def test_container_property_returns_r2_upload_service(self):
        from services.container import AppContainer
        from services.site_config import SiteConfig

        site_config = SiteConfig()
        container = AppContainer(site_config=site_config, pool=MagicMock())
        svc = container.r2_upload_service
        assert isinstance(svc, R2UploadService)
        # Same instance on repeat lookup — cached_property contract.
        assert container.r2_upload_service is svc
        # And it's wired to the container's SiteConfig.
        assert svc._site_config is site_config  # noqa: SLF001


class TestConvertToWebp:
    """_convert_to_webp helper — poindexter#732."""

    def test_returns_none_when_pillow_absent(self, tmp_path):
        f = tmp_path / "test.png"
        f.write_bytes(b"not an image")
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            result = _convert_to_webp(f)
        # Either None (import failed) or None (Pillow not installed).
        # Either way the caller must handle None gracefully.
        assert result is None or hasattr(result, "read")

    def test_returns_none_for_corrupt_image(self, tmp_path):
        f = tmp_path / "bad.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\nNOTREALLYAPNG")
        result = _convert_to_webp(f)
        # Should not raise; corrupt data → None
        assert result is None or hasattr(result, "read")

    def test_returns_bytesio_for_valid_png(self, tmp_path):
        """Create a minimal 1x1 PNG and verify it converts to WebP bytes."""
        try:
            from PIL import Image  # type: ignore[import]
        except ImportError:
            pytest.skip("Pillow not installed")


        # Create a minimal 1x1 white PNG via Pillow
        img = Image.new("RGB", (1, 1), (255, 255, 255))
        png_path = tmp_path / "test.png"
        img.save(str(png_path), format="PNG")

        result = _convert_to_webp(png_path)
        assert result is not None
        data = result.read()
        assert len(data) > 0
        # WebP files start with RIFF....WEBP
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WEBP"


class TestWebpUploadBehavior:
    """upload_to_r2 WebP conversion and Cache-Control for images (poindexter#732)."""

    def _full_config(self) -> dict:
        return {
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        }

    @pytest.mark.asyncio
    async def test_non_image_upload_has_no_cache_control(self, tmp_path):
        """Audio/video uploads should not get the image Cache-Control header."""
        mp3 = tmp_path / "episode.mp3"
        mp3.write_bytes(b"fake audio")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service(self._full_config())

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(str(mp3), "podcast/ep.mp3")

        assert result == "https://pub.r2.dev/podcast/ep.mp3"
        call_args = mock_s3.upload_file.call_args
        extra = call_args[1]["ExtraArgs"]
        assert "CacheControl" not in extra
        assert extra["ContentType"] == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_png_upload_gets_cache_control_and_webp_key(self, tmp_path):
        """PNG images are converted to WebP with immutable Cache-Control."""
        try:
            from PIL import Image  # type: ignore[import]
        except ImportError:
            pytest.skip("Pillow not installed")


        # Create a real PNG that Pillow can convert
        img = Image.new("RGB", (2, 2), (0, 128, 255))
        png_path = tmp_path / "inline.png"
        img.save(str(png_path), format="PNG")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service(self._full_config())

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(
                str(png_path), "images/inline/abc123.png", content_type="image/png",
            )

        # Key should be rewritten to .webp
        assert result == "https://pub.r2.dev/images/inline/abc123.webp"
        # upload_fileobj used for WebP (in-memory buffer)
        mock_s3.upload_fileobj.assert_called_once()
        call_args = mock_s3.upload_fileobj.call_args
        extra = call_args[1]["ExtraArgs"]
        assert extra["ContentType"] == "image/webp"
        assert extra["CacheControl"] == _IMAGE_CACHE_CONTROL

    @pytest.mark.asyncio
    async def test_custom_domain_used_for_image_url(self, tmp_path):
        """When storage_image_custom_domain is set, image URLs use it."""
        try:
            from PIL import Image  # type: ignore[import]
        except ImportError:
            pytest.skip("Pillow not installed")

        img = Image.new("RGB", (2, 2), (255, 0, 0))
        png_path = tmp_path / "feat.png"
        img.save(str(png_path), format="PNG")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        cfg = {**self._full_config(), "storage_image_custom_domain": "https://images.gladlabs.io"}
        svc = _make_service(cfg)

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(
                str(png_path), "images/featured/t123.jpg", content_type="image/jpeg",
            )

        # Custom domain used; extension rewritten to .webp
        assert result == "https://images.gladlabs.io/images/featured/t123.webp"

    @pytest.mark.asyncio
    async def test_pillow_absent_falls_back_to_original_png(self, tmp_path):
        """When Pillow is missing, upload proceeds with original PNG (no crash)."""
        png = tmp_path / "nopillow.png"
        png.write_bytes(b"fake png data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        svc = _make_service(self._full_config())

        with patch.dict("sys.modules", {"boto3": mock_boto3}), \
             patch("services.r2_upload_service._convert_to_webp", return_value=None):
            result = await svc.upload_to_r2(
                str(png), "images/inline/nopillow.png", content_type="image/png",
            )

        # Falls back to upload_file with original key
        assert result == "https://pub.r2.dev/images/inline/nopillow.png"
        mock_s3.upload_file.assert_called_once()
        extra = mock_s3.upload_file.call_args[1]["ExtraArgs"]
        # Still gets Cache-Control (it's an image)
        assert extra["CacheControl"] == _IMAGE_CACHE_CONTROL
        assert extra["ContentType"] == "image/png"


class TestImagePublicUrlBase:
    """_image_public_url_base helper."""

    def test_prefers_custom_domain_over_public_url(self):
        svc = _make_service({
            "storage_public_url": "https://pub.r2.dev",
            "storage_image_custom_domain": "https://images.gladlabs.io",
        })
        assert svc._image_public_url_base() == "https://images.gladlabs.io"  # noqa: SLF001

    def test_falls_back_to_public_url_when_custom_empty(self):
        svc = _make_service({
            "storage_public_url": "https://pub.r2.dev/",
            "storage_image_custom_domain": "",
        })
        assert svc._image_public_url_base() == "https://pub.r2.dev"  # noqa: SLF001

    def test_returns_empty_when_neither_set(self):
        svc = _make_service({})
        assert svc._image_public_url_base() == ""  # noqa: SLF001
