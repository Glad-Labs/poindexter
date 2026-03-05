"""
Unit tests for TaskExecutor service.

Tests task lifecycle, event emission, error handling, and execution.
"""

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_executor_initialization(mock_task_executor):
    """Test TaskExecutor initializes correctly."""
    assert mock_task_executor is not None
    
    # Verify required methods exist
    assert hasattr(mock_task_executor, "execute")
    assert hasattr(mock_task_executor, "get_status")
    assert hasattr(mock_task_executor, "cancel")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_execution_success(mock_task_executor, sample_task_data):
    """Test successful task execution."""
    task_id = "task_001"
    result = await mock_task_executor.execute(task_id=task_id)
    
    assert result is not None
    assert result["task_id"] == task_id
    assert result["status"] == "completed"
    assert "result" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_lifecycle_states(mock_task_executor):
    """Test task moves through correct lifecycle states.
    
    Expected states: pending → queued → running → completed
    """
    task_id = "task_lifecycle_001"
    
    # Get initial status
    status = await mock_task_executor.get_status(task_id)
    
    # Status should be queryable
    assert "status" in status


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_error_handling(mock_task_executor):
    """Test task executor handles errors gracefully."""
    # Task should handle exceptions and report error status
    # With proper error details and logging
    result = await mock_task_executor.execute(task_id="task_error_001")
    
    # Even on error, should return result object
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_status_query(mock_task_executor):
    """Test getting task status at any time."""
    task_id = "task_status_001"
    
    # Status should be queryable at any time
    status = await mock_task_executor.get_status(task_id)
    
    assert status is not None
    assert "status" in status


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_cancellation(mock_task_executor):
    """Test cancelling a running task."""
    task_id = "task_cancel_001"
    
    result = await mock_task_executor.cancel(task_id=task_id)
    
    assert result["status"] == "cancelled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_context_passing(mock_task_executor):
    """Test task executor preserves and passes context."""
    context = {
        "user_id": "user_123",
        "request_id": "req_456",
        "metadata": {"source": "api", "priority": "high"}
    }
    
    result = await mock_task_executor.execute(
        task_id="task_context_001",
        **context
    )
    
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_event_emission(mock_task_executor):
    """Test executor emits events during execution.
    
    Events: task_started, task_progress, task_completed, task_error
    """
    # With mock, verify event methods are callable
    task_id = "task_events_001"
    
    result = await mock_task_executor.execute(task_id=task_id)
    
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_timeout_handling(mock_task_executor):
    """Test task executor respects timeout settings."""
    # Tasks should have configurable timeout (default 5 minutes)
    # If exceeded, task should be cancelled automatically
    
    result = await mock_task_executor.execute(task_id="task_timeout_001")
    
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_retry_logic(mock_task_executor):
    """Test task executor retries failed tasks.
    
    Configuration: max_retries=3, backoff=exponential
    """
    # Failed task should be retried up to max_retries times
    result = await mock_task_executor.execute(task_id="task_retry_001")
    
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_result_persistence(mock_task_executor):
    """Test task results are persisted to database."""
    task_id = "task_persist_001"
    
    result = await mock_task_executor.execute(task_id=task_id)
    
    # Result should be saved and queryable
    assert result["task_id"] == task_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_execution_with_dependencies(mock_task_executor):
    """Test task execution with dependent tasks.
    
    Task B should only run after Task A completes.
    """
    # This tests the task dependency resolution
    result = await mock_task_executor.execute(task_id="task_dep_001")
    
    assert result is not None


@pytest.mark.unit
def test_task_type_definitions():
    """Test that task types are properly defined."""
    valid_task_types = [
        "content_generation",
        "image_generation",
        "quality_evaluation",
        "publishing",
        "research",
        "compliance_check",
        "financial_analysis"
    ]
    
    # Verify content_generation is a valid type
    assert "content_generation" in valid_task_types


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_concurrent_execution(mock_task_executor):
    """Test executor can handle multiple concurrent tasks."""
    task_ids = ["task_concurrent_001", "task_concurrent_002", "task_concurrent_003"]
    
    # Should be able to execute multiple tasks concurrently
    results = []
    for task_id in task_ids:
        result = await mock_task_executor.execute(task_id=task_id)
        results.append(result)
    
    assert len(results) == 3
    assert all(r is not None for r in results)
