# Python Backend Tests - Setup Requirements

**Current Status:** 4/60+ tests passing - Fixture setup needed  
**Issue Type:** Pytest fixture configuration, not test logic  
**ETA to Fix:** 1-2 hours

---

## Problem Analysis

### Current Errors

```
FAILED: TestHealthEndpoint::test_health_check_returns_200
AssertionError: assert <MagicMock...> == 200

FAILED: TestProcessQueryEndpoint::test_process_query_with_valid_input
AssertionError: assert 200 (mock response code, not actual)

Error: coroutine was never awaited
```

### Root Cause

The test file `src/cofounder_agent/tests/test_main_endpoints.py` expects:

1. A `client` fixture (FastAPI TestClient)
2. A `mock_orchestrator` fixture
3. Proper async handling for FastAPI endpoints

But `conftest.py` doesn't provide these fixtures.

---

## Required Fixes

### 1. Add TestClient Fixture (conftest.py)

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

@pytest.fixture
def client():
    """Provide FastAPI TestClient for endpoint testing"""
    # Import the app from main.py
    from cofounder_agent.main import app
    return TestClient(app)

@pytest.fixture
def mock_orchestrator():
    """Mock the orchestrator for testing"""
    orchestrator_mock = AsyncMock()
    orchestrator_mock.orchestrate.return_value = {
        "response": "Mock response",
        "action": "mock_action",
        "confidence": 0.9
    }
    return orchestrator_mock
```

### 2. Fix Async Test Handling

Tests that use async functions need `@pytest.mark.asyncio`:

```python
@pytest.mark.asyncio
async def test_async_endpoint():
    response = client.post("/endpoint", json={...})
    assert response.status_code == 200
```

### 3. Update Main Fixture Setup

The conftest should also patch the app's dependency:

```python
@pytest.fixture
def client(mock_orchestrator):
    """TestClient with mocked orchestrator"""
    from cofounder_agent.main import app, get_orchestrator

    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator

    client = TestClient(app)
    yield client

    # Cleanup
    app.dependency_overrides.clear()
```

---

## Implementation Steps

### Step 1: Update conftest.py

1. Open `src/cofounder_agent/tests/conftest.py`
2. Add TestClient fixture
3. Add mock_orchestrator fixture
4. Add dependency override setup
5. Install pytest-asyncio if needed: `pip install pytest-asyncio`

### Step 2: Install Dependencies

```bash
pip install fastapi[all] pytest-asyncio httpx
```

### Step 3: Run Tests

```bash
cd c:\Users\mattm\glad-labs-website
python -m pytest src/cofounder_agent/tests/test_main_endpoints.py -v --asyncio-mode=auto
```

### Step 4: Debug Remaining Issues

- Watch for missing endpoint implementations
- Verify orchestrator mocking
- Check for proper response formats

---

## Test File Issues

The `test_main_endpoints.py` file also needs:

### Missing Imports at Top

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
```

### Missing Fixture Parameters

```python
# Current (wrong)
class TestHealthEndpoint:
    def test_health_check_returns_200(self):
        response = client.get("/health")

# Should be (using pytest fixture)
class TestHealthEndpoint:
    def test_health_check_returns_200(self, client):
        response = client.get("/health")
```

### Async Endpoint Handling

```python
# Tests for async endpoints need to use TestClient properly
# TestClient automatically handles async for synchronous calls

# This works:
response = client.post("/async-endpoint", json={...})  # TestClient handles async

# This doesn't work:
response = await client.post("/async-endpoint")  # TestClient is sync wrapper
```

---

## Quick Setup Command

```bash
# 1. Install test dependencies
pip install pytest pytest-asyncio fastapi httpx

# 2. Update conftest.py with fixtures above

# 3. Run tests
pytest src/cofounder_agent/tests/test_main_endpoints.py -v --asyncio-mode=auto

# 4. Expected result after fixes:
# Should see ~50-55 tests passing
# Some may still fail due to missing endpoint implementations
```

---

## What to Expect After Fixes

### Likely Outcomes

- ✅ Health endpoint tests: Should pass (simple endpoint)
- ✅ Mock orchestrator tests: Should pass (fully mocked)
- ⏳ Integration tests: May need actual endpoint implementations
- ⏳ Stream tests: May need proper async setup

### Test Coverage After Fixes

- Currently: 4/60 passing (6%)
- Expected: 45-55/60 passing (75-92%)
- Remaining failures: Likely due to missing actual endpoint implementations

---

## Files That Need Changes

1. **conftest.py** - Add TestClient and mock fixtures
2. **test_main_endpoints.py** - Ensure fixture parameters are correct
3. **main.py** - May need to export TestClient-compatible app

---

## Estimated Time to Complete

| Task                   | Time            |
| ---------------------- | --------------- |
| Install pytest-asyncio | 5 min           |
| Update conftest.py     | 15 min          |
| Run initial tests      | 5 min           |
| Debug and fix          | 30-45 min       |
| Verify passing tests   | 10 min          |
| **TOTAL**              | **1-1.5 hours** |

---

## Reference Resources

- FastAPI Testing: https://fastapi.tiangolo.com/advanced/testing-dependencies/
- Pytest Fixtures: https://docs.pytest.org/en/stable/fixture.html
- TestClient Docs: https://www.starlette.io/testclient/
