# Archived Legacy Unit Tests

**Date Archived:** February 6, 2026  
**Reason:** Import path issues prevented discovery and execution via root pytest

## Why These Were Moved

The `/tests/unit/` directory contained 82 test files (16MB) with valuable test code for:

- AI agents (CreativeAgent, FinancialAgent, ResearchAgent, etc.)
- Backend services (CostTracking, ORM models, etc.)
- MCP protocol implementations

### Problem

These tests were originally designed to run from within `src/cofounder_agent/tests/` with relative imports like:

```python
from ...financial_agent.cost_tracking import CostTrackingService
```

When pytest runs from the project root, these imports fail because:

- Relative imports expect a different directory structure
- PYTHONPATH configuration in `conftest.py` adds `src/cofounder_agent` to path
- But the tests expect to run as part of that directory structure

### Result

- ❌ `npm run test:python:unit` shows these as inaccessible
- ✅ Integration tests (141 passing) appear to cover the same areas
- ⏭️ These tests are valuable but not currently maintained

## How to Restore These Tests

### Option A: Move Back & Accept Non-Discovery (Current Approach)

```bash
cd archive/tests-unit-legacy-not-running
mv unit ../../tests/
# Tests won't run from pytest but are available for inspection/restoration
```

### Option B: Fix Imports & Restore to tests/unit/

1. **Update relative imports** to absolute imports

   ```python
   # Before
   from ...financial_agent.cost_tracking import CostTrackingService
   
   # After  
   from agents.financial_agent.cost_tracking import CostTrackingService
   ```

2. **Verify PYTHONPATH** in root `conftest.py` includes correct paths
3. **Run pytest** to verify discovery
4. **Estimated effort:** 2-3 hours

### Option C: Move into src/cofounder_agent/tests/ (Original Location)

If these are only for backend testing:

```bash
mv unit ../../src/cofounder_agent/tests/
# Configure src/cofounder_agent/pytest.ini to discover them
# Run: cd src/cofounder_agent && pytest
```

## Contents

```
unit/
├── __init__.py
├── agents/                    # Agent-specific tests (16 files)
│   ├── test_config.py
│   ├── test_cost_tracking.py
│   ├── test_creative_agent.py
│   ├── test_financial_agent.py
│   ├── test_image_agent.py
│   ├── test_market_insight_agent.py
│   ├── test_publishing_agent.py
│   ├── test_qa_agent.py
│   ├── test_research_agent.py
│   ├── test_strapi_client.py
│   ├── test_summarizer_agent.py
│   ├── test_firestore_client.py
│   ├── test_pubsub_client.py
│   ├── test_logging_config.py
│   ├── test_orchestrator_init.py
│   └── test_orchestrator_start_stop.py
├── backend/                   # Backend service tests
│   ├── conftest.py
│   ├── test_*.py files
│   └── CI_CD_SETUP_GUIDE.md
├── mcp/                       # MCP protocol tests
│   ├── test_*.py files
│   └── conftest.py
└── [total: 82 .py files]
```

## Test Quality Notes

✅ **Strengths:**

- Well-structured with proper pytest fixtures
- Good mocking patterns (Mock, AsyncMock, patch)
- Comprehensive test coverage for each component
- Properly commented test functions

❌ **Issues:**

- Relative imports break when run from project root
- Some tests may reference outdated code
- No recent maintenance or updates

## Decision Timeline

- **Current State:** Archived as "not running"
- **Next Step:** Decide whether to fix (Option B) or keep archived
- **Recommendation:** Fix imports if agent testing is priority, otherwise keep archived as reference

---

## When to Restore

Consider restoring these tests if:

1. **You need unit-level testing** for agents (beyond current integration tests)
2. **You want faster feedback** on agent changes (unit tests run in ~1-2 seconds each vs integration 60+ seconds)
3. **You're debugging specific agent behavior** (isolated testing vs full system)

Don't restore if:

1. **Integration tests provide sufficient coverage** (current state - 141 passing)
2. **Development velocity is more important than unit test speed**
3. **Maintenance burden isn't justified by value**

---

**Status:** 🔴 Not currently discoverable via pytest  
**Restoration Effort:** 🟡 2-3 hours (Option B) or 🟢 30 minutes (Option C)  
**Last Review:** February 6, 2026
