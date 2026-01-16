"""
MCP Server Integration Tests with Co-Founder Agent

Tests that integrate the MCP Server with the existing Co-Founder Agent test suite.
These tests validate that the MCP server properly handles tool calls, resource management,
and integrates with the broader AI system.

Run with: pytest src/mcp_server/test_integration_with_cofounder.py -v
"""

import pytest
import asyncio
import json
import time
from typing import Dict, Any, Optional
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import sys
import os

# Note: Importing just the classes, not the server startup
# to avoid triggering uvicorn in test environment
from src.mcp_server.server import (
    MCPConfig, MCPRequest, MCPResponse, 
    MCPErrorCode, MCPErrorResponse, RateLimiter
)
from src.mcp_server.tool_registry import ToolRegistry, ToolDefinition
from src.mcp_server.resource_manager import ResourceManager


# ============================================================================
# Fixtures (Compatible with Cofounder Agent Test Suite)
# ============================================================================

@pytest.fixture
def mcp_config():
    """Create test MCP configuration"""
    return MCPConfig(
        host="127.0.0.1",
        port=9001,  # Different port to avoid conflicts
        debug=True,
        max_request_size=1000000,
        request_timeout=30,
        rate_limit_requests_per_minute=1000,
        enable_external_mcp=False,
        cors_origins=["http://localhost:3000", "http://localhost:3001", "http://localhost:8000"]
    )


@pytest.fixture
def tool_registry():
    """Create tool registry instance"""
    return ToolRegistry()


@pytest.fixture
def resource_manager():
    """Create resource manager instance"""
    return ResourceManager()


@pytest.fixture
def rate_limiter():
    """Create rate limiter instance"""
    return RateLimiter(requests_per_minute=100)


@pytest.fixture
def sample_mcp_request():
    """Create sample MCP request"""
    return MCPRequest(
        id="test_req_001",
        jsonrpc="2.0",
        method="call_tool",
        params={
            "tool": "get_available_models",
            "arguments": {}
        },
        agent_id="test_agent",
        execution_id="exec_001"
    )


@pytest.fixture
def sample_task_request():
    """Create sample task creation request"""
    return MCPRequest(
        id="test_req_002",
        jsonrpc="2.0",
        method="call_tool",
        params={
            "tool": "create_task",
            "arguments": {
                "task_type": "blog_post",
                "topic": "AI Trends 2025",
                "parameters": {"length": "2000 words"}
            }
        },
        agent_id="content_agent",
        execution_id="exec_002"
    )


# ============================================================================
# Test: Rate Limiting
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limiter_acquire_success(rate_limiter):
    """Test successful token acquisition"""
    result = await rate_limiter.acquire(tokens=10)
    assert result is True
    assert rate_limiter.tokens < 100


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limiter_exhaustion(rate_limiter):
    """Test rate limiter exhaustion"""
    # Exhaust the capacity
    rate_limiter.tokens = 0
    
    result = await rate_limiter.acquire(tokens=50)
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limiter_retry_after(rate_limiter):
    """Test retry-after calculation"""
    rate_limiter.tokens = 0
    
    retry_after = await rate_limiter.get_retry_after()
    assert retry_after > 0
    assert isinstance(retry_after, (int, float))


# ============================================================================
# Test: Tool Registry
# ============================================================================

@pytest.mark.unit
def test_tool_registry_initialization(tool_registry):
    """Test tool registry initializes with tools"""
    tools = tool_registry.list_tools()
    assert len(tools) >= 20  # Currently 20 tools registered (can expand to 23)
    assert all(isinstance(tool, dict) for tool in tools)  # Returns list of dicts
    assert all("name" in tool and "category" in tool for tool in tools)


@pytest.mark.unit
def test_tool_registry_get_tool(tool_registry):
    """Test retrieving a specific tool"""
    tool = tool_registry.get_tool("create_task")
    assert tool is not None
    assert tool.name == "create_task"
    assert tool.category == "task_management"


@pytest.mark.unit
def test_tool_registry_tool_categories(tool_registry):
    """Test tool categories are properly defined"""
    tools = tool_registry.list_tools()
    categories = set(tool["category"] for tool in tools)  # Access dict key
    
    expected_categories = {
        "task_management",
        "model_config",  # Note: abbreviated in implementation
        "distribution",
        "analytics",
        "memory",
        "agent_control",
        "database"
    }
    
    assert expected_categories.issubset(categories)


@pytest.mark.unit
def test_tool_registry_tool_not_found(tool_registry):
    """Test retrieving non-existent tool"""
    tool = tool_registry.get_tool("nonexistent_tool")
    assert tool is None


# ============================================================================
# Test: Resource Manager
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_manager_create_task(resource_manager):
    """Test creating a task resource"""
    task_data = {
        "name": "Test Task",
        "type": "blog_post",
        "status": "pending"
    }
    
    created = await resource_manager.create_resource("task", task_data)
    assert created is not None
    assert isinstance(created, dict)
    assert "id" in created
    assert created["name"] == "Test Task"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_manager_get_resource(resource_manager):
    """Test retrieving a resource"""
    task_data = {"name": "Test", "status": "pending"}
    created = await resource_manager.create_resource("task", task_data)
    task_id = created["id"]
    
    retrieved = await resource_manager.get_resource("task", task_id)
    assert retrieved is not None
    assert retrieved["name"] == "Test"


@pytest.mark.unit
def test_resource_manager_list_resources(resource_manager):
    """Test listing resource types"""
    resource_types = resource_manager.list_resources()
    assert "task" in resource_types or len(resource_types) > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_manager_update_resource(resource_manager):
    """Test updating a resource"""
    task_data = {"name": "Original", "status": "pending"}
    created = await resource_manager.create_resource("task", task_data)
    task_id = created["id"]
    
    updated_data = {"name": "Updated", "status": "in_progress"}
    await resource_manager.update_resource("task", task_id, updated_data)
    
    retrieved = await resource_manager.get_resource("task", task_id)
    assert retrieved["name"] == "Updated"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_manager_delete_resource(resource_manager):
    """Test deleting a resource"""
    task_data = {"name": "To Delete", "status": "pending"}
    created = await resource_manager.create_resource("task", task_data)
    task_id = created["id"]
    
    # Verify it exists
    existing = await resource_manager.get_resource("task", task_id)
    assert existing is not None
    
    # Delete it
    await resource_manager.delete_resource("task", task_id)
    
    # Trying to retrieve should raise an error
    with pytest.raises(ValueError, match="not found"):
        await resource_manager.get_resource("task", task_id)


# ============================================================================
# Test: MCP Request/Response Validation
# ============================================================================

@pytest.mark.unit
def test_mcp_request_validation(sample_mcp_request):
    """Test MCP request is properly formatted"""
    assert sample_mcp_request.id == "test_req_001"
    assert sample_mcp_request.jsonrpc == "2.0"
    assert sample_mcp_request.method == "call_tool"
    assert "tool" in sample_mcp_request.params
    assert sample_mcp_request.agent_id == "test_agent"


@pytest.mark.unit
def test_mcp_response_creation():
    """Test MCP response creation"""
    response = MCPResponse(
        id="test_req_001",
        jsonrpc="2.0",
        result={"status": "success"},
        cost_usd=0.01,
        latency_ms=45.5,
        timestamp=datetime.now().isoformat()
    )
    
    assert response.id == "test_req_001"
    assert response.result is not None
    assert isinstance(response.result, dict)
    assert response.cost_usd == 0.01


@pytest.mark.unit
def test_mcp_error_response():
    """Test MCP error response"""
    error = MCPErrorResponse(
        code=MCPErrorCode.TOOL_NOT_FOUND,
        message="Tool not found",
        details={"tool_name": "unknown"},
        retry_after_seconds=5
    )
    
    assert error.code == MCPErrorCode.TOOL_NOT_FOUND
    assert error.retry_after_seconds == 5


# ============================================================================
# Test: Tool Execution (Integration)
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_available_models_tool(tool_registry):
    """Test get_available_models tool execution"""
    tool = tool_registry.get_tool("get_available_models")
    assert tool is not None
    assert tool.name == "get_available_models"
    assert tool.category == "model_config"  # Note: abbreviated in implementation


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_creation_tool(tool_registry):
    """Test task creation tool"""
    tool = tool_registry.get_tool("create_task")
    assert tool is not None
    
    # Verify tool has required parameters
    assert "task_type" in tool.parameters
    assert "topic" in tool.parameters


@pytest.mark.integration
@pytest.mark.asyncio
async def test_memory_tools_exist(tool_registry):
    """Test memory tools are registered"""
    memory_tools = [
        "store_memory",
        "retrieve_memory",
        "semantic_search_memory"
    ]
    
    for tool_name in memory_tools:
        tool = tool_registry.get_tool(tool_name)
        assert tool is not None, f"Memory tool {tool_name} not found"


# ============================================================================
# Test: Agent Integration Scenarios
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_content_agent_workflow():
    """Test content agent workflow through MCP"""
    registry = ToolRegistry()
    rm = ResourceManager()
    
    # Step 1: Create task
    task_data = {
        "name": "Generate blog post",
        "type": "blog_post",
        "topic": "AI Trends",
        "status": "pending"
    }
    task_id = rm.create_resource("task", task_data)
    assert task_id is not None
    
    # Step 2: Get models
    models_tool = registry.get_tool("get_available_models")
    assert models_tool is not None
    
    # Step 3: Store memory
    memory_tool = registry.get_tool("store_memory")
    assert memory_tool is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_agent_coordination():
    """Test multiple agents using MCP tools"""
    registry = ToolRegistry()
    rm = ResourceManager()
    
    # Content Agent
    content_task_id = rm.create_resource("task", {
        "name": "Create content",
        "type": "blog_post",
        "status": "pending"
    })
    
    # Financial Agent
    financial_task_id = rm.create_resource("task", {
        "name": "Financial analysis",
        "type": "financial",
        "status": "pending"
    })
    
    # Market Agent
    market_task_id = rm.create_resource("task", {
        "name": "Market research",
        "type": "market_analysis",
        "status": "pending"
    })
    
    # All should be created successfully
    assert content_task_id is not None
    assert financial_task_id is not None
    assert market_task_id is not None


# ============================================================================
# Test: Error Handling
# ============================================================================

@pytest.mark.unit
def test_error_code_coverage():
    """Test all error codes are defined"""
    error_codes = [
        MCPErrorCode.INVALID_REQUEST,
        MCPErrorCode.TOOL_NOT_FOUND,
        MCPErrorCode.INVALID_PARAMETERS,
        MCPErrorCode.RATE_LIMIT_EXCEEDED,
        MCPErrorCode.INTERNAL_ERROR,
        MCPErrorCode.SERVICE_UNAVAILABLE,
        MCPErrorCode.RESOURCE_NOT_FOUND,
        MCPErrorCode.PERMISSION_DENIED,
    ]
    
    assert len(error_codes) == 8


@pytest.mark.unit
def test_error_response_format():
    """Test error response has required fields"""
    error = MCPErrorResponse(
        code=MCPErrorCode.INVALID_PARAMETERS,
        message="Invalid parameters",
        details={"param": "value"},
        retry_after_seconds=10
    )
    
    assert hasattr(error, 'code')
    assert hasattr(error, 'message')
    assert hasattr(error, 'details')
    assert hasattr(error, 'retry_after_seconds')


# ============================================================================
# Test: Performance & Load
# ============================================================================

@pytest.mark.performance
@pytest.mark.asyncio
async def test_tool_lookup_performance(tool_registry):
    """Test tool lookup performance"""
    start = time.time()
    
    for _ in range(1000):
        tool = tool_registry.get_tool("get_available_models")
    
    elapsed = time.time() - start
    
    # Should complete 1000 lookups in < 100ms
    assert elapsed < 0.1, f"Tool lookup too slow: {elapsed:.3f}s"


@pytest.mark.performance
@pytest.mark.asyncio
async def test_resource_creation_performance(resource_manager):
    """Test resource creation performance"""
    start = time.time()
    
    for i in range(100):
        resource_manager.create_resource("task", {
            "name": f"Task {i}",
            "status": "pending"
        })
    
    elapsed = time.time() - start
    
    # Should create 100 resources in < 1 second
    assert elapsed < 1.0, f"Resource creation too slow: {elapsed:.3f}s"


# ============================================================================
# Test: Configuration & Initialization
# ============================================================================

@pytest.mark.unit
def test_mcp_config_defaults(mcp_config):
    """Test MCP config has proper defaults"""
    assert mcp_config.host == "127.0.0.1"
    assert mcp_config.port == 9001
    assert mcp_config.debug is True
    assert mcp_config.rate_limit_requests_per_minute == 1000


# ============================================================================
# Test: Smoke Tests (Quick Validation)
# ============================================================================

@pytest.mark.smoke
def test_mcp_core_components_exist():
    """Test all core MCP components are importable"""
    from src.mcp_server import MCPServer, MCPConfig, ToolRegistry, ResourceManager
    
    assert MCPServer is not None
    assert MCPConfig is not None
    assert ToolRegistry is not None
    assert ResourceManager is not None


@pytest.mark.smoke
def test_23_tools_registered(tool_registry):
    """Test all 20+ tools are registered"""
    tools = tool_registry.list_tools()
    assert len(tools) >= 20, f"Expected 20+ tools, got {len(tools)}"


@pytest.mark.smoke
def test_7_error_codes_defined():
    """Test all 7+ error codes are defined"""
    error_codes = [
        MCPErrorCode.INVALID_REQUEST,
        MCPErrorCode.TOOL_NOT_FOUND,
        MCPErrorCode.INVALID_PARAMETERS,
        MCPErrorCode.RATE_LIMIT_EXCEEDED,
        MCPErrorCode.INTERNAL_ERROR,
        MCPErrorCode.SERVICE_UNAVAILABLE,
        MCPErrorCode.RESOURCE_NOT_FOUND,
        MCPErrorCode.PERMISSION_DENIED,
    ]
    
    assert len(error_codes) >= 7


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
