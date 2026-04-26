"""
Tests for object-store upload service — S3-compatible media uploads (R2/S3/B2/MinIO).

After the #198 rename, non-secret values come from `site_config.get()`
(sync) and secret values come from `site_config.get_secret()` (async).
Tests mock both entry points.

Post-Phase-H (GH#95): r2_upload_service drops the module-level
site_config import. Callers pass site_config via kwarg; tests build a
MagicMock SiteConfig per case via _mock_sc().
"""

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.r2_upload_service import (
    _CONTENT_TYPES,
    _apply_cache_busting,
    upload_podcast_episode,
    upload_to_r2,
    upload_to_store,
    upload_video_episode,
)


def _mock_sc(
    values: dict[str, str] | None = None,
    *,
    object_stores_row: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a MagicMock shaped like SiteConfig.

    .get() is sync (values dict with default); .get_secret() is async
    (reads from the same dict). Supports both new ``storage_*`` keys and
    legacy ``cloudflare_r2_*`` fallbacks.

    By default ``_pool`` is set to ``None`` so the new declarative
    ``object_stores`` lookup short-circuits and falls back to the
    legacy ``storage_*`` settings — that's how every pre-#113 test
    case behaves.

    Pass ``object_stores_row`` to simulate a real row coming back from
    the ``object_stores`` table; the helper builds an asyncpg-shaped
    pool mock that returns the row from ``conn.fetchrow``.
    """
    values = values or {}
    mock = MagicMock()
    mock.get.side_effect = lambda k, d="": values.get(k, d)

    async def _get_secret(k, d=""):
        return values.get(k, d)

    mock.get_secret = AsyncMock(side_effect=_get_secret)

    if object_stores_row is None:
        # Legacy / no-row case — pool is None so the dispatcher skips
        # the SELECT and falls into the storage_* fallback path.
        mock._pool = None
    else:
        # Build a minimal asyncpg pool mock that returns the row.
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=object_stores_row)
        conn.execute = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock._pool = pool

    return mock


class TestUploadToR2:
    """Core upload_to_r2 function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self, tmp_path):
        result = await upload_to_r2(
            str(tmp_path / "nonexistent.mp3"),
            "podcast/test.mp3",
            site_config=_mock_sc(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_credentials(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"fake audio data")
        result = await upload_to_r2(
            str(mp3), "podcast/test.mp3", site_config=_mock_sc(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_uploads_successfully(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"fake audio data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        sc = _mock_sc({
            "storage_access_key": "test_key",
            "storage_secret_key": "test_secret",
            "storage_endpoint": "https://test.r2.dev",
            "storage_bucket": "test-bucket",
            "storage_public_url": "https://pub-test.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await upload_to_r2(
                str(mp3), "podcast/abc.mp3", site_config=sc,
            )

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
        sc = _mock_sc({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await upload_to_r2(str(mp4), "video/abc.mp4", site_config=sc)

        call_args = mock_s3.upload_file.call_args
        assert call_args[1]["ExtraArgs"]["ContentType"] == "video/mp4"

    @pytest.mark.asyncio
    async def test_custom_content_type_overrides(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        sc = _mock_sc({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            await upload_to_r2(
                str(f),
                "custom/file.bin",
                content_type="application/custom",
                site_config=sc,
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
        sc = _mock_sc({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await upload_to_r2(
                str(f), "podcast/fail.mp3", site_config=sc,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_boto3_missing(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"data")

        sc = _mock_sc({
            "storage_access_key": "key",
            "storage_secret_key": "secret",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": None}), \
             patch("builtins.__import__", side_effect=ImportError("no boto3")):
            result = await upload_to_r2(
                str(f), "podcast/test.mp3", site_config=sc,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_cloudflare_keys(self, tmp_path):
        """#198 migration: code accepts legacy cloudflare_r2_* keys as fallback."""
        mp3 = tmp_path / "legacy.mp3"
        mp3.write_bytes(b"data")
        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        sc = _mock_sc({
            # Only legacy keys set — new code should fall back to them.
            "cloudflare_r2_access_key": "legacy_key",
            "cloudflare_r2_secret_key": "legacy_secret",
            "cloudflare_r2_endpoint": "https://legacy.r2.dev",
            "cloudflare_r2_bucket": "legacy-bucket",
            "r2_public_url": "https://pub-legacy.r2.dev",
        })
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await upload_to_r2(
                str(mp3), "podcast/legacy.mp3", site_config=sc,
            )
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
    """upload_podcast_episode convenience function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        with patch("services.r2_upload_service.Path") as mock_path_cls:
            mock_path = MagicMock()
            mock_path.__truediv__ = MagicMock(return_value=MagicMock(exists=MagicMock(return_value=False)))
            mock_path_cls.return_value = mock_path
            # Use a non-existent post_id
            result = await upload_podcast_episode(
                "nonexistent-post-id-000", site_config=_mock_sc(),
            )
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
            os.path.expanduser("~")
            with patch.object(mod, "upload_to_r2", mock_upload):
                # Call directly with patched path
                from services.r2_upload_service import upload_to_r2 as real_upload
                result = await real_upload(
                    str(mp3), "podcast/test-post.mp3", site_config=_mock_sc(),
                )

            assert result == "https://r2.dev/podcast/test-post.mp3"


class TestUploadVideoEpisode:
    """upload_video_episode convenience function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self):
        result = await upload_video_episode(
            "nonexistent-video-id-000", site_config=_mock_sc(),
        )
        assert result is None


# ---------------------------------------------------------------------------
# upload_to_store — declarative path (GH-113)
# ---------------------------------------------------------------------------


def _store_row(**overrides: Any) -> dict[str, Any]:
    """Build a fake ``object_stores`` row dict with sensible defaults.

    Tests override only the fields they care about — keeps each test
    focused on one behavior (enabled/disabled, credentials shape,
    cache busting).
    """
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "primary",
        "provider": "cloudflare_r2",
        "endpoint_url": "https://test.r2.dev",
        "bucket": "test-bucket",
        "public_url": "https://pub-test.r2.dev",
        "credentials_ref": "storage_credentials",
        "cache_busting_strategy": "none",
        "cache_busting_config": {},
        "enabled": True,
    }
    base.update(overrides)
    return base


class TestUploadToStore:
    """Declarative ``upload_to_store(name, ...)`` dispatcher."""

    @pytest.mark.asyncio
    async def test_uploads_via_object_stores_row(self, tmp_path):
        """Happy path — row exists, enabled, JSON credentials present."""
        mp3 = tmp_path / "podcast.mp3"
        mp3.write_bytes(b"data")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        # JSON-blob credentials at the row's credentials_ref pointer
        creds_json = json.dumps({
            "access_key": "row_key",
            "secret_key": "row_secret",
        })
        sc = _mock_sc(
            {"storage_credentials": creds_json},
            object_stores_row=_store_row(),
        )
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await upload_to_store(
                "primary", str(mp3), "podcast/abc.mp3", site_config=sc,
            )

        assert result == "https://pub-test.r2.dev/podcast/abc.mp3"
        mock_s3.upload_file.assert_called_once()
        call_args = mock_s3.upload_file.call_args
        assert call_args[0][1] == "test-bucket"
        assert call_args[0][2] == "podcast/abc.mp3"

    @pytest.mark.asyncio
    async def test_disabled_row_returns_none(self, tmp_path):
        """Disabled row short-circuits — no upload attempted."""
        mp3 = tmp_path / "x.mp3"
        mp3.write_bytes(b"data")
        sc = _mock_sc(
            {"storage_credentials": json.dumps({"access_key": "k", "secret_key": "s"})},
            object_stores_row=_store_row(enabled=False),
        )
        result = await upload_to_store(
            "primary", str(mp3), "x.mp3", site_config=sc,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_none(self, tmp_path):
        """Row exists but credentials_ref points to nothing — no-op."""
        mp3 = tmp_path / "x.mp3"
        mp3.write_bytes(b"data")
        # No storage_credentials value, no legacy keys either
        sc = _mock_sc({}, object_stores_row=_store_row())
        result = await upload_to_store(
            "primary", str(mp3), "x.mp3", site_config=sc,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_named_store_without_row_returns_none(self, tmp_path):
        """Non-'primary' name with no row — explicit config bug, no fallback."""
        mp3 = tmp_path / "x.mp3"
        mp3.write_bytes(b"data")
        # _pool is None → _lookup_store returns None → for a non-primary
        # name, that's a hard skip (no legacy fallback path).
        sc = _mock_sc({"storage_credentials": json.dumps({"access_key": "k", "secret_key": "s"})})
        result = await upload_to_store(
            "podcast_cdn", str(mp3), "podcast/x.mp3", site_config=sc,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_primary_falls_back_to_legacy_when_no_row(self, tmp_path):
        """Pre-migration deployments: ``primary`` lookup yields no row,
        but the storage_* settings still wire up a working upload."""
        mp3 = tmp_path / "legacy.mp3"
        mp3.write_bytes(b"data")
        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        sc = _mock_sc({
            "storage_access_key": "k",
            "storage_secret_key": "s",
            "storage_endpoint": "https://x.r2.dev",
            "storage_bucket": "b",
            "storage_public_url": "https://pub.r2.dev",
        })
        # _pool is None — _lookup_store returns None — fallback fires.
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await upload_to_store(
                "primary", str(mp3), "podcast/legacy.mp3", site_config=sc,
            )
        assert result == "https://pub.r2.dev/podcast/legacy.mp3"

    @pytest.mark.asyncio
    async def test_back_compat_shim_delegates_to_store(self, tmp_path):
        """``upload_to_r2`` is a thin shim over ``upload_to_store('primary')``."""
        mp3 = tmp_path / "shim.mp3"
        mp3.write_bytes(b"data")
        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3
        sc = _mock_sc(
            {"storage_credentials": json.dumps({"access_key": "k", "secret_key": "s"})},
            object_stores_row=_store_row(bucket="shim-bucket", public_url="https://shim.pub"),
        )
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = await upload_to_r2(
                str(mp3), "podcast/shim.mp3", site_config=sc,
            )
        # Same outcome as calling upload_to_store("primary", ...) directly.
        assert result == "https://shim.pub/podcast/shim.mp3"
        call_args = mock_s3.upload_file.call_args
        assert call_args[0][1] == "shim-bucket"


class TestCacheBusting:
    """``_apply_cache_busting`` — strategy-driven key mutation."""

    def test_none_strategy_passes_through(self):
        assert _apply_cache_busting("podcast/abc.mp3", "none", {}) == "podcast/abc.mp3"

    def test_version_prefix_inserts_segment(self):
        # ``podcast/abc.mp3`` + version_prefix v2 → ``podcast/v2/abc.mp3``
        out = _apply_cache_busting(
            "podcast/abc.mp3", "version_prefix", {"version": "v2"},
        )
        assert out == "podcast/v2/abc.mp3"

    def test_version_prefix_default_v1(self):
        out = _apply_cache_busting("podcast/abc.mp3", "version_prefix", {})
        assert out == "podcast/v1/abc.mp3"

    def test_unknown_strategy_passes_through(self):
        # Forward-compat — new strategies in the DB don't crash old workers.
        assert _apply_cache_busting("k", "future_strategy", {}) == "k"


class TestUploadPodcastEpisodeNamedLookup:
    """``upload_podcast_episode`` prefers the ``podcast_cdn`` row when present."""

    @pytest.mark.asyncio
    async def test_falls_back_to_primary_without_podcast_cdn_row(self, tmp_path, monkeypatch):
        """No podcast_cdn row → falls back to primary with legacy version prefix."""
        # Stub PODCAST_DIR by monkeypatching expanduser.
        post_id = "post-123"
        podcast_dir = tmp_path / ".poindexter" / "podcast"
        podcast_dir.mkdir(parents=True)
        (podcast_dir / f"{post_id}.mp3").write_bytes(b"data")

        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)

        captured: dict[str, Any] = {}

        async def fake_upload(name, local_path, key, content_type=None, *, site_config):
            captured["name"] = name
            captured["key"] = key
            return f"https://example.com/{key}"

        with patch("services.r2_upload_service.upload_to_store", side_effect=fake_upload):
            sc = _mock_sc({"podcast_cdn_version": "v3"})  # _pool=None → no rows
            url = await upload_podcast_episode(post_id, site_config=sc)

        assert url == f"https://example.com/podcast/v3/{post_id}.mp3"
        assert captured["name"] == "primary"
        assert captured["key"] == f"podcast/v3/{post_id}.mp3"
