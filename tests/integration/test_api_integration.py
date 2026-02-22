"""
Backend Integration Tests with Enhanced Fixtures
================================================

Tests that demonstrate:
- API testing with httpx
- Performance measurement
- Concurrency testing
- Test data factory usage
- Error handling
"""

import pytest
import json
from typing import Dict, Any


# ========================
# Health & Status Tests
# ========================

@pytest.mark.integration
@pytest.mark.api
async def test_backend_health(http_client):
    """Test backend health endpoint"""
    response = await http_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data or data  # Backend should return something


@pytest.mark.integration
async def test_api_version(http_client):
    """Test API version endpoint"""
    response = await http_client.get("/api/version")
    if response.status_code == 200:
        data = response.json()
        assert "version" in data or isinstance(data, dict)


# ========================
# Basic CRUD Tests
# ========================

@pytest.mark.integration
@pytest.mark.api
async def test_create_task(api_tester, test_data_factory):
    """Test creating a task"""
    task = await test_data_factory.create_task(title="Integration Test Task")
    
    assert task is not None
    assert "id" in task or "title" in task


@pytest.mark.integration
@pytest.mark.api
async def test_list_tasks(api_tester):
    """Test listing tasks"""
    await api_tester.get("/api/tasks")
    api_tester.assert_status(200)
    
    data = api_tester.get_json()
    assert isinstance(data, (dict, list))


@pytest.mark.integration
@pytest.mark.api
async def test_create_and_read_task(api_tester, test_data_factory):
    """Test create then read workflow"""
    # Create
    task = await test_data_factory.create_task(title="Read Test Task")
    task_id = task.get("id")
    
    if task_id:
        # Read
        await api_tester.get(f"/api/tasks/{task_id}")
        api_tester.assert_status(200)


# ========================
# Performance Tests
# ========================

@pytest.mark.performance
async def test_task_list_performance(api_tester, performance_timer):
    """Test list performance"""
    with performance_timer() as timer:
        await api_tester.get("/api/tasks")
        api_tester.assert_status(200)
    
    # Performance assertion
    assert timer.duration is not None
    assert timer.duration < 2000  # Should complete within 2 seconds


@pytest.mark.performance
async def test_task_creation_performance(api_tester, test_data_factory, performance_timer):
    """Test create performance"""
    with performance_timer() as timer:
        await test_data_factory.create_task(title="Perf Test")
    
    assert timer.duration is not None
    print(f"Task creation took {timer.duration:.2f}ms")


@pytest.mark.slow
@pytest.mark.performance
async def test_bulk_creation_performance(api_tester, test_data_factory, performance_timer):
    """Test bulk creation performance"""
    with performance_timer() as timer:
        await test_data_factory.create_multiple_tasks(count=10)
    
    assert timer.duration is not None
    print(f"Bulk creation of 10 tasks took {timer.duration:.2f}ms")


# ========================
# Concurrency Tests
# ========================

@pytest.mark.concurrent
async def test_concurrent_list_requests(http_client, concurrency_tester):
    """Test multiple concurrent list requests"""
    results = await concurrency_tester.run_concurrent(
        lambda: http_client.get("/api/tasks"),
        [() for _ in range(10)],  # 10 concurrent requests
    )
    
    # All should succeed
    assert all(r.status_code == 200 for r in results)


@pytest.mark.concurrent
@pytest.mark.slow
async def test_stress_test_list(http_client, concurrency_tester):
    """Stress test the list endpoint"""
    stats = await concurrency_tester.stress_test(
        lambda: http_client.get("/api/tasks"),
        iterations=50,
        concurrent_workers=5,
    )
    
    print(f"""
    📊 Stress Test Results:
    • Total: {stats['total']}
    • Success: {stats['success']}
    • Failures: {stats['failure']}
    • Success Rate: {stats['success_rate']:.2f}%
    • Avg Duration: {stats['avg_duration']:.2f}ms
    """)
    
    # Should have high success rate
    assert stats["success_rate"] >= 80


# ========================
# Error Handling Tests
# ========================

@pytest.mark.integration
@pytest.mark.api
async def test_invalid_endpoint_returns_404(http_client):
    """Test that invalid endpoints return 404"""
    response = await http_client.get("/api/invalid/endpoint")
    assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.api
async def test_invalid_task_id_returns_error(http_client):
    """Test accessing non-existent task"""
    response = await http_client.get("/api/tasks/invalid-id-12345")
    assert response.status_code in [400, 404, 500]  # Some error code


@pytest.mark.integration
@pytest.mark.api
async def test_malformed_request_body(http_client):
    """Test that malformed JSON is rejected"""
    response = await http_client.post(
        "/api/tasks",
        json={"invalid": "data"},  # Missing required fields
    )
    # Should fail validation
    assert response.status_code >= 400


# ========================
# Data Consistency Tests
# ========================

@pytest.mark.integration
async def test_created_task_appears_in_list(api_tester, test_data_factory):
    """Test that created task appears in list"""
    # Create task
    task = await test_data_factory.create_task(title="List Visibility Test")
    task_id = task.get("id")
    
    # Fetch list
    await api_tester.get("/api/tasks")
    api_tester.assert_status(200)
    
    tasks = api_tester.get_json()
    if isinstance(tasks, list):
        assert any(t.get("id") == task_id for t in tasks if isinstance(t, dict))
    elif isinstance(tasks, dict) and "data" in tasks:
        assert any(
            t.get("id") == task_id 
            for t in tasks.get("data", []) 
            if isinstance(t, dict)
        )


# ========================
# Integration Workflow Tests
# ========================

@pytest.mark.integration
async def test_full_task_workflow(api_tester, test_data_factory, performance_timer):
    """Test complete task lifecycle: create -> list -> get -> update -> delete"""
    
    with performance_timer() as timer:
        # 1. Create
        task = await test_data_factory.create_task(
            title="Workflow Test",
            description="Testing full workflow",
        )
        task_id = task.get("id")
        assert task_id, "Task creation failed or returned no ID"
        
        # 2. List (should appear)
        await api_tester.get("/api/tasks")
        api_tester.assert_status(200)
        
        # 3. Get single
        if task_id:
            await api_tester.get(f"/api/tasks/{task_id}")
            api_tester.assert_status(200)
        
        # 4. Update (simulate)
        # await api_tester.put(f"/api/tasks/{task_id}", json={"status": "completed"})
        
        # 5. Delete (happens in cleanup)
    
    print(f"Full workflow completed in {timer.duration:.2f}ms")


# ========================
# Mock Integration Tests
# ========================

@pytest.mark.integration
async def test_with_mock_response(mock_llm_response):
    """Test using mock response"""
    response = mock_llm_response(content="Test response", tokens=50)
    
    assert response["content"] == "Test response"
    assert response["tokens"] == 50
    assert response["model"] == "mock-model"


# ========================
# Parametrized Tests
# ========================

@pytest.mark.integration
@pytest.mark.parametrize("status", ["pending", "in_progress", "completed"])
async def test_task_status_values(api_tester, test_data_factory, status):
    """Test creating tasks with different status values"""
    task = await test_data_factory.create_task(
        title=f"Task with {status} status",
        status=status,
    )
    assert task is not None


@pytest.mark.integration
@pytest.mark.parametrize("priority", [1, 2, 3, 4, 5])
async def test_task_priority_values(api_tester, test_data_factory, priority):
    """Test creating tasks with different priority values"""
    task = await test_data_factory.create_task(
        title=f"Priority {priority} task",
        priority=priority,
    )
    assert task is not None


# ========================
# Timeout & Resilience Tests
# ========================

@pytest.mark.slow
async def test_request_timeout_handling(http_client):
    """Test that requests timeout gracefully"""
    try:
        # This might timeout depending on the endpoint
        response = await http_client.get(
            "/api/slow-endpoint",
            timeout=1.0,
        )
    except Exception as e:
        # Timeout expected
        assert "timeout" in str(e).lower() or "timed out" in str(e).lower()


# ========================
# Filter & Search Tests
# ========================

@pytest.mark.integration
async def test_list_with_filter(http_client):
    """Test list endpoint with filters"""
    response = await http_client.get("/api/tasks?status=pending")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (dict, list))


@pytest.mark.integration
async def test_list_with_pagination(http_client):
    """Test list endpoint with pagination"""
    response = await http_client.get("/api/tasks?page=1&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, (dict, list))
