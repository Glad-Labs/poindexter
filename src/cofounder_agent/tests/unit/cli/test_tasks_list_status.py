"""Regression: ``poindexter tasks list --status`` must accept every status the
pipeline actually writes — notably ``approved``, which ``tasks approve`` sets.

2026-05-31: the CLI's ``_VALID_STATUSES`` click.Choice omitted ``approved``
(behind a stale "removed in 2026-04" docstring), so
``poindexter tasks list --status approved`` failed click validation even though
``tasks approve`` produces that status and the worker API filters on it. Result:
approved posts were invisible to the operator ("I've lost them"). This pins the
contract so the list filter can never drift behind the statuses the pipeline
emits again.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.tasks import _VALID_STATUSES, tasks_group


@pytest.fixture
def runner():
    return CliRunner()


def test_live_statuses_are_valid_list_choices():
    """Every status the pipeline writes must be filterable from `tasks list`."""
    # Statuses the system actually produces on pipeline_tasks.status.
    produced = {
        "pending",
        "in_progress",
        "awaiting_approval",
        "approved",
        "published",
        "rejected",
        "rejected_retry",
        "rejected_final",
        "failed",
        "cancelled",
        "superseded",
    }
    missing = produced - set(_VALID_STATUSES)
    assert not missing, f"CLI cannot filter these live statuses: {sorted(missing)}"


def test_list_accepts_status_approved(runner):
    """`--status approved` passes click validation and forwards to the API."""
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.get.return_value = MagicMock()
    fake_client.json_or_raise.return_value = {
        "tasks": [
            {"id": "6a7e6951abcd", "status": "approved", "title": "Indie games"},
        ],
        "total": 1,
    }

    with patch("poindexter.cli.tasks.WorkerClient", return_value=fake_client):
        result = runner.invoke(tasks_group, ["list", "--status", "approved"])

    assert result.exit_code == 0, result.output
    assert "Invalid value" not in result.output  # would mean click rejected it
    assert "approved" in result.output
    # The status filter must reach the worker API verbatim.
    _, kwargs = fake_client.get.call_args
    assert kwargs["params"].get("status") == "approved"
