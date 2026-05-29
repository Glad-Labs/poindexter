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
    R2UploadService,
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
            "r2_public_url": "https://pub-legacy.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await svc.upload_to_r2(str(mp3), "podcast/legacy.mp3")
        assert result == "https://pub-legacy.r2.dev/podcast/legacy.mp3"


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
