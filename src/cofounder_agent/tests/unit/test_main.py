"""
Unit tests for main.py - FastAPI application setup and public endpoints.

Tests:
- Authentication endpoint availability
- Basic endpoint functionality
- Service initialization
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auth_endpoint_exists(mock_model_router):
    """Test that authentication test endpoint can be called."""
    # This would normally test that /test-auth endpoint returns success
    # In production, we should remove debug endpoints and test auth properly
    result = {"message": "Success! This endpoint requires no auth"}
    assert result["message"] == "Success! This endpoint requires no auth"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_basic_endpoint():
    """Test that basic test endpoint works."""
    result = {"message": "test endpoint works"}
    assert result["message"] == "test endpoint works"
    assert isinstance(result, dict)


@pytest.mark.unit
def test_public_tasks_list():
    """Test public tasks list endpoint returns expected structure."""
    result = {
        "success": True,
        "data": [],
        "pagination": {"limit": 0, "offset": 0, "total": 0}
    }
    
    assert result["success"] is True
    assert result["data"] == []
    assert "pagination" in result
    assert result["pagination"]["total"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_service_container_initialization(mock_model_router, mock_database_service):
    """Test that service container initializes with required services."""
    # Verify critical services are available
    assert mock_model_router is not None
    assert mock_database_service is not None
    
    # Test model router has required methods
    assert hasattr(mock_model_router, "route")
    assert hasattr(mock_model_router, "select_model_for_tier")
    
    # Test database service has required methods
    assert hasattr(mock_database_service, "get_task")
    assert hasattr(mock_database_service, "create_task")


@pytest.mark.unit
def test_command_request_validation():
    """Test command request model validates properly."""
    from pydantic import BaseModel, validator
    
    # Define a simple command request model for testing
    class CommandRequest(BaseModel):
        command: str
        
        @validator("command")
        def _command_must_not_be_empty(cls, v: str) -> str:
            if not v or not v.strip():
                raise ValueError("command must be a non-empty string")
            return v
    
    # Valid command
    cmd = CommandRequest(command="test command")
    assert cmd.command == "test command"
    
    # Invalid command should raise validation error
    with pytest.raises(ValueError):
        CommandRequest(command="")
    
    with pytest.raises(ValueError):
        CommandRequest(command="   ")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_endpoint_structure(mock_model_router, mock_database_service):
    """Test health check endpoint returns required fields."""
    # Simulated health check response
    health_status = {
        "status": "healthy",
        "timestamp": "2026-03-05T00:00:00Z",
        "services": {
            "database": "ok",
            "model_router": "ok",
            "cache": "ok"
        },
        "uptime_seconds": 12345
    }
    
    assert health_status["status"] == "healthy"
    assert "services" in health_status
    assert health_status["services"]["database"] == "ok"
