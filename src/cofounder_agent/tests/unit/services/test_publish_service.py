"""
Unit tests for services/publish_service.py

Covers the unified publish_post_from_task function — the single code path
that ALL publish routes call.  A bug here breaks the entire content pipeline.

Test groups:
- Happy path: mock db_service, verify post created with correct fields
- Missing content: task_data has no content, returns error gracefully
- Scheduling logic (_calculate_scheduled_publish_time):
    - Under daily cap with no posts today: publishes immediately (None)
    - At daily cap: schedules for tomorrow
    - Spacing check: published recently with spacing -> returns future time
    - DB failure in scheduling: falls through to immediate publish
- Search engine ping is fire-and-forget (doesn't block on failure)
- Dev.to cross-post is non-blocking (failure doesn't break publish)
- PublishResult and helper coverage
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.publish_service import (
    PublishResult,
    _calculate_scheduled_publish_time,
    _parse_json_field,
    publish_post_from_task,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(
    *,
    today_count: int = 0,
    latest_today=None,
    max_per_day: str = "3",
    spacing_hours: str = "4",
):
    """Build a mock db_service with sensible defaults for publishing."""
    db = AsyncMock()

    # create_post returns an object with an .id
    created_post = SimpleNamespace(id="post-abc-123")
    db.create_post = AsyncMock(return_value=created_post)

    # update_task_status is fire-and-forget
    db.update_task_status = AsyncMock()

    # Settings lookup
    async def _get_setting(key, default=None):
        mapping = {
            "max_posts_per_day": max_per_day,
            "publish_spacing_hours": spacing_hours,
        }
        return mapping.get(key, default)

    db.get_setting_value = AsyncMock(side_effect=_get_setting)

    # Pool with acquire context manager for scheduling queries
    conn = AsyncMock()
    row = {"cnt": today_count, "latest": latest_today}
    conn.fetchrow = AsyncMock(return_value=row)

    pool = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)

    # Idempotency guard in publish_service calls pool.fetchrow directly
    pool.fetchrow = AsyncMock(return_value=None)
    db.pool = pool
    db.cloud_pool = None  # Ensure cloud_pool fallback uses db.pool

    return db


def _make_task(
    *,
    topic: str = "AI Revolution",
    content: str = "# Great Title\n\nSome article body here.",
    result: dict | None = None,
    task_metadata: dict | None = None,
    extra: dict | None = None,
):
    """Build a minimal task dict for publish_post_from_task."""
    task = {
        "topic": topic,
        "content": content,
        "result": json.dumps(result) if result else "{}",
        "task_metadata": json.dumps(task_metadata) if task_metadata else "{}",
    }
    if extra:
        task.update(extra)
    return task


def _stub_lazy_imports():
    """Return a dict of patches for all lazy imports inside publish_post_from_task.

    The function uses local `from X import Y` statements. We must patch the
    source modules so that when the function does `from services.foo import bar`,
    the import system finds our mock.
    """
    # utils.json_encoder — needed at the top of the function
    json_encoder_mod = MagicMock()
    json_encoder_mod.convert_decimals = lambda x: x
    json_encoder_mod.safe_json_dumps = json.dumps

    # services.default_author + services.category_resolver — author/category
    # helpers lifted out of content_router_service during Phase E2.
    default_author_mod = MagicMock()
    default_author_mod.get_or_create_default_author = AsyncMock(return_value="author-1")
    category_resolver_mod = MagicMock()
    category_resolver_mod.select_category_for_topic = AsyncMock(return_value="cat-1")

    # services.webhook_delivery_service
    webhook_mod = MagicMock()
    webhook_mod.emit_webhook_event = AsyncMock()

    # services.social_poster
    social_mod = MagicMock()
    social_mod.generate_and_distribute_social_posts = AsyncMock()

    # services.devto_service
    devto_svc_instance = AsyncMock()
    devto_svc_instance.cross_post_by_post_id = AsyncMock()
    devto_mod = MagicMock()
    devto_mod.DevToCrossPostService = MagicMock(return_value=devto_svc_instance)

    # services.revalidation_service — Glad-Labs/poindexter#327: publish_service
    # now imports trigger_isr_revalidate (the slug-aware wrapper) from
    # services.revalidation_service, not trigger_nextjs_revalidation from
    # routes.revalidate_routes. Keep the legacy stub too for tests that
    # still reach for the old import path.
    reval_mod = MagicMock()
    reval_mod.trigger_nextjs_revalidation = AsyncMock(return_value=True)
    reval_mod.trigger_isr_revalidate = AsyncMock(return_value=True)
    legacy_reval_routes = MagicMock()
    legacy_reval_routes.trigger_nextjs_revalidation = AsyncMock(return_value=True)

    # services.integrations.operator_notify — replaces the legacy
    # services.task_executor._notify_openclaw stub. publish_service now
    # imports notify_operator from this module.
    operator_notify_mod = MagicMock()
    operator_notify_mod.notify_operator = AsyncMock()

    # services.podcast_service
    podcast_mod = MagicMock()
    podcast_mod.generate_podcast_episode = AsyncMock()

    return {
        "utils.json_encoder": json_encoder_mod,
        "services.default_author": default_author_mod,
        "services.category_resolver": category_resolver_mod,
        "services.webhook_delivery_service": webhook_mod,
        "services.social_poster": social_mod,
        "services.devto_service": devto_mod,
        "services.revalidation_service": reval_mod,
        "routes.revalidate_routes": legacy_reval_routes,
        "services.integrations.operator_notify": operator_notify_mod,
        "services.podcast_service": podcast_mod,
    }


class _LazyImportContext:
    """Context manager that injects mock modules for lazy imports, then restores originals."""

    def __init__(self, overrides=None):
        self.stubs = _stub_lazy_imports()
        if overrides:
            for mod_name, mod_mock in overrides.items():
                self.stubs[mod_name] = mod_mock
        self._saved = {}

    def __enter__(self):
        for mod_name, mock_mod in self.stubs.items():
            self._saved[mod_name] = sys.modules.get(mod_name)
            sys.modules[mod_name] = mock_mod
        return self.stubs

    def __exit__(self, *exc):
        for mod_name, original in self._saved.items():
            if original is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = original
        return False


# ---------------------------------------------------------------------------
# PublishResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishResult:
    """PublishResult dataclass-like behavior."""

    def test_success_result_to_dict(self):
        r = PublishResult(
            success=True,
            post_id="p1",
            post_slug="my-slug",
            published_url="/posts/my-slug",
            post_title="My Title",
            revalidation_success=True,
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["post_id"] == "p1"
        assert d["post_slug"] == "my-slug"
        assert d["error"] is None

    def test_error_result_to_dict(self):
        r = PublishResult(success=False, error="boom")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "boom"
        assert d["post_id"] is None


# ---------------------------------------------------------------------------
# _parse_json_field
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseJsonField:
    """_parse_json_field safely handles strings, dicts, None, and junk."""

    def test_parses_json_string(self):
        assert _parse_json_field('{"a": 1}', "test") == {"a": 1}

    def test_returns_dict_as_is(self):
        d = {"x": 2}
        assert _parse_json_field(d, "test") is d

    def test_returns_empty_for_none(self):
        assert _parse_json_field(None, "test") == {}

    def test_returns_empty_for_empty_string(self):
        assert _parse_json_field("", "test") == {}

    def test_returns_empty_for_invalid_json(self):
        assert _parse_json_field("{bad json", "test") == {}

    def test_returns_empty_for_non_dict_type(self):
        assert _parse_json_field([1, 2], "test") == {}


# ---------------------------------------------------------------------------
# Happy path: publish_post_from_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishHappyPath:
    """publish_post_from_task with valid data creates a post and returns success."""

    @pytest.mark.asyncio
    @patch("services.static_export_service.export_post", new_callable=AsyncMock)
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_creates_post_with_correct_fields(self, mock_sched, mock_ping, mock_hooks, mock_export):
        db = _make_db()
        task = _make_task(
            topic="AI Revolution",
            content="# Great Title\n\nBody text here.",
            result={"seo_description": "A great post", "seo_keywords": ["ai", "ml"]},
        )

        with _LazyImportContext():
            result = await publish_post_from_task(
                db_service=db,
                task=task,
                task_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                publisher="test-user",
                trigger_revalidation=True,
                queue_social=False,
            )

        assert result.success is True
        assert result.post_id == "post-abc-123"
        assert result.post_title == "Great Title"
        assert "great-title" in result.post_slug
        assert result.published_url.startswith("/posts/")

        # Verify create_post was called with the right shape
        db.create_post.assert_awaited_once()
        post_data = db.create_post.call_args[0][0]
        assert post_data["title"] == "Great Title"
        assert post_data["author_id"] == "author-1"
        assert post_data["category_id"] == "cat-1"
        assert post_data["status"] == "published"
        assert post_data["seo_description"] == "A great post"
        assert post_data["seo_keywords"] == "ai, ml"

    @pytest.mark.asyncio
    async def test_idempotency_guard_skips_duplicate(self):
        """If a post already exists for this task, return success without creating another."""
        db = _make_db()
        # Simulate existing post matching the task_id suffix in slug
        task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        existing_row = {"id": "existing-post-id", "slug": f"great-title-{task_id[:8]}", "title": "Great Title"}
        db.pool.fetchrow = AsyncMock(return_value=existing_row)
        task = _make_task()

        result = await publish_post_from_task(
            db_service=db, task=task, task_id=task_id, queue_social=False,
        )

        assert result.success is True
        assert result.post_id == "existing-post-id"
        assert result.post_slug == f"great-title-{task_id[:8]}"
        db.create_post.assert_not_awaited()  # No new post created

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_updates_task_status_to_published(self, mock_sched, mock_ping, mock_hooks):
        db = _make_db()
        task = _make_task()

        with _LazyImportContext():
            await publish_post_from_task(
                db_service=db, task=task, task_id="tid-12345678", queue_social=False,
            )

        db.update_task_status.assert_awaited_once()
        call_args = db.update_task_status.call_args
        assert call_args[0][0] == "tid-12345678"
        assert call_args[0][1] == "published"

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_slug_format(self, mock_sched, mock_ping, mock_hooks):
        """Slug is lowercase, hyphenated, with task_id suffix."""
        db = _make_db()
        task = _make_task(content="# My Cool Post!\n\nBody here.")
        tid = "abcd1234-0000-0000-0000-000000000000"

        with _LazyImportContext():
            result = await publish_post_from_task(
                db_service=db, task=task, task_id=tid, queue_social=False,
            )

        assert result.success is True
        assert result.post_slug.endswith("-abcd1234")
        assert "my-cool-post" in result.post_slug


# ---------------------------------------------------------------------------
# Missing content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishMissingContent:
    """publish_post_from_task returns error when content or topic is missing."""

    @pytest.mark.asyncio
    async def test_missing_content_returns_error(self):
        db = _make_db()
        task = _make_task(topic="AI", content="")

        result = await publish_post_from_task(
            db_service=db, task=task, task_id="tid-00000000"
        )

        assert result.success is False
        assert "Missing content or topic" in result.error
        db.create_post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_topic_returns_error(self):
        db = _make_db()
        task = _make_task(topic="", content="")

        result = await publish_post_from_task(
            db_service=db, task=task, task_id="tid-00000001"
        )

        assert result.success is False
        assert "Missing content or topic" in result.error

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_content_from_result_fallback(self, mock_sched, mock_ping, mock_hooks):
        """Content found in result.draft_content when task.content is empty."""
        db = _make_db()
        task = _make_task(
            topic="Robots",
            content="",
            result={"draft_content": "# Robot Post\n\nRobots are cool."},
        )

        with _LazyImportContext():
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-fallback", queue_social=False,
            )

        assert result.success is True
        assert result.post_title == "Robot Post"


# ---------------------------------------------------------------------------
# DB failure on create_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishDbFailure:
    """create_post raising an exception returns a clean error."""

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_create_post_exception_returns_error(self, mock_sched, mock_ping, mock_hooks):
        db = _make_db()
        db.create_post = AsyncMock(side_effect=RuntimeError("connection lost"))
        task = _make_task()

        with _LazyImportContext():
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-fail", queue_social=False,
            )

        assert result.success is False
        assert "connection lost" in result.error


# ---------------------------------------------------------------------------
# Scheduling logic: _calculate_scheduled_publish_time
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchedulingLogic:
    """_calculate_scheduled_publish_time spacing and daily-cap logic."""

    @pytest.mark.asyncio
    async def test_no_posts_today_returns_none(self):
        """Under daily cap with zero posts today -> publish immediately."""
        db = _make_db(today_count=0, latest_today=None)
        result = await _calculate_scheduled_publish_time(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_under_cap_with_enough_spacing_returns_none(self):
        """Under cap and last post was 5 hours ago (spacing=4) -> publish now."""
        five_hours_ago = datetime.now(timezone.utc) - timedelta(hours=5)
        db = _make_db(today_count=1, latest_today=five_hours_ago, spacing_hours="4")
        result = await _calculate_scheduled_publish_time(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_under_cap_within_spacing_returns_future(self):
        """Under cap but last post was recent (spacing=4) -> returns future time.

        We set latest_today to (now - 1 hour) so that earliest_next = now + 3h,
        which is definitely in the future AND on the same UTC day (we use a
        short spacing of 4h and only 1h ago, so 3h ahead stays within the day
        unless we're very close to midnight -- handled by the fallback branch).
        """
        now = datetime.now(timezone.utc)
        latest = now - timedelta(hours=1)
        earliest_next = latest + timedelta(hours=4)  # now + 3h

        db = _make_db(today_count=1, latest_today=latest, spacing_hours="4")
        result = await _calculate_scheduled_publish_time(db)

        assert result is not None
        assert result > now
        # If spacing stays within today, we get the exact spaced time
        if earliest_next.date() == now.date():
            assert abs((result - earliest_next).total_seconds()) < 5
        else:
            # Near midnight: code falls through to next-day scheduler
            assert result.date() >= (now + timedelta(days=1)).date()

    @pytest.mark.asyncio
    async def test_naive_datetime_gets_utc_timezone(self):
        """If DB returns a naive datetime, it should be treated as UTC."""
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        db = _make_db(today_count=1, latest_today=one_hour_ago, spacing_hours="4")
        result = await _calculate_scheduled_publish_time(db)
        assert result is not None
        assert result.tzinfo is not None

    @pytest.mark.asyncio
    async def test_daily_cap_reached_schedules_future(self):
        """At daily cap (3/3) -> schedules for a future date."""
        db = _make_db(today_count=3, max_per_day="3")

        # For the next-day query, return 0 posts so it picks that day
        next_day_conn = AsyncMock()
        next_day_conn.fetchrow = AsyncMock(return_value={"cnt": 0, "latest": None})
        next_day_ctx = AsyncMock()
        next_day_ctx.__aenter__ = AsyncMock(return_value=next_day_conn)
        next_day_ctx.__aexit__ = AsyncMock(return_value=False)

        # First acquire() is for the today query (already returns cnt=3),
        # second acquire() is for the next-day query.
        call_count = 0
        today_ctx = db.pool.acquire()  # grab the original context

        def _acquire():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return today_ctx
            return next_day_ctx

        db.pool.acquire = MagicMock(side_effect=_acquire)

        result = await _calculate_scheduled_publish_time(db)
        assert result is not None
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        assert result.date() >= tomorrow
        # Should be in the 8-18 UTC window
        assert 8 <= result.hour <= 18

    @pytest.mark.asyncio
    async def test_settings_lookup_failure_returns_none(self):
        """If get_setting_value raises, fall through to immediate publish."""
        db = _make_db()
        db.get_setting_value = AsyncMock(side_effect=RuntimeError("settings dead"))
        result = await _calculate_scheduled_publish_time(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_db_query_failure_returns_none(self):
        """If the pool query fails, fall through to immediate publish."""
        db = _make_db()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("connection reset"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        db.pool.acquire = MagicMock(return_value=ctx)

        result = await _calculate_scheduled_publish_time(db)
        assert result is None


# ---------------------------------------------------------------------------
# Search engine ping is fire-and-forget
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchEnginePing:
    """_ping_search_engines failures don't propagate."""

    @pytest.mark.asyncio
    async def test_ping_failure_does_not_raise(self):
        """Even if httpx calls fail, the function completes silently."""

        from services.publish_service import _ping_search_engines

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network down"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await _ping_search_engines("https://gladlabs.io", "https://gladlabs.io/posts/test")

    @pytest.mark.asyncio
    async def test_ping_success_completes(self):
        """Successful pings complete without error."""
        from services.publish_service import _ping_search_engines

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await _ping_search_engines("https://gladlabs.io", "https://gladlabs.io/posts/test")

        # Both IndexNow and Google ping were attempted
        assert mock_client.get.await_count == 2


# ---------------------------------------------------------------------------
# Dev.to cross-post is non-blocking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDevtoCrossPost:
    """Dev.to cross-post failure doesn't break the publish flow."""

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_devto_import_failure_still_succeeds(self, mock_sched, mock_ping, mock_hooks):
        """If DevToCrossPostService init fails, publish still succeeds."""
        db = _make_db()
        task = _make_task()

        # Override devto_service to raise on import
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(side_effect=ImportError("no devto"))

        with _LazyImportContext(overrides={"services.devto_service": devto_mod}):
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-devto", queue_social=False,
            )

        assert result.success is True


# ---------------------------------------------------------------------------
# Scheduled publish time is applied to post_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScheduledPublishApplied:
    """When scheduling returns a future time, it gets set on the post."""

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    async def test_scheduled_time_set_on_post_data(self, mock_ping, mock_hooks):
        # honor_pacing=True opts into the scheduling path. The default
        # changed to False in commit 3f60ec4c because human approval is
        # the gate — callers that want pacing must opt in explicitly.
        future_time = datetime.now(timezone.utc) + timedelta(hours=10)
        db = _make_db()
        task = _make_task()

        with (
            patch(
                "services.publish_service._calculate_scheduled_publish_time",
                new_callable=AsyncMock, return_value=future_time,
            ),
            _LazyImportContext(),
        ):
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-sched",
                queue_social=False, honor_pacing=True,
            )

        assert result.success is True
        post_data = db.create_post.call_args[0][0]
        assert post_data["published_at"] == future_time

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    async def test_no_schedule_means_no_published_at_key(self, mock_ping, mock_hooks):
        db = _make_db()
        task = _make_task()

        with (
            patch(
                "services.publish_service._calculate_scheduled_publish_time",
                new_callable=AsyncMock, return_value=None,
            ),
            _LazyImportContext(),
        ):
            await publish_post_from_task(
                db_service=db, task=task, task_id="tid-nosched",
                queue_social=False, honor_pacing=True,
            )

        post_data = db.create_post.call_args[0][0]
        assert "published_at" not in post_data


# ---------------------------------------------------------------------------
# Webhook emission is non-blocking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookNonBlocking:
    """Webhook failure doesn't break publish."""

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_webhook_failure_still_succeeds(self, mock_sched, mock_ping, mock_hooks):
        db = _make_db()
        task = _make_task()

        webhook_mod = MagicMock()
        webhook_mod.emit_webhook_event = AsyncMock(side_effect=RuntimeError("webhook dead"))

        with _LazyImportContext(overrides={"services.webhook_delivery_service": webhook_mod}):
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-webhook", queue_social=False,
            )

        assert result.success is True


# ---------------------------------------------------------------------------
# ISR revalidation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRevalidation:
    """ISR revalidation behavior."""

    @pytest.mark.asyncio
    @patch("services.publish_service._post_has_pending_gates", new_callable=AsyncMock, return_value=False)
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_revalidation_success_reflected_in_result(self, mock_sched, mock_ping, mock_hooks, mock_gates):
        db = _make_db()
        task = _make_task()

        # #327: publish_service now imports trigger_isr_revalidate from
        # services.revalidation_service (not the legacy
        # trigger_nextjs_revalidation). Stub both so this test stays
        # valid regardless of which symbol is referenced inside.
        # #24 strict-mode: also stub _post_has_pending_gates → False so
        # the test asserts the no-gate path (revalidation fires). The
        # gate-defer path is tested separately in TestGateDeferral.
        reval_mod = MagicMock()
        reval_mod.trigger_nextjs_revalidation = AsyncMock(return_value=True)
        reval_mod.trigger_isr_revalidate = AsyncMock(return_value=True)

        with _LazyImportContext(overrides={"services.revalidation_service": reval_mod}):
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-reval",
                trigger_revalidation=True, queue_social=False,
            )

        assert result.success is True
        assert result.revalidation_success is True

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_revalidation_skipped_when_disabled(self, mock_sched, mock_ping, mock_hooks):
        db = _make_db()
        task = _make_task()

        with _LazyImportContext():
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-noreval",
                trigger_revalidation=False, queue_social=False,
            )

        assert result.success is True
        assert result.revalidation_success is False


# ===========================================================================
# Coverage fills for previously-untested helpers + fire_post_distribution_hooks
# ===========================================================================
#
# Targets the 277 uncovered lines reported by:
#   pytest --cov=services.publish_service ... → 48% baseline
# Specifically:
#   - 132-137: _post_has_pending_gates DB-error fallback
#   - 142-154: _sync_published_post local-only / failure handling
#   - 203-224: _embed_published_post happy path + provider-missing skip
#   - 1066-1236: fire_post_distribution_hooks (entire function)
#   - Gate-deferral branch in publish_post_from_task (line 685+)


# ---------------------------------------------------------------------------
# _post_has_pending_gates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostHasPendingGates:
    """The probe used by both publish + fire_post_distribution_hooks to
    decide whether to defer distribution."""

    @pytest.mark.asyncio
    async def test_returns_true_when_pending_row_exists(self):
        from services.publish_service import _post_has_pending_gates
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"?column?": 1})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        assert await _post_has_pending_gates(pool, "post-1") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_pending_rows(self):
        from services.publish_service import _post_has_pending_gates
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        assert await _post_has_pending_gates(pool, "post-1") is False

    @pytest.mark.asyncio
    async def test_returns_false_on_db_error(self):
        """DB error → False (publish-friendly default; better to ship than block)."""
        from services.publish_service import _post_has_pending_gates
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("DB blip"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        assert await _post_has_pending_gates(pool, "post-1") is False


# ---------------------------------------------------------------------------
# _sync_published_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSyncPublishedPost:
    """Cloud-DB sync after a publish — only fires on the local worker.

    Coverage target: lines 142-154 of services/publish_service.py.
    """

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    async def test_skips_when_not_running_local(self, _hooks):
        from services.publish_service import _sync_published_post
        # Should silently no-op — no exception, no work
        await _sync_published_post("post-1")

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=True)
    async def test_pushes_to_cloud_when_local(self, _hooks):
        from services.publish_service import _sync_published_post
        sync_instance = AsyncMock()
        sync_instance.push_post = AsyncMock(return_value=True)
        sync_instance.__aenter__ = AsyncMock(return_value=sync_instance)
        sync_instance.__aexit__ = AsyncMock(return_value=False)

        sync_mod = MagicMock()
        sync_mod.SyncService = MagicMock(return_value=sync_instance)

        with patch.dict(sys.modules, {"services.sync_service": sync_mod}):
            await _sync_published_post("post-xyz")

        sync_instance.push_post.assert_awaited_once_with("post-xyz")

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=True)
    async def test_swallows_sync_exception(self, _hooks):
        """Sync failure must NOT propagate — the post is already published locally."""
        from services.publish_service import _sync_published_post

        sync_mod = MagicMock()
        sync_mod.SyncService = MagicMock(side_effect=RuntimeError("cloud unreachable"))

        with patch.dict(sys.modules, {"services.sync_service": sync_mod}):
            # Must not raise
            await _sync_published_post("post-fail")

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=True)
    async def test_logs_warning_on_push_returns_false(self, _hooks):
        """push_post returning False is logged but doesn't raise."""
        from services.publish_service import _sync_published_post
        sync_instance = AsyncMock()
        sync_instance.push_post = AsyncMock(return_value=False)
        sync_instance.__aenter__ = AsyncMock(return_value=sync_instance)
        sync_instance.__aexit__ = AsyncMock(return_value=False)

        sync_mod = MagicMock()
        sync_mod.SyncService = MagicMock(return_value=sync_instance)

        with patch.dict(sys.modules, {"services.sync_service": sync_mod}):
            await _sync_published_post("post-fail")  # no raise


# ---------------------------------------------------------------------------
# _embed_published_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedPublishedPost:
    """pgvector embedding of a freshly-published post (RAG fodder).

    Coverage target: lines 203-224 of services/publish_service.py.
    """

    @pytest.mark.asyncio
    async def test_skips_when_embeddings_db_unavailable(self):
        from services.publish_service import _embed_published_post
        db = MagicMock()
        db.embeddings = None
        # Should no-op silently
        await _embed_published_post(db, {"id": "p", "title": "t", "content": "c"})

    @pytest.mark.asyncio
    async def test_skips_when_ollama_provider_not_registered(self):
        from services.publish_service import _embed_published_post
        db = MagicMock()
        db.embeddings = MagicMock()

        registry_mod = MagicMock()
        registry_mod.get_llm_providers = MagicMock(return_value=[])  # no ollama
        embed_svc_mod = MagicMock()

        with patch.dict(sys.modules, {
            "plugins.registry": registry_mod,
            "services.embedding_service": embed_svc_mod,
        }):
            await _embed_published_post(db, {"id": "p", "title": "t", "content": "c"})

    @pytest.mark.asyncio
    async def test_happy_path_embeds_post(self):
        from services.publish_service import _embed_published_post
        db = MagicMock()
        db.embeddings = MagicMock()

        ollama_provider = MagicMock()
        ollama_provider.name = "ollama_native"
        registry_mod = MagicMock()
        registry_mod.get_llm_providers = MagicMock(return_value=[ollama_provider])

        embed_instance = MagicMock()
        embed_instance.embed_post = AsyncMock()
        embed_svc_mod = MagicMock()
        embed_svc_mod.EmbeddingService = MagicMock(return_value=embed_instance)

        post = {"id": "p1", "title": "Title", "content": "body"}
        with patch.dict(sys.modules, {
            "plugins.registry": registry_mod,
            "services.embedding_service": embed_svc_mod,
        }):
            await _embed_published_post(db, post)

        embed_instance.embed_post.assert_awaited_once_with(post)

    @pytest.mark.asyncio
    async def test_swallows_embedding_exception(self):
        """Embedding failure is non-fatal — the published post is already live."""
        from services.publish_service import _embed_published_post
        db = MagicMock()
        db.embeddings = MagicMock()

        registry_mod = MagicMock()
        registry_mod.get_llm_providers = MagicMock(side_effect=RuntimeError("registry boom"))
        embed_svc_mod = MagicMock()

        with patch.dict(sys.modules, {
            "plugins.registry": registry_mod,
            "services.embedding_service": embed_svc_mod,
        }):
            # Must not raise
            await _embed_published_post(db, {"id": "p", "title": "t", "content": "c"})


# ---------------------------------------------------------------------------
# Gate-deferral branch in publish_post_from_task (#24)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishGateDeferral:
    """When the post has pending approval gates, distribution hooks
    (social/devto/podcast/video/short/RSS/ISR/static-export) are skipped.

    Coverage target: branches around line 685+ in publish_post_from_task.
    """

    @pytest.mark.asyncio
    @patch("services.publish_service._post_has_pending_gates", new_callable=AsyncMock, return_value=True)
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=True)
    @patch("services.publish_service._ping_search_engines", new_callable=AsyncMock)
    @patch("services.publish_service._calculate_scheduled_publish_time", new_callable=AsyncMock, return_value=None)
    async def test_pending_gates_skips_devto_social_isr(
        self, _sched, _ping, _hooks, mock_gates,
    ):
        """When gates are pending, social + devto + ISR + static-export are NOT fired."""
        db = _make_db()
        task = _make_task()

        # Devto module — track whether the service was instantiated
        devto_instance = AsyncMock()
        devto_instance.cross_post_by_post_id = AsyncMock()
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(return_value=devto_instance)

        # Social module — track the call
        social_mod = MagicMock()
        social_mod.generate_and_distribute_social_posts = AsyncMock()

        # Reval module
        reval_mod = MagicMock()
        reval_mod.trigger_isr_revalidate = AsyncMock(return_value=True)

        with _LazyImportContext(overrides={
            "services.devto_service": devto_mod,
            "services.social_poster": social_mod,
            "services.revalidation_service": reval_mod,
        }):
            result = await publish_post_from_task(
                db_service=db, task=task, task_id="tid-gated",
                queue_social=True, trigger_revalidation=True,
            )

        assert result.success is True
        # NEITHER distribution path should have been triggered
        social_mod.generate_and_distribute_social_posts.assert_not_called()
        devto_mod.DevToCrossPostService.assert_not_called()
        reval_mod.trigger_isr_revalidate.assert_not_called()
        # ISR result reflects the deferral
        assert result.revalidation_success is False


# ---------------------------------------------------------------------------
# fire_post_distribution_hooks — gate-engine re-trigger
# ---------------------------------------------------------------------------


def _make_post_row(
    *, post_id="post-1", title="My Title", slug="my-title",
    content="body", excerpt="excerpt", keywords="ai,ml",
    media: list[str] | None = None,
):
    return {
        "id": post_id,
        "title": title,
        "slug": slug,
        "content": content,
        "excerpt": excerpt,
        "seo_keywords": keywords,
        "media_to_generate": media or [],
    }


def _make_pool_for_fire(post_row, pending_gates: bool = False, status_flip: bool = True):
    """Build a pool that:
    - First acquire(): _post_has_pending_gates query → returns 1 row if pending_gates else None
    - Second acquire(): SELECT post by id → returns post_row
    - Third acquire(): UPDATE posts SET status='published' → returns 'UPDATE 1' or 'UPDATE 0'
    """
    # Connections for each acquire() — same conn instance, but separate
    # fetchrow / execute return values per call.
    conn = AsyncMock()
    pending_value = {"?column?": 1} if pending_gates else None
    conn.fetchrow = AsyncMock(side_effect=[pending_value, post_row])
    conn.execute = AsyncMock(return_value="UPDATE 1" if status_flip else "UPDATE 0")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.unit
class TestFirePostDistributionHooks:
    """fire_post_distribution_hooks re-fires distribution when approval gates clear.

    Coverage target: lines 1051-1236 (the entire function).
    """

    @pytest.mark.asyncio
    async def test_refuses_when_gates_still_pending(self):
        from services.publish_service import fire_post_distribution_hooks
        # Pool returns a pending row → refusal
        post_row = _make_post_row()
        pool, _conn = _make_pool_for_fire(post_row, pending_gates=True)
        db = MagicMock()
        db.pool = pool
        db.cloud_pool = None

        result = await fire_post_distribution_hooks(db, "post-1")
        assert result == {"fired": False, "reason": "pending_gates"}

    @pytest.mark.asyncio
    async def test_returns_post_not_found_when_select_returns_none(self):
        from services.publish_service import fire_post_distribution_hooks
        # First fetchrow (pending) → None, second (post lookup) → None
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=[None, None])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        db = MagicMock()
        db.pool = pool
        db.cloud_pool = None

        result = await fire_post_distribution_hooks(db, "ghost-post")
        assert result == {"fired": False, "reason": "post_not_found"}

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    async def test_happy_path_flips_status_and_fires_social_devto(self, _hooks):
        from services.publish_service import fire_post_distribution_hooks
        post_row = _make_post_row()
        pool, _conn = _make_pool_for_fire(post_row, pending_gates=False, status_flip=True)
        db = MagicMock()
        db.pool = pool
        db.cloud_pool = None

        # All the lazy imports
        social_mod = MagicMock()
        social_mod.generate_and_distribute_social_posts = AsyncMock()
        devto_instance = AsyncMock()
        devto_instance.cross_post_by_post_id = AsyncMock()
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(return_value=devto_instance)
        export_mod = MagicMock()
        export_mod.export_post = AsyncMock()
        reval_mod = MagicMock()
        reval_mod.trigger_isr_revalidate = AsyncMock(return_value=True)

        with patch.dict(sys.modules, {
            "services.social_poster": social_mod,
            "services.devto_service": devto_mod,
            "services.static_export_service": export_mod,
            "services.revalidation_service": reval_mod,
        }):
            result = await fire_post_distribution_hooks(db, "post-1")

        assert result["fired"] is True
        assert result["status_flipped"] is True
        assert "social" in result["hooks"]
        assert "devto" in result["hooks"]
        assert "static_export" in result["hooks"]
        assert "isr_revalidate" in result["hooks"]
        # search_engines hook also fires (uses site_config.require)
        assert "search_engines" in result["hooks"]

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    async def test_no_status_flip_when_post_already_published(self, _hooks):
        """If the UPDATE returns 'UPDATE 0' (post not in awaiting_gates),
        status_flipped is NOT in the response and static_export/ISR don't fire
        (since they're inside the UPDATE-1 branch)."""
        from services.publish_service import fire_post_distribution_hooks
        post_row = _make_post_row()
        pool, _conn = _make_pool_for_fire(post_row, pending_gates=False, status_flip=False)
        db = MagicMock()
        db.pool = pool
        db.cloud_pool = None

        social_mod = MagicMock()
        social_mod.generate_and_distribute_social_posts = AsyncMock()
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(return_value=AsyncMock(
            cross_post_by_post_id=AsyncMock(),
        ))

        with patch.dict(sys.modules, {
            "services.social_poster": social_mod,
            "services.devto_service": devto_mod,
        }):
            result = await fire_post_distribution_hooks(db, "post-1")

        assert result["fired"] is True
        assert "status_flipped" not in result
        assert "static_export" not in result["hooks"]
        assert "isr_revalidate" not in result["hooks"]
        # social + devto still fire — they're outside the UPDATE-1 branch
        assert "social" in result["hooks"]
        assert "devto" in result["hooks"]

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=True)
    async def test_fires_podcast_video_short_only_when_in_media_list(self, _hooks):
        """Per-medium hooks only fire for media that is in media_to_generate."""
        from services.publish_service import fire_post_distribution_hooks

        # Only podcast + short — NOT video
        post_row = _make_post_row(media=["podcast", "short"])
        pool, _conn = _make_pool_for_fire(post_row, pending_gates=False, status_flip=False)
        db = MagicMock()
        db.pool = pool
        db.cloud_pool = None

        social_mod = MagicMock()
        social_mod.generate_and_distribute_social_posts = AsyncMock()
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(return_value=AsyncMock(
            cross_post_by_post_id=AsyncMock(),
        ))
        podcast_mod = MagicMock()
        podcast_mod.generate_podcast_episode = AsyncMock()
        video_mod = MagicMock()
        video_mod.generate_video_episode = AsyncMock()
        video_mod.generate_short_video_for_post = AsyncMock()

        with patch.dict(sys.modules, {
            "services.social_poster": social_mod,
            "services.devto_service": devto_mod,
            "services.podcast_service": podcast_mod,
            "services.video_service": video_mod,
        }):
            result = await fire_post_distribution_hooks(db, "post-1")

        assert "podcast" in result["hooks"]
        assert "short" in result["hooks"]
        assert "video" not in result["hooks"]

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    async def test_swallows_individual_hook_failures(self, _hooks):
        """Each hook is wrapped in try/except — one failure doesn't kill the rest."""
        from services.publish_service import fire_post_distribution_hooks
        post_row = _make_post_row()
        pool, _conn = _make_pool_for_fire(post_row, pending_gates=False, status_flip=False)
        db = MagicMock()
        db.pool = pool
        db.cloud_pool = None

        # Social raises, devto succeeds — overall result still fired=True,
        # devto still in hooks, social NOT in hooks.
        social_mod = MagicMock()
        social_mod.generate_and_distribute_social_posts = MagicMock(
            side_effect=RuntimeError("social import busted"),
        )
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(return_value=AsyncMock(
            cross_post_by_post_id=AsyncMock(),
        ))

        with patch.dict(sys.modules, {
            "services.social_poster": social_mod,
            "services.devto_service": devto_mod,
        }):
            result = await fire_post_distribution_hooks(db, "post-1")

        assert result["fired"] is True
        assert "social" not in result["hooks"]
        assert "devto" in result["hooks"]

    @pytest.mark.asyncio
    @patch("services.publish_service._should_run_post_publish_hooks", return_value=False)
    async def test_uses_cloud_pool_when_present(self, _hooks):
        """If db_service has a cloud_pool, that's the pool used (not db.pool)."""
        from services.publish_service import fire_post_distribution_hooks
        post_row = _make_post_row()
        cloud_pool, _conn = _make_pool_for_fire(post_row, pending_gates=False)

        # local pool — should NEVER be acquired
        local_pool = MagicMock()
        local_pool.acquire = MagicMock(side_effect=AssertionError("should use cloud_pool"))

        db = MagicMock()
        db.pool = local_pool
        db.cloud_pool = cloud_pool

        social_mod = MagicMock()
        social_mod.generate_and_distribute_social_posts = AsyncMock()
        devto_mod = MagicMock()
        devto_mod.DevToCrossPostService = MagicMock(return_value=AsyncMock(
            cross_post_by_post_id=AsyncMock(),
        ))

        with patch.dict(sys.modules, {
            "services.social_poster": social_mod,
            "services.devto_service": devto_mod,
        }):
            result = await fire_post_distribution_hooks(db, "post-1")

        assert result["fired"] is True


# ---------------------------------------------------------------------------
# _ping_search_engines — empty-key/empty-url skip branches (#198)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPingSearchEnginesEmptyConfig:
    """Lines 169-198: when site_config returns '' for the ping URLs the
    function skips that ping entirely."""

    @pytest.mark.asyncio
    async def test_empty_indexnow_url_skips_indexnow(self):
        """indexnow_ping_url='' → IndexNow ping is skipped."""
        from services.publish_service import _ping_search_engines

        sc = MagicMock()
        # IndexNow disabled, sitemap ping disabled too
        sc.get = MagicMock(side_effect=lambda k, default="": "" if "indexnow" in k or "sitemap" in k else default)

        with patch("services.publish_service.site_config", sc):
            mock_client = AsyncMock()
            mock_client.get = AsyncMock()
            with patch("httpx.AsyncClient") as mock_cls:
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                await _ping_search_engines("https://gladlabs.io", "https://gladlabs.io/posts/test")

        # Both pings were skipped → no GET calls
        mock_client.get.assert_not_called()
