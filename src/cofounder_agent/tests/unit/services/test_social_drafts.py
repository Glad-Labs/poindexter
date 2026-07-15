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


def _draft_row(**overrides) -> dict:
    """A social_post_drafts row as approve_draft's first fetchrow returns it."""
    row = {
        "id": "d-1", "platform": "twitter", "content": "copy",
        "platform_config": "{}", "status": "pending",
        "pipeline_task_id": "task-1", "post_id": None,
    }
    row.update(overrides)
    return row


def _post_row(**overrides) -> dict:
    """A posts row as approve_draft's link-resolution fetchrow returns it."""
    row = {"id": "post-1", "slug": "real-slug-abcd1234", "status": "published"}
    row.update(overrides)
    return row


def _list_row(**overrides) -> dict:
    """A full social_post_drafts row as list_drafts' SELECT d.*, ... returns
    it — includes every column _row_to_dataclass reads, unlike _draft_row
    (which only carries approve_draft's narrower first-fetchrow columns)."""
    row = {
        "id": "d-1", "pipeline_task_id": "task-1", "post_id": None,
        "platform": "twitter", "content": "copy", "platform_config": "{}",
        "status": "pending", "postiz_post_id": None, "error": None,
        "retry_count": 0, "last_retry_at": None, "created_at": None,
        "approved_at": None, "posted_at": None, "resolved_post_status": None,
        "article_title": None, "resolved_post_id": None,
    }
    row.update(overrides)
    return row


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
# create_draft — idempotency per (pipeline_task_id, platform, subreddit).
# Regression (poindexter#833): finalize re-runs (preview_gate regen loops,
# checkpoint restore, task retry) re-inserted a fresh draft per platform on
# every pass — task 511012cc stacked 3 identical Bluesky drafts and all three
# were posted. create_draft must skip when an active (pending/failed) or
# already-posted draft exists for the key, returning the existing id.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_draft_skips_when_active_or_posted_draft_exists():
    """Guarded insert returns no id (key already taken) → create_draft fetches
    and returns the existing draft's id instead of stacking a duplicate."""
    pool, conn = _make_pool()
    conn.fetchval.side_effect = [None, "existing-1"]
    with patch("services.social_drafts.SOCIAL_DRAFT_CREATED_TOTAL") as metric:
        svc = SocialDraftsService()
        result = await svc.create_draft(
            pipeline_task_id="task-1",
            platform="bluesky",
            content="same promo again",
            platform_config={},
            pool=pool,
        )
    assert result == "existing-1"
    assert conn.fetchval.call_count == 2
    metric.labels.assert_not_called()  # nothing was created


@pytest.mark.asyncio
async def test_create_draft_increments_metric_only_on_real_insert():
    pool, conn = _make_pool(fetchval="new-1")
    with patch("services.social_drafts.SOCIAL_DRAFT_CREATED_TOTAL") as metric:
        svc = SocialDraftsService()
        result = await svc.create_draft(
            pipeline_task_id="task-1",
            platform="twitter",
            content="fresh copy",
            platform_config={},
            pool=pool,
        )
    assert result == "new-1"
    conn.fetchval.assert_called_once()
    metric.labels.assert_called_once_with(platform="twitter")


@pytest.mark.asyncio
async def test_create_draft_sql_dedups_on_task_platform_subreddit():
    """The INSERT carries the dedup guard: NOT EXISTS over
    pending/failed/posted rows for the natural key, with the partial-index
    ON CONFLICT backstop, and the subreddit key as its own parameter."""
    pool, conn = _make_pool(fetchval="id-1")
    svc = SocialDraftsService()
    await svc.create_draft(
        pipeline_task_id="task-2",
        platform="reddit",
        content="reddit copy",
        platform_config={"subreddit": "r/LocalLLaMA"},
        pool=pool,
    )
    sql = conn.fetchval.call_args[0][0].lower()
    assert "not exists" in sql
    assert "'pending'" in sql and "'failed'" in sql and "'posted'" in sql
    assert "on conflict" in sql and "do nothing" in sql
    assert "coalesce(platform_config->>'subreddit', '')" in sql
    # Subreddit key rides as the 5th parameter ($5); '' for non-reddit drafts.
    assert conn.fetchval.call_args[0][5] == "r/LocalLLaMA"


@pytest.mark.asyncio
async def test_create_draft_subreddit_key_empty_for_text_platforms():
    pool, conn = _make_pool(fetchval="id-2")
    svc = SocialDraftsService()
    await svc.create_draft(
        pipeline_task_id="task-3",
        platform="twitter",
        content="tweet",
        platform_config={},
        pool=pool,
    )
    assert conn.fetchval.call_args[0][5] == ""


# ---------------------------------------------------------------------------
# existing_draft_keys — the atom's pre-LLM filter seam
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_draft_keys_returns_platform_subreddit_tuples():
    pool, conn = _make_pool()
    conn.fetch.return_value = [
        {"platform": "twitter", "subreddit": ""},
        {"platform": "reddit", "subreddit": "r/LocalLLaMA"},
    ]
    svc = SocialDraftsService()
    keys = await svc.existing_draft_keys("task-1", pool)
    assert keys == {("twitter", ""), ("reddit", "r/LocalLLaMA")}
    sql = conn.fetch.call_args[0][0].lower()
    # Active + posted drafts block re-creation; rejected ones do not (an
    # operator reject followed by a regen loop legitimately gets fresh copy).
    assert "'pending'" in sql and "'failed'" in sql and "'posted'" in sql
    assert "'rejected'" not in sql


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
# list_drafts — post_status (resolved via post_id or task metadata), so
# callers (the console's action inbox) can tell a genuinely-approvable draft
# from one that would 409 (post not published yet — see approve_draft's
# post-link gate). Regression: the inbox surfaced drafts as "up for
# approval" before their post was approved, so every click 409'd.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_drafts_includes_resolved_post_status():
    pool, _conn = _make_pool(fetch=[_list_row(resolved_post_status="published")])
    svc = SocialDraftsService()
    drafts = await svc.list_drafts(None, None, None, pool)
    assert drafts[0].post_status == "published"


@pytest.mark.asyncio
async def test_list_drafts_post_status_none_when_no_post_yet():
    pool, _conn = _make_pool(fetch=[_list_row(resolved_post_status=None)])
    svc = SocialDraftsService()
    drafts = await svc.list_drafts(None, None, None, pool)
    assert drafts[0].post_status is None


@pytest.mark.asyncio
async def test_list_drafts_query_resolves_post_by_id_or_task_metadata():
    """The join must resolve the same way approve_draft's own _resolve_post
    does: post_id when linked, else the latest posts row matching
    pipeline_task_id metadata — one source of truth for both code paths."""
    pool, conn = _make_pool(fetch=[])
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool)
    sql = conn.fetch.call_args[0][0].lower()
    assert "p.id = d.post_id" in sql
    assert "metadata->>'pipeline_task_id'" in sql


@pytest.mark.asyncio
async def test_list_drafts_title_from_post_when_available():
    """Once a posts row exists, its title (possibly revised post-writing)
    wins over the task's original topic."""
    pool, _conn = _make_pool(
        fetch=[_list_row(article_title="Real Published Title", resolved_post_id="post-99")]
    )
    svc = SocialDraftsService()
    drafts = await svc.list_drafts(None, None, None, pool)
    assert drafts[0].title == "Real Published Title"
    assert drafts[0].resolved_post_id == "post-99"


@pytest.mark.asyncio
async def test_list_drafts_title_falls_back_to_task_topic():
    """Before a posts row exists (task still awaiting_approval), the SQL
    COALESCEs to pipeline_tasks.topic — resolved_post_id stays None."""
    pool, _conn = _make_pool(
        fetch=[_list_row(article_title="Original Task Topic", resolved_post_id=None)]
    )
    svc = SocialDraftsService()
    drafts = await svc.list_drafts(None, None, None, pool)
    assert drafts[0].title == "Original Task Topic"
    assert drafts[0].resolved_post_id is None


@pytest.mark.asyncio
async def test_list_drafts_query_joins_pipeline_tasks_for_title():
    """Locks in the join + COALESCE the title/id resolution depends on —
    same rigor as the existing post-resolution join assertion above."""
    pool, conn = _make_pool(fetch=[])
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool)
    sql = conn.fetch.call_args[0][0].lower()
    assert "join pipeline_tasks" in sql
    assert "pt.task_id = d.pipeline_task_id" in sql
    assert "coalesce(rp.title, pt.topic)" in sql
    assert "coalesce(d.post_id, rp.id)" in sql


# ---------------------------------------------------------------------------
# cancel_orphaned_for_rejected_tasks — the reject → social-draft cascade reaper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_orphaned_returns_rowcount_from_command_tag():
    pool, _conn = _make_pool(execute="UPDATE 2")
    svc = SocialDraftsService()
    n = await svc.cancel_orphaned_for_rejected_tasks(pool)
    assert n == 2


@pytest.mark.asyncio
async def test_cancel_orphaned_zero_on_empty_tag():
    # No rows matched → asyncpg tag "UPDATE 0" → 0 cancelled.
    pool, _conn = _make_pool(execute="UPDATE 0")
    svc = SocialDraftsService()
    assert await svc.cancel_orphaned_for_rejected_tasks(pool) == 0


@pytest.mark.asyncio
async def test_cancel_orphaned_targets_pending_failed_and_terminal_only():
    # The reaper must only touch pending/failed drafts of terminally-rejected
    # tasks — never 'posted' drafts (Case-2 live posts) and never valid
    # 'approved'/'awaiting_approval' content awaiting publish.
    pool, _conn = _make_pool(execute="UPDATE 0")
    svc = SocialDraftsService()
    await svc.cancel_orphaned_for_rejected_tasks(pool)
    _conn.execute.assert_called_once()
    sql = _conn.execute.call_args[0][0].lower()
    statuses = _conn.execute.call_args[0][1]
    assert "set status = 'rejected'" in sql
    assert "d.status in ('pending', 'failed')" in sql
    assert "posted" not in sql  # posted drafts are never cancelled
    # terminal-reject task statuses passed as the bound array; excludes
    # rejected_retry (re-runs) and approved/awaiting_approval (awaiting publish).
    assert set(statuses) == {"rejected_final", "rejected", "dismissed"}
    assert "rejected_retry" not in statuses
    assert "approved" not in statuses


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
    row = _draft_row(id="d2", platform="linkedin", content="some content")
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [row, _post_row()]
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
    row = _draft_row(id="d3", content="nice post")
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [row, _post_row()]
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
    row = _draft_row(id="d4", content="authed post")
    pool, _conn = _make_pool()
    _conn.fetchrow.side_effect = [row, _post_row()]
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
    row = _draft_row(id="d7", content="ai post")
    pool, _conn = _make_pool()
    _conn.fetchrow.side_effect = [row, _post_row()]
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
    row = _draft_row(id="d8", content="human post")
    pool, _conn = _make_pool()
    _conn.fetchrow.side_effect = [row, _post_row()]
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
    row = _draft_row(id="d9", platform="bluesky", content="skeet")
    pool, _conn = _make_pool()
    _conn.fetchrow.side_effect = [row, _post_row()]
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


# ---------------------------------------------------------------------------
# approve_draft — post-link gate + URL repair (social-drafts linking bug).
# approve_draft is the last gate before content goes public: it must refuse
# to push a draft whose blog post is not live, and must guarantee the URL in
# the pushed copy is the post's real live URL.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_draft_blocked_when_post_missing():
    """No posts row for the draft's pipeline_task_id → refuse to post (the
    promo link would 404) and leave the draft pending so it can be approved
    again once the post is live. Regression: two drafts for task f3a71ef6
    were pushed to X/Bluesky on 2026-07-10 while the post was still
    awaiting_approval — with a dead link."""
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [_draft_row(), None]
    sc = _make_site_config({"postiz_integration_id_twitter": "uuid-abc"})

    with patch("services.social_drafts.PostizClient") as mock_cls:
        svc = SocialDraftsService()
        result = await svc.approve_draft("d-1", pool, sc)

    assert result["success"] is False
    assert "publish" in result["error"].lower()
    mock_cls.assert_not_called()
    # Block ≠ failure: no writes at all (status stays pending — NOT failed,
    # so RetryFailedSocialDraftsJob doesn't burn retries on it).
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_approve_draft_blocked_when_post_not_live():
    """A staged post (status='approved', awaiting scheduled_publisher) is not
    live yet — pushing social now would promote a 404."""
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [_draft_row(), _post_row(status="approved")]
    sc = _make_site_config({"postiz_integration_id_twitter": "uuid-abc"})

    with patch("services.social_drafts.PostizClient") as mock_cls:
        svc = SocialDraftsService()
        result = await svc.approve_draft("d-1", pool, sc)

    assert result["success"] is False
    assert "approved" in result["error"]  # names the current post status
    mock_cls.assert_not_called()
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_approve_draft_repairs_dead_url():
    """The empty-slug URL a pre-fix draft baked in (`.../posts/ `) is
    rewritten to the live post URL before the copy reaches Postiz, and the
    repaired content is persisted back to the row."""
    draft = _draft_row(
        content="Unlock local LLM speed! https://www.gladlabs.io/posts/ #vram",
        pipeline_task_id="f3a71ef6-27a9-47db-ad3c-426d7fc35a2f",
    )
    post = _post_row(slug="why-vram-bandwidth-matters-f3a71ef6")
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [draft, post]
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
        "site_url": "https://gladlabs.io",
    })

    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "pz-9", "error": None}
        )
        svc = SocialDraftsService()
        result = await svc.approve_draft("d-1", pool, sc)

    assert result["success"] is True
    pushed = mock_cls.return_value.create_post.call_args.kwargs["content"]
    assert (
        "https://gladlabs.io/posts/why-vram-bandwidth-matters-f3a71ef6" in pushed
    )
    assert "gladlabs.io/posts/ " not in pushed
    assert pushed.endswith("#vram")
    # The repair + post_id link is persisted on the draft row.
    link_sqls = [c[0][0].lower() for c in conn.execute.call_args_list]
    assert any("content" in s and "post_id" in s for s in link_sqls)


@pytest.mark.asyncio
async def test_approve_draft_appends_url_when_missing():
    """Copy that somehow carries no post URL gets the live URL appended —
    a promo post without a link is useless."""
    draft = _draft_row(content="Great new read on VRAM bandwidth!")
    post = _post_row(slug="why-vram-bandwidth-matters-f3a71ef6")
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [draft, post]
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
        "site_url": "https://gladlabs.io",
    })

    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "pz-10", "error": None}
        )
        svc = SocialDraftsService()
        await svc.approve_draft("d-1", pool, sc)

    pushed = mock_cls.return_value.create_post.call_args.kwargs["content"]
    assert pushed.endswith(
        "https://gladlabs.io/posts/why-vram-bandwidth-matters-f3a71ef6"
    )


# ---------------------------------------------------------------------------
# find_posts_missing_social_coverage / reconcile_missing_drafts (#863) —
# reconciliation sweep for the swallowed-exception bug: a published post
# whose social.generate_drafts run silently produced zero drafts gets a
# fresh regeneration attempt, gated on the atom's own idempotency guard.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_posts_missing_coverage_queries_published_within_window():
    pool, conn = _make_pool(fetch=[])
    svc = SocialDraftsService()
    await svc.find_posts_missing_social_coverage(pool, lookback_days=14)
    sql = conn.fetch.call_args[0][0].lower()
    assert "p.status = 'published'" in sql
    assert "social.generate_drafts" in sql
    assert conn.fetch.call_args[0][1] == 14


@pytest.mark.asyncio
async def test_find_posts_missing_coverage_walks_nodes_key_not_bare_array():
    """Regression: pipeline_templates.graph_def is stored as a top-level
    OBJECT (keys: name/edges/entry/nodes/description — column default is
    '{}'::jsonb, an object), not a bare array. jsonb_array_elements(graph_def)
    directly raises 'cannot extract elements from an object' against the
    real schema — verified against a live DB during development. The node
    list lives under graph_def->'nodes'."""
    pool, conn = _make_pool(fetch=[])
    svc = SocialDraftsService()
    await svc.find_posts_missing_social_coverage(pool, lookback_days=14)
    sql = conn.fetch.call_args[0][0].lower()
    assert "jsonb_array_elements(tpl.graph_def->'nodes')" in sql


@pytest.mark.asyncio
async def test_find_posts_missing_coverage_returns_atom_input_fields():
    row = {
        "pipeline_task_id": "task-1", "title": "T", "slug": "t-slug",
        "content": "body", "excerpt": "ex", "seo_description": "sd",
        "seo_keywords": "a,b",
    }
    pool, _conn = _make_pool(fetch=[row])
    svc = SocialDraftsService()
    result = await svc.find_posts_missing_social_coverage(pool, lookback_days=14)
    assert result == [row]


@pytest.mark.asyncio
async def test_reconcile_calls_atom_and_counts_new_drafts(monkeypatch):
    """A post missing bluesky coverage: existing_draft_keys grows by one
    after the atom runs → drafts_created reflects the real delta, not just
    'the atom ran'."""
    pool, _conn = _make_pool()
    svc = SocialDraftsService()
    monkeypatch.setattr(
        svc,
        "find_posts_missing_social_coverage",
        AsyncMock(return_value=[
            {"pipeline_task_id": "task-1", "title": "T", "slug": "s",
             "content": "c", "excerpt": "e", "seo_description": "sd",
             "seo_keywords": "kw"},
        ]),
    )
    monkeypatch.setattr(
        svc,
        "existing_draft_keys",
        AsyncMock(side_effect=[set(), {("bluesky", "")}]),
    )
    generate = AsyncMock(return_value={})
    monkeypatch.setattr("modules.content.api.generate_social_drafts", generate)

    sc = _make_site_config({})
    result = await svc.reconcile_missing_drafts(pool, sc, lookback_days=14)

    generate.assert_awaited_once()
    state = generate.await_args.args[0]
    assert state["task_id"] == "task-1"
    assert state["title"] == "T"
    assert state["post_slug"] == "s"
    assert state["site_config"] is sc
    assert result["candidates_checked"] == 1
    assert result["drafts_created"] == 1
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_reconcile_skips_candidates_without_task_id(monkeypatch):
    pool, _conn = _make_pool()
    svc = SocialDraftsService()
    monkeypatch.setattr(
        svc,
        "find_posts_missing_social_coverage",
        AsyncMock(return_value=[{"pipeline_task_id": None, "title": "T"}]),
    )
    generate = AsyncMock()
    monkeypatch.setattr("modules.content.api.generate_social_drafts", generate)

    result = await svc.reconcile_missing_drafts(pool, _make_site_config({}), 14)

    generate.assert_not_awaited()
    assert result["candidates_checked"] == 0


@pytest.mark.asyncio
async def test_reconcile_collects_per_task_errors_and_continues(monkeypatch):
    """One task's regeneration raising must not abort the reconciliation
    sweep for the remaining candidates."""
    pool, _conn = _make_pool()
    svc = SocialDraftsService()
    monkeypatch.setattr(
        svc,
        "find_posts_missing_social_coverage",
        AsyncMock(return_value=[
            {"pipeline_task_id": "task-1", "title": "A", "slug": "a"},
            {"pipeline_task_id": "task-2", "title": "B", "slug": "b"},
        ]),
    )
    monkeypatch.setattr(svc, "existing_draft_keys", AsyncMock(return_value=set()))
    generate = AsyncMock(side_effect=[RuntimeError("boom"), {}])
    monkeypatch.setattr("modules.content.api.generate_social_drafts", generate)

    result = await svc.reconcile_missing_drafts(pool, _make_site_config({}), 14)

    assert generate.await_count == 2
    assert result["candidates_checked"] == 2
    assert len(result["errors"]) == 1
    assert "task-1" in result["errors"][0]


@pytest.mark.asyncio
async def test_approve_draft_stamps_post_id_and_approved_at():
    """A successful approve links the draft to its posts row (post_id) and
    stamps approved_at alongside posted_at."""
    pool, conn = _make_pool()
    conn.fetchrow.side_effect = [_draft_row(), _post_row(id="post-42")]
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
        "site_url": "https://gladlabs.io",
    })

    with patch("services.social_drafts.PostizClient") as mock_cls:
        instance = mock_cls.return_value
        instance.create_post = AsyncMock(
            return_value={"success": True, "post_id": "pz-11", "error": None}
        )
        svc = SocialDraftsService()
        result = await svc.approve_draft("d-1", pool, sc)

    assert result["success"] is True
    sqls = [c[0][0].lower() for c in conn.execute.call_args_list]
    assert any("post_id" in s for s in sqls)
    assert any("approved_at" in s and "posted" in s for s in sqls)
