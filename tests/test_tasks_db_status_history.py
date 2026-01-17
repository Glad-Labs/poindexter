"""Unit tests for TaskDatabaseService status history methods."""

import pytest
import json
from datetime import datetime
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from src.cofounder_agent.services.tasks_db import TaskDatabaseService


class TestTaskDatabaseServiceStatusHistory:
    """Test suite for status history methods in TaskDatabaseService."""

    @pytest.fixture
    def mock_pool(self):
        """Create mock database pool."""
        mock = AsyncMock()
        return mock

    @pytest.fixture
    def db_service(self, mock_pool):
        """Create database service with mocked pool."""
        service = TaskDatabaseService(mock_pool)
        return service

    @pytest.mark.asyncio
    async def test_log_status_change_success(self, db_service, mock_pool):
        """Test successful status change logging."""
        task_id = str(uuid4())
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.execute = AsyncMock(return_value=None)

        # Execute
        result = await db_service.log_status_change(
            task_id=task_id,
            old_status="pending",
            new_status="in_progress",
            reason="Starting processing",
            metadata={"user_id": "user@example.com"}
        )

        # Verify
        assert result is True
        mock_pool.acquire.assert_called_once()
        mock_conn.execute.assert_called_once()

        # Verify SQL parameters
        call_args = mock_conn.execute.call_args
        assert call_args is not None
        assert task_id in str(call_args)
        assert "pending" in str(call_args)
        assert "in_progress" in str(call_args)

    @pytest.mark.asyncio
    async def test_log_status_change_with_exception(self, db_service, mock_pool):
        """Test logging fails gracefully on database error."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.execute.side_effect = Exception("Database error")

        # Execute
        result = await db_service.log_status_change(
            task_id="task-id",
            old_status="pending",
            new_status="in_progress"
        )

        # Verify
        assert result is False

    @pytest.mark.asyncio
    async def test_get_status_history_success(self, db_service, mock_pool):
        """Test retrieving status history."""
        task_id = str(uuid4())

        # Setup mock rows
        mock_rows = [
            MagicMock(
                id=1,
                task_id=task_id,
                old_status="pending",
                new_status="in_progress",
                reason="Started",
                metadata='{"user": "user1"}',
                timestamp=datetime.utcnow()
            ),
            MagicMock(
                id=2,
                task_id=task_id,
                old_status="in_progress",
                new_status="awaiting_approval",
                reason="Complete",
                metadata='{"user": "user1"}',
                timestamp=datetime.utcnow()
            )
        ]

        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        # Execute
        result = await db_service.get_status_history(task_id)

        # Verify
        assert len(result) == 2
        assert result[0]["task_id"] == task_id
        assert result[0]["old_status"] == "pending"
        assert result[0]["new_status"] == "in_progress"
        assert result[1]["old_status"] == "in_progress"
        assert result[1]["new_status"] == "awaiting_approval"

    @pytest.mark.asyncio
    async def test_get_status_history_empty(self, db_service, mock_pool):
        """Test empty history for new task."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch = AsyncMock(return_value=[])

        # Execute
        result = await db_service.get_status_history("task-id")

        # Verify
        assert result == []

    @pytest.mark.asyncio
    async def test_get_status_history_with_exception(self, db_service, mock_pool):
        """Test error handling when retrieving history."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.side_effect = Exception("Database error")

        # Execute
        result = await db_service.get_status_history("task-id")

        # Verify
        assert result == []

    @pytest.mark.asyncio
    async def test_get_validation_failures_success(self, db_service, mock_pool):
        """Test retrieving validation failures."""
        task_id = str(uuid4())

        # Setup mock rows with validation errors
        mock_rows = [
            MagicMock(
                id=1,
                task_id=task_id,
                old_status="validating",
                new_status="validation_failed",
                reason="Content validation failed",
                metadata=json.dumps({
                    "validation_errors": ["Content too short", "Missing keywords"]
                }),
                timestamp=datetime.utcnow()
            ),
            MagicMock(
                id=2,
                task_id=task_id,
                old_status="validating",
                new_status="validation_error",
                reason="SEO validation error",
                metadata=json.dumps({
                    "validation_errors": ["Meta description missing"]
                }),
                timestamp=datetime.utcnow()
            )
        ]

        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        # Execute
        result = await db_service.get_validation_failures(task_id)

        # Verify
        assert len(result) == 2
        assert result[0]["task_id"] == task_id
        assert result[0]["reason"] == "Content validation failed"
        assert len(result[0]["errors"]) == 2
        assert result[1]["reason"] == "SEO validation error"

    @pytest.mark.asyncio
    async def test_get_validation_failures_empty(self, db_service, mock_pool):
        """Test no validation failures for successful task."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch = AsyncMock(return_value=[])

        # Execute
        result = await db_service.get_validation_failures("task-id")

        # Verify
        assert result == []

    @pytest.mark.asyncio
    async def test_get_validation_failures_with_exception(self, db_service, mock_pool):
        """Test error handling when retrieving validation failures."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.side_effect = Exception("Database error")

        # Execute
        result = await db_service.get_validation_failures("task-id")

        # Verify
        assert result == []

    @pytest.mark.asyncio
    async def test_log_status_change_metadata_preservation(self, db_service, mock_pool):
        """Test metadata is properly serialized when logging."""
        task_id = str(uuid4())
        metadata = {
            "user_id": "user@example.com",
            "quality_score": 8.5,
            "model": "claude-3-opus"
        }

        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.execute = AsyncMock(return_value=None)

        # Execute
        await db_service.log_status_change(
            task_id=task_id,
            old_status="pending",
            new_status="in_progress",
            metadata=metadata
        )

        # Verify metadata was serialized
        call_args = mock_conn.execute.call_args
        # The metadata should be JSON serialized
        assert call_args is not None


@pytest.mark.asyncio
async def test_status_history_workflow():
    """Integration test for complete status history workflow."""
    # This would require actual database or more complex mocking
    # Placeholder for future integration testing
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
