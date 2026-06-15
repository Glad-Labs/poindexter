"""CLI wiring for ``poindexter tasks edit-body|replace-image|regen-image`` (#523).

Each test mocks ``WorkerClient`` and asserts the command POSTs the right payload
to the right worker-API route. The service logic itself is covered by
tests/unit/modules/content/test_post_edit_service.py.
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
    """AsyncMock WorkerClient whose json_or_raise yields ``result``."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = MagicMock()
    client.get.return_value = MagicMock()
    client.json_or_raise.return_value = result
    return client


def test_replace_image_posts_payload(runner):
    client = _fake_client(
        {"ok": True, "field": "featured", "detail": "d", "new_url": "https://cdn/x.png"},
    )
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(
            tasks_group,
            ["replace-image", "abc123", "--which", "featured", "--url", "https://cdn/x.png"],
        )
    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert args[0] == "/api/tasks/abc123/replace-image"
    assert kwargs["json"] == {"which": "featured", "url": "https://cdn/x.png"}


def test_regen_image_posts_payload(runner):
    client = _fake_client(
        {"ok": True, "field": "inline:2", "detail": "d", "new_url": "u"},
    )
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(
            tasks_group,
            ["regen-image", "abc123", "--which", "inline:2", "--prompt", "a teal robot"],
        )
    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert args[0] == "/api/tasks/abc123/regen-image"
    assert kwargs["json"] == {"which": "inline:2", "prompt": "a teal robot"}


def test_edit_body_find_replace_posts_payload(runner):
    client = _fake_client(
        {"ok": True, "field": "body", "detail": "edited", "warnings": ["w1"]},
    )
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = runner.invoke(
            tasks_group,
            ["edit-body", "abc123", "--find", "[memory/x] ", "--replace", ""],
        )
    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert args[0] == "/api/tasks/abc123/edit-body"
    assert kwargs["json"] == {"find": "[memory/x] ", "replace": ""}
    assert "w1" in result.output  # validator warning surfaced


def test_edit_body_editor_mode(runner):
    """No --find: fetch current body, open $EDITOR, POST the saved result."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = MagicMock()
    client.post.return_value = MagicMock()
    client.json_or_raise.side_effect = [
        {"content": "old body"},                              # _fetch_task_body GET
        {"ok": True, "field": "body", "detail": "edited"},    # edit-body POST
    ]
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client), patch(
        "poindexter.cli.tasks.click.edit", return_value="new body",
    ):
        result = runner.invoke(tasks_group, ["edit-body", "abc123"])
    assert result.exit_code == 0, result.output
    args, kwargs = client.post.call_args
    assert args[0] == "/api/tasks/abc123/edit-body"
    assert kwargs["json"] == {"new_content": "new body"}


def test_edit_body_editor_mode_no_change_skips_post(runner):
    """If $EDITOR returns the body unchanged, no edit is POSTed."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = MagicMock()
    client.json_or_raise.return_value = {"content": "same body"}
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client), patch(
        "poindexter.cli.tasks.click.edit", return_value="same body",
    ):
        result = runner.invoke(tasks_group, ["edit-body", "abc123"])
    assert result.exit_code == 0, result.output
    assert "(no changes)" in result.output
    client.post.assert_not_called()
