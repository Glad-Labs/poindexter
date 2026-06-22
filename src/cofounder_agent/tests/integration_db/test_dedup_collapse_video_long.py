"""#1460 video_long de-dup migration — real-DB tests (the logic is SQL).

Two contracts (the migration's one-time de-dup pass itself was folded into the
Phase F squash baseline and its file deleted, so the third test — which loaded
the migration module to replay the ``_DEDUP_*``/``_RELABEL`` statements against a
hand-seeded pre-migration state — was removed with it):

1. ``test_unique_guard_blocks_second_video_row`` — the partial unique index
   ``uniq_media_assets_post_video_type`` blocks a second video row per post.
2. ``test_record_media_asset_video_upsert_is_idempotent`` — the recorder's
   ON CONFLICT path (Task 5) resolves against that real index and refreshes the
   existing row instead of raising. (The unit test can only assert the SQL string
   contains ON CONFLICT; conflict inference can only be verified against PG.)

``schema_loaded`` applies the baseline at session start, so the unique index +
backup table already exist when these run.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _new_post(conn, slug: str):
    return await conn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at) "
        "VALUES (gen_random_uuid(), 'P', $1, 'b', 'published', NOW()) RETURNING id",
        slug,
    )


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
