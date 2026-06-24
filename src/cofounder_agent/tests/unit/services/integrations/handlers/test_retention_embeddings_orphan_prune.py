"""Tests for the embeddings_orphan_prune retention handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_pool(execute_result: str = "DELETE 3"):
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_result)
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.mark.asyncio
async def test_posts_orphan_deletes_and_returns_count():
    """posts handler runs a LEFT JOIN DELETE and returns deleted count."""
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, conn = _make_pool("DELETE 7")
    row = {
        "name": "embeddings.orphan_prune.posts",
        "config": {"source_table": "posts", "batch_size": 500},
    }

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 7
    assert result["source_table"] == "posts"
    assert result["batch_size"] == 500
    sql_called = conn.execute.call_args[0][0]
    assert "posts" in sql_called.lower()
    assert "left join posts" in sql_called.lower()


@pytest.mark.asyncio
async def test_audit_handler_joins_audit_log():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, conn = _make_pool("DELETE 2")
    row = {"name": "embeddings.orphan_prune.audit", "config": {"source_table": "audit"}}

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 2
    sql_called = conn.execute.call_args[0][0]
    assert "audit_log" in sql_called.lower()


@pytest.mark.asyncio
async def test_brain_handler_uses_compound_key():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, conn = _make_pool("DELETE 0")
    row = {"name": "embeddings.orphan_prune.brain", "config": {"source_table": "brain"}}

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 0
    sql_called = conn.execute.call_args[0][0]
    assert "brain_decisions" in sql_called.lower()
    assert "split_part" in sql_called.lower()


@pytest.mark.asyncio
async def test_unknown_source_raises():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, _ = _make_pool()
    row = {"name": "embeddings.orphan_prune.memory", "config": {"source_table": "memory"}}

    with pytest.raises(ValueError, match="no handler for source_table"):
        await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)


@pytest.mark.asyncio
async def test_missing_source_table_raises():
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
    )

    pool, _ = _make_pool()
    row = {"name": "embeddings.orphan_prune.posts", "config": {}}

    with pytest.raises((ValueError, KeyError)):
        await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)


@pytest.mark.asyncio
async def test_default_batch_size_used_when_not_specified():
    """config without batch_size uses the default of 1000."""
    from services.integrations.handlers.retention_embeddings_orphan_prune import (
        embeddings_orphan_prune,
        _DEFAULT_BATCH_SIZE,
    )

    pool, conn = _make_pool("DELETE 0")
    row = {"name": "embeddings.orphan_prune.posts", "config": {"source_table": "posts"}}

    result = await embeddings_orphan_prune(None, site_config=None, row=row, pool=pool)

    assert result["batch_size"] == _DEFAULT_BATCH_SIZE
    # Verify the batch_size was passed to the SQL
    sql_args = conn.execute.call_args[0]
    assert _DEFAULT_BATCH_SIZE in sql_args
