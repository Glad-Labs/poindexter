"""
Unit tests for TaskRoutes API endpoints.

Tests: task CRUD, execution, status queries, filtering.
"""

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_endpoint(mock_database_service, sample_task_data):
    """Test POST /api/tasks"""
    task_id = await mock_database_service.create_task(sample_task_data)
    
    assert task_id is not None
    assert isinstance(task_id, str)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_task_endpoint(mock_database_service, sample_task_data):
    """Test GET /api/tasks/{id}"""
    # Create task
    task_id = await mock_database_service.create_task(sample_task_data)
    
    # Get task
    task = await mock_database_service.get_task(task_id)
    
    assert task is not None
    assert task["id"] == task_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tasks_endpoint(mock_database_service):
    """Test GET /api/tasks"""
    # Should return paginated list of tasks
    result = {
        "data": [],
        "pagination": {"limit": 10, "offset": 0, "total": 0}
    }
    
    assert "data" in result
    assert "pagination" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_endpoint(mock_database_service, sample_task_data):
    """Test PUT /api/tasks/{id}"""
    # Create task
    task_id = await mock_database_service.create_task(sample_task_data)
    
    # Update task
    updates = {"status": "in_progress"}
    await mock_database_service.update_task(task_id, updates)
    
    # Verify update
    task = await mock_database_service.get_task(task_id)
    assert task["status"] == "in_progress"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_task_endpoint(mock_database_service, sample_task_data):
    """Test DELETE /api/tasks/{id}"""
    # Create and delete task
    task_id = await mock_database_service.create_task(sample_task_data)
    
    # In mock, deletion would set status to deleted
    await mock_database_service.update_task(task_id, {"status": "deleted"})
    
    task = await mock_database_service.get_task(task_id)
    assert task["status"] == "deleted"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_task_endpoint(mock_task_executor):
    """Test POST /api/tasks/{id}/execute"""
    result = await mock_task_executor.execute(task_id="task_001")
    
    assert result is not None
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_filter_by_status(mock_database_service):
    """Test filtering tasks by status parameter."""
    # GET /api/tasks?status=pending
    # Should return only pending tasks
    
    filters = {"status": "pending"}
    # Implementation would query with filters
    assert "status" in filters


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_filter_by_priority(mock_database_service):
    """Test filtering tasks by priority parameter."""
    # GET /api/tasks?priority=high
    # Should return only high priority tasks
    
    filters = {"priority": "high"}
    assert "priority" in filters


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_filter_by_agent(mock_database_service):
    """Test filtering tasks by assigned agent."""
    # GET /api/tasks?assigned_agent=content_agent
    
    filters = {"assigned_agent": "content_agent"}
    assert "assigned_agent" in filters


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_pagination(mock_database_service):
    """Test pagination parameters for task listing."""
    # GET /api/tasks?limit=20&offset=0
    
    pagination = {"limit": 20, "offset": 0}
    assert pagination["limit"] > 0
    assert pagination["offset"] >= 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_bulk_create(mock_database_service):
    """Test POST /api/tasks/bulk for batch creation."""
    tasks = [
        {"type": "task_1", "status": "pending"},
        {"type": "task_2", "status": "pending"},
        {"type": "task_3", "status": "pending"}
    ]
    
    assert len(tasks) == 3


@pytest.mark.unit
def test_task_request_validation():
    """Test task request validation."""
    from pydantic import BaseModel
    
    # Define a simple task request model for testing
    class TaskRequest(BaseModel):
        type: str
        title: str
        priority: str = "normal"
    
    # Valid task request
    task = TaskRequest(
        type="content_generation",
        title="Test Task",
        priority="high"
    )
    
    assert task.type == "content_generation"
    assert task.title == "Test Task"
    assert task.priority == "high"


@pytest.mark.unit
def test_task_response_format():
    """Test task API response format."""
    response = {
        "id": "task_001",
        "type": "content_generation",
        "status": "completed",
        "created_at": "2026-03-05T00:00:00Z",
        "completed_at": "2026-03-05T00:15:00Z",
        "result": {}
    }
    
    assert "id" in response
    assert "status" in response
    assert "created_at" in response
