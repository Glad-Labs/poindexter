"""Real-Postgres verification of the CrosspostToDevtoJob content-type gate.

The candidate query is the correctness core — the unit tests only see the SQL
string (mocked pool). This exercises it against the migrated schema: only
posts whose content-type is on the allowlist AND that score >= the floor are
selected, and a post with no ``post_content_types`` row is fail-closed out.
HTTP is stubbed; only *selection* is asserted (which post ids the job asked to
cross-post).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from services.jobs.crosspost_to_devto import CrosspostToDevtoJob

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg():
    return {
        "_site_config": _FakeSiteConfig(
            {
                "devto_syndicate_content_types": "ai-ml",
                "devto_syndicate_min_quality": "80",
            }
        ),
        "batch_size": 50,
    }


async def _seed_post(conn, *, slug, content_type, score) -> str:
    """Insert a published post + task/version (for quality) + optional
    content-type row. ``content_type=None`` seeds no label (fail-closed case).
    Returns post id."""
    task_id = f"task-{slug}"
    await conn.execute(
        "INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage, niche_slug) "
        "VALUES ($1, 'blog_post', $2, 'published', 'done', 'glad-labs')",
        task_id, slug,
    )
    await conn.execute(
        "INSERT INTO pipeline_versions (task_id, version, title, quality_score) "
        "VALUES ($1, 1, $2, $3)",
        task_id, slug, score,
    )
    meta = f'{{"pipeline_task_id": "{task_id}"}}'
    pid = await conn.fetchval(
        "INSERT INTO posts (id, title, slug, status, content, published_at, metadata) "
        "VALUES ($1, $2, $3, 'published', 'body', now(), $4::jsonb) RETURNING id",
        uuid.uuid4(), slug, slug, meta,
    )
    if content_type:
        await conn.execute(
            "INSERT INTO post_content_types (post_id, content_type, source) "
            "VALUES ($1, $2, 'test')",
            pid, content_type,
        )
    return str(pid)


async def _reset(conn):
    # Deleting posts cascades their post_content_types rows.
    await conn.execute("DELETE FROM posts WHERE slug LIKE 'gate-%'")
    await conn.execute("DELETE FROM pipeline_versions WHERE task_id LIKE 'task-gate-%'")
    await conn.execute("DELETE FROM pipeline_tasks WHERE task_id LIKE 'task-gate-%'")


async def test_gate_selects_only_allowlisted_high_quality(test_pool):
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await _reset(conn)
            keep = await _seed_post(conn, slug="gate-keep", content_type="ai-ml", score=90)
            lowscore = await _seed_post(
                conn, slug="gate-lowscore", content_type="ai-ml", score=60
            )
            offtype = await _seed_post(
                conn, slug="gate-offtype", content_type="gaming", score=95
            )
            notype = await _seed_post(
                conn, slug="gate-notype", content_type=None, score=95
            )

    asked: list[str] = []
    svc = AsyncMock()
    svc._get_api_key = AsyncMock(return_value="dt_key")

    async def _cp(post_id):
        asked.append(post_id)
        return f"https://dev.to/g/{post_id}"

    svc.cross_post_by_post_id = AsyncMock(side_effect=_cp)

    with patch("services.devto_service.DevToCrossPostService", return_value=svc):
        result = await CrosspostToDevtoJob().run(test_pool, _cfg())

    assert result.ok is True
    # Only the allowlisted-type, high-quality post is selected.
    assert keep in asked
    assert lowscore not in asked      # below the 80 floor
    assert offtype not in asked       # content-type not in allowlist
    assert notype not in asked        # no post_content_types row → fail-closed

    async with test_pool.acquire() as conn:
        await _reset(conn)
