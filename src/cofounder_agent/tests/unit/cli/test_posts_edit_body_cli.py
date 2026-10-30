"""Click CLI tests for ``poindexter posts edit-body`` and the ``posts get`` fix.

``edit-body`` reads the stored markdown (``GET /api/posts/{slug}?format=markdown``),
applies one edit, saves the pre-edit post to a backup file, and PATCHes
``/api/posts/{id}``, which re-exports a published post and refreshes the site
cache. Before it existed, editing a live post meant a hand-written UPDATE plus
an export script run inside the worker container (2026-10-30). The HTTP helpers
are patched so no test makes a real call.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import posts_group

_POST = {
    "id": "15d7ba33-656e-4d93-9752-4f06ba77f2ef",
    "slug": "fastapi-post",
    "title": "FastAPI Best Practices",
    "status": "published",
    "content": "## One\n\nOld line.\n\n## Two\n\nMore text.\n",
    "excerpt": "Old excerpt about the old body.",
    "seo_title": "Old SEO",
    "seo_description": "Old description",
}
_LIVE = {"success": True, "live": {"refreshed": True, "slug": "fastapi-post",
                                   "exported": True, "revalidated": True}}


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, args, tmp_path, *, post=None, patch_result=None):
    get = AsyncMock(return_value=dict(post or _POST))
    patch_ = AsyncMock(return_value=patch_result or _LIVE)
    with patch("poindexter.cli.posts._get_post_markdown", new=get), \
         patch("poindexter.cli.posts._patch_post", new=patch_):
        result = runner.invoke(
            posts_group, ["edit-body", *args, "--backup-dir", str(tmp_path)]
        )
    return result, get, patch_


def test_edit_body_is_registered():
    assert "edit-body" in posts_group.commands


def test_find_replace_patches_content_and_writes_backup(runner, tmp_path):
    result, get, patch_ = _invoke(
        runner, ["fastapi-post", "--find", "Old line.", "--replace", "New line."], tmp_path
    )
    assert result.exit_code == 0, result.output
    get.assert_awaited_once_with("fastapi-post")
    post_id, updates = patch_.await_args.args
    assert post_id == _POST["id"]
    assert updates == {"content": _POST["content"].replace("Old line.", "New line.")}
    # The pre-edit post is on disk before anything is written.
    backups = list(tmp_path.iterdir())
    assert len(backups) == 1
    saved = json.loads(backups[0].read_text())
    assert saved["content"] == _POST["content"]
    assert saved["seo_title"] == "Old SEO"
    assert "Updated and live" in result.output
    # A body edit that leaves the old excerpt in place says so.
    assert "excerpt unchanged" in result.output


def test_find_miss_fails_without_writing(runner, tmp_path):
    result, _, patch_ = _invoke(runner, ["fastapi-post", "--find", "not there"], tmp_path)
    assert result.exit_code != 0
    assert "not found" in result.output
    patch_.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


def test_ambiguous_find_needs_all(runner, tmp_path):
    result, _, patch_ = _invoke(runner, ["fastapi-post", "--find", "## "], tmp_path)
    assert result.exit_code != 0
    assert "2 times" in result.output
    patch_.assert_not_awaited()

    result, _, patch_ = _invoke(
        runner, ["fastapi-post", "--find", "## ", "--replace", "### ", "--all"], tmp_path
    )
    assert result.exit_code == 0, result.output
    assert patch_.await_args.args[1]["content"].count("### ") == 2


def test_from_file_replaces_body_and_sets_meta(runner, tmp_path):
    src = tmp_path / "new.md"
    src.write_text("## Fresh\n\nAll new.\n", encoding="utf-8")
    backups = tmp_path / "backups"
    result, _, patch_ = _invoke(
        runner,
        ["fastapi-post", "--from-file", str(src), "--seo-title", "New SEO",
         "--excerpt", "New excerpt."],
        backups,
    )
    assert result.exit_code == 0, result.output
    assert patch_.await_args.args[1] == {
        "content": "## Fresh\n\nAll new.\n",
        "seo_title": "New SEO",
        "excerpt": "New excerpt.",
    }
    assert "excerpt unchanged" not in result.output


def test_meta_only_edit_leaves_body_alone(runner, tmp_path):
    result, _, patch_ = _invoke(
        runner, ["fastapi-post", "--seo-description", "Better description"], tmp_path
    )
    assert result.exit_code == 0, result.output
    assert patch_.await_args.args[1] == {"seo_description": "Better description"}


def test_dry_run_writes_nothing(runner, tmp_path):
    result, _, patch_ = _invoke(
        runner, ["fastapi-post", "--find", "Old line.", "--replace", "X", "--dry-run"], tmp_path
    )
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output
    patch_.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


def test_empty_body_is_refused(runner, tmp_path):
    src = tmp_path / "empty.md"
    src.write_text("   \n", encoding="utf-8")
    result, _, patch_ = _invoke(runner, ["fastapi-post", "--from-file", str(src)], tmp_path)
    assert result.exit_code != 0
    patch_.assert_not_awaited()


def test_find_and_from_file_are_exclusive(runner, tmp_path):
    src = tmp_path / "new.md"
    src.write_text("x", encoding="utf-8")
    result, get, _ = _invoke(
        runner, ["fastapi-post", "--find", "a", "--from-file", str(src)], tmp_path
    )
    assert result.exit_code != 0
    get.assert_not_awaited()


def test_live_refresh_failure_is_reported(runner, tmp_path):
    result, _, _ = _invoke(
        runner, ["fastapi-post", "--find", "Old line.", "--replace", "New line."], tmp_path,
        patch_result={"success": True, "live": {"refreshed": False, "exported": False,
                                                "revalidated": True, "slug": "fastapi-post"}},
    )
    assert result.exit_code == 0, result.output
    assert "NOT refreshed" in result.output


def test_editor_path_patches_the_edited_text(runner, tmp_path):
    with patch("poindexter.cli.posts.click.edit", return_value="## Edited\n"):
        result, _, patch_ = _invoke(runner, ["fastapi-post"], tmp_path)
    assert result.exit_code == 0, result.output
    assert patch_.await_args.args[1] == {"content": "## Edited\n"}


def test_editor_closed_without_changes_is_a_noop(runner, tmp_path):
    with patch("poindexter.cli.posts.click.edit", return_value=None):
        result, _, patch_ = _invoke(runner, ["fastapi-post"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "no changes" in result.output
    patch_.assert_not_awaited()


@pytest.mark.asyncio
async def test_markdown_fetch_asks_for_markdown_and_unwraps_data():
    from unittest.mock import MagicMock

    from poindexter.cli import posts as cli_posts

    client = MagicMock()
    client.get = AsyncMock(return_value="resp")
    client.json_or_raise = AsyncMock(return_value={"data": {"id": "p1"}, "meta": {}})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch.object(cli_posts, "WorkerClient", return_value=cm):
        assert await cli_posts._get_post_markdown("s") == {"id": "p1"}
    client.get.assert_awaited_once_with("/api/posts/s", params={"format": "markdown"})


def test_posts_get_reads_the_data_envelope(runner):
    """The route answers {data, meta}; plain output printed '?' for every field."""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, path):
            return None

        async def json_or_raise(self, resp):
            return {"data": {"id": "p1", "title": "Hello", "status": "published",
                             "slug": "hello"}, "meta": {}}

    with patch("poindexter.cli.posts.WorkerClient", return_value=_Client()):
        result = runner.invoke(posts_group, ["get", "hello"])
    assert result.exit_code == 0, result.output
    assert "Hello" in result.output
    assert "p1" in result.output
    assert "?" not in result.output.split("\n")[1]
