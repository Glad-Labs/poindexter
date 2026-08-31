"""Unit tests for SocialDraftsService and PostizClient (offline)."""
from __future__ import annotations

import json
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from services.social_drafts import (
    _KEY_HELD_STATUSES,
    _LIVE_STATUSES,
    SocialDraftsService,
)

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


def _make_list_pool(rows=None, counts=None):
    """Pool for list_drafts, which issues TWO fetches: page, then status counts.

    A single ``fetch`` return_value would feed draft rows to the counts reader
    (which wants ``{status, n}``), so the two calls are scripted separately.
    """
    pool, conn = _make_pool()
    count_rows = [
        {"status": status, "n": n} for status, n in (counts or {}).items()
    ]
    conn.fetch.side_effect = [list(rows or []), count_rows]
    return pool, conn


def _page_sql(conn) -> str:
    """The page query — call 0, since call 1 is the status-counts aggregate."""
    return conn.fetch.call_args_list[0][0][0].lower()


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
    """The INSERT carries the dedup guard: NOT EXISTS over the live-key
    statuses for the natural key, with the partial-index ON CONFLICT
    backstop, and the subreddit key as its own parameter."""
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
    assert "on conflict" in sql and "do nothing" in sql
    assert "coalesce(platform_config->>'subreddit', '')" in sql
    # Subreddit key rides as the 5th parameter ($5); '' for non-reddit drafts.
    assert conn.fetchval.call_args[0][5] == "r/LocalLLaMA"
    # The live-key statuses ride as $6 rather than inline literals, so the
    # guard and existing_draft_keys can't drift apart.
    assert conn.fetchval.call_args[0][6] == list(_KEY_HELD_STATUSES)
    assert "rejected" not in _KEY_HELD_STATUSES
    # ON CONFLICT must name the partial index's own predicate, which covers
    # the live statuses MINUS posted (a posted row is caught by NOT EXISTS,
    # but is not in the unique index — it's no longer mutually exclusive).
    assert "('pending', 'scheduled', 'failed')" in sql


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
    # Live-key + posted drafts block re-creation; rejected ones do not (an
    # operator reject followed by a regen loop legitimately gets fresh copy).
    # Bound as a parameter, shared with create_draft's guard.
    assert conn.fetch.call_args[0][1] == "task-1"
    assert conn.fetch.call_args[0][2] == list(_KEY_HELD_STATUSES)
    assert "posted" in _KEY_HELD_STATUSES
    assert "rejected" not in _KEY_HELD_STATUSES


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
    pool, _conn = _make_list_pool([_list_row(resolved_post_status="published")])
    svc = SocialDraftsService()
    page = await svc.list_drafts(None, None, None, pool)
    assert page.rows[0].post_status == "published"


@pytest.mark.asyncio
async def test_list_drafts_post_status_none_when_no_post_yet():
    pool, _conn = _make_list_pool([_list_row(resolved_post_status=None)])
    svc = SocialDraftsService()
    page = await svc.list_drafts(None, None, None, pool)
    assert page.rows[0].post_status is None


@pytest.mark.asyncio
async def test_list_drafts_query_resolves_post_by_id_or_task_metadata():
    """The join must resolve the same way approve_draft's own _resolve_post
    does: post_id when linked, else the latest posts row matching
    pipeline_task_id metadata — one source of truth for both code paths."""
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool)
    sql = _page_sql(conn)
    assert "p.id = d.post_id" in sql
    assert "metadata->>'pipeline_task_id'" in sql


@pytest.mark.asyncio
async def test_list_drafts_title_from_post_when_available():
    """Once a posts row exists, its title (possibly revised post-writing)
    wins over the task's original topic."""
    pool, _conn = _make_list_pool(
        [_list_row(article_title="Real Published Title", resolved_post_id="post-99")]
    )
    svc = SocialDraftsService()
    page = await svc.list_drafts(None, None, None, pool)
    assert page.rows[0].title == "Real Published Title"
    assert page.rows[0].resolved_post_id == "post-99"


@pytest.mark.asyncio
async def test_list_drafts_title_falls_back_to_task_topic():
    """Before a posts row exists (task still awaiting_approval), the SQL
    COALESCEs to pipeline_tasks.topic — resolved_post_id stays None."""
    pool, _conn = _make_list_pool(
        [_list_row(article_title="Original Task Topic", resolved_post_id=None)]
    )
    svc = SocialDraftsService()
    page = await svc.list_drafts(None, None, None, pool)
    assert page.rows[0].title == "Original Task Topic"
    assert page.rows[0].resolved_post_id is None


@pytest.mark.asyncio
async def test_list_drafts_query_joins_pipeline_tasks_for_title():
    """Locks in the join + COALESCE the title/id resolution depends on —
    same rigor as the existing post-resolution join assertion above."""
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool)
    sql = _page_sql(conn)
    assert "join pipeline_tasks" in sql
    assert "pt.task_id = d.pipeline_task_id" in sql
    assert "coalesce(rp.title, pt.topic)" in sql
    assert "coalesce(d.post_id, rp.id)" in sql


# ---------------------------------------------------------------------------
# list_drafts — pagination + counts. social_post_drafts only grows (one row
# per platform per post, tombstones never pruned: 67 of 77 rows were already
# posted/rejected when the cap landed), and the endpoint returned every row on
# every console poll. The cap must not strand an approval, so live rows sort
# first, and counts must span the table so operator KPIs don't silently report
# the window instead.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_drafts_applies_limit_and_offset():
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool, limit=50, offset=100)
    sql = _page_sql(conn)
    # $1 is the live-status array bound for the ordering; limit/offset follow.
    assert "limit $2" in sql
    assert "offset $3" in sql
    assert conn.fetch.call_args_list[0][0][1:] == (
        list(_LIVE_STATUSES), 50, 100,
    )


@pytest.mark.asyncio
async def test_list_drafts_unbounded_when_limit_none():
    """The default stays unbounded for callers that genuinely need every row;
    the HTTP surface is where the cap is enforced."""
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool)
    assert "limit $" not in _page_sql(conn)


@pytest.mark.asyncio
async def test_list_drafts_sorts_live_rows_ahead_of_recency():
    """The whole point of the ordering: a cap may drop posted/rejected
    tombstones, never a pending/scheduled/failed row awaiting action. Without
    this, an old pending draft ages out of the window and its approval is
    stranded with no UI trace."""
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, None, pool, limit=50)
    sql = _page_sql(conn)
    assert "order by (d.status = any($1::text[])) desc, d.created_at desc" in sql
    assert conn.fetch.call_args_list[0][0][1] == list(_LIVE_STATUSES)
    # A scheduled draft is awaiting its slot, not a tombstone — it must never
    # be the row a page cap drops.
    assert "scheduled" in _LIVE_STATUSES


@pytest.mark.asyncio
async def test_list_drafts_orders_by_fire_time_only_when_asked():
    """The queue view wants fire order; the review list wants recency.

    Sorting on scheduled_at unconditionally would scramble the tombstone
    half out of recency order, because a posted row keeps the scheduled_at
    it fired from.
    """
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, None, "scheduled", pool, order_by_schedule=True)
    assert "d.scheduled_at asc nulls last" in _page_sql(conn)

    pool2, conn2 = _make_list_pool()
    await svc.list_drafts(None, None, None, pool2)
    assert "scheduled_at asc" not in _page_sql(conn2)


@pytest.mark.asyncio
async def test_list_drafts_placeholders_shift_past_filters():
    """limit/offset bind after the filter args — an off-by-one here would
    silently filter on the limit integer."""
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts("post-1", "task-1", "pending", pool, limit=25, offset=5)
    sql = _page_sql(conn)
    assert "d.post_id = $1" in sql
    assert "d.pipeline_task_id = $2" in sql
    assert "d.status = $3" in sql
    # $4 is the live-status array bound for the ordering; limit/offset follow.
    assert "limit $5" in sql
    assert "offset $6" in sql
    assert conn.fetch.call_args_list[0][0][1:] == (
        "post-1", "task-1", "pending", list(_LIVE_STATUSES), 25, 5,
    )


@pytest.mark.asyncio
async def test_list_drafts_returns_status_counts_and_total():
    pool, _conn = _make_list_pool(
        [_list_row()], counts={"pending": 10, "posted": 26, "rejected": 41}
    )
    svc = SocialDraftsService()
    page = await svc.list_drafts(None, None, None, pool, limit=1)
    assert page.status_counts == {"pending": 10, "posted": 26, "rejected": 41}
    # total spans the table, not the one-row window it was cut to.
    assert page.total == 77
    assert len(page.rows) == 1


@pytest.mark.asyncio
async def test_list_drafts_total_narrows_to_filtered_status():
    """total is "rows matching every filter"; status_counts stays the full
    breakdown so a status-filtered call still reports honest KPIs."""
    pool, _conn = _make_list_pool(
        [_list_row()], counts={"pending": 10, "posted": 26}
    )
    svc = SocialDraftsService()
    page = await svc.list_drafts(None, None, "pending", pool, limit=50)
    assert page.total == 10
    assert page.status_counts == {"pending": 10, "posted": 26}


@pytest.mark.asyncio
async def test_list_drafts_counts_query_ignores_status_filter():
    """The counts aggregate keeps the post/task scope but drops the status
    predicate — otherwise filtering to one status would zero out every other
    KPI on the console's social panel."""
    pool, conn = _make_list_pool()
    svc = SocialDraftsService()
    await svc.list_drafts(None, "task-1", "pending", pool, limit=50)
    counts_sql = conn.fetch.call_args_list[1][0][0].lower()
    assert "group by d.status" in counts_sql
    assert "d.pipeline_task_id = $1" in counts_sql
    assert "d.status =" not in counts_sql
    # ...and it binds only the scope arg, not the status one.
    assert conn.fetch.call_args_list[1][0][1:] == ("task-1",)


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
async def test_cancel_orphaned_targets_live_drafts_and_terminal_tasks_only():
    # The reaper must only touch live (pending/scheduled/failed) drafts of
    # terminally-rejected tasks — never 'posted' drafts (Case-2 live posts)
    # and never valid 'approved'/'awaiting_approval' content awaiting publish.
    pool, _conn = _make_pool(execute="UPDATE 0")
    svc = SocialDraftsService()
    await svc.cancel_orphaned_for_rejected_tasks(pool)
    _conn.execute.assert_called_once()
    sql = _conn.execute.call_args[0][0].lower()
    task_statuses = _conn.execute.call_args[0][1]
    draft_statuses = _conn.execute.call_args[0][2]
    assert "set status = 'rejected'" in sql
    assert draft_statuses == list(_LIVE_STATUSES)
    assert "posted" not in draft_statuses  # posted drafts are never cancelled
    # terminal-reject task statuses passed as the bound array; excludes
    # rejected_retry (re-runs) and approved/awaiting_approval (awaiting publish).
    # 'expired' joined with poindexter#981: a task auto-expired past the
    # approval TTL is terminal-dead, so its speculative promos must be reaped
    # the same as a rejected one's. 'failed' joined 2026-08-20: the stale
    # sweep writes it only after max retries, so it is just as dead — its
    # absence stranded a failed task's promos in 'pending' for 8 days.
    assert set(task_statuses) == {
        "rejected_final", "rejected", "dismissed", "expired", "failed",
    }
    assert "rejected_retry" not in task_statuses
    assert "approved" not in task_statuses


@pytest.mark.asyncio
async def test_cancel_orphaned_clears_the_fire_time():
    """A scheduled orphan is the dangerous one — it has a timer on it.

    Cancelling must drop scheduled_at too, or the row still reads as queued
    to the console and to anything summarising upcoming promos.
    """
    pool, _conn = _make_pool(execute="UPDATE 0")
    svc = SocialDraftsService()
    await svc.cancel_orphaned_for_rejected_tasks(pool)
    sql = _conn.execute.call_args[0][0].lower()
    assert "scheduled_at = null" in sql
    assert "scheduled" in _LIVE_STATUSES


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
# approve_draft — single-flight advisory lock (no duplicate promo)
# ---------------------------------------------------------------------------


class _LockConn:
    """Fake asyncpg conn modelling pg_try_advisory_lock over shared state.

    fetchval routes on the SQL: the advisory lock/unlock calls mutate the
    shared ``held`` set (True on first acquire of a key, False while held);
    every other call returns None. fetchrow routes on the SQL too and returns
    the same rows every time (NOT a consuming pop) so BOTH racing callers can
    independently read the draft + post — the lock, not an exhausted script,
    is what must stop the second create_post. execute is a no-op. One conn is
    shared across all ``pool.acquire()`` blocks so the lock state is
    process-wide, exactly like a real advisory lock.
    """

    def __init__(self, held: set, draft_row, post_row):
        self._held = held
        self._draft_row = draft_row
        self._post_row = post_row

    async def fetchval(self, sql, *args):
        if "pg_try_advisory_lock" in sql:
            key = tuple(args)
            if key in self._held:
                return False
            self._held.add(key)
            return True
        if "pg_advisory_unlock" in sql:
            self._held.discard(tuple(args))
            return True
        return None

    async def fetchrow(self, sql, *args):
        if "FROM social_post_drafts" in sql:
            return self._draft_row
        if "FROM posts" in sql:
            return self._post_row
        return None

    async def execute(self, *args, **kwargs):
        return ""


class _LockPool:
    def __init__(self, draft_row, post_row):
        self._conn = _LockConn(set(), draft_row, post_row)

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return None

        return _Ctx()


@pytest.mark.asyncio
async def test_concurrent_approve_posts_only_once():
    """Two concurrent approvals of the SAME draft — the fire-sweep racing a
    manual approve — must post to Postiz exactly once. Without the per-draft
    advisory lock both cleared the status check and both called create_post,
    double-posting the promo (the approve-path twin of poindexter#833)."""
    import asyncio

    # Both callers can read the SAME draft + published post independently, so
    # without the lock BOTH would clear the gate and call create_post twice —
    # the lock is the only thing that can hold the count to one.
    pool = _LockPool(_draft_row(id="dup-1", content="promo"), _post_row())
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
    })

    async def _slow_create_post(*a, **k):
        # Hold the lock across a real suspension so the racing call reaches its
        # pg_try_advisory_lock and gets False while this one is mid-post.
        await asyncio.sleep(0.05)
        return {"success": True, "post_id": "pz-dup", "error": None}

    with patch(
        "services.social_drafts.PostizClient.create_post",
        new_callable=AsyncMock,
        side_effect=_slow_create_post,
    ) as create_post:
        svc = SocialDraftsService()
        results = await asyncio.gather(
            svc.approve_draft("dup-1", pool, sc),
            svc.approve_draft("dup-1", pool, sc),
        )

    assert create_post.await_count == 1, "promo posted more than once"
    successes = [r for r in results if r.get("success")]
    contended = [r for r in results if r.get("contended")]
    assert len(successes) == 1
    assert len(contended) == 1


@pytest.mark.asyncio
async def test_lock_contention_skips_postiz_entirely():
    """When the advisory lock is already held (pg_try_advisory_lock → False),
    approve_draft returns contended and NEVER constructs a Postiz call."""
    pool = _LockPool(_draft_row(id="held-1"), _post_row())

    # Force fetchval to report the lock as already held (another approver).
    async def _always_held(sql, *args):
        if "pg_try_advisory_lock" in sql:
            return False
        return None

    pool._conn.fetchval = _always_held  # type: ignore[method-assign]
    sc = _make_site_config({
        "postiz_integration_id_twitter": "uuid-abc",
        "postiz_api_url": "http://postiz:3000",
    })
    with patch("services.social_drafts.PostizClient") as mock_cls:
        svc = SocialDraftsService()
        result = await svc.approve_draft("held-1", pool, sc)

    assert result["success"] is False
    assert result["contended"] is True
    mock_cls.assert_not_called()  # never even built a client


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
    # Appended with THIS draft's surface tag — approve is the last place the
    # outbound link is touched, so it is where attribution is settled.
    assert pushed.endswith(
        "https://gladlabs.io/posts/why-vram-bandwidth-matters-f3a71ef6"
        "?utm_source=twitter&utm_medium=social"
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


# ---------------------------------------------------------------------------
# Scheduling — the local queue that replaces the Postiz UI.
#
# The load-bearing property throughout: the queue lives HERE, so approve's
# publish gate is re-checked at fire time. A Postiz-side schedule would run
# that check when the operator picked the time, and a post whose publish
# slipped would still promote itself at a URL that 404s.
# ---------------------------------------------------------------------------

def _sched_site_config(**overrides) -> MagicMock:
    settings = {"operator_timezone": "America/New_York"}
    settings.update(overrides)
    sc = _make_site_config(settings)
    from services.clock import resolve_operator_tz

    sc.timezone = resolve_operator_tz(settings["operator_timezone"])
    return sc


@pytest.mark.asyncio
async def test_schedule_draft_reads_clock_words_in_operator_timezone():
    """'tomorrow 9am' means 9am where the operator is, not 9am UTC.

    Storage stays UTC (store-UTC/present-local), so the stored instant is
    9am New York expressed as 13:00/14:00Z depending on DST.
    """
    pool, conn = _make_pool(fetchrow={"status": "pending"})
    sc = _sched_site_config()
    svc = SocialDraftsService()

    result = await svc.schedule_draft("d-1", "tomorrow 9am", pool, sc)

    assert result["success"] is True
    stored = conn.execute.call_args[0][2]
    local = stored.astimezone(sc.timezone)
    assert (local.hour, local.minute) == (9, 0)
    assert stored.utcoffset().total_seconds() == 0  # persisted as UTC
    assert "status = 'scheduled'" in conn.execute.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_schedule_draft_refuses_a_past_time_unless_forced():
    """A past slot is nearly always a typo'd year or a missed am/pm.

    Silently posting immediately is the wrong recovery from a typo, so it
    refuses — but --force is there for "yes, send it on the next sweep".
    """
    pool, _conn = _make_pool(fetchrow={"status": "pending"})
    sc = _sched_site_config()
    svc = SocialDraftsService()

    refused = await svc.schedule_draft("d-1", "2020-01-01 09:00", pool, sc)
    assert refused["success"] is False
    assert "in the past" in refused["error"]

    forced = await svc.schedule_draft(
        "d-1", "2020-01-01 09:00", pool, sc, force=True
    )
    assert forced["success"] is True


@pytest.mark.asyncio
async def test_schedule_draft_rejects_unparseable_and_terminal_rows():
    pool, _conn = _make_pool(fetchrow={"status": "posted"})
    sc = _sched_site_config()
    svc = SocialDraftsService()

    bad_spec = await svc.schedule_draft("d-1", "whenever-ish", pool, sc)
    assert bad_spec["success"] is False
    assert "could not parse" in bad_spec["error"].lower()

    # An already-posted promo can't be un-sent by scheduling it.
    terminal = await svc.schedule_draft("d-1", "tomorrow 9am", pool, sc)
    assert terminal["success"] is False
    assert "posted" in terminal["error"]


@pytest.mark.asyncio
async def test_unschedule_returns_to_pending_not_rejected():
    """"Not at that time" is not "not at all" — it goes back for a decision."""
    pool, conn = _make_pool(fetchrow={"id": "d-1"})
    svc = SocialDraftsService()

    result = await svc.unschedule_draft("d-1", pool)

    assert result["success"] is True
    sql = conn.fetchrow.call_args[0][0].lower()
    assert "status = 'pending'" in sql
    assert "scheduled_at = null" in sql
    assert "rejected" not in sql


@pytest.mark.asyncio
async def test_fire_due_drafts_approves_through_the_publish_gate():
    """Firing goes through approve_draft — that IS the gate re-check."""
    import datetime as _dt

    due = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
    pool, _conn = _make_pool(
        fetch=[{"id": "d-1", "platform": "twitter", "scheduled_at": due}]
    )
    sc = _sched_site_config()
    svc = SocialDraftsService()
    svc.approve_draft = AsyncMock(return_value={"success": True})

    result = await svc.fire_due_drafts(pool, sc)

    svc.approve_draft.assert_awaited_once()
    assert svc.approve_draft.await_args[0][0] == "d-1"
    assert result == {
        "due": 1, "posted": 1, "blocked": 0, "failed": 0, "overdue": 0,
    }


@pytest.mark.asyncio
async def test_fire_due_drafts_holds_a_gate_blocked_draft_at_its_slot():
    """Post not live yet → keep the slot and retry next sweep, don't fail it.

    A gate refusal isn't a failure: it must not burn RetryFailedSocialDrafts
    retries or move the row out of the queue.
    """
    import datetime as _dt

    due = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1)
    pool, conn = _make_pool(
        fetch=[{"id": "d-1", "platform": "twitter", "scheduled_at": due}]
    )
    conn.fetchval.return_value = "scheduled"  # _still_scheduled → gate-blocked
    sc = _sched_site_config()
    svc = SocialDraftsService()
    svc.approve_draft = AsyncMock(
        return_value={"success": False, "error": "post is status='approved'"}
    )

    result = await svc.fire_due_drafts(pool, sc)

    assert result["blocked"] == 1
    assert result["failed"] == 0
    assert result["posted"] == 0


@pytest.mark.asyncio
async def test_fire_due_drafts_skips_and_reports_an_overdue_draft():
    """An outage must not silently turn a timed promo into an untimed one.

    Past the grace period the draft stays queued and raises a finding, so a
    human decides whether hours-late news is still worth posting.
    """
    import datetime as _dt

    stale = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=9)
    pool, _conn = _make_pool(
        fetch=[{"id": "d-1", "platform": "twitter", "scheduled_at": stale}]
    )
    sc = _sched_site_config(social_schedule_max_lateness_minutes="180")
    svc = SocialDraftsService()
    svc.approve_draft = AsyncMock()

    with patch("utils.findings.emit_finding") as emit:
        result = await svc.fire_due_drafts(pool, sc)

    svc.approve_draft.assert_not_awaited()
    assert result["overdue"] == 1 and result["posted"] == 0
    assert emit.call_count == 1
    assert emit.call_args.kwargs["dedup_key"] == "social-draft-overdue:d-1"


@pytest.mark.asyncio
async def test_auto_schedule_is_double_gated_and_defaults_off():
    """Both gates default closed: the switch AND a per-platform entry.

    Turning the switch on without naming any platform (in either offsets or
    prime times) must change nothing — that's what makes enabling auto-drip
    a deliberate two-step.
    """
    pool, _conn = _make_pool(fetch=[])
    svc = SocialDraftsService()

    off = await svc.auto_schedule_ready_drafts(
        pool, _sched_site_config(social_schedule_offsets="twitter=1h")
    )
    assert off["scheduled"] == 0 and "enabled=false" in off["detail"]

    no_platforms = await svc.auto_schedule_ready_drafts(
        pool, _sched_site_config(social_schedule_enabled="true")
    )
    assert no_platforms["scheduled"] == 0
    assert "empty" in no_platforms["detail"]


@pytest.mark.asyncio
async def test_auto_schedule_staggers_from_publish_time():
    import datetime as _dt

    published = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
    pool, conn = _make_pool()
    # Two fetches: the eligible drafts, then the already-claimed slots.
    conn.fetch.side_effect = [
        [{"id": "d-1", "platform": "linkedin", "published_at": published}],
        [],
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true", social_schedule_offsets="linkedin=3h"
    )
    svc = SocialDraftsService()

    result = await svc.auto_schedule_ready_drafts(pool, sc)

    assert result["scheduled"] == 1
    slot = conn.execute.call_args[0][2]
    assert slot == published + _dt.timedelta(hours=3)


@pytest.mark.asyncio
async def test_auto_schedule_reanchors_a_backlogged_post_to_now():
    """Backfilling an old post must not collapse the platform stagger.

    Anchoring on a long-past published_at would put every slot in the past,
    firing the whole drip in one burst on the next sweep.
    """
    import datetime as _dt

    old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
    pool, conn = _make_pool()
    conn.fetch.side_effect = [
        [{"id": "d-1", "platform": "linkedin", "published_at": old}],
        [],
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true", social_schedule_offsets="linkedin=3h"
    )
    svc = SocialDraftsService()

    await svc.auto_schedule_ready_drafts(pool, sc)

    slot = conn.execute.call_args[0][2]
    now = _dt.datetime.now(_dt.timezone.utc)
    assert slot > now, "a re-anchored slot must be in the future"
    assert slot - now < _dt.timedelta(hours=4), "offset should survive re-anchoring"


@pytest.mark.asyncio
async def test_auto_schedule_pauses_on_malformed_quiet_hours():
    """A bad window must not silently degrade to "no quiet hours".

    That would post inside exactly the window the operator carved out.
    """
    pool, _conn = _make_pool(fetch=[])
    sc = _sched_site_config(
        social_schedule_enabled="true",
        social_schedule_offsets="linkedin=3h",
        social_schedule_quiet_hours="10pm til 7",
    )
    svc = SocialDraftsService()

    result = await svc.auto_schedule_ready_drafts(pool, sc)

    assert result["scheduled"] == 0
    assert "invalid quiet hours" in result["detail"]


# ---------------------------------------------------------------------------
# parse_offsets — one typo must not cost the other platforms their drip
# ---------------------------------------------------------------------------

def test_parse_offsets_keeps_good_pairs_and_drops_bad_ones():
    import datetime as _dt

    from services.social_drafts import parse_offsets

    parsed = parse_offsets(
        "twitter=0m, linkedin=3h, nosuchplatform=1h, reddit=notaduration, "
        "malformed, bluesky=1d"
    )

    assert parsed == {
        "twitter": _dt.timedelta(0),
        "linkedin": _dt.timedelta(hours=3),
        "bluesky": _dt.timedelta(days=1),
    }


def test_parse_offsets_empty_means_auto_slot_nothing():
    from services.social_drafts import parse_offsets

    assert parse_offsets("") == {}
    assert parse_offsets("   ") == {}


# ---------------------------------------------------------------------------
# Prime-time slots — "post at 9am", not "at least 3h after publish".
#
# The problem these fix: offsets are relative to publish, so a post that goes
# live at 11pm promotes at 11pm. Quiet hours was the only lever and it made
# things worse — it clamps every displaced promo to the window's edge, so four
# platforms landed on the same 07:00 minute and the stagger collapsed.
# ---------------------------------------------------------------------------

def test_parse_prime_times_keeps_good_entries_and_drops_bad_ones():
    import datetime as _dt

    from services.social_drafts import parse_prime_times

    parsed = parse_prime_times(
        "twitter=09:00,12:30; nosuchplatform=09:00; reddit=25:00; "
        "linkedin=08:00; malformed; bluesky=10:00,bogus,16:00"
    )

    assert parsed == {
        "twitter": [_dt.time(9, 0), _dt.time(12, 30)],
        "linkedin": [_dt.time(8, 0)],
        # 'bogus' dropped, the two valid times survive
        "bluesky": [_dt.time(10, 0), _dt.time(16, 0)],
    }
    # reddit's only time was out of range, so it gets no entry at all and
    # falls back to its offset rather than silently posting at midnight.
    assert "reddit" not in parsed


def test_parse_prime_times_sorts_and_dedups():
    import datetime as _dt

    from services.social_drafts import parse_prime_times

    assert parse_prime_times("twitter=17:00,09:00,12:30,09:00") == {
        "twitter": [_dt.time(9, 0), _dt.time(12, 30), _dt.time(17, 0)]
    }


def test_next_prime_slot_rolls_a_night_publish_to_the_morning():
    """The headline case: publish at 11pm, promote at 9am."""
    import datetime as _dt

    from services.social_drafts import next_prime_slot

    ny = ZoneInfo("America/New_York")
    floor = _dt.datetime(2026, 8, 9, 23, 0, tzinfo=ny)
    slot = next_prime_slot(
        floor, [_dt.time(9, 0), _dt.time(12, 30), _dt.time(17, 0)], set()
    )
    assert slot == _dt.datetime(2026, 8, 10, 9, 0, tzinfo=ny)


def test_next_prime_slot_takes_the_same_day_when_one_is_still_ahead():
    """A 10am publish shouldn't wait until tomorrow for a 12:30 slot."""
    import datetime as _dt

    from services.social_drafts import next_prime_slot

    ny = ZoneInfo("America/New_York")
    floor = _dt.datetime(2026, 8, 11, 10, 0, tzinfo=ny)
    slot = next_prime_slot(
        floor, [_dt.time(9, 0), _dt.time(12, 30), _dt.time(17, 0)], set()
    )
    assert slot == _dt.datetime(2026, 8, 11, 12, 30, tzinfo=ny)


def test_next_prime_slot_spreads_collisions_across_the_listed_hours():
    """Three posts published overnight must not all fire at 09:00.

    That burst is the exact "same link everywhere at once" pattern the
    stagger exists to prevent.
    """
    import datetime as _dt

    from services.social_drafts import next_prime_slot

    ny = ZoneInfo("America/New_York")
    times = [_dt.time(9, 0), _dt.time(12, 30), _dt.time(17, 0)]
    taken: set = set()
    slots = []
    for minute in (0, 30, 45):
        floor = _dt.datetime(2026, 8, 9, 23, minute, tzinfo=ny)
        slot = next_prime_slot(floor, times, taken)
        taken.add(slot)
        slots.append(slot)

    assert slots == [
        _dt.datetime(2026, 8, 10, 9, 0, tzinfo=ny),
        _dt.datetime(2026, 8, 10, 12, 30, tzinfo=ny),
        _dt.datetime(2026, 8, 10, 17, 0, tzinfo=ny),
    ]
    assert len(set(slots)) == 3, "collisions must produce distinct slots"


def test_next_prime_slot_rolls_to_the_next_day_when_a_day_fills_up():
    import datetime as _dt

    from services.social_drafts import next_prime_slot

    ny = ZoneInfo("America/New_York")
    times = [_dt.time(9, 0)]
    first = _dt.datetime(2026, 8, 10, 9, 0, tzinfo=ny)
    slot = next_prime_slot(
        _dt.datetime(2026, 8, 9, 23, 0, tzinfo=ny), times, {first}
    )
    assert slot == _dt.datetime(2026, 8, 11, 9, 0, tzinfo=ny)


def test_next_prime_slot_returns_none_with_no_times():
    """Caller falls back to the offset slot rather than dropping the draft."""
    import datetime as _dt

    from services.social_drafts import next_prime_slot

    assert next_prime_slot(
        _dt.datetime(2026, 8, 9, 23, 0, tzinfo=timezone.utc), [], set()
    ) is None


@pytest.mark.asyncio
async def test_auto_schedule_uses_prime_time_over_quiet_hours():
    """Prime times win: naming the good hours beats naming the bad ones.

    With only quiet hours, an 11pm publish clamps to the window edge (07:00)
    along with every other platform. With prime times it lands on the hour
    the operator actually chose.
    """
    import datetime as _dt

    ny = ZoneInfo("America/New_York")
    # The publish time MUST be in the future relative to the real wall clock.
    # `auto_schedule_ready_drafts` anchors on `datetime.now()` whenever the
    # publish time has already passed:
    #
    #     anchor = published_at if published_at + offset > now else now
    #
    # so a hardcoded past date silently stops testing the publish-time branch
    # and starts testing "whatever time the suite happens to run". This test
    # was pinned to 2026-08-09 23:00 asserting a 2026-08-10 09:00 slot, and it
    # began failing on EVERY run once that instant passed — CI stayed green
    # only while runs landed before 09:00 Eastern. Deriving the date from the
    # clock keeps the scenario (a 23:00 publish, inside quiet hours) fixed
    # while the calendar moves.
    tomorrow = _dt.datetime.now(ny).date() + _dt.timedelta(days=1)
    published = _dt.datetime.combine(tomorrow, _dt.time(23, 0), tzinfo=ny)
    pool, conn = _make_pool()
    conn.fetch.side_effect = [
        [{"id": "d-1", "platform": "twitter", "published_at": published}],
        [],  # no already-claimed slots
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true",
        social_schedule_offsets="twitter=0m",
        social_schedule_prime_times="twitter=09:00,12:30",
        social_schedule_quiet_hours="22:00-07:00",
    )
    svc = SocialDraftsService()

    result = await svc.auto_schedule_ready_drafts(pool, sc)

    assert result["scheduled"] == 1
    slot = conn.execute.call_args[0][2].astimezone(ny)
    assert (slot.hour, slot.minute) == (9, 0), "should be prime time, not 07:00"
    # 23:00 is past both prime times, so the slot rolls to the NEXT day's
    # first one — relative to the publish date, not to today.
    assert slot.date() == tomorrow + _dt.timedelta(days=1)


@pytest.mark.asyncio
async def test_auto_schedule_opts_in_via_prime_times_alone():
    """A platform needs only ONE of the two maps to be auto-slotted."""
    import datetime as _dt

    ny = ZoneInfo("America/New_York")
    # Future-relative like its siblings. This one's assertion (hour == 8) is
    # satisfied by any 08:00 slot, so a stale literal wouldn't fail it — it
    # would just quietly stop testing the publish-time anchor branch.
    published = _dt.datetime.combine(
        _dt.datetime.now(ny).date() + _dt.timedelta(days=1),
        _dt.time(23, 0), tzinfo=ny,
    )
    pool, conn = _make_pool()
    conn.fetch.side_effect = [
        [{"id": "d-1", "platform": "linkedin", "published_at": published}],
        [],
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true",
        social_schedule_offsets="",  # no offset at all
        social_schedule_prime_times="linkedin=08:00",
    )
    svc = SocialDraftsService()

    result = await svc.auto_schedule_ready_drafts(pool, sc)

    assert result["scheduled"] == 1
    slot = conn.execute.call_args[0][2].astimezone(ny)
    assert (slot.hour, slot.minute) == (8, 0)
    # The eligibility query must have been asked for linkedin.
    assert "linkedin" in conn.fetch.call_args_list[0][0][1]


@pytest.mark.asyncio
async def test_auto_schedule_avoids_slots_already_claimed_in_the_db():
    """A slot another draft already holds is skipped, across sweeps."""
    import datetime as _dt

    ny = ZoneInfo("America/New_York")
    # Future-relative for the same reason as the prime-time test above: a past
    # publish time makes the service anchor on `now`, and then the "already
    # claimed" slot is in the past too — so the collision under test never
    # happens and the assertion passes or fails on the wall clock instead.
    tomorrow = _dt.datetime.now(ny).date() + _dt.timedelta(days=1)
    published = _dt.datetime.combine(tomorrow, _dt.time(23, 0), tzinfo=ny)
    # The slot this draft would otherwise take — the next day's first prime.
    contested = _dt.datetime.combine(
        tomorrow + _dt.timedelta(days=1), _dt.time(9, 0), tzinfo=ny,
    )
    pool, conn = _make_pool()
    conn.fetch.side_effect = [
        [{"id": "d-2", "platform": "twitter", "published_at": published}],
        [{"platform": "twitter", "scheduled_at": contested}],
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true",
        social_schedule_prime_times="twitter=09:00,12:30",
    )
    svc = SocialDraftsService()

    await svc.auto_schedule_ready_drafts(pool, sc)

    slot = conn.execute.call_args[0][2].astimezone(ny)
    assert (slot.hour, slot.minute) == (12, 30), "09:00 was already taken"
    assert slot.date() == contested.date(), "same day, next slot along"


@pytest.mark.asyncio
async def test_offset_still_acts_as_a_floor_under_prime_times():
    """`linkedin=3h` + `08:00` means "at least 3h later, then the next 08:00"."""
    import datetime as _dt

    ny = ZoneInfo("America/New_York")
    # 07:00 publish; a bare 08:00 prime time would fire the same morning, but
    # the 3h offset pushes the floor to 10:00, so it rolls to the next 08:00.
    # Dated relative to now so the offset-vs-prime interaction is what's under
    # test rather than how far the calendar has moved past a literal.
    pub_day = _dt.datetime.now(ny).date() + _dt.timedelta(days=1)
    published = _dt.datetime.combine(pub_day, _dt.time(7, 0), tzinfo=ny)
    pool, conn = _make_pool()
    conn.fetch.side_effect = [
        [{"id": "d-1", "platform": "linkedin", "published_at": published}],
        [],
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true",
        social_schedule_offsets="linkedin=3h",
        social_schedule_prime_times="linkedin=08:00",
    )
    svc = SocialDraftsService()

    await svc.auto_schedule_ready_drafts(pool, sc)

    slot = conn.execute.call_args[0][2].astimezone(ny)
    assert slot == _dt.datetime.combine(
        pub_day + _dt.timedelta(days=1), _dt.time(8, 0), tzinfo=ny,
    )


@pytest.mark.asyncio
async def test_platform_without_prime_times_keeps_offset_behaviour():
    """Mixed config: one platform on prime times, another on a plain offset."""
    import datetime as _dt

    ny = ZoneInfo("America/New_York")
    # Future-relative: the assertion is `published + 2h`, which only holds
    # while the publish time is still ahead of `now`. Pinned to a literal it
    # silently flips to asserting against `now + 2h` once that date passes.
    published = _dt.datetime.combine(
        _dt.datetime.now(ny).date() + _dt.timedelta(days=1),
        _dt.time(10, 0), tzinfo=ny,
    )
    pool, conn = _make_pool()
    conn.fetch.side_effect = [
        [{"id": "d-1", "platform": "bluesky", "published_at": published}],
        [],
    ]
    sc = _sched_site_config(
        social_schedule_enabled="true",
        social_schedule_offsets="bluesky=2h",
        social_schedule_prime_times="twitter=09:00",  # bluesky absent
    )
    svc = SocialDraftsService()

    await svc.auto_schedule_ready_drafts(pool, sc)

    slot = conn.execute.call_args[0][2]
    assert slot == published + _dt.timedelta(hours=2)
