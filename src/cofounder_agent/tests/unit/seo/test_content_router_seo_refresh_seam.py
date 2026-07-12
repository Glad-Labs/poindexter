"""Contract test for the seo_refresh entry seam in content_router_service.

``_load_task_metadata`` surfaces per-task hydration metadata from
``pipeline_versions.stage_data -> 'task_metadata'`` so the seo_refresh graph's
entry atom (``content.load_existing_post``) can read ``post_id`` (and the
downstream republish atom ``seo_opportunity_id`` / ``seo_refresh_scope``) from
the initial state. As of the image_rebuild fix the flattened keys are derived
from the active graph's atom contracts (``_graph_hydration_keys``) rather than a
hardcoded per-template allowlist — so this test drives a seo_refresh-shaped
graph and asserts the four keys still come through, and that produced channels
and unrelated keys do not.

Issue: Glad-Labs/poindexter#763 (SEO Harvest Loop Phase 2).
"""

import json
from types import SimpleNamespace

import pytest

from services import content_router_service as crs


def _meta(*, requires=(), produces=(), inputs=()):
    return SimpleNamespace(
        requires=tuple(requires),
        produces=tuple(produces),
        inputs=tuple(SimpleNamespace(name=n) for n in inputs),
    )


# A faithful-enough seo_refresh shape: the entry atom loads the post from
# post_id/target_query/seo_opportunity_id; the republish atom consumes
# seo_refresh_scope. All four are produced by no atom → all four are hydration
# inputs. content/seo_title are produced, so they are not.
_SEO_REFRESH_GRAPH = {
    "entry": "load",
    "nodes": [
        {"id": "load", "atom": "content.load_existing_post"},
        {"id": "republish", "atom": "content.republish"},
    ],
    "edges": [
        {"from": "load", "to": "republish"},
        {"from": "republish", "to": "END"},
    ],
}
_SEO_REFRESH_METAS = {
    "content.load_existing_post": _meta(
        requires=("post_id",),
        inputs=("post_id", "target_query", "seo_opportunity_id"),
        produces=("content", "seo_title"),
    ),
    "content.republish": _meta(
        requires=("seo_opportunity_id", "seo_refresh_scope"),
        inputs=("seo_opportunity_id", "seo_refresh_scope", "content"),
        produces=("post_id",),
    ),
}


def _resolver(mapping):
    def get_meta(name):
        return mapping.get(name)

    return get_meta


def _loader(graph):
    async def load(pool, slug):
        return graph

    return load


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


async def _load(db):
    return await crs._load_task_metadata(
        db, "task-x", "seo_refresh",
        graph_loader=_loader(_SEO_REFRESH_GRAPH),
        get_meta=_resolver(_SEO_REFRESH_METAS),
    )


@pytest.mark.asyncio
async def test_surfaces_seo_refresh_keys_from_dict_stage_data():
    stage_data = {
        "task_metadata": {
            "post_id": "abc",
            "seo_opportunity_id": "opp1",
            "target_query": "best gpu",
            "seo_refresh_scope": "meta_only",
            "content": "PRIOR BODY should NOT flatten (it is produced in-graph)",
            "unrelated": "ignored",
        }
    }
    out = await _load(_DB(stage_data))
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
    out = await _load(_DB(raw))
    assert out == {"post_id": "xyz"}


@pytest.mark.asyncio
async def test_empty_for_canonical_blog_task():
    # No task_metadata (a normal generation task) → {} so the canonical_blog
    # path is completely unaffected.
    assert await _load(_DB(None)) == {}
    assert await _load(_DB({"task_metadata": {}})) == {}
