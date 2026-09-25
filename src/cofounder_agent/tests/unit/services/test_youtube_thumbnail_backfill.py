"""Backfilling composed thumbnails onto already-published long videos.

The guarantee under test: what ``--apply`` uploads is exactly what the dry
run stored for review. Apply never recomposes a stored thumbnail (a second
model call could write different words), skips videos already set, and
reports a refused upload with its fix.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import youtube_thumbnail_backfill as bf

pytestmark = pytest.mark.asyncio


def _row(task_id: str, video_id: str, *, thumb: str = "", status: str = "") -> dict[str, Any]:
    return {
        "task_id": task_id, "video_path": f"/v/{task_id}.mp4", "video_id": video_id,
        "post_id": f"post-{task_id}", "slug": f"slug-{task_id}", "title": f"Title {task_id}",
        "niche_slug": "glad-labs", "thumbnail_path": thumb, "thumbnail_status": status,
    }


def _pool(rows):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def stored(tmp_path):
    p = tmp_path / "stored.jpg"
    p.write_bytes(b"\xff\xd8jpeg")
    return str(p)


async def test_dry_run_composes_the_missing_and_reports_the_stored(stored):
    pool = _pool([_row("a", "VA"), _row("b", "VB", thumb=stored)])
    compose = AsyncMock(return_value=("/v/a_thumbnail.jpg", {"hook": "No NCCL", "background": "featured_image"}, ""))
    with patch.object(bf, "_compose_and_store", compose):
        out = await bf.backfill_youtube_thumbnails(pool, None)
    assert [o.action for o in out] == ["composed — review, then --apply", "stored (not uploaded)"]
    assert out[0].hook == "No NCCL" and out[1].path == stored
    assert compose.await_count == 1  # the stored one is not recomposed
    pool.execute.assert_not_awaited()  # nothing stamped: nothing was sent


async def test_recompose_replaces_a_stored_one_in_the_dry_run(stored):
    pool = _pool([_row("b", "VB", thumb=stored)])
    compose = AsyncMock(return_value=("/v/b_thumbnail.jpg", {}, ""))
    with patch.object(bf, "_compose_and_store", compose):
        out = await bf.backfill_youtube_thumbnails(pool, None, recompose=True)
    assert compose.await_count == 1 and out[0].path == "/v/b_thumbnail.jpg"


async def test_apply_uploads_the_stored_file_and_never_recomposes(stored):
    pool = _pool([_row("b", "VB", thumb=stored), _row("c", "VC", thumb=stored, status="set")])
    adapter = MagicMock()
    adapter.set_thumbnail = AsyncMock(return_value=(True, "set"))
    compose = AsyncMock()
    with patch.object(bf, "_compose_and_store", compose), patch(
        "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter,
    ):
        out = await bf.backfill_youtube_thumbnails(pool, None, apply=True)
    assert [o.action for o in out] == ["uploaded", "already set"]
    compose.assert_not_awaited()
    adapter.set_thumbnail.assert_awaited_once_with(video_id="VB", thumbnail_path=stored)
    stamp = pool.execute.await_args
    assert "video_thumbnail" in stamp.args[0] and '"status": "set"' in stamp.args[2]


async def test_a_refused_upload_is_reported_with_its_fix(stored):
    pool = _pool([_row("b", "VB", thumb=stored)])
    adapter = MagicMock()
    adapter.set_thumbnail = AsyncMock(return_value=(False, "the channel cannot set custom thumbnails yet"))
    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
        out = await bf.backfill_youtube_thumbnails(pool, None, apply=True)
    assert out[0].failed and "custom thumbnails" in out[0].error
    assert '"status": "failed: the channel' in pool.execute.await_args.args[2]


async def test_the_selector_narrows_to_one_video(stored):
    pool = _pool([_row("a", "VA"), _row("b", "VB", thumb=stored)])
    for selector in ("VB", "post-b", "slug-b", "b"):
        out = await bf.backfill_youtube_thumbnails(pool, None, selector=selector)
        assert [o.video_id for o in out] == ["VB"], selector


async def test_naming_one_video_also_reaches_videos_awaiting_approval():
    pool = _pool([])
    await bf.backfill_youtube_thumbnails(pool, None)
    assert pool.fetch.await_args.args[1] is False          # a sweep: published only
    await bf.backfill_youtube_thumbnails(pool, None, selector="p1")
    assert pool.fetch.await_args.args[1] is True           # one video: any long video


async def test_the_operator_can_write_one_videos_text():
    pool = _pool([_row("a", "VA"), _row("b", "")])
    compose = AsyncMock(return_value=("/v/b_thumbnail.jpg", {"hook": "Paradox Pie", "background": "featured_image"}, ""))
    with patch.object(bf, "_compose_and_store", compose):
        out = await bf.backfill_youtube_thumbnails(pool, None, selector="b", hook="  Paradox  Pie ")
    assert compose.await_args.args[3] == "Paradox  Pie"      # trimmed, passed through
    assert out[0].action == "composed — uploads with the video when you approve it"
    assert out[0].task_id == "b"


@pytest.mark.parametrize(("selector", "apply", "match"), [
    (None, False, "exactly one"),        # no --post: which video?
    ("VA", True, "--apply without --hook"),   # never upload unreviewed text
])
async def test_hook_refuses_what_nobody_would_have_reviewed(selector, apply, match):
    pool = _pool([_row("a", "VA"), _row("c", "VC")])
    with pytest.raises(ValueError, match=match):
        await bf.backfill_youtube_thumbnails(pool, None, selector=selector, apply=apply, hook="text")


async def test_apply_leaves_a_video_not_on_youtube_to_its_approval(stored):
    pool = _pool([_row("b", "", thumb=stored)])
    adapter = MagicMock()
    adapter.set_thumbnail = AsyncMock()
    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
        out = await bf.backfill_youtube_thumbnails(pool, None, selector="b", apply=True)
    assert out[0].action.startswith("not on YouTube yet")
    adapter.set_thumbnail.assert_not_awaited()
    pool.execute.assert_not_awaited()

