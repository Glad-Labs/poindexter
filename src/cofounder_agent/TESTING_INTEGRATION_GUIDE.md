# FastAPI Testing Integration Guide

**Status:** 🚀 Complete  
**Last Updated:** December 12, 2025  
**Test Suite:** 30+ comprehensive test files  
**Coverage:** 80%+ critical paths  

---

## 📋 Overview

This guide covers the complete testing infrastructure for the Glad Labs FastAPI application, including:
- Unit testing patterns
- Integration testing strategies
- End-to-end (E2E) testing
- API endpoint testing
- Database interaction testing
- Best practices and conventions

---

## 🏗️ Testing Architecture

### Test Structure
```
tests/
├── conftest.py                          # Pytest configuration and fixtures
├── pytest.ini                           # Pytest settings
├── run_tests.py                         # Test runner CLI
│
├── Unit Tests (Component Isolation)
│   ├── test_unit_comprehensive.py       # Core logic tests
│   ├── test_unit_settings_api.py        # Settings validation
│   └── test_quality_assessor.py         # Quality assessment logic
│
├── Integration Tests (Service Integration)
│   ├── test_api_integration.py          # API endpoint integration
│   ├── test_fastapi_cms_integration.py  # CMS service integration
│   ├── test_content_pipeline_*          # Content pipeline stages
│   ├── test_phase2_integration.py       # Multi-service flows
│   └── test_route_model_consolidation_integration.py
│
├── E2E Tests (Complete Workflows)
│   ├── test_e2e_fixed.py                # End-to-end flows
│   ├── test_poindexter_e2e.py           # Poindexter workflows
│   └── test_poindexter_orchestrator.py  # Orchestration flows
│
├── API Route Tests (Endpoint Validation)
│   ├── test_auth_routes.py              # Authentication endpoints
│   ├── test_main_endpoints.py           # Core API endpoints
│   ├── test_poindexter_routes.py        # Poindexter API
│   ├── test_subtask_routes.py           # Subtask endpoints
│   ├── test_settings_routes.py          # Settings endpoints
│   └── test_seo_content_generator.py    # SEO generation
│
├── Specialized Tests
│   ├── test_security_validation.py      # Security & auth
│   ├── test_input_validation_webhooks.py # Webhook validation
│   ├── test_integration_settings.py     # Settings integration
│   ├── test_memory_system.py            # Memory operations
│   ├── test_ollama_*.py                 # LLM integration
│   └── test_poindexter_tools.py         # Tool testing
│
└── Test Support
    ├── test_data/                       # Test fixtures and data
    ├── firestore_client.py              # Mock client (for compatibility)
    └── __init__.py
```

---

## 🛠️ Running Tests

### Quick Start

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_api_integration.py

# Run specific test class
pytest tests/test_api_integration.py::TestAPIIntegration

# Run specific test function
pytest tests/test_api_integration.py::TestAPIIntegration::test_health_check

# Run with markers
pytest -m unit                      # Only unit tests
pytest -m integration               # Only integration tests
pytest -m "unit or integration"     # Unit or integration tests
pytest -m "not slow"                # Exclude slow tests
```

### Advanced Options

```bash
# With coverage report
pytest --cov=src/cofounder_agent --cov-report=html

# Show print statements
pytest -s

# Stop on first failure
pytest -x

# Verbose output
pytest -v

# Quiet output
pytest -q

# Run with specific Python version
pytest --tb=short

# Run test suite using runner script
python tests/run_tests.py --type all --coverage --verbose
```

---

## 📝 Test Configuration

### pytest.ini Settings

```ini
[pytest]
minversion = 6.0                          # Minimum pytest version
python_files = test_*.py *_test.py        # Test file patterns
python_classes = Test*                    # Test class patterns
python_functions = test_*                 # Test function patterns
testpaths = .                             # Test directory
pythonpath = ..                           # Python path

addopts = 
    --tb=short                            # Traceback format
    --strict-markers                      # Strict marker checking
    --disable-warnings                    # Disable warnings
    -ra                                   # Report all outcomes

asyncio_mode = auto                       # Auto async mode
asyncio_default_fixture_loop_scope = function  # Fixture scope
```

### Available Markers

```bash
@pytest.mark.unit                  # Unit tests
@pytest.mark.integration           # Integration tests
@pytest.mark.api                   # API tests
@pytest.mark.e2e                   # End-to-end tests
@pytest.mark.performance           # Performance benchmarks
@pytest.mark.slow                  # Slow tests
@pytest.mark.smoke                 # Smoke tests
@pytest.mark.security              # Security tests
@pytest.mark.database              # Database tests
@pytest.mark.websocket             # WebSocket tests
```

---

## 🔧 Common Testing Patterns

### 1. Unit Test Pattern

```python
import pytest
from unittest.mock import Mock, patch
from fastapi import HTTPException

@pytest.mark.unit
class TestMyComponent:
    """Test suite for MyComponent"""
    
    def setup_method(self):
        """Setup for each test"""
        self.component = MyComponent()
    
    def test_valid_input(self):
        """Test with valid input"""
        result = self.component.process("valid_data")
        assert result is not None
        assert result.status == "success"
    
    def test_invalid_input_raises_error(self):
        """Test that invalid input raises error"""
        with pytest.raises(ValueError):
            self.component.process(None)
    
    @patch('module.external_service')
    def test_with_mocked_dependency(self, mock_service):
        """Test with mocked external dependency"""
        mock_service.return_value = {"status": "ok"}
        result = self.component.process("data")
        assert result.status == "success"
        mock_service.assert_called_once()
```

### 2. Async Test Pattern

```python
import pytest
from httpx import AsyncClient
from fastapi import FastAPI

@pytest.mark.asyncio
@pytest.mark.integration
class TestAsyncEndpoint:
    """Test async endpoint"""
    
    async def test_async_operation(self):
        """Test async function"""
        result = await my_async_function()
        assert result is not None
    
    async def test_api_endpoint(self, client: AsyncClient):
        """Test API endpoint with async client"""
        response = await client.get("/api/endpoint")
        assert response.status_code == 200
        assert response.json()["success"] is True
```

### 3. Database Test Pattern

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

@pytest.mark.database
@pytest.mark.integration
class TestDatabaseOperations:
    """Test database operations"""
    
    @pytest.fixture
    async def db_session(self):
        """Create test database session"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with AsyncSession(engine) as session:
            yield session
    
    async def test_create_record(self, db_session: AsyncSession):
        """Test creating a database record"""
        record = MyModel(name="Test")
        db_session.add(record)
        await db_session.commit()
        
        result = await db_session.query(MyModel).first()
        assert result.name == "Test"
```

### 4. FastAPI Client Test Pattern

```python
import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.mark.api
class TestAPIEndpoints:
    """Test FastAPI endpoints"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        return TestClient(app)
    
    def test_get_endpoint(self, client):
        """Test GET endpoint"""
        response = client.get("/api/resource")
        assert response.status_code == 200
        assert "data" in response.json()
    
    def test_post_endpoint(self, client):
        """Test POST endpoint"""
        payload = {"name": "Test Resource"}
        response = client.post("/api/resource", json=payload)
        assert response.status_code == 201
        assert response.json()["id"] is not None
    
    def test_authentication_required(self, client):
        """Test that endpoint requires authentication"""
        response = client.get("/api/protected")
        assert response.status_code == 401
```

### 5. Fixture Pattern

```python
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_database():
    """Mock database service"""
    mock = AsyncMock()
    mock.query.return_value = [{"id": 1, "name": "Test"}]
    return mock

@pytest.fixture
def sample_user_data():
    """Sample user data for tests"""
    return {
        "email": "test@example.com",
        "password": "securepassword123",
        "name": "Test User"
    }

@pytest.fixture
def authenticated_client(client, sample_user_data):
    """Client with authentication"""
    response = client.post("/auth/login", json=sample_user_data)
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
```

---

## 🔍 Testing Best Practices

### 1. Test Organization

**✅ DO:**
- One test class per component/module
- Logical grouping of related tests
- Clear, descriptive test names
- Use fixtures for reusable setup

**❌ DON'T:**
- Mix unit and integration tests in same class
- Use hardcoded test data
- Create test interdependencies
- Skip proper setup/teardown

### 2. Assertion Patterns

```python
# ✅ Good: Clear, specific assertions
assert result.status_code == 200
assert "success" in response.json()
assert len(results) == 3

# ❌ Poor: Vague assertions
assert response
assert result
assert error is None
```

### 3. Mock and Patch Patterns

```python
# ✅ Good: Patch at usage site
@patch('routes.auth_unified.verify_token')
def test_with_mock(self, mock_verify):
    mock_verify.return_value = {"user_id": 1}
    response = client.get("/api/protected")
    assert response.status_code == 200

# ❌ Poor: Patch at definition site
@patch('cryptography.fernet.Fernet')
def test_with_wrong_patch(self, mock_fernet):
    # This won't affect the actual usage
    pass
```

### 4. Async Testing

```python
# ✅ Good: Use pytest-asyncio
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result is not None

# ❌ Poor: Blocking on async
def test_async_function():
    result = asyncio.run(async_function())  # Works but not idiomatic
```

### 5. Error Testing

```python
# ✅ Good: Test specific errors
def test_invalid_user_raises_value_error():
    with pytest.raises(ValueError, match="User not found"):
        get_user(None)

# ❌ Poor: Generic error catching
def test_invalid_user():
    try:
        get_user(None)
    except Exception:
        pass  # Too broad
```

---

## 📊 Coverage Goals

### By Component Type

```
Unit Tests:
  - Pure functions: 100%
  - Validation logic: 100%
  - Utility functions: 95%+
  - Error handling: 90%+

Integration Tests:
  - API endpoints: 85%+
  - Service interactions: 80%+
  - Database operations: 75%+

E2E Tests:
  - Critical user flows: 100%
  - Happy paths: 100%
  - Error scenarios: 80%+
```

### Measuring Coverage

```bash
# Generate HTML coverage report
pytest --cov=src/cofounder_agent --cov-report=html

# Coverage by file
pytest --cov=src/cofounder_agent --cov-report=term-missing

# Coverage for specific module
pytest --cov=src/cofounder_agent.routes --cov-report=html
```

---

## 🐛 Debugging Failed Tests

### 1. View Full Output

```bash
# Show all output including prints
pytest -s -v test_file.py::test_name

# Show last traceback info
pytest --tb=long test_file.py::test_name

# Drop into debugger on failure
pytest --pdb test_file.py::test_name
```

### 2. Common Issues

**Async timeout issues:**
```python
# Increase timeout in pytest.ini or mark
@pytest.mark.timeout(10)
async def test_long_operation():
    await long_operation()
```

**Database state issues:**
```python
# Ensure clean state before each test
@pytest.fixture(autouse=True)
async def reset_database():
    await clear_all_tables()
    yield
    await clear_all_tables()
```

**Import errors:**
```bash
# Check Python path
pytest --co test_file.py  # Collect only, don't run

# Debug imports
python -c "import sys; sys.path.insert(0, '..'); from routes import auth"
```

### 3. Using Debugger

```python
import pdb

# In test code
def test_something():
    result = process_data()
    pdb.set_trace()  # Debugger will stop here
    assert result == expected
```

---

## 🚀 Running Full Test Suite

### Using run_tests.py

```bash
# From tests directory
python run_tests.py --type all --coverage --verbose

# Specific test type
python run_tests.py --type unit
python run_tests.py --type integration
python run_tests.py --type e2e

# Generate report
python run_tests.py --type all --report
```

### Using pytest directly

```bash
# All tests with output
pytest -v

# All tests with coverage
pytest --cov=src/cofounder_agent --cov-report=html -v

# Skip slow tests
pytest -m "not slow" -v

# Stop on first failure
pytest -x -v
```

---

## 📁 Test Data Management

### Test Data Directory

```
tests/test_data/
├── fixtures/                    # Reusable test fixtures
│   ├── users.json              # User test data
│   ├── content.json            # Content samples
│   └── tasks.json              # Task samples
│
├── mocks/                       # Mock responses
│   ├── api_responses.py         # API mock responses
│   └── service_responses.py     # Service mock responses
│
└── seeds/                       # Database seeds
    └── initial_data.sql        # Initial test data
```

### Loading Test Data

```python
import json
from pathlib import Path

@pytest.fixture
def test_users():
    """Load test users"""
    test_data_path = Path(__file__).parent / "test_data" / "users.json"
    with open(test_data_path) as f:
        return json.load(f)

def test_with_user_data(test_users):
    user = test_users[0]
    assert user["email"] is not None
```

---

## 🔐 Security Testing

### Authentication Testing

```python
@pytest.mark.security
class TestAuthentication:
    """Test authentication security"""
    
    def test_invalid_token_rejected(self, client):
        """Test that invalid tokens are rejected"""
        response = client.get(
            "/api/protected",
            headers={"Authorization": "Bearer invalid_token"}
        )
        assert response.status_code == 401
    
    def test_expired_token_rejected(self, client):
        """Test that expired tokens are rejected"""
        expired_token = create_expired_token()
        response = client.get(
            "/api/protected",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.status_code == 401
    
    def test_missing_token_rejected(self, client):
        """Test that missing tokens are rejected"""
        response = client.get("/api/protected")
        assert response.status_code == 401
```

### Input Validation Testing

```python
@pytest.mark.security
class TestInputValidation:
    """Test input validation"""
    
    def test_sql_injection_prevented(self, client):
        """Test SQL injection prevention"""
        payload = {"query": "'; DROP TABLE users; --"}
        response = client.post("/api/search", json=payload)
        assert response.status_code == 400  # Bad request
    
    def test_xss_prevented(self, client):
        """Test XSS prevention"""
        payload = {"content": "<script>alert('xss')</script>"}
        response = client.post("/api/content", json=payload)
        # Should sanitize or reject
        assert "<script>" not in response.json()
```

---

## 📈 Performance Testing

### Load Testing

```python
@pytest.mark.performance
class TestPerformance:
    """Test performance metrics"""
    
    def test_endpoint_response_time(self, client):
        """Test endpoint responds within acceptable time"""
        import time
        start = time.time()
        response = client.get("/api/fast-endpoint")
        elapsed = time.time() - start
        
        assert response.status_code == 200
        assert elapsed < 0.5  # Must respond in < 500ms
    
    @pytest.mark.slow
    def test_large_dataset_handling(self, client):
        """Test handling large datasets"""
        # Create large payload
        large_data = [{"id": i} for i in range(10000)]
        response = client.post("/api/bulk-import", json=large_data)
        
        assert response.status_code == 200
        assert response.json()["imported_count"] == 10000
```

---

## 🔗 Integration Test Checklist

- [ ] Service dependencies properly mocked
- [ ] Database state properly initialized
- [ ] External API calls mocked or stubbed
- [ ] Error responses tested
- [ ] Success responses validated
- [ ] State changes verified
- [ ] Side effects tested
- [ ] Cleanup performed after test

---

## 📚 Test Documentation Template

```python
"""
Test module for [Component Name]
Module: [Module Path]
Coverage: [Coverage %]
Status: [Active/Deprecated]

Tests:
  - test_basic_operation: Basic functionality
  - test_error_handling: Error scenarios
  - test_edge_cases: Edge cases
  
Dependencies:
  - [Dependency 1]
  - [Dependency 2]

Notes:
  - Important test characteristics
  - Known limitations
  - Performance considerations
"""
```

---

## 🛠️ CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: pytest --cov=src/cofounder_agent
      - uses: codecov/codecov-action@v2
```

---

## 📞 Support & Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError` | Path not configured | Check `conftest.py` paths |
| `Timeout in test` | Slow operation | Add `@pytest.mark.timeout(30)` |
| `Fixture not found` | Fixture scope issue | Move to `conftest.py` |
| `Async test hangs` | Event loop issue | Ensure `asyncio_mode = auto` |
| `Database locked` | Test isolation | Use separate test DB |

### Getting Help

1. Check test output: `pytest -v -s`
2. Review conftest.py fixtures
3. Check pytest.ini configuration
4. Review test_data directory
5. Consult specific test file docstrings

---

## ✅ Checklist for New Tests

- [ ] Test is properly marked with marker (`@pytest.mark.*`)
- [ ] Test name clearly describes what it tests
- [ ] Setup/teardown is isolated (no test dependencies)
- [ ] External dependencies are mocked
- [ ] Assertions are specific and clear
- [ ] Error cases are tested
- [ ] Happy path is tested
- [ ] Test is added to appropriate test file
- [ ] Documentation string explains purpose
- [ ] Test runs under 5 seconds (non-slow)

---

## 🎓 Additional Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [FastAPI Testing](https://fastapi.tiangolo.com/advanced/testing-dependencies/)
- [AsyncIO Testing](https://pytest-asyncio.readthedocs.io/)
- [Coverage.py](https://coverage.readthedocs.io/)

---

**Last Updated:** December 12, 2025  
**Maintainer:** Glad Labs Development Team  
**Status:** ✅ Complete and Production-Ready
