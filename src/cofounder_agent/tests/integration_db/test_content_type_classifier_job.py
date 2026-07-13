"""Real-Postgres verification of ClassifyContentTypesJob.

The unit tests see a mocked pool; this exercises the actual round-trip against
the migrated schema — the _UNLABELED_SQL candidate query (incl. the empty-tags
LEFT JOIN) and the INSERT into post_content_types. The LLM is stubbed so only
the DB path is under test.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.classify_content_types import ClassifyContentTypesJob

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MODULE = "services.jobs.classify_content_types"


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg():
    return {
        "_site_config": _FakeSiteConfig(
            {
                "content_type_labels": "ai-ml,pc-hardware,gaming",
                "content_type_classifier_model": "test-model",
                "classify_content_types_enabled": "true",
            }
        ),
        "batch_size": 100,
    }


async def test_job_writes_labels(test_pool):
    pid = uuid.uuid4()
    async with test_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM posts WHERE slug = 'ct-classify-gpu-llm'")
            await conn.execute(
                "INSERT INTO posts (id, title, slug, status, content, published_at) "
                "VALUES ($1, 'GPU for LLMs', 'ct-classify-gpu-llm', 'published', 'body', now())",
                pid,
            )

    with patch(f"{_MODULE}.get_prompt_manager") as gpm, patch(
        f"{_MODULE}.ollama_chat_text",
        new=AsyncMock(
            return_value='{"labels":[{"label":"ai-ml","confidence":0.9},'
            '{"label":"pc-hardware","confidence":0.8}]}'
        ),
    ):
        gpm.return_value.get_prompt = MagicMock(return_value="PROMPT")
        result = await ClassifyContentTypesJob().run(test_pool, _cfg())

    assert result.ok and result.changes_made >= 1
    async with test_pool.acquire() as conn:
        got = {
            r["content_type"]
            for r in await conn.fetch(
                "SELECT content_type FROM post_content_types WHERE post_id = $1", pid
            )
        }
        # idempotency: the post is now labeled, so a second run must not re-fetch it
        rerun = await conn.fetchval(
            "SELECT count(*) FROM posts p WHERE p.id = $1 "
            "AND p.status = 'published' "
            "AND NOT EXISTS (SELECT 1 FROM post_content_types pct WHERE pct.post_id = p.id)",
            pid,
        )
        await conn.execute("DELETE FROM posts WHERE id = $1", pid)  # cascade cleans labels

    assert got == {"ai-ml", "pc-hardware"}
    assert rerun == 0
