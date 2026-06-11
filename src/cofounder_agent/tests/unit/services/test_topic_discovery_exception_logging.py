"""
Unit tests for exception-logging behaviour in services/topic_discovery.py.

Verifies that exceptions returned from asyncio.gather(*web_tasks,
return_exceptions=True) in TopicDiscovery.discover() are logged as warnings
with the source name included, rather than silently discarded (#725).
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from services.topic_discovery import DiscoveredTopic, TopicDiscovery


def _make_pool():
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[
        [],  # published_titles
        [],  # pending_topics
    ])
    pool.execute = AsyncMock()
    return pool


_GOOD_TOPIC = DiscoveredTopic(
    title="Building Local LLM Inference Pipelines with Ollama",
    category="technology",
    source="hackernews",
    source_url="https://example.com",
    relevance_score=5.0,
)


class TestTopicDiscoveryGatherExceptionLogging:
    """asyncio.gather exceptions in discover() produce a WARNING log including
    the source name, and do not crash the overall discover() call."""

    @pytest.mark.asyncio
    async def test_single_source_exception_logs_warning_with_name(self, caplog):
        """When one web-source coroutine raises, the WARNING includes the source name."""
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value={"hackernews", "devto"})
        d._discover_from_knowledge = AsyncMock(return_value=[])
        d._discover_from_codebase = AsyncMock(return_value=[])

        # gather returns [good_list, Exception] — hackernews succeeds, devto fails
        gather_return = [[_GOOD_TOPIC], RuntimeError("devto API timeout")]

        async def _fake_gather(*coros, **kwargs):
            for c in coros:
                c.close()  # prevent "coroutine never awaited" warnings
            return gather_return

        with (
            patch("services.topic_discovery.asyncio.gather", side_effect=_fake_gather),
            caplog.at_level(logging.WARNING, logger="services.topic_discovery"),
        ):
            results = await d.discover(max_topics=10)

        # The successful topic from hackernews makes it through
        assert any(t.title == _GOOD_TOPIC.title for t in results)

        # The failed source was logged as a warning with the source name
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "[TOPIC_DISCOVERY]" in msg and "devto" in msg
            for msg in warning_messages
        ), f"Expected [TOPIC_DISCOVERY] warning mentioning 'devto', got: {warning_messages}"

    @pytest.mark.asyncio
    async def test_all_sources_exception_returns_empty_and_logs_each(self, caplog):
        """When ALL web-source coroutines fail, discover() returns [] and logs each failure."""
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value={"hackernews", "devto"})
        d._discover_from_knowledge = AsyncMock(return_value=[])
        d._discover_from_codebase = AsyncMock(return_value=[])

        gather_return = [
            ConnectionError("hackernews unreachable"),
            TimeoutError("devto timed out"),
        ]

        async def _fake_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            return gather_return

        with (
            patch("services.topic_discovery.asyncio.gather", side_effect=_fake_gather),
            caplog.at_level(logging.WARNING, logger="services.topic_discovery"),
        ):
            results = await d.discover(max_topics=10)

        assert isinstance(results, list)

        # Both source failures should be logged
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        source_warnings = [
            msg for msg in warning_messages if "[TOPIC_DISCOVERY]" in msg
        ]
        assert len(source_warnings) >= 2, (
            f"Expected at least 2 [TOPIC_DISCOVERY] warnings (one per failed source), "
            f"got {len(source_warnings)}: {source_warnings}"
        )

    @pytest.mark.asyncio
    async def test_no_exception_no_warning(self, caplog):
        """When all web sources succeed, no source-failure warning is logged."""
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value={"hackernews"})
        d._discover_from_knowledge = AsyncMock(return_value=[])
        d._discover_from_codebase = AsyncMock(return_value=[])

        gather_return = [[_GOOD_TOPIC]]

        async def _fake_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            return gather_return

        with (
            patch("services.topic_discovery.asyncio.gather", side_effect=_fake_gather),
            caplog.at_level(logging.WARNING, logger="services.topic_discovery"),
        ):
            results = await d.discover(max_topics=10)

        assert len(results) == 1

        source_failure_warnings = [
            r.message for r in caplog.records
            if r.levelname == "WARNING" and "failed" in r.message.lower()
        ]
        assert source_failure_warnings == [], (
            f"Unexpected source-failure warnings: {source_failure_warnings}"
        )

    @pytest.mark.asyncio
    async def test_exception_includes_source_name_in_log(self, caplog):
        """Each WARNING for a failed source includes that source's name."""
        pool = _make_pool()
        d = TopicDiscovery(pool)
        d._get_enabled_sources = AsyncMock(return_value={"hackernews", "devto", "web_search"})
        d._discover_from_knowledge = AsyncMock(return_value=[])
        d._discover_from_codebase = AsyncMock(return_value=[])

        # hackernews succeeds, the other two fail
        gather_return = [
            [_GOOD_TOPIC],
            OSError("devto 503"),
            ValueError("web_search parse error"),
        ]

        async def _fake_gather(*coros, **kwargs):
            for c in coros:
                c.close()
            return gather_return

        with (
            patch("services.topic_discovery.asyncio.gather", side_effect=_fake_gather),
            caplog.at_level(logging.WARNING, logger="services.topic_discovery"),
        ):
            results = await d.discover(max_topics=10)

        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]

        # devto and web_search failures must each appear in a warning
        assert any("devto" in msg for msg in warning_messages), (
            f"Expected warning mentioning 'devto', got: {warning_messages}"
        )
        assert any("web_search" in msg for msg in warning_messages), (
            f"Expected warning mentioning 'web_search', got: {warning_messages}"
        )
