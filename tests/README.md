# Test Suite Organization Guide

## Overview

The test suite has been reorganized into a centralized, scalable structure under the `tests/` directory at the project root. This eliminates fragmentation and provides a single source of truth for all tests.

## Directory Structure

```
tests/
├── conftest.py                 # Central pytest configuration and shared fixtures
├── test_utils.py              # Shared test utilities and helper functions
├── pytest.ini                 # Pytest configuration (can also use pyproject.toml)
├── unit/                      # Unit tests (isolated components, no external services)
│   ├── __init__.py
│   ├── backend/               # Backend/FastAPI tests
│   │   ├── conftest.py       # Local conftest with extended fixtures
│   │   ├── test_*.py         # Individual test files
│   │   └── test_data/        # Test data and fixtures
│   ├── agents/                # AI agent tests
│   │   ├── test_*.py
│   │   └── conftest.py
│   └── mcp/                   # MCP server tests
│       ├── test_*.py
│       └── conftest.py
├── integration/               # Integration tests (multiple components interact)
│   ├── __init__.py
│   ├── test_*.py
│   └── conftest.py           # Local fixtures for integration tests
└── e2e/                       # End-to-end tests (full system workflows)
    ├── __init__.py
    ├── test_*.py
    └── conftest.py
```

## Test Categories

### Unit Tests (`tests/unit/`)

- **Purpose**: Test individual components in isolation
- **Dependencies**: Mock external services, databases, APIs
- **Speed**: Fast (< 1 second each)
- **Location**:
  - `tests/unit/backend/` - Backend services, routes, models
  - `tests/unit/agents/` - AI agent components
  - `tests/unit/mcp/` - MCP protocol implementations

**Examples**:

- Model validation tests
- Service initialization tests
- Route parameter validation
- Utility function tests

### Integration Tests (`tests/integration/`)

- **Purpose**: Test interactions between multiple components
- **Dependencies**: Real database (test instance), real services
- **Speed**: Medium (1-10 seconds each)
- **Pattern**: Test workflows that span multiple layers

**Examples**:

- API endpoint integration with database
- Agent orchestration workflows
- Model router fallback behavior
- Full content pipeline

### End-to-End Tests (`tests/e2e/`)

- **Purpose**: Test complete user workflows and system behavior
- **Dependencies**: Full running system (backend, database, services)
- **Speed**: Slow (10+ seconds each)
- **Pattern**: Test real business scenarios

**Examples**:

- Complete task creation → processing → completion
- Multi-step content generation pipeline
- User authentication → task delegation → result retrieval

## Running Tests

### Run All Tests

```bash
# From project root
pytest                          # Run all tests
pytest -v                       # Verbose output
pytest --tb=short              # Shorter traceback format
```

### Run by Category

```bash
# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# E2E tests only
pytest tests/e2e/

# Backend unit tests only
pytest tests/unit/backend/

# Specific test file
pytest tests/unit/backend/test_api_integration.py

# Specific test class
pytest tests/unit/backend/test_api_integration.py::TestAPIEndpoints

# Specific test function
pytest tests/unit/backend/test_api_integration.py::TestAPIEndpoints::test_health_endpoint
```

### Run by Marker

```bash
# Skip CI tests
pytest -m "not skip_ci"

# Run only API tests
pytest -m "api"

# Run only performance tests
pytest -m "performance"
```

### Run with Coverage

```bash
pytest --cov=src --cov-report=html
```

## Pytest Configuration

### Primary Config: `pytest.ini` (Project Root)

```ini
testpaths = tests          # Discover tests in this directory
pythonpath = .;src;src/cofounder_agent  # Add to Python path
asyncio_mode = auto       # Auto-detect async tests
```

### Backup Config: `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = [".", "src", "src/cofounder_agent"]
asyncio_mode = "auto"
```

## Shared Fixtures and Utilities

### From `conftest.py`:

- `event_loop` - Event loop for async tests
- `test_config_fixture` - Test configuration
- `project_root_path` - Path to project root
- `performance_monitor_fixture` - Monitor async operation performance

### From `test_utils.py`:

- `TestConfig` - Configuration dataclass
- `TestUtils` - Utility functions (assertions, mock creation)
- `PerformanceMonitor` - Track operation timing
- `MockAPIResponse` - Mock HTTP responses

### Usage in Tests:

```python
from tests.test_utils import test_utils, performance_monitor

async def test_something(performance_monitor_fixture):
    # Use fixtures
    result, duration, success = await performance_monitor_fixture.measure_async_operation(
        "my_operation",
        async_function
    )
```

## Test Markers

Available pytest markers for categorizing tests:

```python
@pytest.mark.unit          # Unit test
@pytest.mark.integration   # Integration test
@pytest.mark.e2e          # End-to-end test
@pytest.mark.api          # API test
@pytest.mark.slow         # Slow test (skip with -m "not slow")
@pytest.mark.skip_ci      # Skip in CI environment
@pytest.mark.asyncio      # Async test
@pytest.mark.performance  # Performance test
@pytest.mark.websocket    # WebSocket test
```

## CI/CD Integration

### GitHub Actions

```yaml
- name: Run Unit Tests
  run: pytest tests/unit/ -v

- name: Run Integration Tests
  run: pytest tests/integration/ -v

- name: Run E2E Tests (Skip CI)
  if: github.event_name == 'pull_request'
  run: pytest tests/e2e/ -m "not skip_ci" -v
```

## Migration Notes

### Tests Moved From:

- `src/cofounder_agent/tests/*` → `tests/unit/backend/`
- `src/cofounder_agent/agents/*/tests/*` → `tests/unit/agents/`
- `src/mcp*/test_*.py` → `tests/unit/mcp/`
- `tests/test_*integration*.py` → `tests/integration/`
- `tests/test_phase_3_*.py` → `tests/e2e/`

### Removed:

- `src/cofounder_agent/tests/firestore_client.py` (legacy, not used)

### Import Path Updates:

**Old**:

```python
from src.cofounder_agent.tests.conftest import TEST_CONFIG
```

**New**:

```python
import pytest
from tests.conftest import TEST_CONFIG  # Or use fixtures
```

## Best Practices

1. **Use Fixtures**: Prefer pytest fixtures over manual setup
2. **Mark Tests**: Always mark tests with appropriate markers (@pytest.mark.unit, etc.)
3. **Isolate Tests**: Unit tests should not depend on external services
4. **Mock External Deps**: Use unittest.mock for services, APIs, databases
5. **Descriptive Names**: Test names should clearly describe what's being tested
6. **One Assertion Focus**: Each test should focus on one behavior
7. **Use Conftest Locally**: Local `conftest.py` files provide test-specific fixtures

## Troubleshooting

### Import Errors

```
ModuleNotFoundError: No module named 'src'
```

**Solution**: Ensure `pythonpath` in pytest.ini includes necessary paths.

### Tests Not Discovered

```
collected 0 items
```

**Solution**:

- Check `testpaths` in pytest.ini points to `tests/`
- Ensure test files are named `test_*.py`
- Check test functions are named `test_*`

### Async Test Errors

```
RuntimeError: Event loop is closed
```

**Solution**: Use `@pytest.mark.asyncio` or `event_loop` fixture.

## Questions?

Refer to individual `conftest.py` files in each test category for specific fixtures and configuration.
