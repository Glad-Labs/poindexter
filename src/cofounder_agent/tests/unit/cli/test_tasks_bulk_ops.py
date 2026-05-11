"""Click CLI tests for ``poindexter tasks reject-batch`` / ``approve-batch``.

Bulk variants of the single-id commands. The CLI accepts task IDs from
three sources (positional args, ``--filter`` SQL, ``--from-stdin``),
unions + de-dupes, and fires the worker API one call per task —
matching the existing single-id ``tasks reject`` / ``tasks approve``
contract so each call still writes audit_log rows and gate-history.

Glad-Labs/poindexter — operator efficiency follow-up to the 2026-05-11
session where Matt had to reject batch B tasks individually before the
CLI broke entirely on the port-binding glitch. Mirrors the design of
``post approve-batch`` / ``post reject-batch`` (test_post_approve_bulk.py).

Coverage:

- Positional args → resolved + de-duped + fired in order
- ``--filter`` SQL → resolved via inline asyncpg pool, unioned with args
- ``--from-stdin`` → reads stdin, comments + blanks ignored
- ``--dry-run`` → prints plan + exits 0 without firing
- ``--yes`` skip-prompt → bypasses confirm
- Threshold prompt fires for >5 tasks unless ``--yes``
- ``--feedback`` required on reject-batch
- Per-task failures don't halt the loop; exit code 1 if any failed
- All-failure batch exits 1, all-success exits 0
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.tasks import tasks_group


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_post_action_ok(tid: str, action: str, payload: dict | None = None) -> dict:
    """Stand-in for the real _post_action — returns a happy 200-shape."""
    return {"id": tid, "status": "rejected_retry" if action == "reject" else "approved"}


def _fake_post_action_alternating(tid: str, action: str, payload: dict | None = None) -> dict:
    """Half succeed, half fail — for exit-code testing."""
    if int(tid[-1], 16) % 2 == 0:
        return {"id": tid, "status": "ok"}
    raise RuntimeError(f"simulated worker 500 for {tid}")


def _patch_filter_returns(*ids: str):
    """Patch the asyncpg pool the --filter path opens."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"task_id": tid} for tid in ids])
    pool.close = AsyncMock()
    return patch(
        "asyncpg.create_pool", new=AsyncMock(return_value=pool),
    )


# ---------------------------------------------------------------------------
# reject-batch: input sources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectBatchInputs:
    """The three input sources are unioned and de-duplicated."""

    def test_positional_args_fire_in_order(self, runner):
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "abc-1", "abc-2", "abc-3",
                 "--feedback", "stale topic", "--yes"],
            )

        assert result.exit_code == 0, result.output
        assert mock_post.call_count == 3
        assert [c.args[0] for c in mock_post.call_args_list] == ["abc-1", "abc-2", "abc-3"]
        assert "3 ok, 0 failed" in result.output

    def test_filter_source_runs_sql_then_fires_resolved_ids(self, runner):
        with _patch_filter_returns("filt-1", "filt-2"), patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch",
                 "--filter", "status='awaiting_approval'",
                 "--feedback", "wrong writer", "--yes"],
            )

        assert result.exit_code == 0, result.output
        assert {c.args[0] for c in mock_post.call_args_list} == {"filt-1", "filt-2"}

    def test_stdin_source_reads_ids_ignoring_comments_and_blanks(self, runner):
        stdin_payload = "\n".join([
            "# header comment — should be skipped",
            "stdin-1",
            "",  # blank
            "stdin-2",
            "  ",  # whitespace
            "stdin-3",
        ])
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "--from-stdin",
                 "--feedback", "bulk via pipe", "--yes"],
                input=stdin_payload,
            )

        assert result.exit_code == 0, result.output
        assert [c.args[0] for c in mock_post.call_args_list] == ["stdin-1", "stdin-2", "stdin-3"]

    def test_union_of_three_sources_dedups(self, runner):
        """Positional + --filter + --from-stdin all overlap → no double-fire."""
        with _patch_filter_returns("dup-1", "filt-only"), patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "dup-1", "arg-only",
                 "--filter", "status='x'",
                 "--from-stdin",
                 "--feedback", "test", "--yes"],
                input="dup-1\nstdin-only\n",
            )

        assert result.exit_code == 0, result.output
        # 4 unique IDs from 6 total appearances (dup-1 appears 3x)
        fired = [c.args[0] for c in mock_post.call_args_list]
        assert fired == ["dup-1", "arg-only", "stdin-only", "filt-only"], (
            f"unique-order union should be args → stdin → filter; got {fired}"
        )

    def test_no_inputs_at_all_exits_2_with_usage_error(self, runner):
        result = runner.invoke(
            tasks_group,
            ["reject-batch", "--feedback", "test", "--yes"],
        )
        assert result.exit_code == 2
        assert "no task_ids resolved" in result.output


# ---------------------------------------------------------------------------
# reject-batch: required flags + safety prompts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectBatchSafety:

    def test_feedback_required(self, runner):
        result = runner.invoke(
            tasks_group, ["reject-batch", "abc-1", "--yes"],
        )
        assert result.exit_code == 2
        assert "--feedback is required" in result.output

    def test_dry_run_prints_plan_without_firing(self, runner):
        with patch(
            "poindexter.cli.tasks._post_action",
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "dry-1", "dry-2",
                 "--feedback", "test", "--dry-run"],
            )
        assert result.exit_code == 0, result.output
        mock_post.assert_not_called()
        assert "Would reject 2 task(s)" in result.output
        assert "dry-1" in result.output and "dry-2" in result.output

    def test_under_threshold_skips_confirm_prompt(self, runner):
        """≤5 tasks fire immediately without prompting."""
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ):
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "t-1", "t-2", "t-3",
                 "--feedback", "test"],
                input="",  # no input — if a prompt fires the command would hang or abort
            )
        assert result.exit_code == 0, result.output
        assert "3 ok, 0 failed" in result.output

    def test_over_threshold_aborts_when_prompt_declined(self, runner):
        """>5 tasks AND no --yes → fires the prompt; "n" aborts cleanly."""
        ids = [f"big-{i}" for i in range(8)]
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", *ids, "--feedback", "test"],
                input="n\n",
            )
        assert result.exit_code == 2
        assert "Aborted" in result.output
        mock_post.assert_not_called()

    def test_over_threshold_with_yes_skips_prompt(self, runner):
        ids = [f"big-{i}" for i in range(8)]
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", *ids, "--feedback", "test", "--yes"],
            )
        assert result.exit_code == 0, result.output
        assert mock_post.call_count == 8


# ---------------------------------------------------------------------------
# reject-batch: failure handling + exit codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectBatchFailureHandling:

    def test_per_task_failures_do_not_halt_the_loop(self, runner):
        """Even if task 1 errors, tasks 2/3 still get attempted."""
        with patch(
            "poindexter.cli.tasks._post_action",
            side_effect=_fake_post_action_alternating,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "task-1", "task-2", "task-3", "task-4",
                 "--feedback", "test", "--yes"],
            )

        # Every task was attempted, even after the first error.
        assert mock_post.call_count == 4

    def test_exit_code_1_on_any_failure_0_on_all_success(self, runner):
        """Partial failures exit 1 so wrapping scripts can detect them."""
        with patch(
            "poindexter.cli.tasks._post_action",
            side_effect=_fake_post_action_alternating,
        ):
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "task-1", "task-2",
                 "--feedback", "test", "--yes"],
            )
        # task-1 fails (odd last-hex), task-2 succeeds (even).
        assert result.exit_code == 1, result.output
        assert "1 ok, 1 failed" in result.output


# ---------------------------------------------------------------------------
# approve-batch: parity with reject-batch where applicable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApproveBatch:

    def test_positional_args_approve(self, runner):
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["approve-batch", "a-1", "a-2", "--yes"],
            )
        assert result.exit_code == 0, result.output
        assert mock_post.call_count == 2
        assert all(c.args[1] == "approve" for c in mock_post.call_args_list)
        assert "2 ok, 0 failed" in result.output

    def test_no_feedback_flag_needed(self, runner):
        """approve-batch deliberately has no --feedback — pin that
        we didn't accidentally copy the reject-batch requirement."""
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ):
            result = runner.invoke(
                tasks_group, ["approve-batch", "a-1", "--yes"],
            )
        assert result.exit_code == 0, result.output

    def test_filter_source_supports_quality_gating(self, runner):
        """--filter with a quality_score check — the operator's
        canonical bulk-approve recipe per the spec."""
        with _patch_filter_returns("hq-1", "hq-2", "hq-3"), patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group,
                ["approve-batch",
                 "--filter",
                 "status='awaiting_approval' AND quality_score>=85",
                 "--yes"],
            )
        assert result.exit_code == 0, result.output
        assert mock_post.call_count == 3

    def test_dry_run(self, runner):
        with patch(
            "poindexter.cli.tasks._post_action",
        ) as mock_post:
            result = runner.invoke(
                tasks_group, ["approve-batch", "a-1", "a-2", "--dry-run"],
            )
        assert result.exit_code == 0, result.output
        mock_post.assert_not_called()
        assert "Would approve 2 task(s)" in result.output


# ---------------------------------------------------------------------------
# Single-id commands still work (no regression)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSingleIdCommandsUnchanged:
    """The bulk variants are additive — the existing single-id
    ``tasks reject`` / ``tasks approve`` commands must continue working
    untouched."""

    def test_single_reject_still_works(self, runner):
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(
                tasks_group, ["reject", "single-1", "--feedback", "test"],
            )
        assert result.exit_code == 0, result.output
        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == "single-1"

    def test_single_approve_still_works(self, runner):
        with patch(
            "poindexter.cli.tasks._post_action", side_effect=_fake_post_action_ok,
        ) as mock_post:
            result = runner.invoke(tasks_group, ["approve", "single-2"])
        assert result.exit_code == 0, result.output
        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == "single-2"
