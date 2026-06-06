"""Unit tests for ``services/auto_publish.py`` (Glad-Labs/poindexter#647).

Covers ``auto_publish_task`` — the quality-gated, daily-limited
auto-publish path ported from the deleted task_executor during the
Prefect Stage-4 cutover.

Pinned contract (read from ``auto_publish_task`` ~line 87):

- bails (returns False) when ``published_today >= daily_post_limit``,
  before touching the task — the post stays in awaiting_approval
- bails when the task row is missing a ``featured_image_url`` — we
  never auto-publish image-less posts
- happy path: flips status → approved, stamps publish_mode='auto' +
  auto_published metadata, calls ``publish_post_from_task``, and
  returns True
- ``get_auto_publish_threshold`` reads the app_settings row (0 = off)

Each failure mode leaves the task in awaiting_approval (the safe
failure mode), so the assertions check that ``publish_post_from_task``
is NOT called on the bail paths.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _make_pool():
    """asyncpg pool double: bare ``execute`` + ``acquire()`` ctx manager."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    pool.fetchval = AsyncMock(return_value=0)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db(
    *,
    published_today=0,
    daily_limit="1",
    task=None,
    pool=None,
    cloud_pool=None,
):
    """DatabaseService double covering the surface auto_publish_task uses.

    ``published_today`` is what the daily-limit COUNT(*) returns;
    ``task`` is the row ``get_task`` returns (and re-returns after
    approval). ``daily_limit`` is the app_settings value.
    """
    pool = pool if pool is not None else _make_pool()
    db = MagicMock()
    db.pool = pool
    db.cloud_pool = cloud_pool  # None → check_pool falls back to db.pool

    # The daily-limit check uses check_pool.fetchval — set it on whichever
    # pool check_pool resolves to (cloud_pool or pool).
    check_pool = cloud_pool if cloud_pool is not None else pool
    check_pool.fetchval = AsyncMock(return_value=published_today)

    db.get_setting_value = AsyncMock(return_value=daily_limit)
    db.get_task = AsyncMock(return_value=task)
    db.update_task_status = AsyncMock(return_value=True)
    db.update_task = AsyncMock(return_value=True)
    db.mark_model_performance_outcome = AsyncMock(return_value=None)
    return db


def _make_site_config():
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default=None: default)
    return sc


def _publish_result(success=True):
    return SimpleNamespace(
        success=success,
        error=None if success else "publish failed",
        post_id="post-uuid-1",
        post_slug="auto-published-post",
        published_url="https://gladlabs.io/posts/auto-published-post",
    )


# ---------------------------------------------------------------------------
# get_auto_publish_threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAutoPublishThreshold:
    @pytest.mark.asyncio
    async def test_reads_threshold_from_settings(self):
        from modules.content.auto_publish import get_auto_publish_threshold

        db = MagicMock()
        db.get_setting_value = AsyncMock(return_value="80")
        assert await get_auto_publish_threshold(db) == 80.0

    @pytest.mark.asyncio
    async def test_default_zero_means_disabled(self):
        from modules.content.auto_publish import get_auto_publish_threshold

        db = MagicMock()
        db.get_setting_value = AsyncMock(return_value="0")
        assert await get_auto_publish_threshold(db) == 0.0


# ---------------------------------------------------------------------------
# auto_publish_task — bail paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoPublishBails:
    @pytest.mark.asyncio
    async def test_bails_when_daily_limit_reached(self):
        """published_today >= daily_limit ⇒ returns False, never publishes."""
        from modules.content.auto_publish import auto_publish_task

        db = _make_db(published_today=1, daily_limit="1")
        pub_mock = AsyncMock(return_value=_publish_result())
        with patch("services.publish_service.publish_post_from_task", pub_mock):
            result = await auto_publish_task(
                database_service=db,
                task_id="t-limit",
                quality_score=95.0,
                site_config=_make_site_config(),
            )
        assert result is False
        pub_mock.assert_not_awaited()
        # The task was never approved — it stays in awaiting_approval.
        db.update_task_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bails_when_featured_image_missing(self):
        """A task without a featured_image_url is not auto-published."""
        from modules.content.auto_publish import auto_publish_task

        task = {"task_id": "t-noimg", "featured_image_url": None}
        db = _make_db(published_today=0, daily_limit="1", task=task)
        pub_mock = AsyncMock(return_value=_publish_result())
        with patch("services.publish_service.publish_post_from_task", pub_mock):
            result = await auto_publish_task(
                database_service=db,
                task_id="t-noimg",
                quality_score=95.0,
                site_config=_make_site_config(),
            )
        assert result is False
        pub_mock.assert_not_awaited()
        # We checked the image BEFORE flipping status to approved.
        db.update_task_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bails_when_task_not_found(self):
        """get_task returns None ⇒ return False, no publish."""
        from modules.content.auto_publish import auto_publish_task

        db = _make_db(published_today=0, daily_limit="1", task=None)
        pub_mock = AsyncMock(return_value=_publish_result())
        with patch("services.publish_service.publish_post_from_task", pub_mock):
            result = await auto_publish_task(
                database_service=db,
                task_id="t-missing",
                quality_score=95.0,
                site_config=_make_site_config(),
            )
        assert result is False
        pub_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_database_service_none(self):
        from modules.content.auto_publish import auto_publish_task

        result = await auto_publish_task(
            database_service=None,
            task_id="t-none",
            quality_score=95.0,
            site_config=_make_site_config(),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_fails_closed_when_daily_limit_check_errors(self):
        """A DB error in the daily-limit COUNT(*) must NOT fall through and
        publish anyway (audit H3). Fail closed: return False, leave the task
        in awaiting_approval, never publish. The daily_post_limit rate cap
        is a safety rail — an unverifiable limit means we must not publish.

        This is the module that caused the 2026-05-26 unauthorized auto-publish
        incident; a fail-open daily-limit check could let a DB blip auto-publish
        an unbounded number of posts in a day.
        """
        from modules.content.auto_publish import auto_publish_task

        task = {
            "task_id": "t-dberr",
            "featured_image_url": "https://img/featured.png",
            "task_metadata": {},
        }
        db = _make_db(published_today=0, daily_limit="1", task=task)
        # Make the daily-limit COUNT(*) raise on whichever pool check_pool uses.
        check_pool = db.cloud_pool if db.cloud_pool is not None else db.pool
        check_pool.fetchval = AsyncMock(side_effect=RuntimeError("connection reset"))

        pub_mock = AsyncMock(return_value=_publish_result())
        with patch("services.publish_service.publish_post_from_task", pub_mock):
            result = await auto_publish_task(
                database_service=db,
                task_id="t-dberr",
                quality_score=95.0,
                site_config=_make_site_config(),
            )
        assert result is False
        pub_mock.assert_not_awaited()
        db.update_task_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# auto_publish_task — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoPublishHappyPath:
    @pytest.mark.asyncio
    async def test_publishes_and_updates_status_metadata(self):
        """Clears the gates ⇒ flips status → approved, stamps
        publish_mode='auto' + auto_published metadata, calls
        publish_post_from_task, returns True."""
        from modules.content.auto_publish import auto_publish_task

        task = {
            "task_id": "t-ok",
            "featured_image_url": "https://img/featured.png",
            "task_metadata": {},
        }
        db = _make_db(published_today=0, daily_limit="1", task=task)

        pub_mock = AsyncMock(return_value=_publish_result(success=True))
        # PipelineDB.add_distribution runs inside a suppress() — stub the
        # class so it doesn't try to hit the DB for real.
        pipeline_db = MagicMock()
        pipeline_db.add_distribution = AsyncMock(return_value=None)
        with patch(
            "services.publish_service.publish_post_from_task", pub_mock,
        ), patch(
            "services.pipeline_db.PipelineDB", return_value=pipeline_db,
        ):
            result = await auto_publish_task(
                database_service=db,
                task_id="t-ok",
                quality_score=92.0,
                site_config=_make_site_config(),
            )

        assert result is True
        # Status flipped to approved.
        db.update_task_status.assert_awaited_once_with("t-ok", "approved")
        # update_task stamped publish_mode='auto' + approval_status.
        # It is called positionally: update_task(task_id, {dict}).
        update_call = db.update_task.await_args
        assert update_call.args[0] == "t-ok"
        payload = update_call.args[1]
        assert payload["approval_status"] == "approved"
        assert payload["publish_mode"] == "auto"
        assert "auto_published" in payload["task_metadata"]
        # publish_post_from_task was invoked with the trusted-publisher tag.
        pub_mock.assert_awaited_once()
        assert pub_mock.await_args.kwargs["publisher"] == "auto_publish"
        # Learning-signal flip fired.
        db.mark_model_performance_outcome.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_publish_fails(self):
        """publish_post_from_task returns success=False ⇒ auto_publish
        returns False (post lands in awaiting_approval)."""
        from modules.content.auto_publish import auto_publish_task

        task = {
            "task_id": "t-pubfail",
            "featured_image_url": "https://img/featured.png",
            "task_metadata": {},
        }
        db = _make_db(published_today=0, daily_limit="1", task=task)

        pub_mock = AsyncMock(return_value=_publish_result(success=False))
        with patch("services.publish_service.publish_post_from_task", pub_mock):
            result = await auto_publish_task(
                database_service=db,
                task_id="t-pubfail",
                quality_score=92.0,
                site_config=_make_site_config(),
            )
        assert result is False
        pub_mock.assert_awaited_once()
