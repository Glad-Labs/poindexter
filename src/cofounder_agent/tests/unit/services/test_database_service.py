"""
Unit tests for services/database_service.py

Tests DatabaseService: initialization (requires DATABASE_URL), delegation pattern
(each method delegates to the appropriate sub-module), initialize/close lifecycle,
and a representative cross-section of all 5 module domains.

DB connections are never actually made — asyncpg.create_pool is always mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.database_service import DatabaseService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(database_url: str = "postgresql://user:pass@localhost:5432/test_db") -> DatabaseService:
    """Return a DatabaseService without actually initializing a pool."""
    return DatabaseService(database_url=database_url)


def _attach_mock_modules(svc: DatabaseService) -> dict:
    """Attach MagicMock delegates to all sub-modules and return them."""
    mocks = {
        "users": MagicMock(),
        "tasks": MagicMock(),
        "content": MagicMock(),
        "admin": MagicMock(),
        "writing_style": MagicMock(),
    }
    for name, m in mocks.items():
        setattr(svc, name, m)
    return mocks


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestDatabaseServiceInit:
    def test_explicit_url_used(self):
        svc = DatabaseService(database_url="postgresql://user:pass@host:5432/db")
        assert svc.database_url == "postgresql://user:pass@host:5432/db"

    def test_reads_database_url_env_var(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@host/envdb")
        svc = DatabaseService()
        assert svc.database_url == "postgresql://env:env@host/envdb"

    def test_raises_when_no_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(ValueError, match="DATABASE_URL"):
            DatabaseService()

    def test_pool_starts_as_none(self):
        svc = make_service()
        assert svc.pool is None

    def test_modules_start_as_none(self):
        svc = make_service()
        assert svc.users is None
        assert svc.tasks is None
        assert svc.content is None
        assert svc.admin is None
        assert svc.writing_style is None


# ---------------------------------------------------------------------------
# initialize / close
# ---------------------------------------------------------------------------


class TestDatabaseServiceLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_creates_pool(self):
        svc = make_service()
        mock_pool = AsyncMock()

        with patch("asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)):
            with patch("services.database_service.UsersDatabase") as MockUsers, \
                 patch("services.database_service.TasksDatabase") as MockTasks, \
                 patch("services.database_service.ContentDatabase") as MockContent, \
                 patch("services.database_service.AdminDatabase") as MockAdmin, \
                 patch("services.database_service.WritingStyleDatabase") as MockWS:
                await svc.initialize()

        assert svc.pool is mock_pool
        MockUsers.assert_called_once_with(mock_pool)
        MockTasks.assert_called_once_with(mock_pool)
        MockContent.assert_called_once_with(mock_pool)
        MockAdmin.assert_called_once_with(mock_pool)
        MockWS.assert_called_once_with(mock_pool)

    @pytest.mark.asyncio
    async def test_close_calls_pool_close(self):
        svc = make_service()
        mock_pool = AsyncMock()
        svc.pool = mock_pool

        await svc.close()
        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_with_no_pool_does_not_raise(self):
        svc = make_service()
        await svc.close()  # pool is None, should not raise


# ---------------------------------------------------------------------------
# User delegation methods
# ---------------------------------------------------------------------------


class TestUserDelegation:
    @pytest.mark.asyncio
    async def test_get_user_by_id_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["users"].get_user_by_id = AsyncMock(return_value={"id": "u1"})
        result = await svc.get_user_by_id("u1")
        mocks["users"].get_user_by_id.assert_awaited_once_with("u1")
        assert result == {"id": "u1"}

    @pytest.mark.asyncio
    async def test_get_user_by_email_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["users"].get_user_by_email = AsyncMock(return_value=None)
        await svc.get_user_by_email("user@example.com")
        mocks["users"].get_user_by_email.assert_awaited_once_with("user@example.com")

    @pytest.mark.asyncio
    async def test_create_user_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        user_data = {"email": "new@example.com", "username": "new_user"}
        mocks["users"].create_user = AsyncMock(return_value={"id": "u2", **user_data})
        result = await svc.create_user(user_data)
        mocks["users"].create_user.assert_awaited_once_with(user_data)
        assert result["id"] == "u2"

    @pytest.mark.asyncio
    async def test_get_or_create_oauth_user_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["users"].get_or_create_oauth_user = AsyncMock(return_value={"id": "u3"})
        await svc.get_or_create_oauth_user("github", "gh-123", {"name": "User"})
        mocks["users"].get_or_create_oauth_user.assert_awaited_once_with(
            "github", "gh-123", {"name": "User"}
        )

    @pytest.mark.asyncio
    async def test_unlink_oauth_account_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["users"].unlink_oauth_account = AsyncMock(return_value=True)
        result = await svc.unlink_oauth_account("u1", "github")
        mocks["users"].unlink_oauth_account.assert_awaited_once_with("u1", "github")
        assert result is True


# ---------------------------------------------------------------------------
# Task delegation methods
# ---------------------------------------------------------------------------


class TestTaskDelegation:
    @pytest.mark.asyncio
    async def test_add_task_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        task_data = {"task_name": "Blog post", "topic": "AI"}
        mocks["tasks"].add_task = AsyncMock(return_value={"id": "t1", **task_data})
        result = await svc.add_task(task_data)
        mocks["tasks"].add_task.assert_awaited_once_with(task_data)
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_get_task_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["tasks"].get_task = AsyncMock(return_value={"id": "t1"})
        result = await svc.get_task("t1")
        mocks["tasks"].get_task.assert_awaited_once_with("t1")
        assert result is not None
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_update_task_status_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["tasks"].update_task_status = AsyncMock(return_value=True)
        result = await svc.update_task_status("t1", "completed", "result text")
        mocks["tasks"].update_task_status.assert_awaited_once_with("t1", "completed", "result text")
        assert result is True

    @pytest.mark.asyncio
    async def test_get_tasks_paginated_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["tasks"].get_tasks_paginated = AsyncMock(
            return_value={"tasks": [], "total": 0, "offset": 0, "limit": 20}
        )
        result = await svc.get_tasks_paginated(offset=0, limit=20, status="pending")
        mocks["tasks"].get_tasks_paginated.assert_awaited_once_with(0, 20, "pending", None, None)

    @pytest.mark.asyncio
    async def test_delete_task_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["tasks"].delete_task = AsyncMock(return_value=True)
        result = await svc.delete_task("t1")
        mocks["tasks"].delete_task.assert_awaited_once_with("t1")
        assert result is True


# ---------------------------------------------------------------------------
# Content delegation methods
# ---------------------------------------------------------------------------


class TestContentDelegation:
    @pytest.mark.asyncio
    async def test_create_post_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        post_data = {"title": "My Post", "content": "Content here"}
        mocks["content"].create_post = AsyncMock(return_value={"id": 1, **post_data})
        result = await svc.create_post(post_data)
        mocks["content"].create_post.assert_awaited_once_with(post_data)
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_get_post_by_slug_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["content"].get_post_by_slug = AsyncMock(return_value={"slug": "my-post"})
        result = await svc.get_post_by_slug("my-post")
        mocks["content"].get_post_by_slug.assert_awaited_once_with("my-post")

    @pytest.mark.asyncio
    async def test_get_metrics_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["content"].get_metrics = AsyncMock(return_value={"total_posts": 10})
        result = await svc.get_metrics()
        mocks["content"].get_metrics.assert_awaited_once()


# ---------------------------------------------------------------------------
# Admin delegation methods
# ---------------------------------------------------------------------------


class TestAdminDelegation:
    @pytest.mark.asyncio
    async def test_add_log_entry_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["admin"].add_log_entry = AsyncMock(return_value={"id": 1})
        await svc.add_log_entry("agent-1", "INFO", "Task started", {"task_id": "t1"})
        mocks["admin"].add_log_entry.assert_awaited_once_with(
            "agent-1", "INFO", "Task started", {"task_id": "t1"}
        )

    @pytest.mark.asyncio
    async def test_health_check_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["admin"].health_check = AsyncMock(return_value={"status": "healthy"})
        result = await svc.health_check("cofounder")
        mocks["admin"].health_check.assert_awaited_once_with("cofounder")
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_get_setting_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["admin"].get_setting = AsyncMock(return_value={"key": "theme", "value": "dark"})
        result = await svc.get_setting("theme")
        mocks["admin"].get_setting.assert_awaited_once_with("theme")

    @pytest.mark.asyncio
    async def test_set_setting_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["admin"].set_setting = AsyncMock(return_value={"key": "theme", "value": "light"})
        await svc.set_setting("theme", "light", category="ui", display_name="Theme")
        mocks["admin"].set_setting.assert_awaited_once_with(
            "theme", "light", "ui", "Theme", None
        )

    @pytest.mark.asyncio
    async def test_get_financial_summary_delegates(self):
        svc = make_service()
        mocks = _attach_mock_modules(svc)
        mocks["admin"].get_financial_summary = AsyncMock(return_value={"total_cost": 5.25})
        result = await svc.get_financial_summary(days=7)
        mocks["admin"].get_financial_summary.assert_awaited_once_with(7)
        assert result["total_cost"] == 5.25
