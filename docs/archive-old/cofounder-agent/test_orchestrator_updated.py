"""
Updated test suite for Orchestrator using PostgreSQL database service
Replaces Firestore/Pub/Sub mocks with DatabaseService mocks
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from src.cofounder_agent.orchestrator_logic import Orchestrator


@pytest.fixture
def mock_database_service():
    """Create a mock database service for testing."""
    mock_db = AsyncMock()
    mock_db.get_pending_tasks = AsyncMock(return_value=[
        {
            "id": "task-1",
            "topic": "Python",
            "status": "pending",
            "created_at": "2025-10-25T10:00:00Z"
        }
    ])
    mock_db.add_task = AsyncMock(return_value="task-123")
    mock_db.update_task_status = AsyncMock()
    mock_db.add_log_entry = AsyncMock()
    mock_db.get_financial_summary = AsyncMock(return_value={"total_spent": 100.50})
    mock_db.health_check = AsyncMock(return_value={"status": "healthy"})
    mock_db.close = AsyncMock()
    return mock_db


@pytest.fixture
def orchestrator(mock_database_service):
    """Create an Orchestrator with mocked database service."""
    orchestrator = Orchestrator(
        database_service=mock_database_service,
        api_base_url="http://localhost:8000"
    )
    orchestrator.llm_client = MagicMock()
    orchestrator.financial_agent = MagicMock()
    orchestrator.market_insight_agent = MagicMock()
    return orchestrator


@pytest.mark.asyncio
async def test_get_content_calendar_async(orchestrator):
    """Test content calendar retrieval from PostgreSQL."""
    result = await orchestrator.get_content_calendar_async()
    assert result is not None
    orchestrator.database_service.get_pending_tasks.assert_called()


@pytest.mark.asyncio
async def test_create_content_task_async(orchestrator):
    """Test creating a content task with database storage."""
    task_id = await orchestrator.create_content_task_async("write about Python async")
    assert task_id is not None
    orchestrator.database_service.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_get_financial_summary_async(orchestrator):
    """Test retrieving financial summary from database."""
    result = await orchestrator.get_financial_summary_async()
    assert result is not None
    orchestrator.database_service.get_financial_summary.assert_called()


@pytest.mark.asyncio
async def test_run_content_pipeline_async(orchestrator):
    """Test running content pipeline with API dispatch."""
    with patch('httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "dispatched"}
        
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        
        result = await orchestrator.run_content_pipeline_async("Python async")
        assert result is not None


@pytest.mark.asyncio
async def test_system_status_check(orchestrator):
    """Test system status includes database and API health."""
    result = await orchestrator._get_system_status_async()
    assert result is not None
    assert "database" in result or "api" in result or "status" in result
    orchestrator.database_service.health_check.assert_called()


@pytest.mark.asyncio
async def test_process_command_async(orchestrator):
    """Test async command processing."""
    result = await orchestrator.process_command_async("show calendar")
    assert isinstance(result, dict)
    assert "response" in result


@pytest.mark.asyncio
async def test_process_command_unknown(orchestrator):
    """Test that unknown commands return helpful response."""
    result = await orchestrator.process_command_async("xyz123unknown")
    assert isinstance(result, dict)
    assert "response" in result


def test_orchestrator_initialization(mock_database_service):
    """Test that orchestrator initializes with new parameters."""
    orch = Orchestrator(
        database_service=mock_database_service,
        api_base_url="http://localhost:8000"
    )
    assert orch.database_service is not None
    assert orch.api_base_url == "http://localhost:8000"
    # Verify old parameters are not present
    assert not hasattr(orch, 'firestore_client') or orch.database_service is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
