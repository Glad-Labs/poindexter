"""
Unit tests for bulk_task_routes.py
Tests bulk task creation, update, and management endpoints
"""

import pytest
import json
from fastapi.testclient import TestClient
from fastapi import FastAPI
import os
import sys

# Add parent directory to path for imports to work properly
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


# Use conftest app and client fixtures instead
# This ensures all routes are properly registered




@pytest.mark.unit
@pytest.mark.api
class TestBulkTaskRoutes:
    """Test bulk task endpoints"""

    @pytest.fixture
    def auth_headers(self, client):
        """Get auth headers for protected endpoints"""
        # Try to authenticate
        auth_response = client.post(
            "/api/auth/github/callback",
            json={"code": "test_code", "state": "test_state"}
        )
        if auth_response.status_code == 200:
            token_data = auth_response.json()
            token = token_data.get("token") or token_data.get("access_token")
            return {"Authorization": f"Bearer {token}"}
        return {}

    def test_bulk_create_tasks(self, client, auth_headers):
        """Test creating multiple tasks at once"""
        bulk_data = {
            "tasks": [
                {
                    "task_name": "Bulk Task 1",
                    "topic": "Test Topic 1",
                    "primary_keyword": "test1",
                    "target_audience": "Audience 1",
                    "category": "Tech",
                    "priority": "high"
                },
                {
                    "task_name": "Bulk Task 2",
                    "topic": "Test Topic 2",
                    "primary_keyword": "test2",
                    "target_audience": "Audience 2",
                    "category": "Business",
                    "priority": "medium"
                }
            ]
        }
        
        response = client.post(
            "/api/tasks/bulk/create",
            json=bulk_data,
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [200, 201, 400, 422]
            if response.status_code in [200, 201]:
                data = response.json()
                assert "created" in data or "tasks" in data or "results" in data
        else:
            assert response.status_code == 401

    def test_bulk_create_empty_list(self, client, auth_headers):
        """Test creating tasks with empty list"""
        response = client.post(
            "/api/tasks/bulk/create",
            json={"tasks": []},
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [400, 422, 200]
        else:
            assert response.status_code == 401

    def test_bulk_create_duplicate_tasks(self, client, auth_headers):
        """Test creating duplicate tasks in one request"""
        bulk_data = {
            "tasks": [
                {
                    "task_name": "Duplicate Task",
                    "topic": "Test Topic",
                    "primary_keyword": "test",
                    "target_audience": "Audience",
                    "category": "Tech"
                },
                {
                    "task_name": "Duplicate Task",
                    "topic": "Test Topic",
                    "primary_keyword": "test",
                    "target_audience": "Audience",
                    "category": "Tech"
                }
            ]
        }
        
        response = client.post(
            "/api/tasks/bulk/create",
            json=bulk_data,
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [200, 201, 400, 422]
        else:
            assert response.status_code == 401

    def test_bulk_update_tasks(self, client, auth_headers):
        """Test updating multiple tasks"""
        update_data = {
            "updates": [
                {"task_id": "test_id_1", "status": "completed"},
                {"task_id": "test_id_2", "status": "in_progress"}
            ]
        }
        
        response = client.post(
            "/api/tasks/bulk/update",
            json=update_data,
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [200, 400, 422, 404]
        else:
            assert response.status_code == 401

    def test_bulk_delete_tasks(self, client, auth_headers):
        """Test deleting multiple tasks"""
        delete_data = {
            "task_ids": ["test_id_1", "test_id_2", "test_id_3"]
        }
        
        response = client.delete(
            "/api/tasks/bulk/delete",
            json=delete_data,
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [200, 400, 422, 404]
        else:
            assert response.status_code == 401

    def test_bulk_delete_empty_list(self, client, auth_headers):
        """Test deleting with empty task list"""
        response = client.delete(
            "/api/tasks/bulk/delete",
            json={"task_ids": []},
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [400, 422, 200]
        else:
            assert response.status_code == 401

    def test_bulk_operations_invalid_json(self, client, auth_headers):
        """Test bulk operations with invalid JSON"""
        response = client.post(
            "/api/tasks/bulk/create",
            data="invalid json",
            headers=auth_headers
        )
        
        assert response.status_code in [400, 422]

    def test_bulk_operations_missing_fields(self, client, auth_headers):
        """Test bulk operations with missing required fields"""
        incomplete_data = {
            "tasks": [
                {
                    "task_name": "Task 1"
                    # Missing other required fields
                }
            ]
        }
        
        response = client.post(
            "/api/tasks/bulk/create",
            json=incomplete_data,
            headers=auth_headers
        )
        
        if auth_headers:
            assert response.status_code in [400, 422]
        else:
            assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.performance
class TestBulkTaskPerformance:
    """Test bulk operation performance characteristics"""

    def test_large_bulk_create_limit(self, client, auth_headers):
        """Test maximum bulk create size"""
        # Create 100 tasks - may exceed limit
        large_bulk = {
            "tasks": [
                {
                    "task_name": f"Task {i}",
                    "topic": "Test",
                    "primary_keyword": f"keyword{i}",
                    "target_audience": "Test",
                    "category": "Tech"
                }
                for i in range(100)
            ]
        }
        
        response = client.post(
            "/api/tasks/bulk/create",
            json=large_bulk,
            headers=auth_headers
        )
        
        if auth_headers:
            # Should either succeed or return 413 (payload too large)
            assert response.status_code in [200, 201, 413, 400, 422]
        else:
            assert response.status_code == 401

    def test_bulk_operation_response_time(self, client, auth_headers):
        """Test that bulk operations respond in reasonable time"""
        import time
        
        bulk_data = {
            "tasks": [
                {
                    "task_name": f"Task {i}",
                    "topic": "Test",
                    "primary_keyword": f"keyword{i}",
                    "target_audience": "Test",
                    "category": "Tech"
                }
                for i in range(10)
            ]
        }
        
        start_time = time.time()
        response = client.post(
            "/api/tasks/bulk/create",
            json=bulk_data,
            headers=auth_headers
        )
        elapsed_time = time.time() - start_time
        
        # Should respond within 30 seconds
        assert elapsed_time < 30
        if auth_headers:
            assert response.status_code in [200, 201, 400, 422]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
