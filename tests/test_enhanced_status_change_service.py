"""Integration tests for EnhancedStatusChangeService."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from uuid import uuid4

from src.cofounder_agent.services.enhanced_status_change_service import (
    EnhancedStatusChangeService
)


class TestEnhancedStatusChangeService:
    """Integration tests for enhanced status change service."""

    @pytest.fixture
    def mock_db_service(self):
        """Create mock database service."""
        mock_db = AsyncMock()
        return mock_db

    @pytest.fixture
    def service(self, mock_db_service):
        """Create service instance with mocked database."""
        return EnhancedStatusChangeService(mock_db_service)

    @pytest.mark.asyncio
    async def test_validate_and_change_status_valid_transition(self, service, mock_db_service):
        """Test successful status change with valid transition."""
        task_id = str(uuid4())

        # Setup mock task
        mock_db_service.get_task.return_value = {
            "id": task_id,
            "status": "pending",
            "task_metadata": {}
        }
        mock_db_service.log_status_change.return_value = True
        mock_db_service.update_task.return_value = {"status": "in_progress"}

        # Execute
        success, message, errors = await service.validate_and_change_status(
            task_id=task_id,
            new_status="in_progress",
            reason="Starting processing",
            user_id="user@example.com"
        )

        # Verify
        assert success is True
        assert errors == []
        assert "Status changed" in message
        mock_db_service.get_task.assert_called_once_with(task_id)
        mock_db_service.log_status_change.assert_called_once()
        mock_db_service.update_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_change_status_task_not_found(self, service, mock_db_service):
        """Test status change fails when task doesn't exist."""
        task_id = str(uuid4())
        mock_db_service.get_task.return_value = None

        # Execute
        success, message, errors = await service.validate_and_change_status(
            task_id=task_id,
            new_status="in_progress"
        )

        # Verify
        assert success is False
        assert "not found" in message.lower()
        assert errors == ["task_not_found"]

    @pytest.mark.asyncio
    async def test_validate_and_change_status_invalid_transition(self, service, mock_db_service):
        """Test status change fails with invalid transition."""
        task_id = str(uuid4())

        # Setup mock task (pending status)
        mock_db_service.get_task.return_value = {
            "id": task_id,
            "status": "pending",
            "task_metadata": {}
        }

        # Execute - try invalid transition (pending -> published)
        success, message, errors = await service.validate_and_change_status(
            task_id=task_id,
            new_status="published",
            reason="Invalid transition"
        )

        # Verify
        assert success is False
        assert len(errors) > 0
        mock_db_service.log_status_change.assert_not_called()
        mock_db_service.update_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_status_audit_trail(self, service, mock_db_service):
        """Test retrieving audit trail for a task."""
        task_id = str(uuid4())

        # Setup mock history
        mock_history = [
            {
                "id": 1,
                "task_id": task_id,
                "old_status": "pending",
                "new_status": "in_progress",
                "reason": "Started",
                "metadata": {},
                "timestamp": datetime.utcnow().isoformat()
            },
            {
                "id": 2,
                "task_id": task_id,
                "old_status": "in_progress",
                "new_status": "awaiting_approval",
                "reason": "Complete",
                "metadata": {},
                "timestamp": datetime.utcnow().isoformat()
            }
        ]

        mock_db_service.get_status_history.return_value = mock_history

        # Execute
        result = await service.get_status_audit_trail(task_id)

        # Verify
        assert result["task_id"] == task_id
        assert result["history_count"] == 2
        assert len(result["history"]) == 2
        mock_db_service.get_status_history.assert_called_once_with(task_id, 50)

    @pytest.mark.asyncio
    async def test_get_status_audit_trail_empty(self, service, mock_db_service):
        """Test audit trail is empty for new task."""
        task_id = str(uuid4())
        mock_db_service.get_status_history.return_value = []

        # Execute
        result = await service.get_status_audit_trail(task_id)

        # Verify
        assert result["task_id"] == task_id
        assert result["history_count"] == 0
        assert result["history"] == []

    @pytest.mark.asyncio
    async def test_get_validation_failures(self, service, mock_db_service):
        """Test retrieving validation failures for a task."""
        task_id = str(uuid4())

        # Setup mock failures
        mock_failures = [
            {
                "timestamp": datetime.utcnow().isoformat(),
                "reason": "Content validation failed",
                "errors": ["Content too short", "Missing keywords"],
                "context": {"stage": "validation"}
            },
            {
                "timestamp": datetime.utcnow().isoformat(),
                "reason": "SEO validation failed",
                "errors": ["Meta description missing"],
                "context": {"stage": "seo_check"}
            }
        ]

        mock_db_service.get_validation_failures.return_value = mock_failures

        # Execute
        result = await service.get_validation_failures(task_id)

        # Verify
        assert result["task_id"] == task_id
        assert result["failure_count"] == 2
        assert len(result["failures"]) == 2
        mock_db_service.get_validation_failures.assert_called_once_with(task_id, 50)

    @pytest.mark.asyncio
    async def test_get_validation_failures_empty(self, service, mock_db_service):
        """Test no validation failures for successful task."""
        task_id = str(uuid4())
        mock_db_service.get_validation_failures.return_value = []

        # Execute
        result = await service.get_validation_failures(task_id)

        # Verify
        assert result["task_id"] == task_id
        assert result["failure_count"] == 0
        assert result["failures"] == []

    @pytest.mark.asyncio
    async def test_validate_and_change_status_with_metadata(self, service, mock_db_service):
        """Test status change preserves metadata context."""
        task_id = str(uuid4())

        # Setup mock task
        mock_db_service.get_task.return_value = {
            "id": task_id,
            "status": "pending",
            "task_metadata": {}
        }
        mock_db_service.log_status_change.return_value = True
        mock_db_service.update_task.return_value = {"status": "in_progress"}

        # Setup metadata
        metadata = {
            "quality_score": 8.5,
            "model": "claude-3-opus",
            "execution_time": 45.2
        }

        # Execute
        success, _, errors = await service.validate_and_change_status(
            task_id=task_id,
            new_status="in_progress",
            reason="Starting",
            metadata=metadata,
            user_id="user@example.com"
        )

        # Verify
        assert success is True
        assert errors == []

        # Verify metadata was passed to update_task
        update_call_args = mock_db_service.update_task.call_args
        assert update_call_args is not None
        assert update_call_args[0][0] == task_id
        assert "task_metadata" in update_call_args[0][1]

    @pytest.mark.asyncio
    async def test_validate_and_change_status_logging_failure_nonblocking(
        self, service, mock_db_service
    ):
        """Test that logging failure doesn't block status update."""
        task_id = str(uuid4())

        # Setup mock task
        mock_db_service.get_task.return_value = {
            "id": task_id,
            "status": "pending",
            "task_metadata": {}
        }

        # Logging fails
        mock_db_service.log_status_change.return_value = False

        # But update still succeeds
        mock_db_service.update_task.return_value = {"status": "in_progress"}

        # Execute
        success, message, errors = await service.validate_and_change_status(
            task_id=task_id,
            new_status="in_progress"
        )

        # Verify - status change still succeeds
        assert success is True
        assert errors == []
        mock_db_service.update_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_change_status_db_error_handling(
        self, service, mock_db_service
    ):
        """Test error handling when database operation fails."""
        task_id = str(uuid4())

        # Setup mock task
        mock_db_service.get_task.return_value = {
            "id": task_id,
            "status": "pending",
            "task_metadata": {}
        }

        # Update fails
        mock_db_service.update_task.return_value = None

        # Execute
        success, message, errors = await service.validate_and_change_status(
            task_id=task_id,
            new_status="in_progress"
        )

        # Verify
        assert success is False
        assert "failed" in message.lower()
        assert errors == ["update_failed"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
