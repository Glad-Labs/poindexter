"""
Example Test File - Best Practices Demonstration
Shows proper patterns and structure for testing FastAPI endpoints

To use as template:
1. Copy this file and rename to test_my_feature.py
2. Replace class names and test functions
3. Follow the pattern for similar features
"""

import pytest
import json
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from typing import Dict, Any

# Import utilities (adjust path based on your structure)
try:
    from test_utilities import (
        TestClientFactory,
        MockFactory,
        TestDataBuilder,
        AssertionHelpers,
        AsyncHelpers,
        ParametrizeHelpers,
    )
except ImportError:
    # Fallback if utilities not available
    pass


# ===== TEST CLASS STRUCTURE =====

@pytest.mark.api
@pytest.mark.integration
class TestExampleAPIEndpoints:
    """
    Test suite for Example API endpoints
    
    Tests the following endpoints:
    - GET /api/example/{id}
    - POST /api/example
    - PUT /api/example/{id}
    - DELETE /api/example/{id}
    
    Dependencies mocked:
    - Database service
    - Cache service
    - External API client
    """
    
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Setup mocks for all tests in this class"""
        self.mock_db = MockFactory.mock_database()
        self.mock_cache = MockFactory.mock_cache()
        self.mock_http = MockFactory.mock_http_client()
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        from main import app
        return TestClient(app)
    
    @pytest.fixture
    def sample_data(self):
        """Sample test data"""
        return TestDataBuilder.api_response(
            data={"id": "example_1", "name": "Test Example"}
        )
    
    # ===== SUCCESS TESTS =====
    
    def test_get_example_success(self, client, sample_data):
        """
        Test: GET /api/example/{id} returns item
        Expected: 200 with item data
        """
        # Arrange
        example_id = "example_1"
        
        # Act
        response = client.get(f"/api/example/{example_id}")
        
        # Assert
        AssertionHelpers.assert_success_response(response, 200)
        data = response.json()
        AssertionHelpers.assert_has_keys(data, ["id", "name", "created_at"])
        assert data["id"] == example_id
    
    def test_create_example_success(self, client):
        """
        Test: POST /api/example creates new item
        Expected: 201 with created item
        """
        # Arrange
        payload = {
            "name": "New Example",
            "description": "Test description"
        }
        
        # Act
        response = client.post("/api/example", json=payload)
        
        # Assert
        AssertionHelpers.assert_success_response(response, 201)
        data = response.json()
        assert data["name"] == payload["name"]
        assert "id" in data
        assert "created_at" in data
    
    def test_update_example_success(self, client):
        """
        Test: PUT /api/example/{id} updates item
        Expected: 200 with updated item
        """
        # Arrange
        example_id = "example_1"
        updates = {"name": "Updated Name"}
        
        # Act
        response = client.put(f"/api/example/{example_id}", json=updates)
        
        # Assert
        AssertionHelpers.assert_success_response(response, 200)
        data = response.json()
        assert data["name"] == updates["name"]
    
    def test_delete_example_success(self, client):
        """
        Test: DELETE /api/example/{id} removes item
        Expected: 204 No Content
        """
        # Arrange
        example_id = "example_1"
        
        # Act
        response = client.delete(f"/api/example/{example_id}")
        
        # Assert
        assert response.status_code == 204
    
    # ===== ERROR TESTS =====
    
    def test_get_example_not_found(self, client):
        """
        Test: GET /api/example/{id} returns 404 for missing item
        Expected: 404 Not Found
        """
        # Arrange
        example_id = "nonexistent_id"
        
        # Act
        response = client.get(f"/api/example/{example_id}")
        
        # Assert
        AssertionHelpers.assert_error_response(response, 404)
        data = response.json()
        assert "not found" in data["detail"].lower()
    
    def test_create_example_invalid_data(self, client):
        """
        Test: POST /api/example rejects invalid data
        Expected: 422 Validation Error
        """
        # Arrange
        payload = {"name": ""}  # Empty name is invalid
        
        # Act
        response = client.post("/api/example", json=payload)
        
        # Assert
        AssertionHelpers.assert_error_response(response, 422)
    
    def test_create_example_missing_required_field(self, client):
        """
        Test: POST /api/example rejects missing required field
        Expected: 422 Validation Error
        """
        # Arrange
        payload = {"description": "No name provided"}  # Missing 'name'
        
        # Act
        response = client.post("/api/example", json=payload)
        
        # Assert
        AssertionHelpers.assert_error_response(response, 422)
    
    # ===== AUTHENTICATION TESTS =====
    
    def test_get_example_requires_auth(self, client):
        """
        Test: GET /api/example/{id} requires authentication
        Expected: 401 Unauthorized when no token provided
        """
        # Note: This assumes the endpoint requires auth
        # Arrange
        example_id = "example_1"
        
        # Act - make request without auth header
        response = client.get(f"/api/example/{example_id}")
        
        # Assert
        AssertionHelpers.assert_error_response(response, 401)
    
    # ===== EDGE CASE TESTS =====
    
    def test_example_id_with_special_characters(self, client):
        """
        Test: Handle IDs with special characters
        Expected: Proper handling or validation error
        """
        # Arrange
        example_id = "example-123_test.id"
        
        # Act
        response = client.get(f"/api/example/{example_id}")
        
        # Assert - should either work or return 400
        assert response.status_code in [200, 400, 404]
    
    def test_create_example_with_very_long_name(self, client):
        """
        Test: Handle very long name field
        Expected: Either truncate or reject
        """
        # Arrange
        long_name = "x" * 10000
        payload = {"name": long_name}
        
        # Act
        response = client.post("/api/example", json=payload)
        
        # Assert
        assert response.status_code in [201, 400, 422]
    
    # ===== PARAMETRIZED TESTS =====
    
    @pytest.mark.parametrize("invalid_id", ["", None, 0, "invalid-id"])
    def test_get_example_with_invalid_ids(self, client, invalid_id):
        """
        Test: GET /api/example with various invalid IDs
        Expected: Either 404 or 422
        """
        # Act & Assert - should reject or not find
        response = client.get(f"/api/example/{invalid_id}")
        assert response.status_code in [400, 404, 422]


# ===== ASYNC TEST CLASS =====

@pytest.mark.asyncio
@pytest.mark.integration
class TestExampleAsyncOperations:
    """Test suite for async operations"""
    
    async def test_async_database_operation(self):
        """
        Test: Async database operations execute correctly
        Expected: Results returned asynchronously
        """
        # Arrange
        mock_db = MockFactory.mock_database()
        mock_db.query.return_value = [
            {"id": "1", "name": "Item 1"},
            {"id": "2", "name": "Item 2"}
        ]
        
        # Act
        result = await mock_db.query("SELECT * FROM examples")
        
        # Assert
        assert len(result) == 2
        mock_db.query.assert_called_once()
    
    async def test_concurrent_operations(self):
        """
        Test: Multiple async operations run concurrently
        Expected: All complete without interference
        """
        # Arrange
        async def mock_operation(value):
            return value * 2
        
        # Act
        results = await AsyncHelpers.run_concurrent(
            mock_operation(1),
            mock_operation(2),
            mock_operation(3)
        )
        
        # Assert
        assert results == [2, 4, 6]
    
    @pytest.mark.slow
    async def test_long_operation_timeout(self):
        """
        Test: Long operations eventually timeout
        Expected: Timeout after specified duration
        """
        # Arrange
        async def slow_operation():
            await AsyncHelpers.wait_for(
                lambda: False,
                timeout=2,
                check_interval=0.1
            )
        
        # Act & Assert
        result = await slow_operation()
        assert result is False


# ===== PERFORMANCE TEST CLASS =====

@pytest.mark.performance
class TestExamplePerformance:
    """Performance and load tests"""
    
    def test_endpoint_response_time(self, client):
        """
        Test: Endpoint responds within acceptable time
        Expected: Response time < 500ms
        """
        # Arrange
        import time
        
        # Act
        start = time.time()
        response = client.get("/api/example/example_1")
        elapsed = time.time() - start
        
        # Assert
        assert response.status_code == 200
        PerformanceHelpers.assert_response_time(elapsed, 0.5)
    
    @pytest.mark.slow
    def test_bulk_create_performance(self, client):
        """
        Test: Bulk creation of items
        Expected: Create 100 items quickly
        """
        # Arrange
        import time
        
        # Act
        start = time.time()
        for i in range(100):
            response = client.post(
                "/api/example",
                json={"name": f"Item {i}"}
            )
            assert response.status_code == 201
        elapsed = time.time() - start
        
        # Assert
        assert elapsed < 10  # Should complete in < 10 seconds


# ===== SECURITY TEST CLASS =====

@pytest.mark.security
class TestExampleSecurity:
    """Security-related tests"""
    
    def test_sql_injection_prevention(self, client):
        """
        Test: SQL injection attempts are prevented
        Expected: Request rejected or sanitized
        """
        # Arrange
        malicious_id = "'; DROP TABLE examples; --"
        
        # Act
        response = client.get(f"/api/example/{malicious_id}")
        
        # Assert - should not execute SQL
        assert response.status_code in [400, 404]
    
    def test_xss_prevention(self, client):
        """
        Test: XSS attacks are prevented
        Expected: Script tags removed or escaped
        """
        # Arrange
        payload = {
            "name": "<script>alert('xss')</script>"
        }
        
        # Act
        response = client.post("/api/example", json=payload)
        
        # Assert
        if response.status_code == 201:
            data = response.json()
            assert "<script>" not in data.get("name", "")


# ===== INTEGRATION TEST CLASS =====

@pytest.mark.integration
class TestExampleIntegration:
    """Integration tests spanning multiple components"""
    
    def test_complete_workflow(self, client):
        """
        Test: Complete create-read-update-delete workflow
        Expected: All operations succeed
        """
        # Create
        create_response = client.post(
            "/api/example",
            json={"name": "Test Item"}
        )
        assert create_response.status_code == 201
        item_id = create_response.json()["id"]
        
        # Read
        read_response = client.get(f"/api/example/{item_id}")
        assert read_response.status_code == 200
        
        # Update
        update_response = client.put(
            f"/api/example/{item_id}",
            json={"name": "Updated Item"}
        )
        assert update_response.status_code == 200
        
        # Delete
        delete_response = client.delete(f"/api/example/{item_id}")
        assert delete_response.status_code == 204
        
        # Verify deletion
        verify_response = client.get(f"/api/example/{item_id}")
        assert verify_response.status_code == 404
    
    def test_data_persistence(self, client):
        """
        Test: Data persists across requests
        Expected: Created data is retrievable
        """
        # Create
        payload = {"name": "Persistent Item"}
        create_response = client.post("/api/example", json=payload)
        item_id = create_response.json()["id"]
        
        # Retrieve
        retrieve_response = client.get(f"/api/example/{item_id}")
        
        # Verify
        assert retrieve_response.json()["name"] == payload["name"]


# ===== UTILITY IMPORTS (add to top of file) =====
"""
from test_utilities import PerformanceHelpers
"""

# ===== TEST EXECUTION NOTES =====
"""
Run this test file:
    pytest test_example.py -v
    
Run specific test class:
    pytest test_example.py::TestExampleAPIEndpoints -v
    
Run specific test:
    pytest test_example.py::TestExampleAPIEndpoints::test_get_example_success -v
    
Run with coverage:
    pytest test_example.py --cov=src/cofounder_agent --cov-report=html
    
Run only slow tests:
    pytest test_example.py -m slow
    
Run excluding slow tests:
    pytest test_example.py -m "not slow"
    
Run with specific markers:
    pytest test_example.py -m "api and integration"
"""
