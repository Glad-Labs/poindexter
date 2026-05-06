"""Unit tests for the topic-decision queue cap (#146 + #400).

Verifies that ``TopicDiscovery.queue_topics`` consults
``topic_discovery_max_pending`` before inserting candidates and skips
the insert loop when the awaiting-approval queue is full. The cap is
only enforced when the topic_decision gate is enabled — with the gate
off, the legacy auto-queue path runs unchanged.

Pre-#400 ``queue_topics`` ignored ``queue_at_capacity()`` entirely, so
the cap-skip case was triaged out in the #345 sweep with broken mocks.
This file rebuilds the case against the real INSERT path using the
async-context-manager pool stub from ``test_topic_propose``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig
from services.topic_discovery import (
    DiscoveredTopic,
    QueueTopicsResult,
    TopicDiscovery,
)


def _make_site_config(values: dict[str, str] | None = None) -> SiteConfig:
    return SiteConfig(initial_config=dict(values or {}))


def _make_topic(title: str = "Sample") -> DiscoveredTopic:
    return DiscoveredTopic(
        title=title,
        category="technology",
        source="hackernews",
        source_url="https://example.test",
    )


def _make_pool() -> Any:
    """Mock asyncpg pool — supports the connection-context-manager dance.

    ``queue_topics`` uses ``async with self.pool.acquire() as conn:`` then
    runs ``conn.transaction()`` + ``conn.execute(...)``. ``MagicMock``
    doesn't speak the async ctx-manager protocol out of the box; we wire
    it explicitly so the INSERT path actually runs and ``conn.execute``
    is an ``AsyncMock`` the test can assert on.
    """
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=0)

    class _TxnCtx:
        async def __aenter__(self_inner):
            return conn

        async def __aexit__(self_inner, *_):
            return False

    conn.transaction = lambda: _TxnCtx()

    class _AcquireCtx:
        async def __aenter__(self_inner):
            return conn

        async def __aexit__(self_inner, *_):
            return False

    pool = MagicMock()
    pool.acquire = lambda: _AcquireCtx()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool._conn = conn  # exposed for the test to assert on
    return pool


class TestQueueCap:
    @pytest.mark.asyncio
    async def test_skips_when_queue_at_capacity_with_gate_on(self):
        """gh#400 regression — at-capacity skip MUST be visible.

        Before #400, ``queue_topics`` never consulted
        ``queue_at_capacity`` and would happily insert past the
        operator-set ceiling. The fix returns a ``QueueTopicsResult``
        with ``skipped=True, reason='at_capacity'`` and runs zero
        INSERTs. Logs from this path are how the scheduler tells
        apart "no fresh topics" vs "operator throttled discovery".
        """
        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
            "topic_discovery_max_pending": "5",
        })
        pool = _make_pool()
        discovery = TopicDiscovery(pool, site_config=site_cfg)
        topics = [_make_topic(f"Topic {i}") for i in range(3)]

        async def _fake_at_cap(**kwargs):
            return True

        with patch(
            "services.topic_proposal_service.queue_at_capacity",
            AsyncMock(side_effect=_fake_at_cap),
        ):
            result = await discovery.queue_topics(topics)

        # Back-compat: still int-shaped, still 0 inserted.
        assert int(result) == 0
        # New visibility surface (gh#400).
        assert isinstance(result, QueueTopicsResult)
        assert result.skipped is True
        assert result.reason == "at_capacity"
        # No inserts ran — the cap check short-circuits before the
        # async-with-acquire block executes.
        assert pool._conn.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_proceeds_when_queue_under_capacity(self):
        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
            "topic_discovery_max_pending": "50",
        })
        pool = _make_pool()
        discovery = TopicDiscovery(pool, site_config=site_cfg)
        topics = [_make_topic("Topic alpha")]

        async def _fake_at_cap(**kwargs):
            return False

        with patch(
            "services.topic_proposal_service.queue_at_capacity",
            AsyncMock(side_effect=_fake_at_cap),
        ):
            result = await discovery.queue_topics(topics)

        # One topic should have been inserted via the
        # pipeline_tasks + pipeline_versions INSERT pair.
        assert int(result) == 1
        assert result.skipped is False
        assert result.reason is None
        # Two INSERTs per topic (pipeline_tasks + pipeline_versions).
        assert pool._conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_cap_skipped_when_gate_disabled(self):
        # Gate off → cap doesn't apply (legacy auto-queue stays uncapped).
        site_cfg = _make_site_config({
            "topic_discovery_max_pending": "1",
        })
        pool = _make_pool()
        discovery = TopicDiscovery(pool, site_config=site_cfg)
        topics = [_make_topic("First"), _make_topic("Second")]

        # queue_at_capacity returns False when the gate is off (via the
        # is_gate_enabled short-circuit). Patch it explicitly so the
        # test doesn't depend on the helper's internals being correct.
        async def _fake_at_cap(**kwargs):
            return False

        with patch(
            "services.topic_proposal_service.queue_at_capacity",
            AsyncMock(side_effect=_fake_at_cap),
        ):
            result = await discovery.queue_topics(topics)
        assert int(result) == 2
        assert result.skipped is False

    @pytest.mark.asyncio
    async def test_cap_helper_failure_falls_through(self):
        # If queue_at_capacity itself raises, queue_topics logs WARNING
        # and falls through rather than dropping every candidate. Better
        # to over-queue once during a transient outage than silently
        # block the auto-discovery loop.
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        pool = _make_pool()
        discovery = TopicDiscovery(pool, site_config=site_cfg)
        topics = [_make_topic("Resilient")]

        with patch(
            "services.topic_proposal_service.queue_at_capacity",
            AsyncMock(side_effect=RuntimeError("DB outage")),
        ):
            result = await discovery.queue_topics(topics)
        # Insert ran despite the helper crashing.
        assert int(result) == 1
        assert result.skipped is False


class TestPendingTopicCount:
    @pytest.mark.asyncio
    async def test_returns_neg_one_on_db_error(self):
        from services.topic_proposal_service import pending_topic_count

        # Pool's acquire raises — helper logs and returns -1.
        class _BoomPool:
            def acquire(self_inner):
                raise RuntimeError("connection refused")

        out = await pending_topic_count(pool=_BoomPool())
        assert out == -1
