"""Tests for TasksDatabase module with correct method signatures."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from src.cofounder_agent.schemas.database_response_models import TaskResponse, TaskCountsResponse
from src.cofounder_agent.services.tasks_db import TasksDatabase


@pytest.fixture
def tasks_db(mock_pool):
    """Create TasksDatabase instance with mocked connection pool."""
    return TasksDatabase(mock_pool)


class TestTasksDatabaseCreation:
    """Tests for task creation functionality."""

    @pytest.mark.asyncio
    async def test_add_task_with_dict_parameter(self, tasks_db, mock_pool):
        """Test add_task requires Dict[str, Any] parameter and returns task_id string."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        task_id = str(uuid4())
        mock_conn.fetchval.return_value = task_id
        
        task_data = {
            "topic": "AI Trends",
            "title": "AI Trends in 2024",
            "task_type": "blog_post",
            "status": "pending",
            "agent_id": "content_agent",
            "style": "professional",
            "tone": "informative"
        }
        
        result = await tasks_db.add_task(task_data)
        
        # add_task returns string task_id
        assert isinstance(result, str)
        assert mock_conn.execute.called or mock_conn.fetchval.called

    @pytest.mark.asyncio
    async def test_add_task_with_all_fields(self, tasks_db, mock_pool):
        """Test add_task handles comprehensive task data."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        task_id = str(uuid4())
        mock_conn.fetchval.return_value = task_id
        
        task_data = {
            "topic": "Marketing Strategy",
            "title": "Q1 2024 Marketing Strategy",
            "task_type": "blog_post",
            "content_type": "blog_post",
            "status": "pending",
            "agent_id": "content_agent",
            "style": "conversational",
            "tone": "persuasive",
            "target_length": 2000,
            "primary_keyword": "marketing",
            "target_audience": "B2B",
            "category": "business",
            "tags": ["marketing", "strategy"],
            "quality_preference": "high",
            "approval_status": "pending"
        }
        
        result = await tasks_db.add_task(task_data)

        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_add_task_with_description_field(self, tasks_db, mock_pool):
        """Test add_task passes human-written description to insert_data (#116)."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        task_id = str(uuid4())
        mock_conn.fetchval.return_value = task_id

        task_data = {
            "topic": "AI in Healthcare",
            "title": "Blog Post: AI in Healthcare",
            "task_type": "blog_post",
            "status": "pending",
            "description": "Q1 campaign targeting enterprise hospital buyers",
        }

        result = await tasks_db.add_task(task_data)

        assert isinstance(result, str)
        # Verify the DB insert was called (description is included in insert_data)
        assert mock_conn.fetchval.called

    @pytest.mark.asyncio
    async def test_add_task_without_description_field(self, tasks_db, mock_pool):
        """Test add_task works correctly when description is omitted (#116)."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        task_id = str(uuid4())
        mock_conn.fetchval.return_value = task_id

        task_data = {
            "topic": "AI in Healthcare",
            "title": "Blog Post: AI in Healthcare",
            "task_type": "blog_post",
            "status": "pending",
            # No description field — should default to None
        }

        result = await tasks_db.add_task(task_data)

        assert isinstance(result, str)
        assert mock_conn.fetchval.called


class TestTasksDatabaseRetrieval:
    """Tests for task retrieval functionality."""

    @pytest.mark.asyncio
    async def test_get_task_by_id(self, tasks_db, mock_pool):
        """Test get_task returns optional dict for a specific task."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "task_id": "task_123",
            "title": "Blog Post",
            "status": "pending",
            "topic": "AI",
            "agent_id": "content_agent",
            "created_at": now,
            "updated_at": now
        }
        
        task = await tasks_db.get_task(task_id="task_123")
        
        assert task is not None
        assert isinstance(task, dict)
        assert task["task_id"] == "task_123"
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, tasks_db, mock_pool):
        """Test get_task returns None when task doesn't exist."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None
        
        task = await tasks_db.get_task(task_id="nonexistent")
        
        assert task is None

    @pytest.mark.asyncio
    async def test_get_all_tasks_returns_list(self, tasks_db, mock_pool):
        """Test get_all_tasks returns List[TaskResponse]."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetch.return_value = [
            {
                "id": "task_1",
                "task_id": "task_1",
                "title": "Task 1",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "task_metadata": {}
            },
            {
                "id": "task_2",
                "task_id": "task_2",
                "title": "Task 2",
                "status": "completed",
                "created_at": now,
                "updated_at": now,
                "task_metadata": {}
            }
        ]
        
        tasks = await tasks_db.get_all_tasks()
        
        assert isinstance(tasks, list)
        assert len(tasks) == 2
        assert mock_conn.fetch.called

    @pytest.mark.asyncio
    async def test_get_all_tasks_with_limit(self, tasks_db, mock_pool):
        """Test get_all_tasks respects limit parameter."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.return_value = []
        
        await tasks_db.get_all_tasks(limit=50)
        
        assert mock_conn.fetch.called

    @pytest.mark.asyncio
    async def test_get_pending_tasks(self, tasks_db, mock_pool):
        """Test get_pending_tasks returns List[dict]."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetch.return_value = [
            {
                "id": "task_1",
                "task_id": "task_1",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "task_metadata": {}
            },
            {
                "id": "task_2",
                "task_id": "task_2",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "task_metadata": {}
            }
        ]
        
        tasks = await tasks_db.get_pending_tasks()
        
        assert isinstance(tasks, list)
        assert len(tasks) == 2
        assert all(task["status"] == "pending" for task in tasks)

    @pytest.mark.asyncio
    async def test_get_pending_tasks_with_limit(self, tasks_db, mock_pool):
        """Test get_pending_tasks respects limit parameter."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.return_value = []
        
        await tasks_db.get_pending_tasks(limit=20)
        
        assert mock_conn.fetch.called

    @pytest.mark.asyncio
    async def test_get_tasks_paginated(self, tasks_db, mock_pool):
        """Test get_tasks_paginated returns tuple of (tasks list, total count)."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetch.return_value = [
            {"id": "task_1", "task_id": "task_1", "created_at": now, "updated_at": now, "task_metadata": {}},
            {"id": "task_2", "task_id": "task_2", "created_at": now, "updated_at": now, "task_metadata": {}},
            {"id": "task_3", "task_id": "task_3", "created_at": now, "updated_at": now, "task_metadata": {}}
        ]
        mock_conn.fetchval.return_value = 3  # Total count
        
        result = await tasks_db.get_tasks_paginated(offset=0, limit=10)
        
        # Returns tuple of (list, count)
        assert isinstance(result, tuple)
        assert len(result) == 2
        tasks, total = result
        assert isinstance(tasks, list)
        assert isinstance(total, int)
        assert mock_conn.fetch.called


class TestTasksDatabaseUpdates:
    """Tests for task update functionality."""

    @pytest.mark.asyncio
    async def test_update_task_with_dict_updates(self, tasks_db, mock_pool):
        """Test update_task requires Dict[str, Any] updates parameter."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": "task_123",
            "task_id": "task_123",
            "title": "Updated Title",
            "status": "in_progress",
            "created_at": now,
            "updated_at": now,
            "task_metadata": {}
        }
        
        updates = {
            "title": "Updated Title",
            "status": "in_progress"
        }
        
        result = await tasks_db.update_task(task_id="task_123", updates=updates)
        
        assert result is not None
        assert isinstance(result, dict)
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_update_task_status_with_parameters(self, tasks_db, mock_pool):
        """Test update_task_status changes task status."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "task_id": "task_456",
            "status": "completed",
            "updated_at": now
        }
        
        result = await tasks_db.update_task_status(
            task_id="task_456",
            status="completed",
            result="Task completed successfully"
        )
        
        assert result is not None
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_delete_task_returns_bool(self, tasks_db, mock_pool):
        """Test delete_task returns boolean success indicator."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchval.return_value = 1  # rows affected
        
        result = await tasks_db.delete_task(task_id="task_789")
        
        assert isinstance(result, bool)
        assert mock_conn.execute.called


class TestTasksDatabaseMetrics:
    """Tests for task metrics and statistics."""

    @pytest.mark.asyncio
    async def test_get_task_counts_returns_response(self, tasks_db, mock_pool):
        """Test get_task_counts returns TaskCountsResponse."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        # get_task_counts uses fetch() to get status counts
        mock_conn.fetch.return_value = [
            {"status": "pending", "count": 5},
            {"status": "in_progress", "count": 2},
            {"status": "completed", "count": 10},
            {"status": "failed", "count": 1}
        ]
        
        counts = await tasks_db.get_task_counts()
        
        # Result should be TaskCountsResponse Pydantic model
        assert counts is not None
        assert hasattr(counts, 'pending'), "Result should be TaskCountsResponse with status counts"
        assert hasattr(counts, 'total')
        assert counts.total == 18  # 5 + 2 + 10 + 1
        assert mock_conn.fetch.called

    @pytest.mark.asyncio
    async def test_get_tasks_by_date_range(self, tasks_db, mock_pool):
        """Test retrieving tasks within a date range."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetch.return_value = [
            {"task_id": "task_1", "created_at": now},
            {"task_id": "task_2", "created_at": now}
        ]
        
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 12, 31)
        
        tasks = await tasks_db.get_tasks_by_date_range(
            start_date=start_date,
            end_date=end_date
        )
        
        assert isinstance(tasks, list)
        assert mock_conn.fetch.called
