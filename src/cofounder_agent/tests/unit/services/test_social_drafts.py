"""Unit tests for SocialDraftsService and PostizClient (offline)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.social_drafts import SocialDraftsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(fetchval=None, fetchrow=None, execute=None, fetch=None):
    conn = AsyncMock()
    conn.fetchval.return_value = fetchval
    conn.fetchrow.return_value = fetchrow
    conn.execute.return_value = execute
    conn.fetch.return_value = fetch or []
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _make_site_config(settings: dict[str, str]) -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": settings.get(key, default)
    # get_secret is async (hits DB for is_secret rows); mirror it from the
    # same dict so tests can stub postiz_api_key alongside plain settings.
    sc.get_secret = AsyncMock(
        side_effect=lambda key, default="": settings.get(key, default)
    )
    return sc


# ---------------------------------------------------------------------------
# create_draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_draft_returns_id():
    pool, conn = _make_pool(fetchval="abc-123")
    svc = SocialDraftsService()
    result = await svc.create_draft(
        pipeline_task_id="task-1",
        platform="twitter",
        content="hello world",
        platform_config={},
        pool=pool,
    )
    assert result == "abc-123"
    conn.fetchval.assert_called_once()


@pytest.mark.asyncio
async def test_create_draft_serialises_platform_config():
    pool, conn = _make_pool(fetchval="id-999")
    svc = SocialDraftsService()
    config = {"subreddit": "r/LocalLLaMA"}
    await svc.create_draft(
        pipeline_task_id="task-2",
        platform="reddit",
        content="check this out",
        platform_config=config,
        pool=pool,
    )
    call_args = conn.fetchval.call_args
    # 4th positional arg is the serialised config
    passed_config = call_args[0][4]
    assert json.loads(passed_config) == config


# ---------------------------------------------------------------------------
# reject_draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reject_draft_sets_status():
    pool, _conn = _make_pool()
    svc = SocialDraftsService()
    await svc.reject_draft("draft-1", pool)
    _conn.execute.assert_called_once()
    sql = _conn.execute.call_args[0][0]
    assert "rejected" in sql.lower()


# ---------------------------------------------------------------------------
# approve_draft — row not found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_draft_not_found():
    pool, _ = _make_pool(fetchrow=None)
    sc = _make_site_config({})
    svc = SocialDraftsService()
    result = await svc.approve_draft("nonexistent", pool, sc)
    assert result["success"] is False
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# approve_draft — wrong status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_draft_wrong_status():
    row = {
        "id": "d1", "platform": "twitter",
        "content": "hello", "platform_config": "{}",
        "status": "posted",
    }
    pool, _ = _make_pool(fetchrow=row)
    sc = _make_site_config({})
    svc = SocialDraftsService()
    result = await svc.approve_draft("d1", pool, sc)
    assert result["success"] is False
    assert "posted" in result["error"]


# ---------------------------------------------------------------------------
# approve_draft — missing integration ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_draft_missing_integration_id():
    row = {
        "id": "d2", "platform": "linkedin",
        "content": "some content", "platform_config": "{}",
        "status": "pending",
    }
    pool, _ = _make_pool(fetchrow=row)
    # site_config returns empty string for all keys
    sc = _make_site_config({})
    svc = SocialDraftsService()
    result = await svc.approve_draft("d2", pool, sc)
    assert result["success"] is False
    assert "integration UUID" in result["error"] or "not configured" in result["error"]


# ---------------------------------------------------------------------------
# approve_draft — success path (PostizClient mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_draft_success():
    row = {
        "id": "d3", "platform": "twitter",
        "content": "nice post", "platform_config": "{}",
        "status": "pending",
    }
    pool, conn = _make_pool(fetchrow=row)
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
    })
    with patch(
        "services.social_drafts.PostizClient.create_post",
        new_callable=AsyncMock,
        return_value={"success": True, "post_id": "pz-1", "error": None},
    ):
        svc = SocialDraftsService()
        result = await svc.approve_draft("d3", pool, sc)

    assert result["success"] is True
    assert result.get("postiz_post_id") == "pz-1"


@pytest.mark.asyncio
async def test_approve_draft_passes_api_key_to_postiz():
    """The Postiz org API key (secret) must be forwarded to PostizClient —
    the public API rejects unauthenticated requests with 401."""
    row = {
        "id": "d4", "platform": "twitter",
        "content": "authed post", "platform_config": "{}",
        "status": "pending",
    }
    pool, _conn = _make_pool(fetchrow=row)
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
        "postiz_api_key": "org-secret-key",
    })
    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "pz-2", "error": None}
        )
        svc = SocialDraftsService()
        await svc.approve_draft("d4", pool, sc)

    mock_cls.assert_called_once_with(
        base_url="http://postiz:3000", api_key="org-secret-key"
    )


@pytest.mark.asyncio
async def test_approve_draft_sets_made_with_ai_for_x():
    """X posts carry the made_with_ai disclosure flag (social_x_made_with_ai)."""
    row = {
        "id": "d7", "platform": "twitter",
        "content": "ai post", "platform_config": "{}",
        "status": "pending",
    }
    pool, _conn = _make_pool(fetchrow=row)
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
        "social_x_made_with_ai": "true",
    })
    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "p", "error": None}
        )
        svc = SocialDraftsService()
        await svc.approve_draft("d7", pool, sc)

    settings = mock_cls.return_value.create_post.call_args.kwargs["platform_settings"]
    assert settings["made_with_ai"] is True


@pytest.mark.asyncio
async def test_approve_draft_made_with_ai_disabled_by_setting():
    """social_x_made_with_ai=false flips the flag off."""
    row = {
        "id": "d8", "platform": "twitter",
        "content": "human post", "platform_config": "{}",
        "status": "pending",
    }
    pool, _conn = _make_pool(fetchrow=row)
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
        "social_x_made_with_ai": "false",
    })
    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "p", "error": None}
        )
        svc = SocialDraftsService()
        await svc.approve_draft("d8", pool, sc)

    settings = mock_cls.return_value.create_post.call_args.kwargs["platform_settings"]
    assert settings["made_with_ai"] is False


@pytest.mark.asyncio
async def test_approve_draft_bluesky_maps_type_and_integration():
    """Bluesky drafts resolve to platform_type 'bluesky' + the bluesky
    integration id, and carry no X-only made_with_ai flag."""
    row = {
        "id": "d9", "platform": "bluesky",
        "content": "skeet", "platform_config": "{}",
        "status": "pending",
    }
    pool, _conn = _make_pool(fetchrow=row)
    sc = _make_site_config({
        "postiz_integration_id_bluesky": "uuid-bsky",
        "postiz_api_url": "http://postiz:3000",
    })
    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "p", "error": None}
        )
        svc = SocialDraftsService()
        result = await svc.approve_draft("d9", pool, sc)

    assert result["success"] is True
    kwargs = mock_cls.return_value.create_post.call_args.kwargs
    assert kwargs["integration_id"] == "uuid-bsky"
    assert kwargs["platform_type"] == "bluesky"
    assert "made_with_ai" not in kwargs["platform_settings"]


# ---------------------------------------------------------------------------
# backfill_post_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_post_id_executes_update():
    pool, conn = _make_pool()
    svc = SocialDraftsService()
    await svc.backfill_post_id("task-99", "post-42", pool)
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "post_id" in sql.lower()
    assert "pipeline_task_id" in sql.lower()


# ---------------------------------------------------------------------------
# edit_draft — content only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_draft_content_only():
    pool, conn = _make_pool()
    svc = SocialDraftsService()
    await svc.edit_draft("d5", "new content", None, pool)
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "content" in sql.lower()
    # platform_config should NOT appear in the SQL when not provided
    assert "platform_config" not in sql.lower()


@pytest.mark.asyncio
async def test_edit_draft_with_platform_config():
    pool, conn = _make_pool()
    svc = SocialDraftsService()
    await svc.edit_draft("d6", "updated", {"subreddit": "r/Python"}, pool)
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "platform_config" in sql.lower()
