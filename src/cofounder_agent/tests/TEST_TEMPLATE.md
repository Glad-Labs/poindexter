# Test Template Guide

This guide provides templates and patterns for writing tests in the GLAD Labs test suite.

## Table of Contents

1. [Unit Tests](#unit-tests)
2. [Integration Tests](#integration-tests)
3. [API Endpoint Tests](#api-endpoint-tests)
4. [Async Tests](#async-tests)
5. [Mock Patterns](#mock-patterns)
6. [Test Organization](#test-organization)

---

## Unit Tests

Unit tests test individual functions or methods in isolation.

### Basic Unit Test Template

```python
import pytest
from unittest.mock import Mock, patch

@pytest.mark.unit
class TestMyFunction:
    """Test suite for my_function module"""

    def test_function_with_valid_input(self):
        """Test function returns expected output with valid input"""
        # Arrange
        from my_module import my_function
        input_data = {"key": "value"}

        # Act
        result = my_function(input_data)

        # Assert
        assert result is not None
        assert result["success"] is True

    def test_function_with_invalid_input(self):
        """Test function handles invalid input gracefully"""
        # Arrange
        from my_module import my_function
        invalid_input = None

        # Act & Assert
        with pytest.raises(ValueError):
            my_function(invalid_input)

    def test_function_with_edge_case(self):
        """Test function handles edge cases"""
        # Arrange
        from my_module import my_function
        edge_case_input = {"items": []}

        # Act
        result = my_function(edge_case_input)

        # Assert
        assert result["items_count"] == 0
```

### Unit Test with Mocks

```python
@pytest.mark.unit
class TestFunctionWithDependencies:
    """Test function that has external dependencies"""

    @patch('my_module.external_service.call_api')
    def test_function_calls_external_service(self, mock_api):
        """Test function correctly calls external service"""
        # Arrange
        mock_api.return_value = {"data": "response"}
        from my_module import process_data

        # Act
        result = process_data({"id": 123})

        # Assert
        mock_api.assert_called_once_with(123)
        assert result["data"] == "response"

    @patch('my_module.database.query')
    def test_function_handles_database_error(self, mock_db):
        """Test function handles database errors"""
        # Arrange
        mock_db.side_effect = Exception("DB Connection failed")
        from my_module import fetch_user

        # Act & Assert
        with pytest.raises(Exception):
            fetch_user("user_123")
```

---

## Integration Tests

Integration tests verify that multiple components work together correctly.

### API Integration Test Template

```python
import pytest
from fastapi.testclient import TestClient

@pytest.mark.integration
class TestAPIIntegration:
    """Integration tests for API endpoints"""

    def test_create_and_retrieve_resource(self, client):
        """Test creating and retrieving a resource"""
        # Create
        create_response = client.post("/api/resources", json={
            "name": "Test Resource",
            "description": "For testing"
        })
        assert create_response.status_code == 201
        resource_id = create_response.json()["id"]

        # Retrieve
        get_response = client.get(f"/api/resources/{resource_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "Test Resource"

    def test_update_resource_workflow(self, client):
        """Test complete workflow: create → update → verify"""
        # Create
        create_resp = client.post("/api/resources", json={"name": "Original"})
        resource_id = create_resp.json()["id"]

        # Update
        update_resp = client.put(f"/api/resources/{resource_id}", json={
            "name": "Updated"
        })
        assert update_resp.status_code == 200

        # Verify
        get_resp = client.get(f"/api/resources/{resource_id}")
        assert get_resp.json()["name"] == "Updated"
```

---

## API Endpoint Tests

Test API endpoints for correct HTTP behavior.

### REST API Test Template

```python
@pytest.mark.api
class TestRESTEndpoints:
    """Test REST API endpoints"""

    def test_get_endpoint_returns_list(self, client):
        """GET /api/items should return list"""
        response = client.get("/api/items")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_post_endpoint_creates_resource(self, client):
        """POST /api/items should create and return resource"""
        payload = {"name": "New Item", "value": 100}
        response = client.post("/api/items", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Item"
        assert "id" in data

    def test_post_endpoint_validation_error(self, client):
        """POST with invalid data should return 422"""
        payload = {"invalid_field": "value"}  # Missing required fields
        response = client.post("/api/items", json=payload)
        assert response.status_code == 422

    def test_put_endpoint_updates_resource(self, client):
        """PUT /api/items/{id} should update resource"""
        # Create first
        create_resp = client.post("/api/items", json={"name": "Original"})
        item_id = create_resp.json()["id"]

        # Update
        response = client.put(f"/api/items/{item_id}", json={"name": "Updated"})
        assert response.status_code == 200
        assert response.json()["name"] == "Updated"

    def test_delete_endpoint_removes_resource(self, client):
        """DELETE /api/items/{id} should remove resource"""
        # Create first
        create_resp = client.post("/api/items", json={"name": "To Delete"})
        item_id = create_resp.json()["id"]

        # Delete
        delete_resp = client.delete(f"/api/items/{item_id}")
        assert delete_resp.status_code == 204

        # Verify deletion
        get_resp = client.get(f"/api/items/{item_id}")
        assert get_resp.status_code == 404

    def test_endpoint_not_found(self, client):
        """Non-existent endpoint should return 404"""
        response = client.get("/api/nonexistent")
        assert response.status_code == 404

    def test_endpoint_unauthorized(self, client):
        """Protected endpoint without auth should return 401"""
        response = client.get("/api/protected")
        assert response.status_code == 401
```

---

## Async Tests

Testing async functions and async endpoints.

### Async Function Test Template

```python
import pytest

@pytest.mark.asyncio
@pytest.mark.unit
class TestAsyncFunctions:
    """Test async functions"""

    async def test_async_function_returns_result(self):
        """Test async function returns expected result"""
        # Arrange
        from my_module import async_fetch_data

        # Act
        result = await async_fetch_data("query")

        # Assert
        assert result is not None
        assert "data" in result

    async def test_async_function_with_timeout(self):
        """Test async function with timeout"""
        from my_module import slow_async_operation

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_async_operation(), timeout=0.1)
```

### Async API Test Template

```python
@pytest.mark.asyncio
@pytest.mark.api
class TestAsyncAPIEndpoints:
    """Test async API endpoints"""

    async def test_async_endpoint_response(self, async_client):
        """Test async endpoint returns correct response"""
        response = await async_client.get("/api/async-endpoint")
        assert response.status_code == 200
        data = response.json()
        assert "result" in data
```

---

## Mock Patterns

### Mock External API

```python
@patch('requests.get')
def test_with_mocked_api(mock_get):
    """Test function that calls external API"""
    # Setup mock
    mock_get.return_value.json.return_value = {"status": "success"}

    # Your test code
    from my_module import call_external_api
    result = call_external_api("https://api.example.com/data")

    # Verify
    mock_get.assert_called_once_with("https://api.example.com/data")
    assert result["status"] == "success"
```

### Mock Database Operations

```python
@patch('my_module.database.get_user')
def test_with_mocked_db(mock_db, mock_database):
    """Test function that accesses database"""
    # Setup mock
    mock_db.return_value = {"id": 1, "name": "Test User"}

    # Your test code
    from my_module import process_user
    result = process_user(1)

    # Verify
    mock_db.assert_called_once_with(1)
```

### Mock Async Operations

```python
@pytest.mark.asyncio
@patch('my_module.async_fetch')
async def test_with_mocked_async(mock_fetch):
    """Test async function with mocked async call"""
    # Setup mock
    mock_fetch.return_value = {"data": "mocked"}

    # Your test code
    from my_module import process_async
    result = await process_async()

    # Verify
    mock_fetch.assert_called_once()
```

---

## Test Organization

### File Structure

```
tests/
├── test_unit_*.py           # Unit tests
├── test_integration_*.py    # Integration tests
├── test_api_*.py           # API tests
├── test_e2e_*.py           # End-to-end tests
├── conftest.py             # Fixtures and configuration
└── test_data/              # Test data files
    ├── sample_data.json
    └── fixtures.py
```

### Test Class Organization

```python
@pytest.mark.unit
class TestMyModule:
    """Main test class for my_module"""

    # Happy path tests
    def test_successful_operation(self):
        pass

    def test_valid_input_handling(self):
        pass

    # Error path tests
    def test_invalid_input_handling(self):
        pass

    def test_error_recovery(self):
        pass

    # Edge case tests
    def test_boundary_conditions(self):
        pass

    def test_empty_data_handling(self):
        pass
```

### Test Method Naming

```python
# Pattern: test_<what_is_being_tested>_<condition>_<expected_result>

def test_function_with_valid_input_returns_success():
    pass

def test_function_with_invalid_input_raises_error():
    pass

def test_function_with_none_value_returns_default():
    pass

def test_endpoint_without_authentication_returns_401():
    pass
```

---

## Fixtures Usage

### Use Built-in Fixtures

```python
def test_with_client(client):
    """Use FastAPI test client fixture"""
    response = client.get("/api/health")
    assert response.status_code == 200

def test_with_mock_data(mock_business_data):
    """Use mock business data fixture"""
    assert mock_business_data["revenue"] > 0

def test_with_temp_files(temp_directory):
    """Use temporary directory fixture"""
    test_file = os.path.join(temp_directory, "test.txt")
    with open(test_file, "w") as f:
        f.write("test data")
```

### Create Custom Fixtures

```python
@pytest.fixture
def sample_user():
    """Fixture providing sample user data"""
    return {
        "id": "user_123",
        "name": "Test User",
        "email": "test@example.com"
    }

def test_with_custom_fixture(sample_user):
    """Test using custom fixture"""
    assert sample_user["name"] == "Test User"
```

---

## Best Practices

1. **Keep tests focused** - One test should verify one thing
2. **Use descriptive names** - Test name should describe what is being tested
3. **Follow AAA pattern** - Arrange, Act, Assert
4. **Mock external dependencies** - Don't call real APIs or databases
5. **Test error cases** - Don't just test the happy path
6. **Use fixtures** - Don't repeat setup code
7. **Keep tests fast** - Mock slow operations
8. **Test behavior, not implementation** - Focus on what, not how
9. **Group related tests** - Use test classes for organization
10. **Document complex tests** - Add comments explaining setup

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific file
pytest tests/test_unit_*.py -v

# Run specific test class
pytest tests/test_unit_my_module.py::TestMyModule -v

# Run specific test
pytest tests/test_unit_my_module.py::TestMyModule::test_specific_test -v

# Run with markers
pytest -m unit -v          # Unit tests only
pytest -m integration -v   # Integration tests only
pytest -m "not slow" -v    # Skip slow tests

# Run with coverage
pytest tests/ -v --cov=. --cov-report=html
```

---

## Questions?

For more information, see:

- [TESTING.md](../../docs/reference/TESTING.md) - Comprehensive testing guide
- [conftest.py](./conftest.py) - Available fixtures
- Existing test files in `tests/` directory
