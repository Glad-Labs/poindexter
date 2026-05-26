"""Contract tests for ``publish_post_from_task(stage_only=True)``.

Pins the 2026-05-26 fix for the approve → schedule batch bridge:
``scheduling_service.schedule_batch`` queries
``posts.status IN ('approved', 'awaiting_approval') AND
published_at IS NULL`` for eligible posts. Nothing in the historical
pipeline produced posts at status='approved' — the approve_task
handler with default ``auto_publish=False`` left the pipeline_task
at status='approved' but never created the posts row. Schedule batch
returned ``No eligible posts to schedule`` even though pipeline_tasks
showed two approved.

The fix adds a ``stage_only=True`` path to ``publish_post_from_task``
that creates the posts row at ``status='approved'`` with
``published_at=NULL`` and skips every publish-only side effect
(distribution recording, revalidation, social-queue, cloud sync,
post.published webhook). The approve_task handler now calls this
path on approve-without-auto_publish.

These tests pin the contract — a future refactor that flips the
status to anything else, or that re-introduces the publish-only
side-effects on the stage path, fails here instead of leaving the
operator with another broken bridge.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task() -> dict[str, Any]:
    return {
        "task_id": "11111111-1111-1111-1111-111111111111",
        "topic": "Test post for stage_only contract",
        "task_metadata": {
            "content": "## Heading\n\nBody.",
            "seo_description": "Test excerpt.",
            "seo_keywords": ["test"],
            "featured_image_url": "https://example.com/image.jpg",
        },
        "result": {},
        "category": "technology",
        "primary_keyword": "test",
        "niche_slug": "",
    }


def _make_db_service() -> Any:
    """Build a DatabaseService stub that records create_post + update_task_status."""
    # publish_post_from_task does `getattr(db_service, "cloud_pool", None)
    # or db_service.pool` — set cloud_pool to None explicitly so the
    # default-MagicMock fallthrough doesn't shadow our async-configured
    # pool.
    db = MagicMock()
    db.cloud_pool = None
    db.create_post = AsyncMock(
        side_effect=lambda data: MagicMock(id="22222222-2222-2222-2222-222222222222"),
    )
    db.update_task_status = AsyncMock(return_value=None)

    # Pool needs awaitable .fetchrow / .execute on the top-level pool
    # AND on the async-context-managed connection. publish_service uses
    # both shapes.
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.fetchval = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchval = AsyncMock(return_value=None)
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq_cm)
    db.pool = pool
    return db


@pytest.mark.asyncio
async def test_stage_only_creates_post_at_status_approved() -> None:
    """The created posts row must have status='approved' (not 'published',
    not 'draft', not 'awaiting_gates'). This is the seam schedule_batch
    queries — any other status and the post is invisible to scheduling."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    captured: dict[str, Any] = {}

    async def _record_create_post(data: dict[str, Any]) -> Any:
        captured["post_data"] = data
        return MagicMock(id="22222222-2222-2222-2222-222222222222")

    db.create_post = _record_create_post

    # Patch the internal_link_coherence import so we don't pull in the
    # full pipeline. Stub stages that publish_post_from_task calls.
    with patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
        )

    assert result.success, f"stage_only path failed: {result.error}"
    assert result.staged is True
    assert captured["post_data"]["status"] == "approved", (
        f"Expected status='approved' for schedule_batch eligibility, "
        f"got {captured['post_data']['status']!r}"
    )
    assert "published_at" not in captured["post_data"] or captured["post_data"]["published_at"] is None, (
        "stage_only posts must have published_at NULL — schedule_batch's "
        "WHERE clause filters published_at IS NULL"
    )


@pytest.mark.asyncio
async def test_stage_only_skips_distributed_at_stamp() -> None:
    """Staged posts must NOT be marked distributed — the RSS feed and
    static export gate on distributed_at. A staged post is invisible
    until scheduled_publisher promotes it."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    captured: dict[str, Any] = {}

    async def _record_create_post(data: dict[str, Any]) -> Any:
        captured["post_data"] = data
        return MagicMock(id="22222222-2222-2222-2222-222222222222")

    db.create_post = _record_create_post

    with patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
        )

    assert captured["post_data"].get("distributed_at") is None, (
        "stage_only posts must NOT have distributed_at set — leaks staged "
        "content into the RSS feed and /posts static export before it's "
        "actually scheduled to publish"
    )


@pytest.mark.asyncio
async def test_stage_only_leaves_task_at_status_approved_not_published() -> None:
    """The pipeline_task must stay at status='approved' (the state the
    approve_task handler put it in). Flipping to 'published' would
    confuse downstream consumers + break the schedule_batch flow that
    expects the task to still be in the approval-staged pool."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    captured_status_updates: list[tuple[str, str]] = []

    async def _record_status_update(task_id: str, status: str, *args, **kwargs) -> None:
        captured_status_updates.append((task_id, status))

    db.update_task_status = _record_status_update

    with patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
        )

    # Should have exactly one status update — to 'approved', not 'published'.
    statuses = [s for _tid, s in captured_status_updates]
    assert "published" not in statuses, (
        f"stage_only flipped task to 'published' — should stay 'approved' "
        f"for the staging pool. Saw: {statuses}"
    )
    assert "approved" in statuses, (
        f"stage_only did not update task to 'approved'. Saw: {statuses}"
    )


@pytest.mark.asyncio
async def test_stage_only_and_draft_mode_are_mutually_exclusive() -> None:
    """Both flags flip status away from the default — combining them
    is ambiguous. Caller error should surface loudly per
    feedback_no_silent_defaults."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()

    with pytest.raises(ValueError, match="mutually exclusive"):
        await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            draft_mode=True,
        )


@pytest.mark.asyncio
async def test_publish_result_exposes_staged_field() -> None:
    """The staged field on PublishResult lets callers distinguish a
    staged post (status='approved') from a live publish. Without it
    the approve handler can't tell the difference without re-fetching
    the row, and downstream consumers (operator notify, social queue)
    would treat both as published."""
    from services.publish_service import PublishResult

    live = PublishResult(success=True, post_id="x", post_slug="y", published_url="/posts/y")
    staged = PublishResult(success=True, post_id="x", post_slug="y", published_url="/posts/y", staged=True)
    assert live.staged is False
    assert staged.staged is True
    assert live.to_dict()["staged"] is False
    assert staged.to_dict()["staged"] is True
