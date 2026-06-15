"""Unit tests for services/topic_pool.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.topic_source import DiscoveredTopic
from services.topic_pool import dedup_key, insert_pooled_topics


def test_dedup_key_normalizes_title():
    # case-insensitive + whitespace-collapsed so trivial variants collide
    assert dedup_key("  Local  LLM   Inference ") == dedup_key("local llm inference")
    assert dedup_key("A") != dedup_key("B")


def _conn_returning(ids):
    """Conn whose fetchval pops successive return values (id or None)."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=list(ids))
    return conn


@pytest.mark.asyncio
async def test_insert_counts_only_new_rows():
    # First insert returns an id (new), second returns None (ON CONFLICT no-op).
    conn = _conn_returning(["11111111-1111-1111-1111-111111111111", None])
    topics = [
        DiscoveredTopic(title="One", category="tech", source="web_search",
                        source_url="https://x/1", relevance_score=2.0, description="d1"),
        DiscoveredTopic(title="Two", category="tech", source="web_search"),
    ]
    n = await insert_pooled_topics(
        conn, niche_id="22222222-2222-2222-2222-222222222222",
        source="web_search", topics=topics,
    )
    assert n == 1
    assert conn.fetchval.await_count == 2
    # First positional after SQL is niche_id; title is mapped from DiscoveredTopic.
    first = conn.fetchval.await_args_list[0]
    assert "INSERT INTO topic_pool" in first.args[0]
    assert "ON CONFLICT (niche_id, dedup_key) DO NOTHING" in first.args[0]


@pytest.mark.asyncio
async def test_insert_rejects_unknown_table():
    conn = _conn_returning([])
    with pytest.raises(ValueError):
        await insert_pooled_topics(
            conn, niche_id="x", source="web_search", topics=[], table="pipeline_tasks",
        )
