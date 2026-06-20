"""Click CLI tests for ``poindexter posts unpublish`` (#684).

The command is a thin adapter over ``POST /api/posts/{id}/unpublish``: it
calls the ``_unpublish_post`` HTTP helper and renders the service's result.
These tests patch the helper so the suite never makes a real HTTP call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import posts_group


@pytest.fixture
def runner():
    return CliRunner()


def test_unpublish_command_is_registered():
    # Guards against the command silently failing to import/register.
    assert "unpublish" in posts_group.commands


def test_unpublish_success_prints_retired_message(runner):
    fake = AsyncMock(return_value={"unpublished": True, "slug": "bad-post"})
    with patch("poindexter.cli.posts._unpublish_post", new=fake):
        result = runner.invoke(posts_group, ["unpublish", "550e8400"])
    assert result.exit_code == 0, result.output
    fake.assert_awaited_once_with("550e8400")
    assert "Unpublished" in result.output
    assert "bad-post" in result.output


def test_unpublish_noop_prints_no_change(runner):
    fake = AsyncMock(
        return_value={"unpublished": False, "reason": "not_published"}
    )
    with patch("poindexter.cli.posts._unpublish_post", new=fake):
        result = runner.invoke(posts_group, ["unpublish", "550e8400"])
    assert result.exit_code == 0, result.output
    assert "No change" in result.output
    assert "not_published" in result.output


def test_unpublish_http_error_exits_nonzero(runner):
    # json_or_raise raises RuntimeError on a non-2xx (e.g. 404) — the command
    # must surface it as a non-zero exit, not a traceback.
    fake = AsyncMock(side_effect=RuntimeError("404 Not Found"))
    with patch("poindexter.cli.posts._unpublish_post", new=fake):
        result = runner.invoke(posts_group, ["unpublish", "ghost"])
    assert result.exit_code == 1
