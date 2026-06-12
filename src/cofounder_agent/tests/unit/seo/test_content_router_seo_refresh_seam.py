"""Contract test for the seo_refresh entry seam in content_router_service.

``_load_task_metadata`` surfaces per-task hydration metadata (post_id,
seo_opportunity_id, target_query, seo_refresh_scope) from
``pipeline_versions.stage_data -> 'task_metadata'`` so the seo_refresh graph's
entry atom (``content.load_existing_post``) can read ``post_id`` from the
initial state. It returns ``{}`` for ordinary generation tasks, which carry
none of these keys, so the canonical_blog path is unaffected.

Issue: Glad-Labs/poindexter#763 (SEO Harvest Loop Phase 2).
"""

import json

import pytest

from services import content_router_service as crs


class _Conn:
    def __init__(self, value):
        self._value = value

    async def fetchval(self, sql, *args):
        return self._value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, value):
        self._conn = _Conn(value)

    def acquire(self):
        return self._conn


class _DB:
    def __init__(self, value):
        self.pool = _Pool(value)


@pytest.mark.asyncio
async def test_surfaces_seo_refresh_keys_from_dict_stage_data():
    stage_data = {
        "task_metadata": {
            "post_id": "abc",
            "seo_opportunity_id": "opp1",
            "target_query": "best gpu",
            "seo_refresh_scope": "meta_only",
            "unrelated": "ignored",
        }
    }
    out = await crs._load_task_metadata(_DB(stage_data), "task-1")
    assert out == {
        "post_id": "abc",
        "seo_opportunity_id": "opp1",
        "target_query": "best gpu",
        "seo_refresh_scope": "meta_only",
    }


@pytest.mark.asyncio
async def test_handles_jsonb_returned_as_str():
    # asyncpg returns a JSONB column as a str unless a codec is registered.
    raw = json.dumps({"task_metadata": {"post_id": "xyz"}})
    out = await crs._load_task_metadata(_DB(raw), "task-2")
    assert out == {"post_id": "xyz"}


@pytest.mark.asyncio
async def test_empty_for_canonical_blog_task():
    # No task_metadata (a normal generation task) → {} so the canonical_blog
    # path is completely unaffected.
    assert await crs._load_task_metadata(_DB(None), "task-3") == {}
    assert await crs._load_task_metadata(_DB({"task_metadata": {}}), "task-4") == {}
