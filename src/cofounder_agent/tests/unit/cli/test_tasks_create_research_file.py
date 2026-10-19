"""``poindexter tasks create --research-file``: source facts for the writer.

The file becomes the task's ``metadata.research_context``, which the writer
grounds the draft in and the numeric / self-claim QA rails check it against.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.tasks import tasks_group


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, args):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = MagicMock()
    client.json_or_raise.return_value = {"id": "t1", "status": "pending"}
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client), patch(
        "poindexter.cli.tasks.wait_for_worker", AsyncMock(return_value="http://w"),
    ):
        result = runner.invoke(tasks_group, ["create", *args])
    return result, client


def test_the_file_becomes_the_tasks_research_context(runner, tmp_path):
    facts = tmp_path / "facts.txt"
    facts.write_text("  The voice read $470000 as 47000 dollars.\n", encoding="utf-8")
    result, client = _invoke(runner, ["A story", "--niche", "glad-labs", "--research-file", str(facts)])
    assert result.exit_code == 0, result.output
    payload = client.post.call_args.kwargs["json"]
    assert payload["metadata"] == {"research_context": "The voice read $470000 as 47000 dollars."}
    assert payload["niche_slug"] == "glad-labs"


def test_without_the_option_the_body_is_unchanged(runner):
    result, client = _invoke(runner, ["A story"])
    assert result.exit_code == 0, result.output
    assert "metadata" not in client.post.call_args.kwargs["json"]


def test_an_empty_file_is_refused(runner, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    result, client = _invoke(runner, ["A story", "--research-file", str(empty)])
    assert result.exit_code == 2
    assert "is empty" in result.output
    client.post.assert_not_called()
