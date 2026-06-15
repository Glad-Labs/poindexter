"""Unit tests for the dev_diary legacy-AV purge targeting logic.

The two helpers under test decide *what gets deleted* on two irreversible
surfaces (R2 objects, host-disk files), so they are the safety-critical core:

* ``r2_key_from_url`` — must return a key ONLY for objects under our own
  bucket; an external URL (e.g. a Pexels featured image) must map to ``None``
  so the purge can never issue a delete against a host we don't own.
* ``host_path_from_storage_path`` — must translate a container path to the
  host bind-mount path, and refuse anything without a ``.poindexter/`` segment.

The SQL constants are contract-tested for shape (dev_diary-scoped, AV-only,
never ``featured_image``/``canonical_blog``) — the same cheap guard used for
the incident migration. Heavy deps are lazy-imported inside ``_run``, so this
module imports with stdlib only.

Run:  python -m pytest scripts/test_purge_dev_diary_legacy_av.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

# Underscore-named script so it imports cleanly here (prune_mcp_orphans pattern).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import purge_dev_diary_legacy_av as purge  # noqa: E402

# Placeholder bucket — the real storage_public_url is operator config, never
# hardcoded in mirrored OSS (Glad-Labs/poindexter#485). The helper reads the
# real base from site_config at runtime; these tests only need *a* base.
_BASE = "https://pub-examplebucket.r2.dev"


class TestR2KeyFromUrl:
    def test_podcast_url_under_bucket_yields_key(self) -> None:
        url = f"{_BASE}/podcast/v2/6f48c270.mp3"
        assert purge.r2_key_from_url(url, _BASE) == "podcast/v2/6f48c270.mp3"

    def test_video_url_with_trailing_slash_base(self) -> None:
        url = f"{_BASE}/video/abc.mp4"
        assert purge.r2_key_from_url(url, _BASE + "/") == "video/abc.mp4"

    def test_empty_url_is_none(self) -> None:
        assert purge.r2_key_from_url("", _BASE) is None

    def test_external_pexels_url_is_none(self) -> None:
        # CRITICAL: dev_diary featured images are Pexels-hosted; the purge must
        # never derive a key (and therefore never delete) an external object.
        url = "https://images.pexels.com/photos/7580842/pexels-photo.jpeg"
        assert purge.r2_key_from_url(url, _BASE) is None

    def test_url_equal_to_base_yields_none(self) -> None:
        assert purge.r2_key_from_url(_BASE + "/", _BASE) is None

    def test_other_bucket_host_is_none(self) -> None:
        url = "https://pub-OTHERBUCKET.r2.dev/podcast/v2/x.mp3"
        assert purge.r2_key_from_url(url, _BASE) is None


class TestHostPathFromStoragePath:
    def test_appuser_podcast_path_maps_under_host_root(self) -> None:
        root = Path("/srv/host/.poindexter")
        result = purge.host_path_from_storage_path(
            "/home/appuser/.poindexter/podcast/abc.mp3", root,
        )
        assert result is not None
        assert result.name == "abc.mp3"
        assert result.parent.name == "podcast"
        assert root in result.parents

    def test_root_mount_video_short_path(self) -> None:
        root = Path("/srv/host/.poindexter")
        result = purge.host_path_from_storage_path(
            "/root/.poindexter/video/abc-short.mp4", root,
        )
        assert result is not None
        assert result.name == "abc-short.mp4"
        assert result.parent.name == "video"

    def test_empty_storage_path_is_none(self) -> None:
        assert purge.host_path_from_storage_path("", Path("/x")) is None

    def test_none_storage_path_is_none(self) -> None:
        assert purge.host_path_from_storage_path(None, Path("/x")) is None

    def test_path_without_poindexter_marker_is_none(self) -> None:
        assert purge.host_path_from_storage_path("/some/other/file.mp3", Path("/x")) is None


class TestSelectSqlShape:
    def test_scopes_to_dev_diary_av_only(self) -> None:
        sql = purge._SELECT_DEV_DIARY_AV_SQL
        assert "media_assets" in sql
        assert "template_slug = 'dev_diary'" in sql
        assert "('podcast', 'video', 'video_short')" in sql

    def test_never_touches_featured_image_or_canonical(self) -> None:
        sql = purge._SELECT_DEV_DIARY_AV_SQL
        assert "featured_image" not in sql
        assert "canonical_blog" not in sql

    def test_selects_handles_needed_for_deletion(self) -> None:
        sql = purge._SELECT_DEV_DIARY_AV_SQL
        # url + storage_path are the R2 + disk handles; id keys the row delete.
        for token in ("ma.id", "ma.url", "ma.storage_path"):
            assert token in sql


class TestDeleteSqlShape:
    def test_assets_deleted_by_id_array(self) -> None:
        sql = purge._DELETE_ASSETS_BY_ID_SQL
        assert "DELETE FROM media_assets" in sql
        assert "id = ANY(" in sql
        assert "::uuid[]" in sql

    def test_approvals_scoped_to_dev_diary_av_mediums(self) -> None:
        sql = purge._DELETE_DEV_DIARY_AV_APPROVALS_SQL
        assert "DELETE FROM media_approvals" in sql
        assert "template_slug = 'dev_diary'" in sql
        assert "featured_image" not in sql
