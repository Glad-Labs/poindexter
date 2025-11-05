# Phase 2: Comprehensive Unit Testing Plan

**Status:** 🚀 Ready to Start  
**Current Tests:** 51 collected, 5 smoke tests passing (5/5 ✅)  
**Target:** 20-30 total tests with >80% coverage  
**Timeline:** 2-3 hours  
**Branch:** `feature/crewai-phase1-integration`

---

## 📊 Phase 2 Test Breakdown

### Component 1: ModelRouter Unit Tests ⭐ START HERE

**File:** `src/cofounder_agent/tests/test_model_router.py` (NEW)  
**Time:** 30-40 minutes  
**Tests:** 3-4 unit tests

**What to test:**

1. ✅ Provider selection logic
   - Simple tasks → GPT-3.5 or Ollama Phi (cheapest)
   - Complex tasks → Claude Opus or GPT-4 (most capable)
   - Cost optimization verified

2. ✅ Fallback chain (Ollama → Claude → GPT → Gemini)
   - Ollama unavailable → Falls back to Claude
   - Claude fails → Falls back to GPT
   - All fail → Final fallback to Gemini

3. ✅ Token limiting
   - Summary task → max 150 tokens
   - Analysis task → max 500 tokens
   - Validation on actual requests

4. ✅ Cost tracking (optional)
   - Calculate cost per request
   - Track total costs by provider

**Key Code:**

```python
from services.model_router import ModelRouter, TaskComplexity, ModelProvider

# Initialize
router = ModelRouter()

# Test 1: Provider selection by complexity
async def test_provider_selection_by_complexity():
    # Simple task → budget model
    # Complex task → premium model
    # Verify selection logic

# Test 2: Fallback chain
async def test_fallback_chain_ollama_unavailable():
    # Ollama unavailable
    # Should fallback to Claude
    # Should work seamlessly

# Test 3: Token limits
def test_token_limits_by_task():
    # Summary task → max 150
    # Analysis task → max 500
    # Verify limits enforced
```

---

### Component 2: DatabaseService Unit Tests

**File:** `src/cofounder_agent/tests/test_database_service.py` (NEW)  
**Time:** 40-50 minutes  
**Tests:** 3-4 unit tests

**What to test:**

1. ✅ Async connection pooling
   - Pool initializes correctly
   - Connections acquired and released
   - Pool size respected

2. ✅ Transaction handling
   - Insert/update within transaction
   - Rollback on error
   - Commit on success

3. ✅ CRUD operations
   - Create (insert) new record
   - Read (select) existing record
   - Update (modify) record
   - Delete (remove) record

4. ✅ Error recovery
   - Handle connection timeout
   - Handle SQL errors gracefully
   - Reconnect after failure

**Key Code:**

```python
from services.database_service import DatabaseService

# Initialize
db = DatabaseService("sqlite+aiosqlite:///test.db")
await db.initialize()

# Test 1: Connection pool
async def test_connection_pool_initialization():
    # Pool should exist
    # Min/max size respected
    # Verify with concurrent requests

# Test 2: Transactions
async def test_transaction_rollback_on_error():
    # Insert record
    # Cause error mid-transaction
    # Verify rollback (no record inserted)

# Test 3: CRUD operations
async def test_create_and_read_task():
    # Create task
    # Read it back
    # Verify data matches

# Test 4: Error handling
async def test_connection_error_recovery():
    # Simulate connection error
    # Should retry or raise appropriate error
```

---

### Component 3: ContentRoutes Unit Tests

**File:** `src/cofounder_agent/tests/test_content_routes_unit.py` (NEW)  
**Time:** 30-40 minutes  
**Tests:** 2-3 unit tests

**What to test:**

1. ✅ Endpoint validation
   - POST `/api/content/generate` accepts valid data
   - Rejects missing required fields
   - Validates data types

2. ✅ Data transformation
   - Input data transformed correctly
   - Output formatted as expected
   - Error responses are consistent

3. ✅ Integration with other services
   - Calls ModelRouter correctly
   - Calls DatabaseService correctly
   - Handles service errors

**Key Code:**

```python
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Test 1: Valid request succeeds
def test_generate_content_valid_request():
    response = client.post("/api/content/generate", json={
        "prompt": "Write a blog post about AI",
        "style": "professional",
        "max_tokens": 1000
    })
    assert response.status_code == 200
    assert "content" in response.json()

# Test 2: Missing required field rejected
def test_generate_content_missing_prompt():
    response = client.post("/api/content/generate", json={
        "style": "professional"  # Missing prompt
    })
    assert response.status_code == 422  # Validation error

# Test 3: Error handling
def test_generate_content_service_error():
    # Mock ModelRouter to return error
    # Verify error is handled gracefully
    # Response has appropriate error message
```

---

## 🎯 Success Criteria

**All tests must:**

- ✅ Pass locally: `pytest src/cofounder_agent/tests/test_*.py -v`
- ✅ Async-compatible: Use `@pytest.mark.asyncio` for async tests
- ✅ Have clear docstrings explaining what's tested
- ✅ Use fixtures from `conftest.py` for setup/teardown
- ✅ Mock external services (don't call real APIs)
- ✅ Cover both success and error cases

**Phase 2 completion criteria:**

- [ ] 20-30 total tests collected
- [ ] 0 collection errors
- [ ] All tests passing (100% pass rate)
- [ ] Coverage >80% for tested components
- [ ] 3 new test files created (model_router, database_service, content_routes)
- [ ] Git commits with clear messages

---

## 📝 Test Template (Copy & Modify)

```python
"""
Unit tests for [Component Name]

Tests the [Component] service including:
- [Feature 1]
- [Feature 2]
- Error handling
"""

import pytest
from unittest.mock import patch, AsyncMock
import asyncio


class TestComponentFeature1:
    """Test suite for [Feature 1]"""

    def setup_method(self):
        """Setup before each test"""
        self.fixture = create_fixture()

    def teardown_method(self):
        """Cleanup after each test"""
        self.fixture.cleanup()

    @pytest.mark.asyncio
    async def test_feature_1_success(self):
        """Should succeed with valid input"""
        result = await self.fixture.method()
        assert result is not None
        assert result["key"] == "expected"

    def test_feature_1_validation_error(self):
        """Should reject invalid input"""
        with pytest.raises(ValueError):
            self.fixture.method(invalid_data)


class TestComponentFeature2:
    """Test suite for [Feature 2]"""

    @pytest.mark.asyncio
    async def test_feature_2_with_mock(self):
        """Should handle service failures gracefully"""
        with patch('service.method') as mock:
            mock.side_effect = Exception("Service error")
            result = await component.method()
            assert result["error"] is not None
```

---

## 🔧 Implementation Steps

### Step 1: ModelRouter Tests (30 min)

```bash
# 1. Create test file
# 2. Import ModelRouter
# 3. Write 3-4 tests (provider selection, fallback, token limits)
# 4. Run tests: pytest tests/test_model_router.py -v
# 5. Commit: git add tests/test_model_router.py
```

### Step 2: DatabaseService Tests (40 min)

```bash
# 1. Create test file
# 2. Import DatabaseService
# 3. Write 3-4 tests (pool, transactions, CRUD, errors)
# 4. Run tests: pytest tests/test_database_service.py -v
# 5. Commit: git add tests/test_database_service.py
```

### Step 3: ContentRoutes Tests (30 min)

```bash
# 1. Create test file
# 2. Import content_router and TestClient
# 3. Write 2-3 tests (validation, transformation, integration)
# 4. Run tests: pytest tests/test_content_routes_unit.py -v
# 5. Commit: git add tests/test_content_routes_unit.py
```

### Step 4: Verify & Commit (20 min)

```bash
# 1. Run all tests: pytest src/cofounder_agent/tests/ -v
# 2. Check coverage: pytest --cov=src/cofounder_agent --cov-report=term
# 3. Target: >80% coverage
# 4. Final commit: git add . && git commit -m "test: Phase 2 unit tests complete"
```

---

## 📚 Resources Available

**Working Examples:**

- `test_e2e_fixed.py` - Smoke tests (5 tests, all passing)
- `test_api_integration.py` - API tests (19 tests, good patterns)
- `test_ollama_client.py` - Async tests (27 tests, fixtures demo)

**Best Practices Docs:**

- `docs/reference/TESTING.md` - Comprehensive testing guide
- `docs/reference/TESTING_QUICK_START.md` - Quick patterns
- `conftest.py` - Pytest fixtures and configuration

**Existing Fixtures Available:**

- `test_db_path` - SQLite test database
- `task_store` - Pre-initialized task store
- `app` - FastAPI test client

---

## ⏱️ Timeline

| Phase     | Task                  | Time       | Status     |
| --------- | --------------------- | ---------- | ---------- |
| **2.1**   | ModelRouter tests     | 30-40 min  | ⏳ Pending |
| **2.2**   | DatabaseService tests | 40-50 min  | ⏳ Pending |
| **2.3**   | ContentRoutes tests   | 30-40 min  | ⏳ Pending |
| **2.4**   | Coverage validation   | 10 min     | ⏳ Pending |
| **2.5**   | Git commits           | 10 min     | ⏳ Pending |
| **Total** | Phase 2 Complete      | ~2-3 hours | 🎯 Target  |

---

## 🚀 Next Steps

1. **Start with ModelRouter** (simplest, good patterns)
2. **Move to DatabaseService** (more complex, async/await)
3. **Finish with ContentRoutes** (integration test)
4. **Verify coverage >80%**
5. **Commit Phase 2**

**Ready to begin?** Start with ModelRouter tests and I'll help implement each one! 🎯
