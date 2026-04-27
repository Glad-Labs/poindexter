"""Unit tests for the topic-decision queue cap (#146).

Verifies that ``TopicDiscovery.queue_topics`` consults
``topic_discovery_max_pending`` before inserting candidates and skips
the propose call when the awaiting-approval queue is full. The cap is
only enforced when the topic_decision gate is enabled — with the gate
off, the legacy auto-queue path runs unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig
from services.topic_discovery import DiscoveredTopic, TopicDiscovery


def _make_site_config(values: dict[str, str] | None = None) -> SiteConfig:
    return SiteConfig(initial_config=dict(values or {}))


def _make_topic(title: str = "Sample") -> DiscoveredTopic:
    return DiscoveredTopic(
        title=title,
        category="technology",
        source="hackernews",
        source_url="https://example.test",
    )


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    return pool


class TestQueueCap:
    @pytest.mark.asyncio
    async def test_skips_when_queue_at_capacity_with_gate_on(self):
        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
            "topic_discovery_max_pending": "5",
        })
        pool = _make_pool()
        discovery = TopicDiscovery(pool, site_config=site_cfg)
        topics = [_make_topic(f"Topic {i}") for i in range(3)]

        # queue_at_capacity reads via pending_topic_count → fetchval
        async def _fake_at_cap(**kwargs):
            return True

        with patch(
            "services.topic_proposal_service.queue_at_capacity",
            AsyncMock(side_effect=_fake_at_cap),
        ):
            queued = await discovery.queue_topics(topics)

        assert queued == 0
        # No inserts ran — the cap check short-circuits.
        assert pool.execute.await_count == 0

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
            queued = await discovery.queue_topics(topics)

        # One topic should have been inserted into content_tasks (via
        # the existing INSERT path inside queue_topics).
        assert queued == 1
        assert pool.execute.await_count >= 1

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
            queued = await discovery.queue_topics(topics)
        assert queued == 2

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
            queued = await discovery.queue_topics(topics)
        # Insert ran despite the helper crashing.
        assert queued == 1


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
