# FastAPI Testing Quick Reference

**Version:** 1.0  
**Last Updated:** December 12, 2025  
**Status:** ✅ Ready to Use

---

## 🚀 Quick Start

### Installation

```bash
# Navigate to project
cd src/cofounder_agent

# Install test dependencies (if not already installed)
pip install pytest pytest-asyncio httpx pytest-cov

# Verify installation
pytest --version
```

### Running Tests

```bash
# Run all tests
pytest

# Run tests with output
pytest -v

# Run specific file
pytest tests/test_api_integration.py

# Run specific test
pytest tests/test_api_integration.py::TestAPIIntegration::test_health_check

# Run with coverage
pytest --cov=. --cov-report=html
```

---

## 📋 Test Organization

### File Naming Convention

```
test_*.py          # Test files
*_test.py          # Alternative naming
conftest.py        # Shared fixtures
```

### Test Class Naming

```python
class Test*:       # Test classes (e.g., TestAPIEndpoints)
class Test*Routes: # Route tests
class Test*Service: # Service tests
```

### Test Function Naming

```python
def test_*:        # Test functions
def test_*_success: # Happy path tests
def test_*_error:   # Error tests
def test_*_invalid: # Invalid input tests
```

---

## 🎯 Common Commands

### Filter by Marker

```bash
pytest -m unit                      # Unit tests only
pytest -m integration               # Integration tests only
pytest -m "unit or integration"     # Multiple markers
pytest -m "not slow"                # Exclude marker
```

### Output Control

```bash
pytest -v                           # Verbose
pytest -q                           # Quiet
pytest -s                           # Show print statements
pytest --tb=short                   # Short traceback
pytest --tb=long                    # Long traceback
```

### Execution Control

```bash
pytest -x                           # Stop on first failure
pytest --maxfail=3                  # Stop after 3 failures
pytest -k "test_auth"               # Run tests matching pattern
pytest --co                         # Collect only (don't run)
```

### Performance

```bash
pytest -n auto                      # Parallel execution (requires pytest-xdist)
pytest --durations=10               # Show slowest 10 tests
pytest --timeout=10                 # Set test timeout
```

---

## 📝 Writing Tests - Templates

### Simple API Test

```python
import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.mark.api
def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

### Async Test

```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await my_async_function()
    assert result is not None
```

### Parametrized Test

```python
@pytest.mark.parametrize("email", [
    "valid@example.com",
    "test@domain.co.uk",
    "user+tag@example.com"
])
def test_valid_emails(email):
    assert validate_email(email)
```

### Test with Fixture

```python
@pytest.fixture
def authenticated_client():
    client = TestClient(app)
    response = client.post("/auth/login", json={
        "email": "test@example.com",
        "password": "password"
    })
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client

def test_protected_endpoint(authenticated_client):
    response = authenticated_client.get("/api/protected")
    assert response.status_code == 200
```

### Test with Mock

```python
from unittest.mock import patch, AsyncMock

@patch('services.database_service.DatabaseService.query')
def test_with_mock(mock_query):
    mock_query.return_value = [{"id": 1}]
    result = get_items()
    assert len(result) == 1
    mock_query.assert_called_once()
```

---

## 🔍 Debugging Failed Tests

### 1. Run with Verbose Output

```bash
pytest -v -s test_file.py::test_name
```

### 2. Use Python Debugger

```python
# Add breakpoint in test
import pdb
pdb.set_trace()

# Run with pdb
pytest --pdb test_file.py::test_name
```

### 3. Check Test Output

```bash
# Show last 100 lines
pytest test_file.py -v | tail -100

# Save to file
pytest test_file.py -v > test_results.txt
```

### 4. Common Issues

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Check Python path in conftest.py |
| `Fixture not found` | Move fixture to conftest.py |
| `Async timeout` | Increase timeout or mark as slow |
| `Database locked` | Use separate test database |
| `Import error` | Run from tests directory |

---

## 📊 Coverage Reports

### Generate Coverage

```bash
# HTML report
pytest --cov=. --cov-report=html
# View at: htmlcov/index.html

# Terminal report
pytest --cov=. --cov-report=term-missing

# Specific module
pytest --cov=routes --cov-report=html
```

### Coverage Thresholds

```bash
# Fail if coverage below 80%
pytest --cov=. --cov-fail-under=80
```

---

## 🛠️ Test Utilities

### Using Test Utilities

```python
from test_utilities import (
    TestClientFactory,
    MockFactory,
    TestDataBuilder,
    AssertionHelpers
)

# Create mocks
mock_db = MockFactory.mock_database()
mock_cache = MockFactory.mock_cache()

# Build test data
user = TestDataBuilder.user(email="test@example.com")
task = TestDataBuilder.task(title="Test Task")

# Use assertions
AssertionHelpers.assert_success_response(response, 200)
AssertionHelpers.assert_has_keys(data, ["id", "name"])
```

### Available Helpers

```python
# Mock factories
MockFactory.mock_async_function()
MockFactory.mock_database()
MockFactory.mock_cache()
MockFactory.mock_http_client()

# Test data builders
TestDataBuilder.user()
TestDataBuilder.task()
TestDataBuilder.content()
TestDataBuilder.jwt_token()

# Assertions
AssertionHelpers.assert_success_response()
AssertionHelpers.assert_error_response()
AssertionHelpers.assert_has_keys()
AssertionHelpers.assert_matches_schema()

# Async helpers
AsyncHelpers.wait_for()
AsyncHelpers.run_concurrent()

# Parametrize helpers
ParametrizeHelpers.http_methods()
ParametrizeHelpers.status_codes_success()
ParametrizeHelpers.invalid_inputs()
```

---

## ✅ Pre-Commit Checklist

Before committing code with tests:

- [ ] All tests pass: `pytest`
- [ ] No coverage regression: `pytest --cov`
- [ ] Linting passes: `pylint tests/`
- [ ] No debug statements left
- [ ] No hardcoded values
- [ ] Fixtures are reusable
- [ ] Tests are isolated
- [ ] Docstrings added
- [ ] Markers applied
- [ ] Related tests grouped

---

## 🔗 Test Markers Guide

```python
@pytest.mark.unit                  # Unit test
@pytest.mark.integration           # Integration test
@pytest.mark.api                   # API endpoint test
@pytest.mark.e2e                   # End-to-end test
@pytest.mark.performance           # Performance test
@pytest.mark.slow                  # Slow running test
@pytest.mark.security              # Security test
@pytest.mark.asyncio               # Async test
@pytest.mark.database              # Database test
```

### Using Markers

```bash
# Run only unit tests
pytest -m unit

# Run integration and api tests
pytest -m "integration or api"

# Skip slow tests
pytest -m "not slow"

# Run async and database tests
pytest -m "asyncio and database"
```

---

## 📁 Project Structure

```
src/cofounder_agent/
├── tests/
│   ├── conftest.py              # Global fixtures
│   ├── pytest.ini               # Config
│   ├── run_tests.py             # Test runner
│   ├── test_utilities.py        # Test helpers
│   ├── test_example_best_practices.py
│   │
│   ├── test_api_integration.py
│   ├── test_auth_routes.py
│   ├── test_content_pipeline.py
│   ├── ... (30+ test files)
│   │
│   ├── test_data/               # Test fixtures
│   │   ├── users.json
│   │   ├── content.json
│   │   └── tasks.json
│   │
│   └── __pycache__/
│
├── routes/                       # API routes
├── services/                     # Business logic
├── models/                       # Data models
└── main.py                       # FastAPI app
```

---

## 🎓 Learning Resources

### Documentation
- [Pytest Docs](https://docs.pytest.org/)
- [FastAPI Testing](https://fastapi.tiangolo.com/advanced/testing-dependencies/)
- [AsyncIO Testing](https://pytest-asyncio.readthedocs.io/)

### Files to Review
1. `conftest.py` - Test configuration and fixtures
2. `pytest.ini` - Pytest settings
3. `test_example_best_practices.py` - Example patterns
4. `test_utilities.py` - Helper functions

---

## 💡 Tips & Tricks

### 1. Run Last Failed Tests

```bash
pytest --lf          # Last failed
pytest --ff          # Failed first
```

### 2. Run Tests Matching Pattern

```bash
pytest -k "auth"     # Only auth-related tests
pytest -k "not slow" # Exclude slow tests
```

### 3. Watch Tests

```bash
# Requires pytest-watch
ptw -- -v
```

### 4. Generate Test Report

```bash
pytest --html=report.html --self-contained-html
```

### 5. Profile Tests

```bash
pytest --profile=prof
```

---

## 🐛 Troubleshooting

### Tests Not Found

```bash
# Check pytest can find tests
pytest --collect-only

# Verify naming
# Files: test_*.py or *_test.py
# Functions: def test_*
```

### Import Errors

```bash
# Check PYTHONPATH
python -c "import sys; print(sys.path)"

# Run from correct directory
cd src/cofounder_agent
pytest
```

### Fixture Errors

```bash
# Show available fixtures
pytest --fixtures

# Check fixture scope and dependencies
pytest -v tests/test_file.py -s
```

---

## 📞 Support

**Having issues?**

1. Check the TESTING_INTEGRATION_GUIDE.md for detailed docs
2. Review conftest.py for fixture setup
3. Look at test_example_best_practices.py for patterns
4. Check test_utilities.py for available helpers
5. Run `pytest --help` for all options

---

## 🎉 You're Ready!

Your FastAPI testing infrastructure is set up and ready to use. Start writing tests using the patterns in `test_example_best_practices.py` and you'll have comprehensive test coverage in no time!

**Next Steps:**
1. Create test files following the examples
2. Use test_utilities for common operations
3. Run tests frequently during development
4. Monitor coverage reports
5. Celebrate when tests pass! 🎊
