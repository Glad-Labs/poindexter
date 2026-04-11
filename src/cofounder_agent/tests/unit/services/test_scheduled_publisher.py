"""
Tests for scheduled_publisher — background loop that publishes posts
whose scheduled publication time has arrived.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.scheduled_publisher import run_scheduled_publisher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(rows=None):
    """Build a mock asyncpg pool that returns *rows* from conn.fetch()."""
    rows = rows if rows is not None else []
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)

    # asyncpg's pool.acquire() returns an async context manager directly
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire.return_value = acm

    return pool, conn


async def _run_one_iteration(get_pool):
    """Run the publisher, let it complete one iteration, then cancel.

    The function internally catches CancelledError and breaks, so
    the task finishes normally (no CancelledError propagates).
    """
    task = asyncio.create_task(run_scheduled_publisher(get_pool))
    await asyncio.sleep(0.05)
    task.cancel()
    # run_scheduled_publisher catches CancelledError and returns cleanly
    await task


# ---------------------------------------------------------------------------
# Scheduled post detection
# ---------------------------------------------------------------------------


class TestScheduledPostDetection:
    """Posts with status='scheduled' and published_at <= NOW() are found."""

    @pytest.mark.asyncio
    async def test_publishes_due_posts(self):
        """When the query returns rows, their status is updated to 'published'."""
        rows = [
            {"id": 1, "title": "First Post"},
            {"id": 2, "title": "Second Post"},
        ]
        pool, conn = _make_pool(rows)
        get_pool = AsyncMock(return_value=pool)

        await _run_one_iteration(get_pool)

        # The UPDATE query was executed
        conn.fetch.assert_awaited_once()
        sql = conn.fetch.call_args[0][0]
        assert "UPDATE posts" in sql
        assert "status = 'published'" in sql
        assert "status = 'scheduled'" in sql
        assert "published_at <= NOW()" in sql

    @pytest.mark.asyncio
    async def test_no_posts_due(self):
        """When no posts match, the loop still runs without error."""
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        await _run_one_iteration(get_pool)

        conn.fetch.assert_awaited_once()


# ---------------------------------------------------------------------------
# Publishing trigger logic
# ---------------------------------------------------------------------------


class TestPublishingTrigger:
    """The loop runs immediately on start, then every 60 seconds."""

    @pytest.mark.asyncio
    async def test_first_iteration_runs_without_sleep(self):
        """First check fires without waiting 60 seconds."""
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        iteration_count = 0

        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            nonlocal iteration_count
            if seconds == 60:
                iteration_count += 1
                # After seeing the sleep, cancel to stop the loop
                raise asyncio.CancelledError()
            # Allow other sleeps (like the test's 0.05s) through
            await original_sleep(seconds)

        with patch("services.scheduled_publisher.asyncio.sleep", side_effect=mock_sleep):
            task = asyncio.create_task(run_scheduled_publisher(get_pool))
            await task

        # fetch was called once before the first sleep(60) attempt
        conn.fetch.assert_awaited_once()
        # sleep(60) was attempted exactly once (after the first iteration)
        assert iteration_count == 1

    @pytest.mark.asyncio
    async def test_sleeps_60_seconds_between_iterations(self):
        """After the first run, the loop sleeps 60s before the next check."""
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        sleep_values = []

        async def mock_sleep(seconds):
            sleep_values.append(seconds)
            # Let one sleep through, then cancel on the second
            if len(sleep_values) >= 1:
                raise asyncio.CancelledError()

        with patch("services.scheduled_publisher.asyncio.sleep", side_effect=mock_sleep):
            task = asyncio.create_task(run_scheduled_publisher(get_pool))
            await task

        assert 60 in sleep_values


# ---------------------------------------------------------------------------
# Pool unavailable
# ---------------------------------------------------------------------------


class TestPoolUnavailable:
    """When get_pool() returns None, the loop continues gracefully."""

    @pytest.mark.asyncio
    async def test_none_pool_skips_iteration(self):
        """If the pool is None the loop continues without crashing."""
        call_count = 0

        async def get_pool():
            nonlocal call_count
            call_count += 1
            return None

        await _run_one_iteration(get_pool)

        assert call_count >= 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Errors inside the loop are logged but do not crash the service."""

    @pytest.mark.asyncio
    async def test_db_error_is_caught_and_logged(self):
        """A database exception is caught; the loop keeps running."""
        pool, conn = _make_pool([])
        conn.fetch.side_effect = Exception("connection lost")
        get_pool = AsyncMock(return_value=pool)

        with patch("services.scheduled_publisher.logger") as mock_logger:
            await _run_one_iteration(get_pool)

            mock_logger.error.assert_called()
            error_args = mock_logger.error.call_args[0]
            assert "Error" in error_args[0]

    @pytest.mark.asyncio
    async def test_get_pool_exception_is_caught(self):
        """If get_pool() itself raises, the loop survives."""
        get_pool = AsyncMock(side_effect=RuntimeError("pool init failed"))

        with patch("services.scheduled_publisher.logger") as mock_logger:
            await _run_one_iteration(get_pool)

            mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_cancelled_error_shuts_down_cleanly(self):
        """CancelledError breaks the loop and logs shutdown."""
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        with patch("services.scheduled_publisher.logger") as mock_logger:
            await _run_one_iteration(get_pool)

            # Verify shutdown was logged
            shutdown_logged = any(
                "Shutting down" in str(call)
                for call in mock_logger.info.call_args_list
            )
            assert shutdown_logged

    @pytest.mark.asyncio
    async def test_loop_continues_after_error(self):
        """After an error, the next iteration still runs."""
        pool, conn = _make_pool([])
        call_count = 0

        async def fetch_with_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient failure")
            return []

        conn.fetch.side_effect = fetch_with_error
        get_pool = AsyncMock(return_value=pool)

        sleep_count = 0

        async def mock_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("services.scheduled_publisher.asyncio.sleep", side_effect=mock_sleep):
            task = asyncio.create_task(run_scheduled_publisher(get_pool))
            await task

        # fetch was called at least twice (first errored, second succeeded)
        assert call_count >= 2


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    """Published posts are logged individually."""

    @pytest.mark.asyncio
    async def test_logs_each_published_post(self):
        rows = [
            {"id": 42, "title": "GPU Benchmarks"},
            {"id": 99, "title": "AI News Roundup"},
        ]
        pool, _conn = _make_pool(rows)
        get_pool = AsyncMock(return_value=pool)

        with patch("services.scheduled_publisher.logger") as mock_logger:
            await _run_one_iteration(get_pool)

            # Should have info logs for startup + each published post
            info_calls = mock_logger.info.call_args_list
            published_calls = [
                c for c in info_calls if "Published scheduled post" in str(c)
            ]
            assert len(published_calls) == 2

    @pytest.mark.asyncio
    async def test_logs_startup(self):
        pool, _conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        with patch("services.scheduled_publisher.logger") as mock_logger:
            await _run_one_iteration(get_pool)

            startup_logged = any(
                "Started" in str(call)
                for call in mock_logger.info.call_args_list
            )
            assert startup_logged

    @pytest.mark.asyncio
    async def test_logs_post_title_and_id(self):
        rows = [{"id": 7, "title": "Test Title"}]
        pool, _conn = _make_pool(rows)
        get_pool = AsyncMock(return_value=pool)

        with patch("services.scheduled_publisher.logger") as mock_logger:
            await _run_one_iteration(get_pool)

            published_calls = [
                c for c in mock_logger.info.call_args_list
                if "Published scheduled post" in str(c)
            ]
            assert len(published_calls) == 1
            call_args = published_calls[0][0]
            assert call_args[1] == "Test Title"
            assert call_args[2] == 7


# ---------------------------------------------------------------------------
# SQL correctness
# ---------------------------------------------------------------------------


class TestSQLCorrectness:
    """The UPDATE query uses the correct WHERE clause and RETURNING."""

    @pytest.mark.asyncio
    async def test_query_returns_id_and_title(self):
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        await _run_one_iteration(get_pool)

        sql = conn.fetch.call_args[0][0]
        assert "RETURNING id, title" in sql

    @pytest.mark.asyncio
    async def test_query_sets_updated_at(self):
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        await _run_one_iteration(get_pool)

        sql = conn.fetch.call_args[0][0]
        assert "updated_at = NOW()" in sql

    @pytest.mark.asyncio
    async def test_query_filters_scheduled_status(self):
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        await _run_one_iteration(get_pool)

        sql = conn.fetch.call_args[0][0]
        assert "status = 'scheduled'" in sql

    @pytest.mark.asyncio
    async def test_query_checks_published_at_time(self):
        pool, conn = _make_pool([])
        get_pool = AsyncMock(return_value=pool)

        await _run_one_iteration(get_pool)

        sql = conn.fetch.call_args[0][0]
        assert "published_at <= NOW()" in sql
