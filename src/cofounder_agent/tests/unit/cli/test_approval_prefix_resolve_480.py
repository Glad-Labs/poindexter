"""Regression tests for Glad-Labs/poindexter#480.

``poindexter approve`` and ``poindexter reject`` (the gate-based CLI
commands) historically did exact-match on the ``task_id`` argument.
The Grafana awaiting-approval panel and ``poindexter tasks list``
surface ``LEFT(task_id::text, 8)`` so operators read short prefixes
and paste them back into the CLI — and the gate-based commands then
return "Task X not found" because the service layer does exact-match.

Plus: the gate-based commands don't apply to non-gated awaiting_approval
tasks (the common case for canonical_blog batches). Operators saw
"not found" or "not paused at any gate" with no guidance on what to
do next.

Fix in glad-labs-stack: ``poindexter/cli/approval.py`` adds
``_resolve_task_id_prefix(pool, prefix)`` that expands an 8-char
prefix to its full UUID via ``WHERE task_id::text LIKE prefix || '%'``,
plus catches ``TaskNotPausedError`` and rewrites the message to point
operators at ``poindexter tasks approve`` instead.

These tests pin both behaviours so the next refactor can't silently
sever the UX glue.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.approval import (
    _AmbiguousPrefixError,
    _format_not_paused_hint,
    _resolve_task_id_prefix,
    approve_command,
    reject_command,
)


# ---------------------------------------------------------------------------
# _resolve_task_id_prefix — the helper itself
# ---------------------------------------------------------------------------


def _make_pool_returning(rows: list[dict[str, Any]]) -> MagicMock:
    """asyncpg pool double whose ``.acquire()`` yields a conn whose
    ``.fetch()`` returns the canned row list."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    pool = MagicMock()
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


@pytest.mark.unit
class TestResolveTaskIdPrefix:

    @pytest.mark.asyncio
    async def test_full_uuid_returns_unchanged_no_db_hit(self):
        """36-char UUID with dashes → return as-is, don't touch the DB."""
        pool = MagicMock()  # No .acquire() should be called.
        full = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"
        result = await _resolve_task_id_prefix(pool, full)
        assert result == full
        # The pool's acquire was never called — full UUIDs skip the lookup.
        assert not pool.acquire.called if hasattr(pool.acquire, "called") else True

    @pytest.mark.asyncio
    async def test_single_match_prefix_returns_full_uuid(self):
        """Exactly one match → return the full UUID."""
        full_id = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"
        pool = _make_pool_returning([
            {"task_id": full_id, "status": "awaiting_approval", "topic": "DDR5 ..."},
        ])
        result = await _resolve_task_id_prefix(pool, "6bf91cc3")
        assert result == full_id

    @pytest.mark.asyncio
    async def test_zero_matches_returns_none(self):
        """Caller is expected to raise TaskNotFoundError on None."""
        pool = _make_pool_returning([])
        result = await _resolve_task_id_prefix(pool, "deadbeef")
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_matches_raises_ambiguous(self):
        """Multiple matches → AmbiguousPrefixError listing candidates.

        Pins:
        - The exception type
        - The candidate list shows up in the message (operator picks)
        - The hint to use a longer prefix
        """
        pool = _make_pool_returning([
            {"task_id": "abc11111-...", "status": "awaiting_approval", "topic": "Foo"},
            {"task_id": "abc22222-...", "status": "rejected", "topic": "Bar"},
        ])
        with pytest.raises(_AmbiguousPrefixError) as exc_info:
            await _resolve_task_id_prefix(pool, "abc")
        msg = str(exc_info.value)
        assert "matches 2 tasks" in msg
        assert "abc11111" in msg and "abc22222" in msg
        assert "longer prefix" in msg


# ---------------------------------------------------------------------------
# _format_not_paused_hint — error-message upgrade
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotPausedHint:

    def test_hint_points_at_tasks_approve_subcommand(self):
        """The whole point: when the gate-based command can't approve
        (because the task has no gate), the operator gets a one-line
        actionable next step instead of staring at the error."""
        task_id = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"
        msg = _format_not_paused_hint(task_id, "awaiting_approval")
        assert "poindexter tasks approve" in msg
        assert "poindexter tasks reject" in msg
        # The 8-char prefix the operator will recognise from the dashboard
        # is what we suggest — not the full UUID.
        assert task_id[:8] in msg
        # The current status is shown so the operator knows what state
        # they're working with.
        assert "awaiting_approval" in msg


# ---------------------------------------------------------------------------
# approve_command — end-to-end via CliRunner
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApproveCommandPrefixResolution:

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_prefix_resolves_then_approves(self, runner):
        full_id = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"

        async def fake_resolve(pool, prefix):
            assert prefix == "6bf91cc3"
            return full_id

        fake_approve = AsyncMock(return_value={
            "ok": True, "task_id": full_id, "gate_name": "topic_decision",
        })

        with patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(side_effect=fake_resolve),
        ), patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "services.approval_service.approve",
            new=fake_approve,
        ):
            result = runner.invoke(
                approve_command,
                ["6bf91cc3", "--feedback", "good post"],
            )

        assert result.exit_code == 0, result.output
        # Output shows the resolved full UUID, not the prefix.
        assert full_id in result.output
        fake_approve.assert_awaited_once()
        # The service was called with the EXPANDED task_id, not the prefix.
        assert fake_approve.await_args.kwargs["task_id"] == full_id

    def test_ambiguous_prefix_exits_2_with_disambiguation(self, runner):
        async def fake_resolve(pool, prefix):
            raise _AmbiguousPrefixError(
                "Prefix 'abc' matches 2 tasks (showing up to 5):\n"
                "  abc11111-... status=awaiting_approval topic=Foo\n"
                "  abc22222-... status=rejected topic=Bar\n"
                "\nUse the full task_id or a longer prefix."
            )

        with patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(side_effect=fake_resolve),
        ), patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ):
            result = runner.invoke(approve_command, ["abc"])

        assert result.exit_code == 2
        assert "matches 2 tasks" in result.output
        assert "abc11111" in result.output and "abc22222" in result.output

    def test_not_paused_routes_to_tasks_approve_hint(self, runner):
        """The empirical failure 2026-05-11 20:46 UTC: full UUID lookup
        succeeds but the task has no gate. Operator should see the
        ``poindexter tasks approve`` suggestion in the error."""
        from services.approval_service import TaskNotPausedError

        full_id = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"

        async def fake_resolve(pool, prefix):
            return full_id

        async def fake_approve(**kwargs):
            raise TaskNotPausedError(
                f"Task {full_id} is not paused at any gate "
                f"(current status='awaiting_approval')"
            )

        with patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(side_effect=fake_resolve),
        ), patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "services.approval_service.approve",
            new=AsyncMock(side_effect=fake_approve),
        ):
            result = runner.invoke(approve_command, ["6bf91cc3"])

        assert result.exit_code != 0
        assert "poindexter tasks approve" in result.output
        assert "awaiting_approval" in result.output

    def test_not_found_still_errors_cleanly(self, runner):
        """Zero-match prefix → standard not-found error path."""
        with patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(return_value=None),
        ), patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ):
            result = runner.invoke(approve_command, ["deadbeef"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# reject_command — same shape as approve_command
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectCommandPrefixResolution:
    """Mirrors approve_command coverage at a lower density —
    the helpers are shared so we don't need to re-test every shape."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_prefix_resolves_then_rejects(self, runner):
        full_id = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"
        fake_reject = AsyncMock(return_value={
            "ok": True, "task_id": full_id, "gate_name": "topic_decision",
            "new_status": "rejected",
        })

        with patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(return_value=full_id),
        ), patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "services.approval_service.reject",
            new=fake_reject,
        ):
            result = runner.invoke(
                reject_command,
                ["6bf91cc3", "--reason", "off-brand"],
            )

        assert result.exit_code == 0, result.output
        assert full_id in result.output
        assert fake_reject.await_args.kwargs["task_id"] == full_id

    def test_reject_not_paused_routes_to_tasks_reject_hint(self, runner):
        from services.approval_service import TaskNotPausedError

        full_id = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"

        async def fake_reject(**kwargs):
            raise TaskNotPausedError(
                f"Task {full_id} is not paused at any gate "
                f"(current status='awaiting_approval')"
            )

        with patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(return_value=full_id),
        ), patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "services.approval_service.reject",
            new=AsyncMock(side_effect=fake_reject),
        ):
            result = runner.invoke(reject_command, ["6bf91cc3", "--reason", "test"])

        assert result.exit_code != 0
        assert "poindexter tasks reject" in result.output
