"""``poindexter tasks reject`` / ``reject-batch`` — implicit --retry warning.

2026-07-24: the operator rejected a duplicate-topic task without a flag, got
the silent --retry default, and then had to escalate the rejection to
rejected_final in a second step. The flag pair is now tri-state
(``default=None``): omitting it still behaves as --retry (API default
unchanged) but prints a one-line stderr warning naming the choice and the
--final escape hatch. Passing either flag explicitly stays silent.

Companion route-side contract: tests/unit/routes/test_approval_routes.py
``TestRejectFinalizeEscalation`` (rejected_retry → rejected_final).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from poindexter.cli.tasks import tasks_group

WARN_FRAGMENT = "defaulting to --retry"


@pytest.fixture
def runner():
    return CliRunner()


def _capture_post_action():
    """Patch _post_action, recording (task_id, action, payload) per call."""
    calls: list[tuple[str, str, dict | None]] = []

    def _fake(tid: str, action: str, payload: dict | None = None) -> dict:
        calls.append((tid, action, payload))
        return {"id": tid, "status": "rejected_retry"}

    return calls, patch("poindexter.cli.tasks._post_action", side_effect=_fake)


@pytest.mark.unit
class TestRejectDefaultWarn:
    def test_no_flag_warns_and_sends_retry(self, runner):
        calls, patcher = _capture_post_action()
        with patcher:
            result = runner.invoke(
                tasks_group, ["reject", "task-001", "--feedback", "dupe"],
            )

        assert result.exit_code == 0
        assert WARN_FRAGMENT in result.stderr
        assert "--final" in result.stderr
        assert calls[0][2]["allow_revisions"] is True

    def test_explicit_retry_is_silent(self, runner):
        calls, patcher = _capture_post_action()
        with patcher:
            result = runner.invoke(
                tasks_group,
                ["reject", "task-001", "--feedback", "dupe", "--retry"],
            )

        assert result.exit_code == 0
        assert WARN_FRAGMENT not in result.stderr
        assert calls[0][2]["allow_revisions"] is True

    def test_explicit_final_is_silent_and_sends_final(self, runner):
        calls, patcher = _capture_post_action()
        with patcher:
            result = runner.invoke(
                tasks_group,
                ["reject", "task-001", "--feedback", "dupe", "--final"],
            )

        assert result.exit_code == 0
        assert WARN_FRAGMENT not in result.stderr
        assert calls[0][2]["allow_revisions"] is False

    def test_missing_feedback_still_exits_2_before_warning(self, runner):
        calls, patcher = _capture_post_action()
        with patcher:
            result = runner.invoke(tasks_group, ["reject", "task-001"])

        assert result.exit_code == 2
        assert calls == []


@pytest.mark.unit
class TestRejectBatchDefaultWarn:
    def test_no_flag_warns_once_and_sends_retry(self, runner):
        calls, patcher = _capture_post_action()
        with patcher:
            result = runner.invoke(
                tasks_group,
                ["reject-batch", "task-001", "task-002", "--feedback", "dupe", "--yes"],
            )

        assert result.exit_code == 0
        assert result.stderr.count(WARN_FRAGMENT) == 1
        assert len(calls) == 2
        assert all(payload["allow_revisions"] is True for _, _, payload in calls)

    def test_explicit_final_is_silent_and_sends_final(self, runner):
        calls, patcher = _capture_post_action()
        with patcher:
            result = runner.invoke(
                tasks_group,
                [
                    "reject-batch", "task-001",
                    "--feedback", "dupe", "--final", "--yes",
                ],
            )

        assert result.exit_code == 0
        assert WARN_FRAGMENT not in result.stderr
        assert calls[0][2]["allow_revisions"] is False
