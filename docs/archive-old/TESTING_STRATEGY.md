# Unit Testing & Implementation Strategy - Glad Labs

**Date:** January 9, 2026  
**Objective:** Ensure comprehensive unit tests for FastAPI backend and React UI

## Current State Analysis

### Backend (FastAPI)
✅ **Existing Test Infrastructure:**
- pytest configured in `pyproject.toml`
- Test fixtures in `src/cofounder_agent/tests/conftest.py`
- 50+ existing test files in `src/cofounder_agent/tests/`
- Test coverage configuration available

✅ **Test Categories Present:**
- Unit tests (e.g., test_auth_routes.py, test_content_routes_unit.py)
- Integration tests (e.g., test_integration_settings.py)
- API tests (e.g., test_main_endpoints.py)
- E2E tests (e.g., test_e2e_fixed.py)

❌ **Coverage Gaps:**
- Not all 26 route modules have dedicated unit tests
- Some routes may have partial coverage
- Missing tests for: auth_unified.py, bulk_task_routes.py, model_selection_routes.py
- Missing tests for error scenarios in some routes

### Frontend (React - Oversight Hub & Public Site)
✅ **Existing Test Infrastructure:**
- Jest configured
- 20+ test files across components
- Integration tests present

❌ **Coverage Gaps:**
- Not all components have unit tests
- Missing tests for hooks in some utilities
- Limited integration tests between components

## Action Plan

### Phase 1: Backend Unit Tests (High Priority)
**Goal:** Achieve 80%+ coverage of all FastAPI routes

1. **Audit current tests** - Map which routes have tests
2. **Create missing route tests:**
   - auth_unified.py
   - bulk_task_routes.py
   - model_selection_routes.py
   - command_queue_routes.py
   - websocket_routes.py (partial)
3. **Add error scenario tests** - 4xx/5xx responses
4. **Add validation tests** - Input validation for all endpoints

### Phase 2: Frontend Component Tests
**Goal:** Achieve 75%+ coverage of React components

1. **Oversight Hub (React):**
   - Test all components in src/components/
   - Test all hooks in src/hooks/
   - Test utilities in src/utils/

2. **Public Site (Next.js):**
   - Test page components
   - Test all utility functions
   - Test API integration layer

### Phase 3: E2E Integration Tests
**Goal:** Verify complete workflows work end-to-end

1. **Content creation flow** (UI → API → Database)
2. **Task approval workflow**
3. **Authentication flow**
4. **Model routing**

### Phase 4: CI/CD Integration
**Goal:** Automate test execution on commits

1. **GitHub Actions workflow** for pytest
2. **GitHub Actions workflow** for Jest/React tests
3. **Coverage reporting**
4. **Pre-commit hooks**

---

## Implementation Tasks

### Task 1: Backend Coverage Analysis
**Time: 30 min**
- [ ] Run coverage report: `npm run test:python:coverage`
- [ ] Identify routes with <70% coverage
- [ ] List missing test files

### Task 2: Create Missing Route Tests
**Time: 2-3 hours**
- [ ] auth_unified.py - 20-30 test cases
- [ ] bulk_task_routes.py - 10-15 test cases  
- [ ] model_selection_routes.py - 10-15 test cases
- [ ] command_queue_routes.py - 10-15 test cases
- [ ] websocket_routes.py - 10-15 test cases

### Task 3: Add Error Scenario Tests
**Time: 1-2 hours**
- [ ] 400 Bad Request scenarios
- [ ] 401 Unauthorized scenarios
- [ ] 403 Forbidden scenarios
- [ ] 500 Server Error scenarios
- [ ] Database connection errors
- [ ] Model provider failures

### Task 4: Frontend Component Coverage
**Time: 2-3 hours**
- [ ] Test all TaskManagement components
- [ ] Test all Settings components
- [ ] Test all Form components
- [ ] Test all utility functions
- [ ] Test all custom hooks

### Task 5: Integration Test Suite
**Time: 1-2 hours**
- [ ] Content creation workflow
- [ ] Task approval workflow
- [ ] Authentication flow
- [ ] Model selection flow

---

## Test Structure Standard

### Backend Unit Test Template
```python
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

class TestMyRoute:
    """Test MyRoute endpoints"""
    
    @pytest.mark.unit
    async def test_endpoint_success(self):
        """Test successful endpoint call"""
        response = client.get("/api/myroute")
        assert response.status_code == 200
    
    @pytest.mark.unit
    async def test_endpoint_error(self):
        """Test error handling"""
        response = client.get("/api/myroute/invalid")
        assert response.status_code == 404
```

### Frontend Component Test Template
```javascript
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MyComponent from './MyComponent';

describe('MyComponent', () => {
  it('renders correctly', () => {
    render(<MyComponent />);
    expect(screen.getByText('Expected Text')).toBeInTheDocument();
  });
  
  it('handles user interaction', async () => {
    render(<MyComponent />);
    const button = screen.getByRole('button', { name: /click/i });
    await userEvent.click(button);
    expect(screen.getByText('Updated')).toBeInTheDocument();
  });
});
```

---

## Commands Reference

```bash
# Backend Testing
npm run test:python                # Run all Python tests
npm run test:python:smoke          # Run smoke tests only
npm run test:python:coverage       # Run with coverage report

# Frontend Testing  
npm run test                       # Run all Jest tests
npm run test:coverage             # Run with coverage (from root)

# Both
npm run test:all                  # Run all tests (Python + JS)
npm run test:ci                   # CI mode with coverage

# Code Quality
npm run format:python             # Format Python code
npm run lint:python               # Lint Python code
npm run type:check               # Type check Python code
npm run lint                      # Lint JavaScript
npm run format                    # Format JavaScript
```

---

## Success Criteria

✅ **Backend Tests:**
- [ ] 80%+ code coverage
- [ ] All routes have unit tests
- [ ] All error scenarios tested
- [ ] Tests run in <60 seconds
- [ ] All tests pass consistently

✅ **Frontend Tests:**
- [ ] 75%+ code coverage  
- [ ] All components have tests
- [ ] All hooks have tests
- [ ] User interactions tested
- [ ] Tests run in <60 seconds

✅ **Integration Tests:**
- [ ] Complete workflows tested
- [ ] API response validation
- [ ] Database state verification
- [ ] Error recovery tested

---

## Next Steps

1. **Immediate (10 min):** Run coverage analysis
2. **Next (2-3 hours):** Create missing backend tests
3. **Then (1-2 hours):** Create missing frontend tests
4. **Finally (1 hour):** Set up CI/CD integration

**Total Estimated Time:** 6-8 hours
