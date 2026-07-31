"""Tests for services/blog_task_creation.py (extracted in poindexter#947).

The blog-post creation path moved out of routes/task_routes.py so the HTTP
route and the chat agent's ``create_post`` tool share one implementation.
These pin the transport-agnostic error contract + the invariants the move
carried over (dedup guard wiring, defaults, throttle flag, task_data shape).
Route-level behaviour (HTTP statuses) stays covered by test_task_routes.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import services.blog_task_creation as btc
import services.pipeline_throttle as pipeline_throttle
import services.topic_dedup_guard as topic_dedup_guard
from schemas.task_schemas import UnifiedTaskRequest
from services.blog_task_creation import (
    BlogTaskCreationError,
    create_blog_post_task,
)


class FakeDb:
    def __init__(self):
        self.pool = object()
        self.added: dict[str, Any] | None = None

    async def add_task(self, task_data):
        self.added = task_data
        return task_data["id"]


def _request(**overrides) -> UnifiedTaskRequest:
    fields = dict(
        task_type="blog_post", topic="A perfectly novel topic",
        target_length=1500,
    )
    fields.update(overrides)
    return UnifiedTaskRequest(**fields)


@pytest.fixture
def quiet_guards(monkeypatch):
    """Dedup passes, throttle reports not-full — the happy-path baseline."""

    async def no_dup(topic, *, site_config=None, force=False):
        return None

    async def not_full(pool, *, site_config=None):
        return (False, 0, 0)

    monkeypatch.setattr(topic_dedup_guard, "assert_topic_not_duplicate", no_dup)
    monkeypatch.setattr(pipeline_throttle, "is_queue_full", not_full)


@pytest.mark.unit
class TestCreateBlogPostTask:
    def test_happy_path_shape(self, quiet_guards):
        db = FakeDb()
        out = asyncio.run(create_blog_post_task(
            _request(), db_service=db, site_config=None, user_id="operator",
        ))
        assert out["status"] == "pending"
        assert out["task_id"] == db.added["id"]
        assert "queue_full" not in out
        assert db.added["task_type"] == "blog_post"
        assert db.added["topic"] == "A perfectly novel topic"
        assert db.added["user_id"] == "operator"
        assert db.added["style"] == "narrative"          # default preserved
        assert db.added["tone"] == "professional"        # default preserved
        assert db.added["target_length"] == 1500         # explicit wins

    def test_duplicate_topic_maps_to_409(self, monkeypatch):
        class Match:
            similarity = 0.91
            metadata = {"title": "Existing"}
            source_id = "post-1"

        async def dup(topic, *, site_config=None, force=False):
            raise topic_dedup_guard.DuplicateTopicError(
                topic=topic, match=Match(), threshold=0.8,
            )

        monkeypatch.setattr(topic_dedup_guard, "assert_topic_not_duplicate", dup)
        with pytest.raises(BlogTaskCreationError) as exc_info:
            asyncio.run(create_blog_post_task(
                _request(), db_service=FakeDb(), site_config=None,
            ))
        assert exc_info.value.status_code == 409
        assert "force=true" in exc_info.value.detail

    def test_force_is_threaded_to_dedup_guard(self, monkeypatch):
        seen = {}

        async def spy(topic, *, site_config=None, force=False):
            seen["force"] = force

        async def not_full(pool, *, site_config=None):
            return (False, 0, 0)

        monkeypatch.setattr(topic_dedup_guard, "assert_topic_not_duplicate", spy)
        monkeypatch.setattr(pipeline_throttle, "is_queue_full", not_full)
        asyncio.run(create_blog_post_task(
            _request(force=True), db_service=FakeDb(), site_config=None,
        ))
        assert seen["force"] is True

    def test_auto_topic_without_pool_is_503(self):
        db = FakeDb()
        db.pool = None
        with pytest.raises(BlogTaskCreationError) as exc_info:
            asyncio.run(create_blog_post_task(
                _request(topic="auto"), db_service=db, site_config=None,
            ))
        assert exc_info.value.status_code == 503

    def test_throttled_queue_is_flagged_not_refused(self, monkeypatch):
        async def no_dup(topic, *, site_config=None, force=False):
            return None

        async def full(pool, *, site_config=None):
            return (True, 7, 5)

        monkeypatch.setattr(topic_dedup_guard, "assert_topic_not_duplicate", no_dup)
        monkeypatch.setattr(pipeline_throttle, "is_queue_full", full)
        db = FakeDb()
        out = asyncio.run(create_blog_post_task(
            _request(), db_service=db, site_config=None,
        ))
        assert db.added is not None, "throttle must flag, never refuse"
        assert out["queue_full"] is True
        assert out["queue_position"] == 7 and out["queue_limit"] == 5


@pytest.mark.unit
class TestResolveNicheForTopics:
    def test_unknown_slug_is_404(self, monkeypatch):
        class Nsvc:
            def __init__(self, pool):
                pass

            async def get_by_slug(self, slug):
                return None

        import services.niche_service as niche_service
        monkeypatch.setattr(niche_service, "NicheService", Nsvc)
        with pytest.raises(BlogTaskCreationError) as exc_info:
            asyncio.run(btc.resolve_niche_for_topics(object(), "nope"))
        assert exc_info.value.status_code == 404

    def test_multiple_active_niches_is_422_naming_slugs(self, monkeypatch):
        class Niche:
            def __init__(self, slug):
                self.slug = slug

        class Nsvc:
            def __init__(self, pool):
                pass

            async def list_active(self):
                return [Niche("a"), Niche("b")]

        import services.niche_service as niche_service
        monkeypatch.setattr(niche_service, "NicheService", Nsvc)
        with pytest.raises(BlogTaskCreationError) as exc_info:
            asyncio.run(btc.resolve_niche_for_topics(object(), None))
        assert exc_info.value.status_code == 422
        assert "a, b" in exc_info.value.detail
