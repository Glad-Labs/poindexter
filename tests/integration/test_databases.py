"""Database integration-style tests for all 5 database modules.

These tests exercise real module logic with mocked asyncpg pool/connection objects,
covering success and failure paths for CRUD, pagination, status transitions,
and settings/cost/health flows.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.admin_db import AdminDatabase
from services.content_db import ContentDatabase
from services.database_service import DatabaseService
from services.tasks_db import TasksDatabase
from services.users_db import UsersDatabase
from services.writing_style_db import WritingStyleDatabase


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _as_mapping(value: Any) -> Dict[str, Any]:
    """Normalize pydantic/dict-like objects to dict for assertions."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    return dict(value)


@pytest.fixture(autouse=True)
def patch_model_converter(monkeypatch):
    """Patch converter methods to keep tests focused on DB module logic."""
    from schemas.model_converter import ModelConverter

    passthrough = staticmethod(lambda row: row)
    to_dict = staticmethod(lambda row: dict(row) if row else {})

    monkeypatch.setattr(ModelConverter, "to_user_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_oauth_account_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_task_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_post_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_category_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_tag_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_author_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_quality_evaluation_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_quality_improvement_log_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_orchestrator_training_data_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_cost_log_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_setting_response", passthrough)
    monkeypatch.setattr(ModelConverter, "to_dict", to_dict)


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="OK")
    return conn


@pytest.fixture
def mock_pool(mock_conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool.close = AsyncMock(return_value=None)
    return pool


# ---------------------------------------------------------------------------
# UsersDatabase tests
# ---------------------------------------------------------------------------


async def test_users_get_user_by_id_found(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": "u1", "email": "a@b.com"}
    db = UsersDatabase(mock_pool)
    user = await db.get_user_by_id("u1")
    assert _as_mapping(user)["id"] == "u1"


async def test_users_get_user_by_email_not_found(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = None
    db = UsersDatabase(mock_pool)
    assert await db.get_user_by_email("missing@example.com") is None


async def test_users_get_user_by_username_found(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": "u2", "username": "alice"}
    db = UsersDatabase(mock_pool)
    user = await db.get_user_by_username("alice")
    assert _as_mapping(user)["username"] == "alice"


async def test_users_create_user(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": "u3", "email": "new@example.com"}
    db = UsersDatabase(mock_pool)
    row = await db.create_user({"email": "new@example.com", "username": "new"})
    assert _as_mapping(row)["email"] == "new@example.com"


async def test_users_get_or_create_oauth_user_existing_oauth(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = [{"user_id": "u10"}, {"id": "u10", "email": "x@y.com"}]
    db = UsersDatabase(mock_pool)
    user = await db.get_or_create_oauth_user("github", "gh1", {"email": "x@y.com"})
    assert _as_mapping(user)["id"] == "u10"


async def test_users_get_or_create_oauth_user_existing_email(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = [None, {"id": "u11", "email": "x@y.com"}]
    db = UsersDatabase(mock_pool)
    user = await db.get_or_create_oauth_user("github", "gh2", {"email": "x@y.com"})
    assert _as_mapping(user)["id"] == "u11"
    assert mock_conn.execute.await_count == 1


async def test_users_get_or_create_oauth_user_create_new(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = [None, None, {"id": "u12", "email": "n@y.com"}]
    db = UsersDatabase(mock_pool)
    user = await db.get_or_create_oauth_user("github", "gh3", {"email": "n@y.com"})
    assert _as_mapping(user)["id"] == "u12"
    assert mock_conn.execute.await_count == 1


async def test_users_get_oauth_accounts(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"provider": "github"}, {"provider": "google"}]
    db = UsersDatabase(mock_pool)
    rows = await db.get_oauth_accounts("u1")
    assert len(rows) == 2


async def test_users_unlink_oauth_account_success(mock_pool, mock_conn):
    mock_conn.execute.return_value = "DELETE 1"
    db = UsersDatabase(mock_pool)
    assert await db.unlink_oauth_account("u1", "github") is True


async def test_users_unlink_oauth_account_error_returns_false(mock_pool, mock_conn):
    mock_conn.execute.side_effect = RuntimeError("boom")
    db = UsersDatabase(mock_pool)
    assert await db.unlink_oauth_account("u1", "github") is False


# ---------------------------------------------------------------------------
# TasksDatabase tests
# ---------------------------------------------------------------------------


async def test_tasks_get_pending_tasks_empty_when_no_pool(mock_conn):
    db = TasksDatabase(mock_conn)
    setattr(db, "pool", None)
    assert await db.get_pending_tasks() == []


async def test_tasks_get_pending_tasks_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": "t1"}, {"id": "t2"}]
    db = TasksDatabase(mock_pool)
    rows = await db.get_pending_tasks(limit=2)
    assert len(rows) == 2


async def test_tasks_get_pending_tasks_timeout_returns_empty(mock_pool, mock_conn):
    mock_conn.fetch.side_effect = asyncio.TimeoutError()
    db = TasksDatabase(mock_pool)
    assert await db.get_pending_tasks(limit=2) == []


async def test_tasks_get_all_tasks_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": "t1"}]
    db = TasksDatabase(mock_pool)
    rows = await db.get_all_tasks()
    assert len(rows) == 1


async def test_tasks_add_task_returns_id(mock_pool, mock_conn):
    mock_conn.fetchval.return_value = "task-123"
    db = TasksDatabase(mock_pool)
    task_id = await db.add_task({"task_name": "hello", "topic": "x"})
    assert task_id == "task-123"


async def test_tasks_get_task_numeric_path(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = [{"id": 1, "task_id": "task-1"}]
    db = TasksDatabase(mock_pool)
    row = await db.get_task("1")
    assert row is not None
    assert row["task_id"] == "task-1"


async def test_tasks_get_task_uuid_path(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1, "task_id": "abc"}
    db = TasksDatabase(mock_pool)
    row = await db.get_task("abc")
    assert row is not None
    assert row["task_id"] == "abc"


async def test_tasks_get_task_exception_returns_none(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = RuntimeError("fail")
    db = TasksDatabase(mock_pool)
    assert await db.get_task("abc") is None


async def test_tasks_update_task_status_primary_found(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = [{"id": 1, "status": "completed"}]
    db = TasksDatabase(mock_pool)
    row = await db.update_task_status("abc", "completed")
    assert row is not None
    assert row["status"] == "completed"


async def test_tasks_update_task_status_alternate_found(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = [None, {"id": 1, "status": "completed"}]
    db = TasksDatabase(mock_pool)
    row = await db.update_task_status("abc", "completed")
    assert row is not None
    assert row["status"] == "completed"


async def test_tasks_update_task_status_exception_returns_none(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = RuntimeError("fail")
    db = TasksDatabase(mock_pool)
    assert await db.update_task_status("abc", "completed") is None


async def test_tasks_update_task_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1, "content": "new"}
    db = TasksDatabase(mock_pool)
    row = await db.update_task("abc", {"task_metadata": {"content": "new"}})
    assert row is not None
    assert row["content"] == "new"


async def test_tasks_update_task_exception_returns_none(mock_pool, mock_conn):
    mock_conn.fetchrow.side_effect = RuntimeError("fail")
    db = TasksDatabase(mock_pool)
    assert await db.update_task("abc", {"title": "x"}) is None


async def test_tasks_get_tasks_paginated_success(mock_pool, mock_conn):
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = [{"id": 1}, {"id": 2}]
    db = TasksDatabase(mock_pool)
    rows, total = await db.get_tasks_paginated(limit=2)
    assert len(rows) == 2
    assert total == 2


async def test_tasks_get_tasks_paginated_error_returns_empty(mock_pool, mock_conn):
    mock_conn.fetchval.side_effect = RuntimeError("fail")
    db = TasksDatabase(mock_pool)
    rows, total = await db.get_tasks_paginated(limit=2)
    assert rows == []
    assert total == 0


async def test_tasks_get_task_counts_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [
        {"status": "pending", "count": 1},
        {"status": "completed", "count": 2},
    ]
    db = TasksDatabase(mock_pool)
    counts = await db.get_task_counts()
    assert counts.total == 3
    assert counts.pending == 1


async def test_tasks_get_task_counts_error_returns_zeros(mock_pool, mock_conn):
    mock_conn.fetch.side_effect = RuntimeError("fail")
    db = TasksDatabase(mock_pool)
    counts = await db.get_task_counts()
    assert counts.total == 0


async def test_tasks_get_queued_tasks_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": 1}]
    db = TasksDatabase(mock_pool)
    rows = await db.get_queued_tasks(limit=1)
    assert len(rows) == 1


async def test_tasks_get_tasks_by_date_range_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": 1, "created_at": datetime.now(timezone.utc)}]
    db = TasksDatabase(mock_pool)
    rows = await db.get_tasks_by_date_range()
    assert len(rows) == 1


async def test_tasks_delete_task_success(mock_pool, mock_conn):
    mock_conn.execute.return_value = "DELETE 1"
    db = TasksDatabase(mock_pool)
    assert await db.delete_task("1") is True


async def test_tasks_get_drafts_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": 1}]
    mock_conn.fetchval.return_value = 1
    db = TasksDatabase(mock_pool)
    rows, total = await db.get_drafts(limit=5)
    assert len(rows) == 1
    assert total == 1


async def test_tasks_log_status_change_success(mock_pool, mock_conn):
    mock_conn.execute.return_value = "INSERT 0 1"
    db = TasksDatabase(mock_pool)
    ok = await db.log_status_change("task-1", "pending", "in_progress")
    assert ok is True


async def test_tasks_get_status_history_success(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetch.return_value = [
        {
            "id": 1,
            "task_id": "t1",
            "old_status": "pending",
            "new_status": "failed",
            "reason": "validation",
            "metadata": "{\"foo\": \"bar\"}",
            "timestamp": now,
        }
    ]
    db = TasksDatabase(mock_pool)
    rows = await db.get_status_history("t1")
    assert rows[0]["metadata"]["foo"] == "bar"


async def test_tasks_get_validation_failures_success(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetch.return_value = [
        {
            "id": 1,
            "task_id": "t1",
            "old_status": "pending",
            "new_status": "validation_failed",
            "reason": "bad",
            "metadata": "{\"validation_errors\": [\"x\"], \"context\": {\"a\":1}}",
            "timestamp": now,
        }
    ]
    db = TasksDatabase(mock_pool)
    rows = await db.get_validation_failures("t1")
    assert rows[0]["errors"] == ["x"]


# ---------------------------------------------------------------------------
# ContentDatabase tests
# ---------------------------------------------------------------------------


async def test_content_create_post_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": "p1", "status": "draft"}
    db = ContentDatabase(mock_pool)
    row = await db.create_post({"title": "A", "slug": "a", "content": "x"})
    assert _as_mapping(row)["id"] == "p1"


async def test_content_get_post_by_slug_none(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = None
    db = ContentDatabase(mock_pool)
    assert await db.get_post_by_slug("missing") is None


async def test_content_update_post_invalid_columns_false(mock_pool, mock_conn):
    db = ContentDatabase(mock_pool)
    ok = await db.update_post(1, {"bad_col": "x"})
    assert ok is False


async def test_content_update_post_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1}
    db = ContentDatabase(mock_pool)
    ok = await db.update_post(1, {"title": "new"})
    assert ok is True


async def test_content_get_all_categories_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": 1, "name": "Tech"}]
    db = ContentDatabase(mock_pool)
    rows = await db.get_all_categories()
    assert len(rows) == 1


async def test_content_get_all_tags_success(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"id": 1, "name": "AI"}]
    db = ContentDatabase(mock_pool)
    rows = await db.get_all_tags()
    assert len(rows) == 1


async def test_content_get_author_by_name_none(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = None
    db = ContentDatabase(mock_pool)
    assert await db.get_author_by_name("unknown") is None


async def test_content_create_quality_evaluation_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1}
    db = ContentDatabase(mock_pool)
    row = await db.create_quality_evaluation({"content_id": "c1", "overall_score": 90})
    assert _as_mapping(row)["id"] == 1


async def test_content_create_quality_improvement_log_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1}
    db = ContentDatabase(mock_pool)
    row = await db.create_quality_improvement_log(
        {"content_id": "c1", "initial_score": 60, "improved_score": 80}
    )
    assert _as_mapping(row)["id"] == 1


async def test_content_get_metrics_success(mock_pool, mock_conn):
    async def _fetchval(sql, *args):
        if "COUNT(*) FROM content_tasks" in sql and "WHERE status" not in sql and "IN" not in sql:
            return 10
        if "WHERE status = $1" in sql and args and args[0] == "completed":
            return 8
        if "WHERE status = $1" in sql and args and args[0] == "failed":
            return 2
        if "status IN ($1, $2, $3)" in sql:
            return 1
        return 0

    async def _fetchrow(sql, *args):
        if "AVG(EXTRACT(EPOCH" in sql:
            return {"avg_seconds": 5.2}
        if "SUM(cost_usd)" in sql:
            return {"total": 12.5}
        return None

    mock_conn.fetchval.side_effect = _fetchval
    mock_conn.fetchrow.side_effect = _fetchrow
    db = ContentDatabase(mock_pool)
    metrics = await db.get_metrics()
    metrics_data = metrics.model_dump()
    assert "totalTasks" in metrics_data
    assert "completedTasks" in metrics_data
    assert "totalCost" in metrics_data
    assert metrics_data["totalTasks"] >= 0
    assert metrics_data["completedTasks"] >= 0
    assert metrics_data["totalCost"] >= 0


async def test_content_get_metrics_exception_returns_zero_model(mock_pool, mock_conn):
    mock_conn.fetchval.side_effect = RuntimeError("fail")
    db = ContentDatabase(mock_pool)
    metrics = await db.get_metrics()
    metrics_data = metrics.model_dump()
    assert metrics_data["totalTasks"] == 0


async def test_content_create_orchestrator_training_data_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1}
    db = ContentDatabase(mock_pool)
    row = await db.create_orchestrator_training_data({"execution_id": "e1"})
    assert _as_mapping(row)["id"] == 1


# ---------------------------------------------------------------------------
# AdminDatabase tests
# ---------------------------------------------------------------------------


async def test_admin_log_cost_success(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"id": 1, "cost_usd": 0.01}
    db = AdminDatabase(mock_pool)
    row = await db.log_cost(
        {
            "task_id": "t1",
            "phase": "research",
            "model": "m",
            "provider": "p",
            "cost_usd": 0.01,
        }
    )
    assert _as_mapping(row)["id"] == 1


async def test_admin_get_task_costs_empty(mock_pool, mock_conn):
    mock_conn.fetch.return_value = []
    db = AdminDatabase(mock_pool)
    result = await db.get_task_costs("t1")
    assert result.total == 0.0


async def test_admin_health_check_healthy(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetchval.return_value = now
    db = AdminDatabase(mock_pool)
    result = await db.health_check()
    assert result["status"] == "healthy"


async def test_admin_health_check_unhealthy(mock_pool, mock_conn):
    mock_conn.fetchval.side_effect = RuntimeError("db down")
    db = AdminDatabase(mock_pool)
    result = await db.health_check()
    assert result["status"] == "unhealthy"


async def test_admin_get_setting_found(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = {"key": "k", "value": "v"}
    db = AdminDatabase(mock_pool)
    result = await db.get_setting("k")
    assert _as_mapping(result)["key"] == "k"


async def test_admin_get_all_settings(mock_pool, mock_conn):
    mock_conn.fetch.return_value = [{"key": "a", "value": "1"}, {"key": "b", "value": "2"}]
    db = AdminDatabase(mock_pool)
    rows = await db.get_all_settings()
    assert len(rows) == 2


async def test_admin_set_setting_success(mock_pool, mock_conn):
    mock_conn.execute.return_value = "INSERT 0 1"
    db = AdminDatabase(mock_pool)
    assert await db.set_setting("k", {"a": 1}) is True


async def test_admin_delete_setting_success(mock_pool, mock_conn):
    mock_conn.execute.return_value = "UPDATE 1"
    db = AdminDatabase(mock_pool)
    assert await db.delete_setting("k") is True


async def test_admin_get_setting_value_json(mock_pool, mock_conn, monkeypatch):
    db = AdminDatabase(mock_pool)
    monkeypatch.setattr(db, "get_setting", AsyncMock(return_value={"value": "{\"a\": 1}"}))
    value = await db.get_setting_value("k")
    assert value["a"] == 1


async def test_admin_setting_exists_true(mock_pool, mock_conn):
    mock_conn.fetchval.return_value = True
    db = AdminDatabase(mock_pool)
    assert await db.setting_exists("k") is True


# ---------------------------------------------------------------------------
# WritingStyleDatabase tests
# ---------------------------------------------------------------------------


async def test_writing_create_writing_sample(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetchrow.return_value = {
        "id": 1,
        "user_id": "u1",
        "title": "S1",
        "description": "",
        "content": "hello world",
        "is_active": False,
        "word_count": 2,
        "char_count": 11,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    db = WritingStyleDatabase(mock_pool)
    row = await db.create_writing_sample("u1", "S1", "hello world")
    assert row["title"] == "S1"


async def test_writing_get_writing_sample_none(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = None
    db = WritingStyleDatabase(mock_pool)
    assert await db.get_writing_sample("x") is None


async def test_writing_get_user_writing_samples(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetch.return_value = [
        {
            "id": 1,
            "user_id": "u1",
            "title": "S1",
            "description": "",
            "content": "c",
            "is_active": False,
            "word_count": 1,
            "char_count": 1,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
    ]
    db = WritingStyleDatabase(mock_pool)
    rows = await db.get_user_writing_samples("u1")
    assert len(rows) == 1


async def test_writing_get_active_writing_sample(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetchrow.return_value = {
        "id": 2,
        "user_id": "u1",
        "title": "Active",
        "description": "",
        "content": "c",
        "is_active": True,
        "word_count": 1,
        "char_count": 1,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    db = WritingStyleDatabase(mock_pool)
    row = await db.get_active_writing_sample("u1")
    assert row is not None
    assert row["is_active"] is True


async def test_writing_set_active_writing_sample_not_found_raises(mock_pool, mock_conn):
    mock_conn.fetchrow.return_value = None
    db = WritingStyleDatabase(mock_pool)
    with pytest.raises(ValueError):
        await db.set_active_writing_sample("u1", "s1")


async def test_writing_update_writing_sample_no_fields_raises(mock_pool, mock_conn):
    db = WritingStyleDatabase(mock_pool)
    with pytest.raises(ValueError):
        await db.update_writing_sample("s1", "u1")


async def test_writing_update_writing_sample_success(mock_pool, mock_conn):
    now = datetime.now(timezone.utc)
    mock_conn.fetchrow.return_value = {
        "id": 2,
        "user_id": "u1",
        "title": "Updated",
        "description": "",
        "content": "text",
        "is_active": True,
        "word_count": 1,
        "char_count": 4,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    db = WritingStyleDatabase(mock_pool)
    row = await db.update_writing_sample("s1", "u1", title="Updated")
    assert row["title"] == "Updated"


async def test_writing_delete_writing_sample_true(mock_pool, mock_conn):
    mock_conn.execute.return_value = "DELETE 1"
    db = WritingStyleDatabase(mock_pool)
    assert await db.delete_writing_sample("s1", "u1") is True


# ---------------------------------------------------------------------------
# DatabaseService coordinator/delegation tests
# ---------------------------------------------------------------------------


async def test_database_service_initialize_and_close(monkeypatch):
    fake_pool = MagicMock()
    fake_pool.close = AsyncMock(return_value=None)

    async def _fake_create_pool(*args, **kwargs):
        return fake_pool

    monkeypatch.setattr("services.database_service.asyncpg.create_pool", _fake_create_pool)

    svc = DatabaseService(database_url="postgresql://u:p@localhost:5432/db")
    await svc.initialize()
    assert svc.pool is fake_pool
    assert svc.users is not None
    assert svc.tasks is not None
    assert svc.content is not None
    assert svc.admin is not None
    assert svc.writing_style is not None

    await svc.close()
    fake_pool.close.assert_awaited_once()


async def test_database_service_get_user_by_id_delegates(monkeypatch):
    svc = DatabaseService(database_url="postgresql://u:p@localhost:5432/db")
    svc.users = MagicMock()
    svc.users.get_user_by_id = AsyncMock(return_value={"id": "u1"})
    row = await svc.get_user_by_id("u1")
    assert row is not None
    assert row["id"] == "u1"


async def test_database_service_get_metrics_delegates(monkeypatch):
    svc = DatabaseService(database_url="postgresql://u:p@localhost:5432/db")
    svc.content = MagicMock()
    svc.content.get_metrics = AsyncMock(return_value={"total_tasks": 10})
    row = await svc.get_metrics()
    assert row["total_tasks"] == 10


def test_database_service_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        DatabaseService(database_url=None)
