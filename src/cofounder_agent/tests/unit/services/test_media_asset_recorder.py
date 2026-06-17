"""Tests for ``services/media_asset_recorder.py``.

The recorder is the single source of truth for ``media_assets``
INSERTs across every producer (Stages, jobs, services). Tests cover
the contract every producer relies on: best-effort writes, mime-type
auto-derivation, dedupe predicates for the backfill script.

Closes Glad-Labs/poindexter#161.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.media_asset_recorder import (
    _DEFAULT_MIME_TYPES,
    _json_dumps,
    file_size_safe,
    media_asset_exists,
    record_media_asset,
)


def _fake_pool(fetchval_return: Any = "uuid-abc", fetchval_side_effect: Any = None):
    """Build an asyncpg-shaped pool that returns ``fetchval_return``."""
    conn = MagicMock()
    if fetchval_side_effect is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        conn.fetchval = AsyncMock(return_value=fetchval_return)

    class _Ctx:
        async def __aenter__(self) -> Any:
            return conn

        async def __aexit__(self, *_a) -> None:
            return None

    pool = MagicMock()
    pool.acquire = lambda: _Ctx()
    return pool, conn


# ---------------------------------------------------------------------------
# record_media_asset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRecordMediaAsset:
    async def test_returns_none_when_pool_is_none(self):
        out = await record_media_asset(
            pool=None, post_id="p", asset_type="podcast",
        )
        assert out is None

    async def test_returns_uuid_string_on_success(self):
        pool, conn = _fake_pool(fetchval_return="abc-123")
        out = await record_media_asset(
            pool=pool, post_id="p", asset_type="video",
            storage_path="/x.mp4", public_url="https://cdn/x.mp4",
            file_size_bytes=1024, duration_ms=5000,
            width=1920, height=1080,
            provider_plugin="compositor.ffmpeg_local",
            metadata={"a": 1},
        )
        assert out == "abc-123"
        conn.fetchval.assert_awaited_once()

    async def test_db_failure_returns_none_does_not_raise(self):
        pool, conn = _fake_pool(fetchval_side_effect=RuntimeError("boom"))
        out = await record_media_asset(
            pool=pool, post_id="p", asset_type="podcast",
        )
        assert out is None  # swallowed, not raised

    async def test_auto_derives_mime_type_from_asset_type(self):
        pool, conn = _fake_pool()
        await record_media_asset(
            pool=pool, post_id="p", asset_type="podcast",
        )
        # mime_type is the 13th positional after the SQL — assert via call args
        call_args = conn.fetchval.await_args.args
        assert "audio/mpeg" in call_args

    async def test_auto_mime_for_featured_image(self):
        pool, conn = _fake_pool()
        await record_media_asset(
            pool=pool, post_id="p", asset_type="featured_image",
        )
        assert "image/jpeg" in conn.fetchval.await_args.args

    async def test_explicit_mime_overrides_default(self):
        pool, conn = _fake_pool()
        await record_media_asset(
            pool=pool, post_id="p", asset_type="featured_image",
            mime_type="image/png",
        )
        args = conn.fetchval.await_args.args
        assert "image/png" in args
        # Default 'image/jpeg' should NOT have been used.

    async def test_task_id_is_persisted_when_provided(self):
        """task_id must reach the INSERT so back-stamp works later.

        Producer stages run before the post exists (canonical_blog:
        source_featured_image → ... → finalize → publish). They get
        post_id=None but DO have task_id in scope. Persisting task_id
        is what lets publish_post_from_task close the FK afterward
        (Glad-Labs/glad-labs-stack#193).
        """
        pool, conn = _fake_pool()
        await record_media_asset(
            pool=pool, post_id=None, task_id="task-abc-xyz",
            asset_type="featured_image",
        )
        args = conn.fetchval.await_args.args
        assert "task-abc-xyz" in args, (
            "record_media_asset must pass task_id through to the INSERT"
        )

    async def test_task_id_omitted_defaults_to_none(self):
        """Existing callers that don't pass task_id still work; NULL goes in."""
        pool, conn = _fake_pool()
        await record_media_asset(
            pool=pool, post_id="p", asset_type="podcast",
        )
        args = conn.fetchval.await_args.args
        # task_id is the 8th positional after SQL (post_id, task_id, ...).
        # Just confirm None appears among the args (other params are
        # non-None default values like "" / 0 / 0.0).
        assert None in args
        assert "image/jpeg" not in args

    async def test_unknown_asset_type_uses_octet_stream(self):
        pool, conn = _fake_pool()
        await record_media_asset(
            pool=pool, post_id="p", asset_type="weirdo_type",
        )
        assert "application/octet-stream" in conn.fetchval.await_args.args

    # ---- video-family upsert under the (post_id,type) unique guard (#1460) ----

    async def test_video_family_insert_uses_on_conflict(self):
        """A video row WITH a post_id upserts under uniq_media_assets_post_video_type
        so a re-stamp refreshes the row instead of raising the unique violation."""
        pool, conn = _fake_pool(fetchval_return="id-1")
        await record_media_asset(
            pool=pool, post_id="00000000-0000-0000-0000-000000000001",
            asset_type="video", storage_path="/tmp/x.mp4",
        )
        sql = conn.fetchval.await_args.args[0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql

    async def test_video_short_family_insert_uses_on_conflict(self):
        pool, conn = _fake_pool(fetchval_return="id-1b")
        await record_media_asset(
            pool=pool, post_id="00000000-0000-0000-0000-000000000001",
            asset_type="video_short", storage_path="/tmp/x.mp4",
        )
        assert "ON CONFLICT" in conn.fetchval.await_args.args[0]

    async def test_video_family_without_post_id_has_no_on_conflict(self):
        """Task-keyed renders (media.persist, post_id=None) fall outside the partial
        index predicate, so they keep the plain INSERT — no conflict target exists."""
        pool, conn = _fake_pool(fetchval_return="id-1c")
        await record_media_asset(
            pool=pool, post_id=None, task_id="t-1",
            asset_type="video", storage_path="/tmp/x.mp4",
        )
        assert "ON CONFLICT" not in conn.fetchval.await_args.args[0]

    async def test_image_insert_has_no_on_conflict(self):
        """Images have no (post_id,type) guard — multiple inline_image rows per post
        are legitimate — so they keep the plain INSERT."""
        pool, conn = _fake_pool(fetchval_return="id-2")
        await record_media_asset(
            pool=pool, post_id="00000000-0000-0000-0000-000000000001",
            asset_type="inline_image", storage_path="/tmp/x.png",
        )
        assert "ON CONFLICT" not in conn.fetchval.await_args.args[0]


# ---------------------------------------------------------------------------
# media_asset_exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMediaAssetExists:
    async def test_returns_false_when_pool_is_none(self):
        assert await media_asset_exists(
            pool=None, post_id="p", asset_type="podcast",
        ) is False

    async def test_returns_false_when_post_id_is_none(self):
        pool, _ = _fake_pool()
        assert await media_asset_exists(
            pool=pool, post_id=None, asset_type="podcast",
        ) is False

    async def test_returns_true_when_row_found(self):
        pool, conn = _fake_pool(fetchval_return=1)
        assert await media_asset_exists(
            pool=pool, post_id="p", asset_type="podcast",
            storage_path="/x.mp3",
        ) is True
        conn.fetchval.assert_awaited_once()

    async def test_returns_false_when_no_row(self):
        pool, _ = _fake_pool(fetchval_return=None)
        assert await media_asset_exists(
            pool=pool, post_id="p", asset_type="podcast",
            public_url="https://x.mp3",
        ) is False

    async def test_db_error_returns_false(self):
        pool, _ = _fake_pool(fetchval_side_effect=RuntimeError("boom"))
        assert await media_asset_exists(
            pool=pool, post_id="p", asset_type="podcast",
            storage_path="/x.mp3",
        ) is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestJsonDumps:
    def test_dict(self):
        out = _json_dumps({"a": 1})
        assert '"a": 1' in out

    def test_none(self):
        assert _json_dumps(None) == "{}"

    def test_unserializable_falls_back_to_str(self):
        out = _json_dumps({"a": {1, 2, 3}})
        # set is not JSON-serializable; coerced to str.
        assert '"a"' in out


@pytest.mark.asyncio
class TestFileSizeSafe:
    async def test_existing_file(self, tmp_path):
        f = tmp_path / "x.mp3"
        f.write_bytes(b"hello")
        assert await file_size_safe(str(f)) == 5

    async def test_missing_file_returns_zero(self):
        assert await file_size_safe("/nonexistent/x.mp3") == 0

    async def test_empty_path_returns_zero(self):
        assert await file_size_safe("") == 0


def test_default_mime_types_covers_known_asset_kinds():
    """Sanity check — the producers all use one of these keys."""
    expected = {
        "video_short", "video",
        "podcast", "audio",
        "featured_image", "inline_image", "image_featured", "image",
    }
    assert expected.issubset(set(_DEFAULT_MIME_TYPES.keys()))


def test_mime_map_has_no_video_long():
    """#1460: video_long is collapsed into video. The mime map must not carry
    the dropped type — new inserts only ever use 'video' / 'video_short'."""
    assert "video_long" not in _DEFAULT_MIME_TYPES
    assert _DEFAULT_MIME_TYPES["video"] == "video/mp4"
