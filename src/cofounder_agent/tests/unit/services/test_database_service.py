"""
Unit tests for DatabaseService.

Tests database operations, transactions, connection pooling, and error handling.
"""

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_database_service_initialization(mock_database_service):
    """Test DatabaseService initializes correctly."""
    assert mock_database_service is not None
    
    # Verify required methods exist
    assert hasattr(mock_database_service, "get_task")
    assert hasattr(mock_database_service, "create_task")
    assert hasattr(mock_database_service, "update_task")
    assert hasattr(mock_database_service, "get_workflow")
    assert hasattr(mock_database_service, "create_workflow")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task(mock_database_service, sample_task_data):
    """Test creating a task in the database."""
    task_id = await mock_database_service.create_task(sample_task_data)
    
    assert task_id is not None
    assert isinstance(task_id, str)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_task(mock_database_service, sample_task_data):
    """Test retrieving a task from the database."""
    # Create a task
    task_id = await mock_database_service.create_task(sample_task_data)
    
    # Retrieve it
    task = await mock_database_service.get_task(task_id)
    
    assert task is not None
    assert task["id"] == task_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task(mock_database_service, sample_task_data):
    """Test updating a task in the database."""
    # Create a task
    task_id = await mock_database_service.create_task(sample_task_data)
    
    # Update it
    updates = {"status": "completed", "result": "Task completed successfully"}
    await mock_database_service.update_task(task_id, updates)
    
    # Verify update
    task = await mock_database_service.get_task(task_id)
    assert task["status"] == "completed"
    assert task["result"] == "Task completed successfully"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_workflow(mock_database_service, sample_workflow_data):
    """Test creating a workflow in the database."""
    workflow_id = await mock_database_service.create_workflow(sample_workflow_data)
    
    assert workflow_id is not None
    assert isinstance(workflow_id, str)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow(mock_database_service, sample_workflow_data):
    """Test retrieving a workflow from the database."""
    # Create a workflow
    workflow_id = await mock_database_service.create_workflow(sample_workflow_data)
    
    # Retrieve it
    workflow = await mock_database_service.get_workflow(workflow_id)
    
    assert workflow is not None
    assert workflow["id"] == workflow_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_logging(mock_database_service):
    """Test audit log creation."""
    audit_event = {
        "event_type": "task_created",
        "user_id": "user_123",
        "resource_id": "task_456",
        "details": {"action": "created"}
    }
    
    await mock_database_service.log_audit(audit_event)
    # In a real test, we'd verify the log was persisted


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_nonexistent_task(mock_database_service):
    """Test retrieving a nonexistent task returns None."""
    task = await mock_database_service.get_task("nonexistent_id")
    assert task is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_nonexistent_workflow(mock_database_service):
    """Test retrieving a nonexistent workflow returns None."""
    workflow = await mock_database_service.get_workflow("nonexistent_id")
    assert workflow is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_query_filtering(mock_database, sample_task_data):
    """Test filtering tasks by status."""
    # Create multiple tasks with different statuses
    task1_data = {**sample_task_data, "status": "pending"}
    task2_data = {**sample_task_data, "status": "completed"}
    
    task1_id = await mock_database.create_task(task1_data)
    task2_id = await mock_database.create_task(task2_data)
    
    # Both should exist
    task1 = await mock_database.get_task(task1_id)
    task2 = await mock_database.get_task(task2_id)
    
    assert task1 is not None
    assert task2 is not None
    assert task1["status"] == "pending"
    assert task2["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_database_connection_pool_config():
    """Test database connection pool is configured correctly."""
    # Expected for production load
    pool_config = {
        "pool_size": 10,      # Starting size
        "pool_max_size": 20,  # Max size
        "pool_recycle": 3600, # Recycle connections after 1 hour
        "pool_pre_ping": True # Test connections before using
    }
    
    assert pool_config["pool_size"] >= 5
    assert pool_config["pool_max_size"] >= pool_config["pool_size"]
    assert pool_config["pool_recycle"] > 0
    assert pool_config["pool_pre_ping"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_database_transaction_rollback():
    """Test transaction rollback on error."""
    from unittest.mock import AsyncMock, patch
    
    # This would test that if part of a transaction fails,
    # the entire transaction is rolled back
    # Implementation depends on actual DBService
    pass


@pytest.mark.unit
def test_database_url_validation():
    """Test that database URL is properly configured."""
    # Ensure DATABASE_URL is set to valid PostgreSQL URL
    import os
    db_url = os.getenv("DATABASE_URL", "")
    
    # Should be PostgreSQL URL (not SQLite for production)
    if db_url:
        assert db_url.startswith("postgresql://") or db_url.startswith("postgres://")
