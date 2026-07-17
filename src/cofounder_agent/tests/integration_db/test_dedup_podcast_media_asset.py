"""#884 podcast media_assets de-dup + unique guard — real-DB tests (the logic is SQL).

Three contracts, mirroring the #1460 video de-dup tests
(``test_dedup_collapse_video_long``):

1. ``test_unique_guard_blocks_second_podcast_row`` — the partial unique index
   ``uniq_media_assets_post_podcast_type`` blocks a second post-keyed podcast row.
2. ``test_record_media_asset_podcast_upsert_is_idempotent`` — the recorder's
   ON CONFLICT path resolves against that real index and refreshes the existing
   row instead of raising. (A unit test can only assert the SQL string contains
   ON CONFLICT; conflict inference can only be verified against PG.)
3. ``test_migration_dedups_existing_podcast_rows_and_backs_up_losers`` — the
   migration's one-time de-dup pass keeps the newest row per post, backs the
   losers up to ``media_assets_dedup_backup``, and leaves the post index-clean.

``schema_loaded`` runs every migration (incl. the #884 one) against the fresh
test DB at session start, so the unique index + backup table already exist when
these run.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATION_FILE = (
    "20260717_154103_dedup_podcast_media_assets_and_add_unique_index.py"
)


class _TxnPool:
    """Adapt the rolled-back ``test_txn`` connection to a ``pool.acquire()`` seam
    so migration ``up(pool)`` / ``record_media_asset(pool=...)`` run against it."""

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


def _load_migration():
    """Import the timestamp-prefixed migration module by path (its filename is
    not a valid Python identifier, so a normal import can't reach it)."""
    for p in Path(__file__).resolve().parents:
        cand = (
            p / "src" / "cofounder_agent" / "services" / "migrations"
            / _MIGRATION_FILE
        )
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("_dedup_podcast_mig", cand)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    raise FileNotFoundError(f"could not locate migration {_MIGRATION_FILE}")


async def _new_post(conn, slug: str):
    return await conn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at) "
        "VALUES (gen_random_uuid(), 'P', $1, 'b', 'published', NOW()) RETURNING id",
        slug,
    )


async def test_unique_guard_blocks_second_podcast_row(test_txn) -> None:
    import asyncpg

    post_id = await _new_post(test_txn, "uniq-podcast-884")
    await test_txn.execute(
        "INSERT INTO media_assets (post_id, type, source, storage_provider) "
        "VALUES ($1, 'podcast', 'pipeline', 'local')",
        post_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await test_txn.execute(
            "INSERT INTO media_assets (post_id, type, source, storage_provider) "
            "VALUES ($1, 'podcast', 'reconciliation', 'local')",
            post_id,
        )


async def test_record_media_asset_podcast_upsert_is_idempotent(test_txn) -> None:
    """A second record_media_asset for the same (post, 'podcast') UPDATEs the row
    (same id, refreshed attrs) instead of raising under the unique guard — i.e.
    the ON CONFLICT predicate really matches uniq_media_assets_post_podcast_type.
    This is the regen path (PodcastService.generate_episode force=True)."""
    from services.media_asset_recorder import record_media_asset

    post_id = await _new_post(test_txn, "upsert-podcast-884")
    pool = _TxnPool(test_txn)

    id1 = await record_media_asset(
        pool=pool, post_id=post_id, asset_type="podcast",
        storage_path="/a.mp3", file_size_bytes=10, duration_ms=1000,
    )
    id2 = await record_media_asset(
        pool=pool, post_id=post_id, asset_type="podcast",
        storage_path="/b.mp3", file_size_bytes=20, duration_ms=2000,
    )

    assert id1 and id2 and id1 == id2  # upsert returned the same row id
    rows = await test_txn.fetch(
        "SELECT storage_path, file_size_bytes, duration_ms FROM media_assets "
        "WHERE post_id=$1 AND type='podcast'",
        post_id,
    )
    assert len(rows) == 1  # one row, not two
    assert rows[0]["storage_path"] == "/b.mp3"  # DO UPDATE refreshed the attrs
    assert rows[0]["file_size_bytes"] == 20
    assert rows[0]["duration_ms"] == 2000


async def test_migration_dedups_existing_podcast_rows_and_backs_up_losers(
    test_txn,
) -> None:
    """The migration keeps the newest podcast row per post, backs up the losers,
    and leaves the post with exactly one in-scope podcast row.

    The index is dropped first so pre-migration duplicates can be seeded (they
    could never coexist once the guard exists); up() recreates it."""
    mig = _load_migration()

    # Drop the guard (created by schema_loaded) so we can seed the pre-migration
    # duplicate state this migration exists to clean up. Transaction-local — the
    # session index is restored at test rollback.
    await test_txn.execute("DROP INDEX IF EXISTS uniq_media_assets_post_podcast_type")

    post_id = await _new_post(test_txn, "dedup-podcast-884")
    seeded_ids = []
    # Distinct created_at so "newest wins" is unambiguous. Bind a real datetime —
    # asyncpg's binary protocol rejects a str for a timestamptz param (the SQL
    # cast can't rescue it; the value is encoded before the cast runs).
    for i, day in enumerate((1, 2, 3)):
        ts = _dt.datetime(2026, 1, day, tzinfo=_dt.timezone.utc)
        row_id = await test_txn.fetchval(
            "INSERT INTO media_assets (post_id, type, source, storage_provider, "
            "storage_path, created_at) "
            "VALUES ($1, 'podcast', 'pipeline', 'local', $2, $3) "
            "RETURNING id",
            post_id, f"/ep{i}.mp3", ts,
        )
        seeded_ids.append(row_id)

    # A task-keyed row (post_id NULL) must be left untouched — it's out of scope.
    task_keyed_id = await test_txn.fetchval(
        "INSERT INTO media_assets (task_id, type, source, storage_provider, "
        "storage_path) VALUES ('t-884', 'podcast', 'pipeline', 'local', '/tk.mp3') "
        "RETURNING id",
    )

    await mig.up(_TxnPool(test_txn))

    rows = await test_txn.fetch(
        "SELECT storage_path FROM media_assets WHERE post_id=$1 AND type='podcast'",
        post_id,
    )
    assert len(rows) == 1  # de-duplicated to the single newest render
    assert rows[0]["storage_path"] == "/ep2.mp3"  # 2026-01-03 is newest

    # The two losers were backed up (recoverable), the keeper was not.
    backup = await test_txn.fetch(
        "SELECT id FROM media_assets_dedup_backup WHERE id = ANY($1::uuid[])",
        seeded_ids,
    )
    assert len(backup) == 2

    # The out-of-scope task-keyed row is untouched.
    tk = await test_txn.fetchval(
        "SELECT storage_path FROM media_assets WHERE id=$1", task_keyed_id,
    )
    assert tk == "/tk.mp3"
