"""Contract: media generation is NOT a post-publish hook (poindexter#24).

Media-gen (podcast/video/short) is now the gate driver's job, fired
pre-publish per medium gate (services/jobs/drive_media_gates.py). The
publish path — both ``publish_post_from_task`` (initial publish) and
``fire_post_distribution_hooks`` (gate-clear re-fire) — must NOT schedule
media generation; by the time a post publishes its media already exists
(driver) or will be picked up by the 4h backfill jobs (legacy posts).

Detection seam: every fire-and-forget hook is dispatched via
``_spawn_background(coro, name=...)`` with a descriptive name. Patching
that lets us assert deterministically (no async draining) that no
``podcast_episode`` / ``video_episode`` / ``short_video`` task was scheduled,
while distribution hooks (social/devto/static/RSS) still are.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig

_TEST_SC = SiteConfig(initial_config={"site_url": "https://www.test-site.example.com"})
_TASK_ID = "11111111-1111-1111-1111-111111111111"
_POST_ID = "22222222-2222-2222-2222-222222222222"

_MEDIA_PREFIXES = ("podcast_episode", "video_episode", "short_video")


def _make_task() -> dict[str, Any]:
    return {
        "task_id": _TASK_ID,
        "topic": "No media on publish",
        "task_metadata": {
            "content": "## Heading\n\nBody.",
            "seo_description": "Excerpt.",
            "seo_keywords": ["test"],
            "featured_image_url": "https://example.com/image.jpg",
        },
        "result": {},
        "niche_slug": "ai-ml",
    }


def _make_db_service() -> Any:
    db = MagicMock()
    db.cloud_pool = None
    db.create_post = AsyncMock(side_effect=lambda data: MagicMock(id=_POST_ID))
    db.update_task_status = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.fetchval = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchval = AsyncMock(return_value=None)
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq_cm)
    db.pool = pool
    db._test_conn = conn
    return db


def _patches(spawned: list[str]):
    """Common patch set: hooks on, no gates, and every awaited/notifying
    side-effect stubbed so the full publish path runs without real I/O.
    ``_spawn_background`` records the task name and discards the coroutine."""

    def _fake_spawn(coro, name: str = "", *a, **k):
        spawned.append(name)
        try:
            coro.close()
        except (AttributeError, RuntimeError):
            pass

    return [
        patch("services.publish_service._should_run_post_publish_hooks", return_value=True),
        patch("services.publish_service._post_has_pending_gates", AsyncMock(return_value=False)),
        patch("services.publish_service.create_gates_for_post", AsyncMock(return_value=[])),
        patch("services.publish_service._spawn_background", _fake_spawn),
        patch("services.revalidation_service.trigger_isr_revalidate", AsyncMock(return_value=True)),
        patch("services.static_export_service.export_post", AsyncMock(return_value=True)),
        patch("services.webhook_delivery_service.emit_webhook_event", AsyncMock(return_value=None)),
        patch("services.integrations.operator_notify.notify_operator", AsyncMock(return_value=None)),
    ]


@pytest.mark.asyncio
async def test_publish_does_not_generate_media_for_post() -> None:
    """``publish_post_from_task`` (full publish, worker hooks on, no gates,
    media wanted) must not schedule any podcast/video/short generation."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    spawned: list[str] = []

    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patches(spawned):
            stack.enter_context(p)
        stack.enter_context(
            patch("services.publish_service.resolve_media_to_generate",
                  AsyncMock(return_value=["podcast", "video"])),
        )
        result = await publish_post_from_task(
            db, _make_task(), _TASK_ID,
            publisher="operator-test",
            stage_only=False,
            draft_mode=False,
            site_config=_TEST_SC,
        )

    assert result.success, f"publish failed: {result.error}"
    media_tasks = [n for n in spawned if n.startswith(_MEDIA_PREFIXES)]
    assert media_tasks == [], (
        f"publish scheduled media generation (driver's job now): {media_tasks}"
    )


@pytest.mark.asyncio
async def test_fire_distribution_hooks_does_not_generate_media() -> None:
    """``fire_post_distribution_hooks`` (gate-clear re-fire) must distribute
    (social/devto/static) but NOT (re)generate media."""
    from services.publish_service import fire_post_distribution_hooks

    db = _make_db_service()
    db._test_conn.fetchrow = AsyncMock(return_value={
        "id": _POST_ID,
        "title": "Gate-cleared post",
        "slug": "gate-cleared-post-2222",
        "content": "Body.",
        "excerpt": "Excerpt.",
        "seo_keywords": "test",
        "media_to_generate": ["podcast", "video"],
    })

    spawned: list[str] = []
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patches(spawned):
            stack.enter_context(p)
        result = await fire_post_distribution_hooks(db, _POST_ID, site_config=_TEST_SC)

    assert result.get("fired") is True
    media_tasks = [n for n in spawned if n.startswith(_MEDIA_PREFIXES)]
    assert media_tasks == [], (
        f"gate-clear re-fire scheduled media generation: {media_tasks}"
    )
    # Distribution hooks still fired (proves we removed only media-gen).
    assert any(n.startswith("social_posts") for n in spawned), (
        f"expected social distribution to still fire; saw: {spawned}"
    )
