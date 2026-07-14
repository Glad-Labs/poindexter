"""CLI wiring for ``poindexter tasks unapprove`` — undo an accidental
approve on a task that hasn't published yet.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.tasks import tasks_group


@pytest.fixture
def runner():
    return CliRunner()


def _fake_client(result):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = MagicMock()
    client.json_or_raise.return_value = result
    return client


def test_unapprove_default_posts_awaiting_approval_target(runner):
    client = _fake_client({
        "task_id": "abc123", "status": "awaiting_approval",
        "previous_status": "approved", "posts_row_removed": True,
    })
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(tasks_group, ["unapprove", "abc123"])

    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert args[0] == "/api/tasks/abc123/unapprove"
    assert kwargs["json"] == {"to": "awaiting_approval", "feedback": "Unapproved by operator"}
    assert "awaiting_approval" in result.output


def test_unapprove_to_rejected_final_sends_given_feedback(runner):
    client = _fake_client({
        "task_id": "abc123", "status": "rejected_final",
        "previous_status": "approved", "posts_row_removed": False,
    })
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(
            tasks_group,
            ["unapprove", "abc123", "--to", "rejected_final", "--feedback", "Off-topic"],
        )

    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert kwargs["json"] == {"to": "rejected_final", "feedback": "Off-topic"}
    assert "rejected_final" in result.output


def test_unapprove_to_rejected_without_feedback_errors_before_network_call(runner):
    with patch("poindexter.cli.tasks.WorkerClient") as mock_client_cls:
        result = runner.invoke(
            tasks_group, ["unapprove", "abc123", "--to", "rejected_retry"],
        )

    assert result.exit_code == 2
    assert "--feedback is required" in result.output
    mock_client_cls.assert_not_called()


def test_unapprove_invalid_to_choice_rejected_by_click(runner):
    result = runner.invoke(tasks_group, ["unapprove", "abc123", "--to", "bogus"])
    assert result.exit_code != 0
