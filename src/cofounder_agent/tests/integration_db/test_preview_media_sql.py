"""``media_approval_service.get_preview_media`` against the REAL schema.

The draft preview (``routes/cms_routes.py::preview_post``) reads its media
from this lookup. Its matching is all SQL: a render is recorded against its
task with ``post_id`` NULL until the distribute jobs link it, a pre-cutover row
carries only the post, and either preview branch knows only one of the two
keys. A faked pool can only check the SQL string, so these run it on a seeded,
rolled-back transaction (Glad-Labs/poindexter#1089).
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_BASE = "https://cdn.example"


class _SiteConfig:
    """The two keys the lookup reads, without the app's settings cache."""

    def __init__(self, **values: str) -> None:
        self._values = values

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)


_SC = _SiteConfig(storage_public_url=_BASE, podcast_cdn_version="v2")


async def _new_post(conn, slug: str, task_id: str | None):
    return await conn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at, metadata) "
        "VALUES (gen_random_uuid(), 'P', $1, 'b', 'published', NOW(), "
        "CASE WHEN $2::text IS NULL THEN '{}'::jsonb "
        "ELSE jsonb_build_object('pipeline_task_id', $2::text) END) "
        "RETURNING id",
        slug, task_id,
    )


async def _asset(conn, kind: str, *, post_id=None, task_id=None, path=None, url=None):
    await conn.execute(
        "INSERT INTO media_assets (post_id, task_id, type, source, storage_path, url) "
        "VALUES ($1, $2, $3, 'pipeline', $4, $5)",
        post_id, task_id, kind, path, url,
    )


async def _approval(conn, post_id, medium: str, status: str) -> None:
    await conn.execute(
        "INSERT INTO media_approvals (post_id, medium, status) VALUES ($1, $2, $3)",
        post_id, medium, status,
    )


async def test_task_preview_finds_linked_and_unlinked_renders(test_txn) -> None:
    """A published task's preview: the podcast was linked and approved (no URL
    stamped, so the delivery key), the video is still unlinked (shown, no URL)."""
    from poindexter.services.media_approval_service import get_preview_media

    post_id = await _new_post(test_txn, "preview-media-1089-a", "task-1089-a")
    await _asset(test_txn, "podcast", post_id=post_id, task_id="task-1089-a", path="/p.mp3")
    await _approval(test_txn, post_id, "podcast", "approved")
    await _asset(test_txn, "video", task_id="task-1089-a", path="/v.mp4")

    media = await get_preview_media(test_txn, site_config=_SC, task_id="task-1089-a")

    assert media == {"podcast": f"{_BASE}/podcast/v2/{post_id}.mp3", "video": None}


async def test_post_preview_reaches_the_task_keyed_render(test_txn) -> None:
    """The legacy post branch knows only the post; an unlinked render is found
    through the post's ``pipeline_task_id``."""
    from poindexter.services.media_approval_service import get_preview_media

    post_id = await _new_post(test_txn, "preview-media-1089-b", "task-1089-b")
    await _asset(test_txn, "video", task_id="task-1089-b", path="/v.mp4")

    media = await get_preview_media(test_txn, site_config=_SC, post_id=str(post_id))

    assert media == {"video": None}


async def test_task_preview_reaches_a_post_keyed_row(test_txn) -> None:
    """A pre-cutover row carries only the post; the task branch reaches it
    through the post that task became, and plays its stamped URL."""
    from poindexter.services.media_approval_service import get_preview_media

    post_id = await _new_post(test_txn, "preview-media-1089-c", "task-1089-c")
    stamped = f"{_BASE}/video/{post_id}.mp4"
    await _asset(test_txn, "video", post_id=post_id, url=stamped)
    await _approval(test_txn, post_id, "video", "approved")

    media = await get_preview_media(test_txn, site_config=_SC, task_id="task-1089-c")

    assert media == {"video": stamped}


async def test_unapproved_media_has_no_url_even_when_stamped(test_txn) -> None:
    """A rejected podcast keeps its stamped URL on the row, but the feed dropped
    it, so the preview must not play it either."""
    from poindexter.services.media_approval_service import get_preview_media

    post_id = await _new_post(test_txn, "preview-media-1089-d", "task-1089-d")
    await _asset(
        test_txn, "podcast", post_id=post_id, path="/p.mp3",
        url=f"{_BASE}/podcast/v2/{post_id}.mp3",
    )
    await _approval(test_txn, post_id, "podcast", "rejected")

    media = await get_preview_media(test_txn, site_config=_SC, post_id=str(post_id))

    assert media == {"podcast": None}


async def test_other_posts_shorts_and_empty_rows_are_ignored(test_txn) -> None:
    from poindexter.services.media_approval_service import get_preview_media

    post_id = await _new_post(test_txn, "preview-media-1089-e", "task-1089-e")
    other = await _new_post(test_txn, "preview-media-1089-f", "task-1089-f")
    await _asset(test_txn, "video_short", task_id="task-1089-e", path="/s.mp4")
    await _asset(test_txn, "podcast", task_id="task-1089-e")  # neither path nor URL
    await _asset(test_txn, "podcast", post_id=other, task_id="task-1089-f", path="/o.mp3")

    assert await get_preview_media(test_txn, site_config=_SC, task_id="task-1089-e") == {}
    assert await get_preview_media(test_txn, site_config=_SC, post_id=str(post_id)) == {}
