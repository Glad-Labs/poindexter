"""
Unit tests for services/command_queue.py

Tests CommandQueue enqueue/dequeue lifecycle, status transitions,
retry logic, handler notification, statistics, and module-level
convenience helpers.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from services.command_queue import (
    Command,
    CommandQueue,
    CommandStatus,
    create_command,
    dispatch_compliance_check,
    dispatch_content_generation,
    dispatch_financial_analysis,
    get_command_queue,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def queue():
    """Fresh CommandQueue for each test (not the global singleton)."""
    return CommandQueue()


@pytest.fixture
def basic_command():
    return Command(agent_type="content", action="generate", payload={"topic": "AI"})


# ---------------------------------------------------------------------------
# Command dataclass
# ---------------------------------------------------------------------------


class TestCommand:
    def test_default_id_generated(self):
        c = Command()
        assert c.id and len(c.id) > 0

    def test_unique_ids(self):
        c1 = Command()
        c2 = Command()
        assert c1.id != c2.id

    def test_default_status_pending(self):
        c = Command()
        assert c.status == CommandStatus.PENDING

    def test_to_dict_includes_status_value(self):
        c = Command(agent_type="content", action="run")
        d = c.to_dict()
        assert d["status"] == "pending"
        assert d["agent_type"] == "content"
        assert d["action"] == "run"

    def test_to_dict_has_timestamps(self):
        c = Command()
        d = c.to_dict()
        assert "created_at" in d
        assert "updated_at" in d

    def test_default_retry_count_zero(self):
        c = Command()
        assert c.retry_count == 0
        assert c.max_retries == 3


# ---------------------------------------------------------------------------
# CommandQueue — enqueue / get_command
# ---------------------------------------------------------------------------


class TestCommandQueueEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_returns_id(self, queue, basic_command):
        cmd_id = await queue.enqueue(basic_command)
        assert cmd_id == basic_command.id

    @pytest.mark.asyncio
    async def test_enqueue_stores_command(self, queue, basic_command):
        await queue.enqueue(basic_command)
        retrieved = await queue.get_command(basic_command.id)
        assert retrieved is basic_command

    @pytest.mark.asyncio
    async def test_get_command_missing_returns_none(self, queue):
        assert await queue.get_command("no-such-id") is None

    @pytest.mark.asyncio
    async def test_enqueue_adds_to_internal_queue(self, queue, basic_command):
        await queue.enqueue(basic_command)
        assert queue.queue.qsize() == 1


# ---------------------------------------------------------------------------
# CommandQueue — dequeue
# ---------------------------------------------------------------------------


class TestCommandQueueDequeue:
    @pytest.mark.asyncio
    async def test_dequeue_returns_command_and_sets_processing(self, queue, basic_command):
        await queue.enqueue(basic_command)
        cmd = await queue.dequeue(timeout=1.0)
        assert cmd is basic_command
        assert cmd.status == CommandStatus.PROCESSING
        assert cmd.started_at is not None

    @pytest.mark.asyncio
    async def test_dequeue_timeout_returns_none(self, queue):
        result = await queue.dequeue(timeout=0.05)
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_fifo_order(self, queue):
        c1 = Command(agent_type="a", action="x")
        c2 = Command(agent_type="b", action="y")
        await queue.enqueue(c1)
        await queue.enqueue(c2)
        first = await queue.dequeue(timeout=1.0)
        second = await queue.dequeue(timeout=1.0)
        assert first.id == c1.id
        assert second.id == c2.id


# ---------------------------------------------------------------------------
# CommandQueue — complete / fail / cancel
# ---------------------------------------------------------------------------


class TestCommandQueueLifecycle:
    @pytest.mark.asyncio
    async def test_complete_command(self, queue, basic_command):
        await queue.enqueue(basic_command)
        result = await queue.complete_command(basic_command.id, {"output": "done"})
        assert result.status == CommandStatus.COMPLETED
        assert result.result == {"output": "done"}
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_missing_returns_none(self, queue):
        result = await queue.complete_command("no-id", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_fail_command_retries_below_max(self, queue, basic_command):
        await queue.enqueue(basic_command)
        result = await queue.fail_command(basic_command.id, "timeout", retry=True)
        assert result.status == CommandStatus.PENDING  # re-queued
        assert result.retry_count == 1

    @pytest.mark.asyncio
    async def test_fail_command_no_retry(self, queue, basic_command):
        await queue.enqueue(basic_command)
        result = await queue.fail_command(basic_command.id, "timeout", retry=False)
        assert result.status == CommandStatus.FAILED

    @pytest.mark.asyncio
    async def test_fail_command_exhausted_retries(self, queue):
        cmd = Command(agent_type="x", action="y", max_retries=1)
        cmd.retry_count = 1  # already at max
        await queue.enqueue(cmd)
        result = await queue.fail_command(cmd.id, "error", retry=True)
        assert result.status == CommandStatus.FAILED

    @pytest.mark.asyncio
    async def test_fail_missing_returns_none(self, queue):
        result = await queue.fail_command("no-id", "error")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_pending_command(self, queue, basic_command):
        await queue.enqueue(basic_command)
        result = await queue.cancel_command(basic_command.id)
        assert result.status == CommandStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_completed_leaves_status(self, queue, basic_command):
        await queue.enqueue(basic_command)
        await queue.complete_command(basic_command.id, {})
        result = await queue.cancel_command(basic_command.id)
        # Cannot cancel already-completed
        assert result.status == CommandStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel_missing_returns_none(self, queue):
        result = await queue.cancel_command("no-id")
        assert result is None


# ---------------------------------------------------------------------------
# CommandQueue — list_commands
# ---------------------------------------------------------------------------


class TestCommandQueueList:
    @pytest.mark.asyncio
    async def test_list_all(self, queue):
        c1 = Command(agent_type="a", action="x")
        c2 = Command(agent_type="b", action="y")
        await queue.enqueue(c1)
        await queue.enqueue(c2)
        result = await queue.list_commands()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, queue):
        c1 = Command(agent_type="a", action="x")
        c2 = Command(agent_type="b", action="y")
        await queue.enqueue(c1)
        await queue.enqueue(c2)
        await queue.complete_command(c1.id, {})
        pending = await queue.list_commands(status=CommandStatus.PENDING)
        assert all(c.status == CommandStatus.PENDING for c in pending)

    @pytest.mark.asyncio
    async def test_list_sorted_descending(self, queue):
        from datetime import timedelta

        c1 = Command(agent_type="a", action="x")
        c2 = Command(agent_type="b", action="y")
        # Manually set timestamps so sort order is deterministic
        c1.created_at = "2025-01-01T10:00:00+00:00"
        c2.created_at = "2025-01-01T11:00:00+00:00"
        await queue.enqueue(c1)
        await queue.enqueue(c2)
        result = await queue.list_commands()
        # Newest first (c2 has later created_at)
        assert result[0].id == c2.id


# ---------------------------------------------------------------------------
# CommandQueue — handlers
# ---------------------------------------------------------------------------


class TestCommandQueueHandlers:
    @pytest.mark.asyncio
    async def test_async_handler_called_on_complete(self, queue, basic_command):
        calls: list = []

        async def handler(cmd: Command):
            calls.append(cmd.id)

        queue.register_handler("content", handler)
        await queue.enqueue(basic_command)
        await queue.complete_command(basic_command.id, {})
        assert basic_command.id in calls

    @pytest.mark.asyncio
    async def test_sync_handler_called_on_complete(self, queue, basic_command):
        calls: list = []

        def handler(cmd: Command):
            calls.append(cmd.id)

        queue.register_handler("content", handler)
        await queue.enqueue(basic_command)
        await queue.complete_command(basic_command.id, {})
        assert basic_command.id in calls

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_propagate(self, queue, basic_command):
        async def bad_handler(cmd):
            raise RuntimeError("handler error")

        queue.register_handler("content", bad_handler)
        await queue.enqueue(basic_command)
        # Should not raise — handler exception caught internally
        await queue.complete_command(basic_command.id, {})
        # Command should still be in the store and marked completed
        assert basic_command.status == CommandStatus.COMPLETED

    def test_multiple_handlers_registered(self, queue):
        h1 = MagicMock()
        h2 = MagicMock()
        queue.register_handler("content", h1)
        queue.register_handler("content", h2)
        assert len(queue.handlers["content"]) == 2


# ---------------------------------------------------------------------------
# CommandQueue — stats and clear_old_commands
# ---------------------------------------------------------------------------


class TestCommandQueueStats:
    @pytest.mark.asyncio
    async def test_get_stats_empty(self, queue):
        stats = queue.get_stats()
        assert stats["total_commands"] == 0
        assert stats["pending_commands"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_commands(self, queue):
        cmd = Command(agent_type="a", action="x")
        await queue.enqueue(cmd)
        stats = queue.get_stats()
        assert stats["total_commands"] == 1
        assert "timestamp" in stats

    @pytest.mark.asyncio
    async def test_clear_old_commands_removes_old_completed(self, queue):
        cmd = Command(agent_type="a", action="x")
        await queue.enqueue(cmd)
        await queue.complete_command(cmd.id, {})
        # Force the updated_at to be very old
        from datetime import timedelta

        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        queue.commands[cmd.id].updated_at = old_time.isoformat()
        await queue.clear_old_commands(max_age_hours=24)
        assert cmd.id not in queue.commands

    @pytest.mark.asyncio
    async def test_clear_old_commands_keeps_recent(self, queue):
        cmd = Command(agent_type="a", action="x")
        await queue.enqueue(cmd)
        await queue.complete_command(cmd.id, {})
        # updated_at is recent (just now)
        await queue.clear_old_commands(max_age_hours=24)
        assert cmd.id in queue.commands


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleHelpers:
    @pytest.mark.asyncio
    async def test_create_command_returns_command(self):
        cmd = await create_command("financial", "analyze", {"period": "monthly"})
        assert isinstance(cmd, Command)
        assert cmd.agent_type == "financial"
        assert cmd.action == "analyze"
        assert cmd.payload == {"period": "monthly"}

    @pytest.mark.asyncio
    async def test_dispatch_content_generation(self):
        result = await dispatch_content_generation("AI trends", style="blog")
        assert "command_id" in result
        assert result["status"] == CommandStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_dispatch_financial_analysis(self):
        result = await dispatch_financial_analysis(period="weekly")
        assert "command_id" in result

    @pytest.mark.asyncio
    async def test_dispatch_compliance_check(self):
        result = await dispatch_compliance_check("content-123")
        assert "command_id" in result

    def test_get_command_queue_returns_singleton(self):
        q1 = get_command_queue()
        q2 = get_command_queue()
        assert q1 is q2
        assert isinstance(q1, CommandQueue)
