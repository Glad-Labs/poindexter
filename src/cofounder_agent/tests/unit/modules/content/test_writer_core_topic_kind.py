"""``writer_core._read_topic_kind`` — the [SCREENSHOT:] gate's batch-lineage input.

The kind is read from ``topic_batches`` through ``pipeline_tasks.topic_batch_id``
at draft time rather than stamped into ``stage_data.metadata`` (which the
writer's upsert rewrites wholesale — ``source`` / ``discovered_by`` / ``angle``
are all gone from prod version-1 rows). Only the two real values pass; anything
else — NULL, a half-resolved batch, a test double — reads as "no lineage" so the
keyword half of the gate is the only thing that can qualify the post.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.writer_core import GenerateContentStage


def _db(fetchval):
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    db = MagicMock()
    db.pool = pool
    return db, conn


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["internal", "external"])
async def test_reads_the_batch_kind(kind):
    db, conn = _db(lambda *a: kind)
    out = await GenerateContentStage()._read_topic_kind(db, "task-1")
    assert out == kind
    sql = conn.fetchval.await_args.args[0]
    assert "topic_batches" in sql and "topic_batch_id" in sql
    assert conn.fetchval.await_args.args[1] == "task-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "weird", MagicMock()])
async def test_anything_but_the_two_real_values_is_none(value):
    db, _ = _db(lambda *a: value)
    assert await GenerateContentStage()._read_topic_kind(db, "t") is None


@pytest.mark.asyncio
async def test_no_pool_is_none():
    db = MagicMock()
    db.pool = None
    assert await GenerateContentStage()._read_topic_kind(db, "t") is None


@pytest.mark.asyncio
async def test_read_error_fails_open_to_none():
    def _boom(*a):
        raise RuntimeError("db down")

    db, _ = _db(_boom)
    assert await GenerateContentStage()._read_topic_kind(db, "t") is None


@pytest.mark.asyncio
async def test_two_pass_draft_node_threads_topic_kind_into_the_draft_call(monkeypatch):
    """state["topic_kind"] → generate_with_context(topic_kind=…)."""
    from modules.content.atoms import two_pass_writer as tp
    from poindexter.services.site_config import SiteConfig

    seen = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **kw):
        seen["topic_kind"] = kw.get("topic_kind")
        return "A clean first draft with no markers."

    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False,
    )
    thread = "two_pass-test-topic-kind"
    tp._SITE_CONFIG_REGISTRY[thread] = SiteConfig(initial_config={})
    tp._POOL_REGISTRY[thread] = None
    try:
        await tp._draft_node({
            "topic": "T", "angle": "A", "snippets": [], "pool_thread": thread,
            "topic_kind": "internal", "task_id": "t1",
        })
    finally:
        tp._SITE_CONFIG_REGISTRY.pop(thread, None)
        tp._POOL_REGISTRY.pop(thread, None)
    assert seen.get("topic_kind") == "internal"


@pytest.mark.asyncio
async def test_two_pass_run_seeds_topic_kind_into_state():
    """run(topic_kind=…) lands on the declared state channel — an undeclared
    channel is how the auto-publish gate starved for six weeks."""
    from modules.content.atoms import two_pass_writer as tp

    assert "topic_kind" in tp._State.__annotations__
