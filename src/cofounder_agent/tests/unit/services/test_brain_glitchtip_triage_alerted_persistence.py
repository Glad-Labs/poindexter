"""Alerted-issue dedupe survives a brain restart (poindexter#1048).

Every brain restart used to reset the in-memory set, so a still-open noisy
issue re-paged once per deploy (TerminationSignal: five pages in three days,
2026-09-12). The set is now mirrored into ``brain_knowledge``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import glitchtip_triage_probe as gt


def _pool(rows=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.execute = AsyncMock()
    return pool


@pytest.fixture(autouse=True)
def _reset_state():
    gt._alerted_ids.clear()
    gt._alerted_loaded = False
    yield
    gt._alerted_ids.clear()
    gt._alerted_loaded = False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_restored_from_brain_knowledge_once_per_process():
    pool = _pool(rows=[{"attribute": "alerted:953"}, {"attribute": "alerted:1193"}])
    await gt._load_alerted_ids(pool)
    await gt._load_alerted_ids(pool)  # second call is a no-op
    assert gt._alerted_ids == {"953", "1193"}
    assert pool.fetch.await_count == 1
    sql = pool.fetch.await_args.args[0]
    assert "brain_knowledge" in sql and "expires_at" in sql
    assert pool.fetch.await_args.args[1] == gt.ALERTED_ENTITY


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_and_forget_write_the_brain_knowledge_row():
    pool = _pool()
    await gt._persist_alerted(pool, "953", "TerminationSignal: 15")
    sql, *args = pool.execute.await_args.args
    assert "INSERT INTO brain_knowledge" in sql and "ON CONFLICT (entity, attribute)" in sql
    assert args[:2] == [gt.ALERTED_ENTITY, "alerted:953"] and args[3] == gt.ALERTED_TTL_DAYS
    await gt._forget_alerted(pool, "953")
    sql, *args = pool.execute.await_args.args
    assert sql.startswith("DELETE FROM brain_knowledge") and args == [gt.ALERTED_ENTITY, "alerted:953"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_store_failures_never_break_the_cycle():
    pool = _pool()
    pool.fetch = AsyncMock(side_effect=RuntimeError("db away"))
    pool.execute = AsyncMock(side_effect=RuntimeError("db away"))
    await gt._load_alerted_ids(pool)   # logs, marks loaded, keeps a cold set
    await gt._persist_alerted(pool, "1", "t")
    await gt._forget_alerted(pool, "1")
    assert gt._alerted_loaded is True and gt._alerted_ids == set()
