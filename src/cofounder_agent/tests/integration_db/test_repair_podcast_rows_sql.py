"""scripts/repair_podcast_rows_to_delivered.py's SQL against the REAL schema.

The repair swaps rows between ``media_assets`` and ``media_assets_dedup_backup``
with explicit column lists and guarded writes (Glad-Labs/poindexter#1090). A fake
connection can only check the order of statements; these run them on a seeded,
rolled-back transaction.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _load():
    root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "repair_podcast_rows_to_delivered.py").is_file()
    )
    spec = spec_from_file_location(
        "repair_podcast_rows_to_delivered_idb", root / "scripts" / "repair_podcast_rows_to_delivered.py",
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REPAIR = _load()
_URL = "https://cdn.example/podcast/v2/p.mp3"


async def _post(conn, slug: str):
    return await conn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at) "
        "VALUES (gen_random_uuid(), 'P', $1, 'b', 'published', NOW()) RETURNING id",
        slug,
    )


async def _approved(conn, post_id) -> None:
    await conn.execute(
        "INSERT INTO media_approvals (post_id, medium, status, dispatched_at) "
        "VALUES ($1, 'podcast', 'approved', NOW())",
        post_id,
    )


async def _keeper(conn, post_id, *, size=900, duration_ms=90_000):
    return await conn.fetchval(
        "INSERT INTO media_assets (post_id, task_id, type, source, storage_path, "
        "file_size_bytes, duration_ms, metadata) "
        "VALUES ($1, 'task-rerender', 'podcast', 'pipeline', '/gone/rerender.mp3', $2, $3, "
        "'{\"audio_qa\": {\"ok\": true}}'::jsonb) RETURNING id::text",
        post_id, size, duration_ms,
    )


async def _backup(conn, post_id, *, size=None, duration_ms=None, source="reconciliation"):
    return await conn.fetchval(
        "INSERT INTO media_assets_dedup_backup (id, post_id, type, source, url, "
        "file_size_bytes, duration_ms, platform_video_ids) "
        "VALUES (gen_random_uuid(), $1, 'podcast', $2, $3, $4, $5, '{}'::jsonb) RETURNING id::text",
        post_id, source, _URL, size, duration_ms,
    )


async def test_candidates_and_backups_select_on_real_schema(test_txn) -> None:
    post_id = await _post(test_txn, "repair-1090-select")
    await _approved(test_txn, post_id)
    keeper = await _keeper(test_txn, post_id)
    stub = await _backup(test_txn, post_id)

    rows = [r for r in await test_txn.fetch(REPAIR.CANDIDATES_SQL) if r["post_id"] == str(post_id)]
    assert [(r["keeper_id"], r["keeper_size"]) for r in rows] == [(keeper, 900)]
    backups = await test_txn.fetch(REPAIR.BACKUPS_SQL, str(post_id))
    assert [b["id"] for b in backups] == [stub]


async def test_restore_swaps_the_delivered_row_back(test_txn) -> None:
    post_id = await _post(test_txn, "repair-1090-restore")
    await _approved(test_txn, post_id)
    keeper = await _keeper(test_txn, post_id)
    stub = await _backup(test_txn, post_id)
    plan = REPAIR.Plan(str(post_id), "restore", keeper, 500, stub, needs_duration=True)

    await REPAIR.apply_plan(test_txn, plan, url=_URL, duration_ms=187_080)

    live = await test_txn.fetch(
        "SELECT id::text AS id, url, file_size_bytes, duration_ms, metadata->>'poindexter_1090' AS note "
        "FROM media_assets WHERE post_id = $1 AND type = 'podcast'",
        post_id,
    )
    assert len(live) == 1
    assert (live[0]["id"], live[0]["url"], live[0]["file_size_bytes"], live[0]["duration_ms"]) == (
        stub, _URL, 500, 187_080,
    )
    assert keeper in live[0]["note"]
    backed_up = await test_txn.fetch(
        "SELECT id::text AS id, file_size_bytes, task_id FROM media_assets_dedup_backup WHERE post_id = $1",
        post_id,
    )
    # The re-render is kept, not deleted; the restored row left the backup table.
    assert [(b["id"], b["file_size_bytes"], b["task_id"]) for b in backed_up] == [
        (keeper, 900, "task-rerender"),
    ]


async def test_restore_keeps_a_pipeline_rows_own_size_and_duration(test_txn) -> None:
    post_id = await _post(test_txn, "repair-1090-exact")
    await _approved(test_txn, post_id)
    keeper = await _keeper(test_txn, post_id)
    exact = await _backup(test_txn, post_id, size=500, duration_ms=60_000, source="pipeline")
    plan = REPAIR.Plan(str(post_id), "restore", keeper, 500, exact)

    await REPAIR.apply_plan(test_txn, plan, url=_URL, duration_ms=None)

    row = await test_txn.fetchrow(
        "SELECT file_size_bytes, duration_ms FROM media_assets WHERE post_id = $1", post_id,
    )
    assert (row["file_size_bytes"], row["duration_ms"]) == (500, 60_000)


async def test_stamp_and_describe(test_txn) -> None:
    stamped = await _post(test_txn, "repair-1090-stamp")
    keeper = await _keeper(test_txn, stamped)
    await REPAIR.apply_plan(test_txn, REPAIR.Plan(str(stamped), "stamp", keeper, 900), url=_URL, duration_ms=None)
    row = await test_txn.fetchrow(
        "SELECT url, storage_provider, file_size_bytes FROM media_assets WHERE id = $1::uuid", keeper,
    )
    assert (row["url"], row["storage_provider"], row["file_size_bytes"]) == (_URL, "cloudflare_r2", 900)

    described = await _post(test_txn, "repair-1090-describe")
    keeper = await _keeper(test_txn, described, size=900, duration_ms=90_000)
    await REPAIR.apply_plan(
        test_txn, REPAIR.Plan(str(described), "describe", keeper, 500), url=_URL, duration_ms=187_080,
    )
    row = await test_txn.fetchrow(
        "SELECT url, file_size_bytes, duration_ms, metadata->>'poindexter_1090' AS note "
        "FROM media_assets WHERE id = $1::uuid",
        keeper,
    )
    assert (row["url"], row["file_size_bytes"], row["duration_ms"]) == (_URL, 500, 187_080)
    assert '"previous_file_size_bytes": 900' in row["note"]
    assert '"previous_duration_ms": 90000' in row["note"]


async def test_a_row_that_gained_a_url_is_not_touched(test_txn) -> None:
    post_id = await _post(test_txn, "repair-1090-raced")
    keeper = await _keeper(test_txn, post_id)
    await test_txn.execute("UPDATE media_assets SET url = 'https://elsewhere' WHERE id = $1::uuid", keeper)
    with pytest.raises(RuntimeError, match="stamp"):
        await REPAIR.apply_plan(test_txn, REPAIR.Plan(str(post_id), "stamp", keeper, 900), url=_URL, duration_ms=None)
