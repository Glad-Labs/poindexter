"""#1460 video_long de-dup migration — real-DB tests (the logic is SQL).

Three contracts:

1. ``test_dedup_keeps_youtube_then_pipeline_then_newest`` — per post, one
   video-family survivor by smart priority (has a platform video id > source
   ``pipeline`` > newest), surviving ``video_long`` relabeled to ``video``,
   losers archived to ``media_assets_dedup_backup``.
2. ``test_unique_guard_blocks_second_video_row`` — the partial unique index
   ``uniq_media_assets_post_video_type`` blocks a second video row per post.
3. ``test_record_media_asset_video_upsert_is_idempotent`` — the recorder's
   ON CONFLICT path (Task 5) resolves against that real index and refreshes the
   existing row instead of raising. (The unit test can only assert the SQL string
   contains ON CONFLICT; conflict inference can only be verified against PG.)

``schema_loaded`` runs the migration at session start, so the unique index +
backup table already exist when these run. Test 1 drops the index inside its
rolled-back ``test_txn`` to recreate the pre-migration state where duplicates
can be inserted; tests 2/3 rely on the index being present.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_INDEX = "uniq_media_assets_post_video_type"


def _mig():
    """Resolve the timestamped migration module by suffix (not pinned to HHMMSS)."""
    import services.migrations as m

    name = next(
        n
        for _, n, _ in pkgutil.iter_modules(m.__path__)
        if n.endswith("_dedup_and_collapse_video_long")
    )
    return importlib.import_module(f"services.migrations.{name}")


async def _new_post(conn, slug: str):
    return await conn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at) "
        "VALUES (gen_random_uuid(), 'P', $1, 'b', 'published', NOW()) RETURNING id",
        slug,
    )


async def test_dedup_keeps_youtube_then_pipeline_then_newest(test_txn) -> None:
    mig = _mig()
    # Recreate the pre-migration state: drop the guard so duplicate video rows
    # can be inserted (reverted on the test_txn rollback).
    await test_txn.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    post_id = await _new_post(test_txn, "dedup-mig-1460")
    # platform_video_ids is NOT NULL DEFAULT '{}' — use '{}'::jsonb for "no id",
    # never NULL. The video_long pipeline row carries the YouTube id.
    await test_txn.execute(
        """
        INSERT INTO media_assets
            (post_id, type, source, storage_provider, storage_path, platform_video_ids, created_at)
        VALUES
          ($1, 'video_long',  'pipeline',       'local', '/p/yt.mp4',    '{"youtube":"yt"}'::jsonb, NOW() - interval '2 day'),
          ($1, 'video',       'reconciliation', 'local', '/p/recon.mp4', '{}'::jsonb,               NOW()),
          ($1, 'video_short', 'pipeline',       'local', '/p/s1.mp4',    '{}'::jsonb,               NOW() - interval '1 day'),
          ($1, 'video_short', 'pipeline',       'local', '/p/s2.mp4',    '{}'::jsonb,               NOW())
        """,
        post_id,
    )
    # Run the migration's de-dup statements (NOT the index rebuild) on this
    # rolled-back connection.
    await test_txn.execute(mig._DEDUP_BACKUP_DDL)
    await test_txn.execute(mig._DEDUP_LOSERS_TEMP)
    await test_txn.execute(mig._DEDUP_ARCHIVE)
    await test_txn.execute(mig._DEDUP_DELETE)
    await test_txn.execute(mig._RELABEL)

    rows = await test_txn.fetch(
        "SELECT type, storage_path FROM media_assets WHERE post_id=$1 ORDER BY type",
        post_id,
    )
    kinds = sorted(r["type"] for r in rows)
    assert kinds == ["video", "video_short"]  # one survivor per family
    long_row = next(r for r in rows if r["type"] == "video")
    assert long_row["storage_path"] == "/p/yt.mp4"  # YouTube-id row won + relabeled
    short_row = next(r for r in rows if r["type"] == "video_short")
    assert short_row["storage_path"] == "/p/s2.mp4"  # newest short won

    archived = await test_txn.fetchval(
        "SELECT count(*) FROM media_assets_dedup_backup WHERE post_id=$1", post_id
    )
    assert archived == 2  # the two losers were backed up before deletion


async def test_unique_guard_blocks_second_video_row(test_txn) -> None:
    import asyncpg

    post_id = await _new_post(test_txn, "uniq-mig-1460")
    await test_txn.execute(
        "INSERT INTO media_assets (post_id, type, source, storage_provider) "
        "VALUES ($1, 'video', 'pipeline', 'local')",
        post_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await test_txn.execute(
            "INSERT INTO media_assets (post_id, type, source, storage_provider) "
            "VALUES ($1, 'video', 'reconciliation', 'local')",
            post_id,
        )


async def test_record_media_asset_video_upsert_is_idempotent(test_txn) -> None:
    """A second record_media_asset for the same (post, 'video') UPDATEs the row
    (same id, refreshed attrs) instead of raising under the unique guard — i.e.
    the ON CONFLICT predicate really matches uniq_media_assets_post_video_type."""
    from services.media_asset_recorder import record_media_asset

    class _TxnPool:
        """Adapt the rolled-back test_txn connection to pool.acquire()."""

        def __init__(self, conn) -> None:
            self._conn = conn

        def acquire(self):
            conn = self._conn

            class _Ctx:
                async def __aenter__(self):
                    return conn

                async def __aexit__(self, *_a):
                    return False

            return _Ctx()

    post_id = await _new_post(test_txn, "upsert-mig-1460")
    pool = _TxnPool(test_txn)

    id1 = await record_media_asset(
        pool=pool, post_id=post_id, asset_type="video",
        storage_path="/a.mp4", file_size_bytes=10, width=1920, height=1080,
    )
    id2 = await record_media_asset(
        pool=pool, post_id=post_id, asset_type="video",
        storage_path="/b.mp4", file_size_bytes=20, width=1280, height=720,
    )

    assert id1 and id2 and id1 == id2  # upsert returned the same row id
    rows = await test_txn.fetch(
        "SELECT storage_path, file_size_bytes FROM media_assets "
        "WHERE post_id=$1 AND type='video'",
        post_id,
    )
    assert len(rows) == 1  # one row, not two
    assert rows[0]["storage_path"] == "/b.mp4"  # DO UPDATE refreshed the attrs
    assert rows[0]["file_size_bytes"] == 20
