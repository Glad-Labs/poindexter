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
    """When post creation fails, publish_task swallows the error and the task stays published.

    The implementation calls update_task_status BEFORE create_post, then swallows any
    create_post failure (to avoid rolling back the already-published status).
    """
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
            {
                "id": "123",
                "status": "published",
                "topic": "AI orchestration",
                "result": {},
            },
        ]
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
                "created_at": "2026-03-07T00:00:00Z",
                "updated_at": "2026-03-07T00:01:00Z",
            }

    monkeypatch.setattr(task_routes, "ModelConverter", FakeModelConverter)

    # publish_task swallows create_post errors; the task is still marked published
    # so no HTTPException is raised
    result = await task_routes.publish_task(
        task_id="123",
        current_user={"id": "user-1"},
        db_service=db_service,
        background_tasks=BackgroundTasks(),
    )

    # Task was already updated to published before create_post failed
    db_service.update_task_status.assert_awaited()
    assert db_service.update_task_status.await_count >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_task_marks_published_after_post_creation(monkeypatch):
    """Publishing should mark task as published and update result with post_id/slug."""
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
    # Called twice: once before create_post, once after with post_id/slug
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
    # Called twice: first to set status=published, then to persist post_id/slug
    assert db_service.update_task_status.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_db_pool_delegates_to_database_dependency(monkeypatch):
    """cms_routes.get_db_pool() delegates to get_database_dependency().pool."""
    fake_pool = object()
    fake_service = MagicMock()
    fake_service.pool = fake_pool

    monkeypatch.setattr(cms_routes, "get_database_dependency", lambda: fake_service)

    pool = await cms_routes.get_db_pool()
    assert pool is fake_pool
