"""Regression tests for publish consistency and CMS DB pool initialization."""

import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException

# Some route imports require structlog, which may be unavailable in lightweight test envs.
if "structlog" not in sys.modules:
    structlog_stub = ModuleType("structlog")
    setattr(structlog_stub, "get_logger", lambda *args, **kwargs: MagicMock())
    sys.modules["structlog"] = structlog_stub

from routes import cms_routes, task_routes


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_task_fails_without_marking_published(monkeypatch):
    """Publishing should fail if post creation fails, without setting task status to published."""
    db_service = MagicMock()
    db_service.get_task = AsyncMock(
        return_value={
            "id": "123",
            "status": "approved",
            "topic": "AI orchestration",
            "result": {
                "content": "# AI orchestration\n\nDetailed content.",
                "seo_description": "SEO description",
                "seo_keywords": ["ai", "agents"],
            },
        }
    )
    db_service.create_post = AsyncMock(side_effect=RuntimeError("insert failed"))
    db_service.update_task_status = AsyncMock()

    monkeypatch.setattr(
        "services.content_router_service._get_or_create_default_author",
        AsyncMock(return_value="author-1"),
        raising=False,
    )
    monkeypatch.setattr(
        "services.content_router_service._select_category_for_topic",
        AsyncMock(return_value="category-1"),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        await task_routes.publish_task(
            task_id="123",
            current_user={"id": "user-1"},
            db_service=db_service,
            background_tasks=BackgroundTasks(),
        )

    assert exc.value.status_code == 500
    db_service.update_task_status.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_task_marks_published_after_post_creation(monkeypatch):
    """Publishing should mark task as published only after post creation succeeds."""
    updated_task = {
        "id": "123",
        "status": "published",
        "task_type": "blog_post",
        "topic": "AI orchestration",
        "created_at": "2026-03-07T00:00:00Z",
        "updated_at": "2026-03-07T00:01:00Z",
    }

    db_service = MagicMock()
    db_service.get_task = AsyncMock(
        side_effect=[
            {
                "id": "123",
                "status": "approved",
                "topic": "AI orchestration",
                "result": {
                    "content": "# AI orchestration\n\nDetailed content.",
                    "seo_description": "SEO description",
                    "seo_keywords": ["ai", "agents"],
                },
            },
            updated_task,
        ]
    )
    db_service.create_post = AsyncMock(return_value=SimpleNamespace(id="post-1"))
    db_service.update_task_status = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(
        "services.content_router_service._get_or_create_default_author",
        AsyncMock(return_value="author-1"),
        raising=False,
    )
    monkeypatch.setattr(
        "services.content_router_service._select_category_for_topic",
        AsyncMock(return_value="category-1"),
        raising=False,
    )
    class FakeModelConverter:
        @staticmethod
        def to_task_response(task):
            return task

        @staticmethod
        def task_response_to_unified(task):
            return {
                "id": task["id"],
                "task_type": task.get("task_type", "blog_post"),
                "topic": task.get("topic", ""),
                "status": task["status"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
            }

    monkeypatch.setattr(task_routes, "ModelConverter", FakeModelConverter)

    response = await task_routes.publish_task(
        task_id="123",
        current_user={"id": "user-1"},
        db_service=db_service,
        background_tasks=BackgroundTasks(),
    )

    assert response.status == "published"
    db_service.create_post.assert_awaited_once()
    db_service.update_task_status.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_db_pool_is_concurrency_safe(monkeypatch):
    """Concurrent /api/posts calls should not observe an uninitialized pool."""

    class FakeDatabaseService:
        instances = 0
        initialize_calls = 0

        def __init__(self):
            FakeDatabaseService.instances += 1
            self.pool = None

        async def initialize(self):
            FakeDatabaseService.initialize_calls += 1
            await asyncio.sleep(0.01)
            self.pool = object()

    # Reset globals and replace implementation for deterministic behavior.
    monkeypatch.setattr(cms_routes, "_db_service", None)
    monkeypatch.setattr(cms_routes, "_db_service_init_lock", asyncio.Lock())
    monkeypatch.setattr(cms_routes, "DatabaseService", FakeDatabaseService)

    pools = await asyncio.gather(*[cms_routes.get_db_pool() for _ in range(5)])

    assert FakeDatabaseService.instances == 1
    assert FakeDatabaseService.initialize_calls == 1
    assert all(pool is pools[0] for pool in pools)
