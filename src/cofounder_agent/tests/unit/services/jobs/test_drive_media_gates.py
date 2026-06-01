"""Contract tests for the media-gate driver (Glad-Labs/poindexter#24).

``drive_once(pool)`` walks every ``approved`` post's gate workflow:
- a pending medium gate with no artifact yet -> generate that medium
  (one medium per tick — single-fire, the _artifact_exists guard makes
  it idempotent);
- the ``final`` gate -> auto-approve (D2) then publish;
- all gates decided -> publish via publish_now.

Real-Postgres ``db_pool`` (session loop). Generators + publish distribution
are patched so we assert the driver's decisions, not real GPU/network work.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.gates.post_approval_gates import (
    approve_gate,
    create_gates_for_post,
    media_gate_sequence,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_approved_post(pool, *, media, approve_all, slug):
    """Wipe gate/post tables (isolation — the driver scans ALL approved
    posts, and db_pool only cleans niches) then create one approved post +
    its media gate sequence."""
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


@pytest.fixture
def approved_post_with_gates(db_pool):
    _n = {"i": 0}

    async def _factory(media, approve_all=False):
        _n["i"] += 1
        return await _make_approved_post(
            db_pool, media=media, approve_all=approve_all,
            slug=f"driver-test-{_n['i']}",
        )

    return _factory


def _patch_publish_distribution(monkeypatch):
    """Neutralize publish_now's distribution so a publish-on-clear test
    asserts only the status flip (no real social/devto/R2/ISR I/O)."""
    monkeypatch.setattr(
        "services.publish_service._spawn_background",
        lambda coro, name="": getattr(coro, "close", lambda: None)(),
    )
    monkeypatch.setattr(
        "services.static_export_service.export_post", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.revalidation_service.trigger_isr_revalidate", AsyncMock(return_value=True),
    )


async def test_driver_generates_pending_medium(db_pool, approved_post_with_gates, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "services.podcast_service.generate_podcast_episode",
        lambda *a, **k: calls.append("podcast"),
    )
    monkeypatch.setattr(
        "services.video_service.generate_video_episode",
        lambda *a, **k: calls.append("video"),
    )
    await approved_post_with_gates(["podcast", "video"])  # both pending, no artifacts

    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)

    assert calls == ["podcast"]  # only the first pending medium fires this tick


async def test_driver_publishes_when_all_gates_approved(db_pool, approved_post_with_gates, monkeypatch):
    _patch_publish_distribution(monkeypatch)
    post_id = await approved_post_with_gates(["podcast"], approve_all=True)

    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)

    status = await db_pool.fetchval("SELECT status FROM posts WHERE id::text = $1", post_id)
    assert status == "published"


async def test_text_only_post_auto_publishes(db_pool, approved_post_with_gates, monkeypatch):
    _patch_publish_distribution(monkeypatch)
    post_id = await approved_post_with_gates([])  # only the 'final' gate, pending

    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)

    status = await db_pool.fetchval("SELECT status FROM posts WHERE id::text = $1", post_id)
    assert status == "published"
