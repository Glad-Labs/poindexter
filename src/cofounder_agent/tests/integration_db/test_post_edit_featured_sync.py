"""Real-Postgres check of PostEditService's featured-image ``posts`` sync.

The unit tests drive a fake pool, so they see the SQL but never run it. This
runs ``_sync_post_featured`` against the migrated ``posts`` table. The UPDATE
must reach a staged (``approved`` / ``scheduled``) row as well as a live one,
move ``cover_image_url`` with ``featured_image_url``, and report each row's
status through RETURNING, which decides whether the static export is rebuilt
(poindexter#1103).

No ``site_config`` is wired, so a live row reports "run the rebuild manually"
rather than reaching object storage.
"""

from __future__ import annotations

import json
import uuid

import pytest

from poindexter.modules.content.post_edit_service import PostEditService

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

OLD = "https://cdn.example/pipeline-hero.webp"
NEW = "https://cdn.example/operator-pick.webp"


async def _post(conn, *, status: str) -> tuple[str, uuid.UUID]:
    """Insert a post as publish would for a fresh task. Returns (task_id, post id)."""
    task_id = str(uuid.uuid4())
    slug = f"featured-sync-{task_id[:8]}"
    post_id = await conn.fetchval(
        "INSERT INTO posts (id, title, slug, status, content, featured_image_url, "
        "cover_image_url, metadata) "
        "VALUES ($1, $2, $3, $4, 'body', $5, $5, $6::jsonb) RETURNING id",
        uuid.uuid4(), slug, slug, status, OLD,
        json.dumps({"pipeline_task_id": task_id}),
    )
    return task_id, post_id


async def _images(conn, post_id: uuid.UUID) -> tuple[str | None, str | None]:
    row = await conn.fetchrow(
        "SELECT featured_image_url, cover_image_url FROM posts WHERE id = $1", post_id,
    )
    return row["featured_image_url"], row["cover_image_url"]


@pytest.mark.parametrize("status", ["approved", "scheduled"])
async def test_a_staged_post_takes_the_new_image(test_txn, status):
    task_id, post_id = await _post(test_txn, status=status)

    warnings = await PostEditService(pool=test_txn)._sync_post_featured(task_id, NEW)

    assert await _images(test_txn, post_id) == (NEW, NEW)
    assert warnings == [f"staged post updated ({status}) — it goes live with this image"]


async def test_a_live_post_takes_the_new_image_and_asks_for_a_rebuild(test_txn):
    task_id, post_id = await _post(test_txn, status="published")

    warnings = await PostEditService(pool=test_txn)._sync_post_featured(task_id, NEW)

    assert await _images(test_txn, post_id) == (NEW, NEW)
    assert any("rebuild_static_export" in w for w in warnings), warnings


async def test_removing_a_live_hero_clears_the_cover_too(test_txn):
    """The static export serves ``featured_image_url or cover_image_url``, so a
    cover left behind would put the removed image straight back on the site."""
    task_id, post_id = await _post(test_txn, status="published")

    await PostEditService(pool=test_txn)._sync_post_featured(task_id, None)

    assert await _images(test_txn, post_id) == (None, None)


async def test_a_task_without_a_post_touches_nothing(test_txn):
    _, other_post = await _post(test_txn, status="published")

    warnings = await PostEditService(pool=test_txn)._sync_post_featured(
        str(uuid.uuid4()), NEW,
    )

    assert warnings == []
    assert await _images(test_txn, other_post) == (OLD, OLD)
