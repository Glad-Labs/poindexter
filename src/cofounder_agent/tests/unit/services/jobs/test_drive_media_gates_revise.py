"""Contract test for reject -> revise/regenerate via the driver (D1, #24).

When the operator ``revise``s a medium gate (state -> 'revising'), the
driver must: delete the stale ``media_assets`` row, re-trigger generation,
and reset the gate toward ``pending`` for re-review. A ``revising`` gate is
NOT surfaced by ``advance_workflow`` (it only returns 'pending' gates), so
the driver scans for it directly — and regenerates ONCE per tick.
"""

from __future__ import annotations

import pytest

from services.gates.post_approval_gates import (
    create_gates_for_post,
    get_gates_for_post,
    media_gate_sequence,
    revise_gate,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_approved_post(pool, *, media, slug):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM post_approval_gates")
        await conn.execute("DELETE FROM media_assets")
        await conn.execute("DELETE FROM posts")
        post_id = await conn.fetchval(
            "INSERT INTO posts (title, slug, status, content, media_to_generate) "
            "VALUES ($1, $2, 'approved', $3, $4::text[]) RETURNING id::text",
            f"Post {slug}", slug, "Body.", media,
        )
    await create_gates_for_post(pool, post_id, media_gate_sequence(media))
    return post_id


async def test_driver_regenerates_revising_medium(db_pool, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "services.podcast_service.generate_podcast_episode",
        lambda *a, **k: calls.append("podcast"),
    )

    post_id = await _make_approved_post(db_pool, media=["podcast"], slug="revise-test")

    # A podcast artifact already exists (it was generated + reviewed once).
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO media_assets (post_id, type, source) "
            "VALUES ($1::uuid, 'podcast', 'test')",
            post_id,
        )

    # Operator bounces the podcast back for a redo.
    await revise_gate(db_pool, post_id, "podcast", approver="op", feedback="redo the intro")

    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)

    # Regenerated exactly once this tick.
    assert calls == ["podcast"]
    # Stale artifact was deleted (so the fresh generation isn't shadowed by
    # the _artifact_exists guard next tick).
    remaining = await db_pool.fetchval(
        "SELECT count(*) FROM media_assets WHERE post_id::text = $1 AND type = 'podcast'",
        post_id,
    )
    assert remaining == 0
    # Gate reset toward pending for operator re-review.
    states = {g["gate_name"]: g["state"] for g in await get_gates_for_post(db_pool, post_id)}
    assert states["podcast"] == "pending"
