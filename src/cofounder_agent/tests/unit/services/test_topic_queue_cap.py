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

    @pytest.mark.asyncio
    async def test_returns_zero_when_pool_none(self):
        # The propose CLI may be invoked before the pool fixture is
        # wired (e.g. ``poindexter topics propose --dry-run``). The
        # helper must not crash on ``pool=None`` — returning 0 keeps
        # the cap-check inert so the dry-run path always proceeds.
        from services.topic_proposal_service import pending_topic_count

        assert await pending_topic_count(pool=None) == 0

    @pytest.mark.asyncio
    async def test_returns_int_from_fetchval(self):
        # Happy path: fetchval returns a numeric scalar — helper
        # coerces to int. Guards against fetchval being mocked to
        # return None (CTE returned no rows) → must coerce to 0.
        from services.topic_proposal_service import pending_topic_count

        pool = _make_pool()
        pool._conn.fetchval = AsyncMock(return_value=7)
        assert await pending_topic_count(pool=pool) == 7

        pool._conn.fetchval = AsyncMock(return_value=None)
        assert await pending_topic_count(pool=pool) == 0


class TestResolveMaxPending:
    """resolve_max_pending must handle three site_config shapes:

    1. ``None`` — falls back to DEFAULT_MAX_PENDING.
    2. Real ``SiteConfig`` — routes through ``get_int`` (preferred path,
       silently coerces malformed values to the default).
    3. Dict-only stub (legacy tests / fakes) — routes through ``get`` +
       int() with a try/except fallback.
    """

    def test_returns_default_when_site_config_none(self):
        from services.topic_proposal_service import (
            DEFAULT_MAX_PENDING,
            resolve_max_pending,
        )

        assert resolve_max_pending(None) == DEFAULT_MAX_PENDING

    def test_uses_get_int_when_available(self):
        # Real SiteConfig — the documented preferred path. Seeded
        # value must round-trip back through resolve_max_pending.
        from services.topic_proposal_service import resolve_max_pending

        site_cfg = _make_site_config({"topic_discovery_max_pending": "12"})
        assert resolve_max_pending(site_cfg) == 12

    def test_falls_back_when_get_int_raises(self):
        # Defensive path: if a custom SiteConfig-like stub crashes
        # inside get_int (e.g. a Mock that hasn't been wired), we
        # must still return DEFAULT_MAX_PENDING and emit a warning
        # rather than propagating the exception to the propose CLI.
        from services.topic_proposal_service import (
            DEFAULT_MAX_PENDING,
            resolve_max_pending,
        )

        class _BrokenCfg:
            def get_int(self, *_a, **_k):
                raise RuntimeError("settings reload in progress")

        assert resolve_max_pending(_BrokenCfg()) == DEFAULT_MAX_PENDING

    def test_falls_back_when_dict_stub_value_not_int(self):
        # The dict-only stub path: site_config has no ``get_int``
        # method, so resolve_max_pending hits ``site_config.get(...)``
        # and int()'s the raw value. A garbage string must not crash
        # — falls through to the default with a logged warning.
        from services.topic_proposal_service import (
            DEFAULT_MAX_PENDING,
            resolve_max_pending,
        )

        class _DictStub:
            def __init__(self, raw):
                self._raw = raw

            def get(self, _key, default=None):
                return self._raw

        assert resolve_max_pending(_DictStub("not-a-number")) == DEFAULT_MAX_PENDING


class TestQueueAtCapacityHelper:
    """Direct coverage for the queue_at_capacity helper. Existing
    tests exercise it indirectly via ``queue_topics`` — these pin its
    own contract so refactors of either side stay safe."""

    @pytest.mark.asyncio
    async def test_returns_false_when_gate_disabled(self):
        # Gate off ⇒ helper short-circuits to False BEFORE touching
        # the pool. We hand it a pool that would raise if acquire()
        # were called, to prove the short-circuit really fires.
        from services.topic_proposal_service import queue_at_capacity

        class _PoisonPool:
            def acquire(self_inner):
                raise AssertionError(
                    "queue_at_capacity must not touch the pool when "
                    "the gate is off"
                )

        site_cfg = _make_site_config({})  # gate flag absent → off
        assert (
            await queue_at_capacity(pool=_PoisonPool(), site_config=site_cfg)
        ) is False

    @pytest.mark.asyncio
    async def test_treats_db_outage_as_at_capacity(self):
        # pending_topic_count returns -1 on a DB outage; the helper
        # must treat that as "at capacity" so a transient outage
        # doesn't unleash a flood of proposals once it recovers.
        from services.topic_proposal_service import queue_at_capacity

        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
            "topic_discovery_max_pending": "5",
        })

        class _BoomPool:
            def acquire(self_inner):
                raise RuntimeError("connection refused")

        assert (
            await queue_at_capacity(pool=_BoomPool(), site_config=site_cfg)
        ) is True


class TestProposeTopicValidation:
    """propose_topic must bail loudly on bad inputs rather than
    inserting a malformed row. These cover the precondition checks
    at the top of the function — pure validation, no DB."""

    @pytest.mark.asyncio
    async def test_empty_topic_raises_value_error(self):
        from services.topic_proposal_service import propose_topic

        with pytest.raises(ValueError, match="non-empty"):
            await propose_topic(
                topic="",
                site_config=_make_site_config(),
                pool=_make_pool(),
                notify=False,
            )

    @pytest.mark.asyncio
    async def test_whitespace_only_topic_raises_value_error(self):
        # Hand-typed CLI input can include trailing whitespace; the
        # ``.strip()`` check ensures "   \t\n" is treated the same
        # as the empty string.
        from services.topic_proposal_service import propose_topic

        with pytest.raises(ValueError, match="non-empty"):
            await propose_topic(
                topic="   \t\n",
                site_config=_make_site_config(),
                pool=_make_pool(),
                notify=False,
            )

    @pytest.mark.asyncio
    async def test_none_pool_raises_runtime_error(self):
        # ``pool=None`` is a programmer error — the function must
        # surface it as RuntimeError so the CLI prints a clean
        # "pool not initialised" rather than crashing inside the
        # async-with-acquire block with a less-readable AttributeError.
        from services.topic_proposal_service import propose_topic

        with pytest.raises(RuntimeError, match="pool is required"):
            await propose_topic(
                topic="A real topic",
                site_config=_make_site_config(),
                pool=None,
                notify=False,
            )


class TestProposeTopicGateOff:
    """When the topic_decision gate is OFF the manual propose path
    skips pause_at_gate entirely — the row lands at status='pending'
    just like an anticipation_engine auto-proposal."""

    @pytest.mark.asyncio
    async def test_returns_pending_status_when_gate_off(self):
        from services.topic_proposal_service import propose_topic

        site_cfg = _make_site_config({})  # gate flag absent → off
        pool = _make_pool()

        # pause_at_gate must NOT be called when the gate is off; patch
        # it to a sentinel that explodes if accidentally invoked.
        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(side_effect=AssertionError(
                "pause_at_gate must not be called when gate is off"
            )),
        ):
            result = await propose_topic(
                topic="Async event loops in Python",
                site_config=site_cfg,
                pool=pool,
                notify=False,
            )

        assert result["ok"] is True
        assert result["gate_enabled"] is False
        assert result["awaiting_gate"] is None
        assert result["status"] == "pending"
        assert result["queue_full"] is False
        # Two INSERTs per topic (pipeline_tasks + pipeline_versions).
        assert pool._conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_primary_keyword_falls_back_to_first_tag(self):
        # Caller omitted primary_keyword — resolver must use the
        # first non-empty tag. Whitespace-only tags get filtered
        # before the fallback runs.
        from services.topic_proposal_service import propose_topic

        pool = _make_pool()

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ):
            result = await propose_topic(
                topic="GPU thermals on the 5090",
                tags=["   ", "vrm-design", "thermal-paste"],
                site_config=_make_site_config({}),
                pool=pool,
                notify=False,
            )

        assert result["ok"] is True
        # Inspect the pipeline_versions INSERT — positional args are
        # (sql, task_id, metadata_json, now). The JSON payload at
        # args[2] records the resolved primary_keyword downstream
        # stages consume.
        version_call = pool._conn.execute.call_args_list[1]
        metadata_json = version_call.args[2]
        assert '"primary_keyword": "vrm-design"' in metadata_json
        # Empty/whitespace tags are filtered out of the persisted list.
        assert '"   "' not in metadata_json
