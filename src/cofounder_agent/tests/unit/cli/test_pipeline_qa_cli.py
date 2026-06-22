"""CLI tests for the self-heal-before-paging findings surface (Task 6).

``poindexter pipeline qa <task>`` prints the recorded QA-rail findings, and
``poindexter pipeline list-paused`` marks a ``qa_flagged`` row with ``⚑`` so a
draft QA flagged (non-approvable but NOT discarded) is visible at a glance.
Mirrors the mock style in ``test_pipeline_cli.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.pipeline import pipeline_group


@pytest.fixture
def runner():
    return CliRunner()


def _patched_pool():
    return patch(
        "poindexter.cli.pipeline._make_pool",
        new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
    )


# ---------------------------------------------------------------------------
# qa command
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPipelineQaCommand:
    def test_prints_feedback(self, runner):
        feedback = (
            "Final score: 79/100 (REJECTED)\n"
            "- programmatic_validator FAIL: unlinked source"
        )
        with _patched_pool(), patch(
            "poindexter.cli.pipeline.resolve_uuid_prefix",
            new=AsyncMock(return_value="t1full"),
        ), patch(
            "poindexter.cli.pipeline.tasks_mcp.get_task_qa_feedback",
            new=AsyncMock(return_value=feedback),
        ):
            result = runner.invoke(pipeline_group, ["qa", "t1"])
        assert result.exit_code == 0, result.output
        assert "79/100" in result.output
        assert "programmatic_validator" in result.output

    def test_task_not_found_exit_1(self, runner):
        with _patched_pool(), patch(
            "poindexter.cli.pipeline.resolve_uuid_prefix",
            new=AsyncMock(return_value=None),
        ):
            result = runner.invoke(pipeline_group, ["qa", "nope"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_no_feedback_yet_friendly_message(self, runner):
        with _patched_pool(), patch(
            "poindexter.cli.pipeline.resolve_uuid_prefix",
            new=AsyncMock(return_value="t1full"),
        ), patch(
            "poindexter.cli.pipeline.tasks_mcp.get_task_qa_feedback",
            new=AsyncMock(return_value=""),
        ):
            result = runner.invoke(pipeline_group, ["qa", "t1"])
        assert result.exit_code == 0, result.output
        assert "no qa feedback" in result.output.lower()


# ---------------------------------------------------------------------------
# list-paused ⚑ marker
# ---------------------------------------------------------------------------


def _paused_pool(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    pool.close = AsyncMock()
    return pool, conn


@pytest.mark.unit
class TestListPausedFlagMarker:
    def test_flagged_row_gets_marker(self, runner):
        rows = [{
            "task_id": "11111111-1111-1111-1111-111111111111",
            "gate_name": "preview_gate",
            "gate_paused_at": None,
            "status": "awaiting_gate",
            "topic": "DDR5 latency",
            "template_slug": "canonical_blog",
            "qa_flagged": True,
        }]
        pool, conn = _paused_pool(rows)
        with patch("poindexter.cli.pipeline._make_pool",
                   new=AsyncMock(return_value=pool)):
            result = runner.invoke(pipeline_group, ["list-paused"])
        assert result.exit_code == 0, result.output
        assert "⚑" in result.output
        # The SELECT must actually fetch the flag (else it reads NULL in prod).
        assert "qa_flagged" in conn.fetch.await_args.args[0]

    def test_unflagged_row_no_marker(self, runner):
        rows = [{
            "task_id": "22222222-2222-2222-2222-222222222222",
            "gate_name": "draft_gate",
            "gate_paused_at": None,
            "status": "awaiting_gate",
            "topic": "PCIe 5 SSDs",
            "template_slug": "canonical_blog",
            "qa_flagged": False,
        }]
        pool, _ = _paused_pool(rows)
        with patch("poindexter.cli.pipeline._make_pool",
                   new=AsyncMock(return_value=pool)):
            result = runner.invoke(pipeline_group, ["list-paused"])
        assert result.exit_code == 0, result.output
        assert "⚑" not in result.output
