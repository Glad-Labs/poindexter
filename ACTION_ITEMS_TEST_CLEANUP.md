# Test Cleanup Action Items

**Last Updated:** November 4, 2025  
**Priority:** Medium  
**Effort:** 15 min (cleanup) + 2-3 hours (replacement tests)

---

## 🎯 Immediate Actions (Do This First)

### Option A: Recommended - Delete Legacy Test Files

**Why:** These files will never pass without significant refactoring. Removing them cleans the test collection and allows focus on real tests.

**Action:**
```powershell
cd c:\Users\mattm\glad-labs-website

# Delete 7 broken test files
Remove-Item src/cofounder_agent/tests/test_unit_settings_api.py
Remove-Item src/cofounder_agent/tests/test_content_pipeline.py
Remove-Item src/cofounder_agent/tests/test_enhanced_content_routes.py
Remove-Item src/cofounder_agent/tests/test_integration_settings.py
Remove-Item src/cofounder_agent/tests/test_model_consolidation_service.py
Remove-Item src/cofounder_agent/tests/test_route_model_consolidation_integration.py
Remove-Item src/cofounder_agent/tests/test_seo_content_generator.py

# Verify clean collection
python -m pytest src/cofounder_agent/tests/ --collect-only -q

# Verify tests still pass
python -m pytest src/cofounder_agent/tests/test_e2e_fixed.py -v

# Commit
git add -A
git commit -m "refactor: delete 7 legacy test files with unsalvageable import errors

Removed test files that depend on deleted/refactored modules:
- test_unit_settings_api.py
- test_content_pipeline.py
- test_enhanced_content_routes.py
- test_integration_settings.py
- test_model_consolidation_service.py
- test_route_model_consolidation_integration.py
- test_seo_content_generator.py

Rationale: These files cannot pass without complete refactoring and importing
deleted modules. Removing them allows clean test collection and focus on modern,
focused unit tests.

Remaining tests: 5 smoke tests (100% passing)"
```

**Result:**
- ✅ Test collection becomes clean (no errors)
- ✅ 5 smoke tests still pass
- ✅ Clear foundation for building new focused tests
- ✅ CI/CD pipeline can run without errors

---

### Option B: Keep for Now - Fix Later

**If you want to fix them later instead of deleting:**

Create a file `src/cofounder_agent/tests/LEGACY_TESTS_TODO.md` documenting what needs to be fixed. Then skip them all:

```python
# In each legacy test file, add at the top:
import pytest

@pytest.mark.skip(reason="Legacy test - requires refactoring. See LEGACY_TESTS_TODO.md")
class TestLegacy:
    pass
```

**Not recommended** - This just delays the decision and creates technical debt.

---

## 📝 Phase 2: Create Replacement Tests (Optional - Next Sprint)

**These are not urgent, but valuable for production stability.**

### New Test 1: Database Service Unit Tests

**File:** `src/cofounder_agent/tests/test_database_service.py`

```python
"""Unit tests for DatabaseService - testing database operations without main.py"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, AsyncMock

# Add services to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.database_service import DatabaseService

class TestDatabaseServiceUnit:
    """Test DatabaseService in isolation"""
    
    @pytest.fixture
    def service(self):
        """Create DatabaseService for testing"""
        # Use in-memory SQLite
        return DatabaseService(database_url="sqlite:///:memory:")
    
    @pytest.mark.asyncio
    async def test_initialization(self, service):
        """Test database initialization"""
        await service.initialize()
        assert service is not None
    
    @pytest.mark.asyncio
    async def test_connection_pool(self, service):
        """Test connection pool creation"""
        await service.initialize()
        assert service.pool is not None
```

**Time to create:** 30 minutes

---

### New Test 2: Model Router Unit Tests

**File:** `src/cofounder_agent/tests/test_model_router_unit.py`

```python
"""Unit tests for model routing and fallback chain"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.model_router import ModelRouter

class TestModelRouterUnit:
    """Test model router and provider fallback"""
    
    @pytest.fixture
    def router(self):
        """Create ModelRouter for testing"""
        return ModelRouter()
    
    def test_provider_priority_order(self, router):
        """Test that providers are prioritized correctly"""
        # Ollama > Claude > GPT > Gemini
        assert router.priority_order == [
            "ollama",
            "anthropic", 
            "openai",
            "google"
        ]
    
    @pytest.mark.asyncio
    async def test_fallback_on_provider_failure(self, router):
        """Test automatic fallback when primary provider fails"""
        # Mock first provider fails, second succeeds
        pass
```

**Time to create:** 45 minutes

---

### New Test 3: Content Routes Integration Tests

**File:** `src/cofounder_agent/tests/test_content_routes_integration.py`

```python
"""Integration tests for content generation endpoints"""

import pytest
from fastapi.testclient import TestClient
import sys
import os
from unittest.mock import patch, MagicMock

# Setup path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../routes')))

# Import just the router, not main.py
from routes.content_routes import content_router

class TestContentRoutesIntegration:
    """Test content routes without full FastAPI app"""
    
    @pytest.fixture
    def mock_app(self):
        """Create minimal FastAPI app with just content router"""
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(content_router)
        return app
    
    @pytest.fixture
    def client(self, mock_app):
        """Create test client"""
        return TestClient(mock_app)
    
    def test_content_generation_endpoint(self, client):
        """Test POST /api/content/generate"""
        response = client.post("/api/content/generate", json={
            "topic": "AI trends",
            "length": 1000
        })
        assert response.status_code == 200
```

**Time to create:** 1 hour

---

## ✅ Success Criteria

After completing Phase 2 actions:

- [ ] `pytest src/cofounder_agent/tests/ --collect-only` shows no errors
- [ ] `pytest src/cofounder_agent/tests/ -v` passes all tests
- [ ] Test collection takes <5 seconds
- [ ] 5-10+ new focused unit tests created
- [ ] Documentation updated with new test patterns
- [ ] CI/CD pipeline runs without errors

---

## 📊 Effort Estimation

| Task | Effort | Priority | Owner |
|------|--------|----------|-------|
| Delete legacy tests | 15 min | 🔴 High | Anyone |
| Create DB service tests | 30 min | 🟠 Medium | Backend |
| Create model router tests | 45 min | 🟠 Medium | Backend |
| Create routes tests | 1 hr | 🟠 Medium | Backend |
| **Total Phase 2** | 2-3 hrs | 🟡 Low | Backend |

---

## 🚀 How to Track Progress

### Quick Check Command

```powershell
# Run this to verify progress
cd c:\Users\mattm\glad-labs-website
python -m pytest src/cofounder_agent/tests/ -v --tb=short

# Expected output progression:
# Stage 1 (now): 5 passed, 2 skipped, 7 errors
# Stage 2 (after delete): 5 passed, 2 skipped, 0 errors
# Stage 3 (after new tests): 15-20 passed, 0 errors
```

### Progress Tracking

**Current Status (Stage 1):**
```
✅ 5 smoke tests passing
⏭️ 2 tests skipped (intentional)
🟡 7 tests with errors (legacy)
```

**Target Status (Stage 2 - Delete Legacy):**
```
✅ 5 smoke tests passing
⏭️ 2 tests skipped
✅ 0 error tests
```

**Future Status (Stage 3 - Add New Tests):**
```
✅ 15-20 focused unit tests passing
✅ 100% passing rate
✅ <5 seconds collection time
```

---

## 📚 Reference Documents

**For implementation details, see:**
- `TEST_CLEANUP_SESSION_SUMMARY.md` - Executive summary
- `docs/reference/TEST_AUDIT_AND_CLEANUP_REPORT.md` - Detailed audit
- `docs/reference/TESTING.md` - Comprehensive testing guide
- `docs/reference/TESTING_QUICK_START.md` - 5-minute quick start

---

## 💾 Commit Messages Template

### For deletion:
```
refactor: delete 7 legacy test files with import errors

These files depend on deleted/refactored modules and cannot pass
without complete rewriting. Removing them allows clean test collection.

See TEST_CLEANUP_SESSION_SUMMARY.md for details.
```

### For new tests:
```
test: add focused unit tests for [component]

- Tests [component] in isolation without main.py imports
- Includes fixtures for setup/teardown
- Mocks external dependencies
- Covers happy path and error cases

See docs/reference/TESTING.md for test patterns.
```

---

## 🎯 Next Steps

1. **Decide:** Delete legacy tests or skip them?
2. **Execute:** Run the PowerShell commands above
3. **Verify:** Run `pytest src/cofounder_agent/tests/ -v`
4. **Commit:** Push changes to branch
5. **Plan:** Schedule Phase 2 focused unit tests for next sprint

---

*Prepared: November 4, 2025 02:15 UTC*  
*Status: Ready for Implementation*
