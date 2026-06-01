"""Contract tests for ``publish_now`` (Glad-Labs/poindexter#24).

``publish_now(pool, post_id)`` is the explicit publish+distribute entrypoint
the media-gate driver (and ``tasks publish``) use: it flips a gates-cleared
``approved`` post to ``published`` and fires distribution — but NEVER
generates media (that's the driver's pre-publish job). It refuses (no-op)
while any approval gate is still pending.

Real-Postgres ``db_pool`` (session loop). Distribution side-effects are
patched so we assert the status transition + gate-respecting behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.gates.post_approval_gates import (
    approve_gate,
    create_gates_for_post,
    media_gate_sequence,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_approved_post(pool, *, media, approve_all, slug):
    """Wipe gate/post tables (isolation — db_pool only cleans niches) then
    create one approved post + its media gate sequence."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM post_approval_gates")
        await conn.execute("DELETE FROM media_assets")
        await conn.execute("DELETE FROM posts")
        post_id = await conn.fetchval(
            "INSERT INTO posts (title, slug, status, content, media_to_generate) "
            "VALUES ($1, $2, 'approved', $3, $4::text[]) RETURNING id::text",
            f"Post {slug}", slug, "Body.", media,
        )
    gates = media_gate_sequence(media)
    await create_gates_for_post(pool, post_id, gates)
    if approve_all:
        for name in gates:
            await approve_gate(pool, post_id, name, approver="test")
    return post_id


def _distribution_patches():
    """Neutralize publish_now's fire-and-forget + awaited distribution so the
    test asserts the status flip without real network/R2 I/O."""
    return [
        patch("services.publish_service._spawn_background", lambda coro, name="": getattr(coro, "close", lambda: None)()),
        patch("services.static_export_service.export_post", AsyncMock(return_value=True)),
        patch("services.revalidation_service.trigger_isr_revalidate", AsyncMock(return_value=True)),
    ]


async def test_publish_now_flips_approved_post_to_published(db_pool):
    from services.publish_service import publish_now

    post_id = await _make_approved_post(
        db_pool, media=["podcast"], approve_all=True, slug="publish-now-ok",
    )
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _distribution_patches():
            stack.enter_context(p)
        result = await publish_now(db_pool, post_id)

    assert result["published"] is True
    status = await db_pool.fetchval("SELECT status FROM posts WHERE id::text = $1", post_id)
    assert status == "published"
    # Distribution fired (static export hook recorded).
    assert "static_export" in result["hooks"]


async def test_publish_now_refuses_when_gate_pending(db_pool):
    from services.publish_service import publish_now

    post_id = await _make_approved_post(
        db_pool, media=["podcast"], approve_all=False, slug="publish-now-pending",
    )
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _distribution_patches():
            stack.enter_context(p)
        result = await publish_now(db_pool, post_id)

    assert result["published"] is False
    assert result["reason"] == "pending_gates"
    status = await db_pool.fetchval("SELECT status FROM posts WHERE id::text = $1", post_id)
    assert status == "approved"  # untouched — still parked
