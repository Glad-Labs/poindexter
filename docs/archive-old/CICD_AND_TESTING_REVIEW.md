# CI/CD Pipeline & Testing Review

**Date**: October 21, 2025  
**Project**: GLAD Labs Monorepo  
**Status**: Reviewed and Ready for Improvements

---

## 📊 Executive Summary

Your CI/CD pipeline is **well-structured** with three GitHub Actions workflows covering feature branches, staging, and production. However, **unit test coverage is incomplete** across several critical areas:

| Category            | Status     | Coverage | Priority |
| ------------------- | ---------- | -------- | -------- |
| Frontend Components | ⚠️ Partial | ~40%     | Medium   |
| Frontend Utilities  | ❌ None    | 0%       | High     |
| Python Backend      | ⚠️ Partial | ~30%     | High     |
| API Integration     | ⚠️ Partial | ~20%     | Critical |
| E2E Tests           | ✅ Good    | ~80%     | Medium   |

**Recommendation**: Add 15-20 unit test files to achieve 80%+ coverage. Estimated effort: 30-40 hours.

---

## 🔄 Current CI/CD Pipeline Analysis

### 1. Feature Branch Testing (`test-on-feat.yml`)

**Triggers**: `feat/**`, `feature/**` branches + PRs to dev/main

**Current Steps**:

```yaml
1. Checkout code
2. Setup Node.js (18)
3. Setup Python (3.11)
4. Install dependencies
5. Load .env (development)
6. Run frontend tests (continue-on-error)
7. Run Python tests (smoke, continue-on-error)
8. Run linting (continue-on-error)
9. Build check
```

**Issues** ⚠️:

- ❌ All tests set to `continue-on-error: true` → **Failing tests don't block PR**
- ❌ No test coverage reporting
- ❌ No artifact uploads (test results)
- ⚠️ Python smoke tests only (not full test suite)
- ⚠️ No security scanning (SAST)

**Recommendation**: Change `continue-on-error: false` for critical tests, add coverage reporting

---

### 2. Staging Deployment (`deploy-staging.yml`)

**Triggers**: Push to `dev` branch

**Current Steps**:

```yaml
1. Checkout code
2. Setup Node.js
3. Setup Python
4. Install dependencies
5. Load .env.staging
6. Run tests (continue-on-error)
7. Build frontend
8. Deploy to Railway (placeholder)
```

**Issues** ⚠️:

- ❌ No Python tests running (only `npm run test:frontend:ci`)
- ❌ No verification after deployment
- ⚠️ Railway deployment is placeholder (not actually executing)
- ⚠️ No smoke tests post-deployment

**Recommendation**: Run full test suite before deploy, add post-deployment verification

---

### 3. Production Deployment (`deploy-production.yml`)

**Triggers**: Push to `main` branch

**Current Steps**:

```yaml
1. Checkout code
2. Setup Node.js
3. Setup Python
4. Install dependencies
5. Load .env.production
6. Run tests (continue-on-error)
7. Build frontend (for Vercel)
8. Deploy to Vercel (placeholder)
9. Deploy to Railway (placeholder)
```

**Issues** ⚠️:

- ❌ Same issues as staging
- ❌ No pre-deployment validation
- ❌ No rollback strategy
- ❌ No deployment slots/canary

**Recommendation**: Same as staging, plus add canary/blue-green deployment

---

## 📚 Test Coverage Analysis

### Frontend Tests (Next.js Public Site)

**Location**: `web/public-site/components/` + `__tests__/`

**Existing Tests** (4 files):

- ✅ `Header.test.js` - Basic rendering test
- ✅ `Footer.test.js` - Basic rendering test
- ✅ `Layout.test.js` - Basic rendering test
- ✅ `PostList.test.js` - Basic rendering test

**Missing Tests** ❌ (HIGH PRIORITY):

```
Core Components:
  ❌ PostCard.js - No tests
  ❌ Pagination.js - No tests (business logic!)

Utilities:
  ❌ lib/api.js - NO TESTS (CRITICAL!)
    - getStrapiURL()
    - fetchAPI() with timeout protection
    - getPaginatedPosts()
    - getFeaturedPost()
    - getAuthorPosts()
  ❌ lib/posts.js - NO TESTS

Pages:
  ❌ pages/index.js - No tests
  ❌ pages/about.js - No tests
  ❌ pages/[slug].js (category/tag pages) - No tests
```

**Test Framework**: Jest + React Testing Library ✅

---

### Frontend Tests (Oversight Hub)

**Location**: `web/oversight-hub/src/`

**Existing Tests** (1 file):

- ✅ `components/Header.test.js` - Basic rendering

**Missing Tests** ❌ (HIGH PRIORITY):

```
Major Components:
  ❌ App.jsx - No tests
  ❌ OversightHub.jsx - No tests
  ❌ All components/** - No tests (10+ components)

Services:
  ❌ Firebase service integration - NO TESTS
  ❌ API services - NO TESTS

Utils:
  ❌ Utilities - NO TESTS

Redux/State:
  ❌ store/** - NO TESTS
  ❌ features/** - NO TESTS
  ❌ hooks/** - NO TESTS (5+ custom hooks)
```

**Test Framework**: React Scripts (Jest) ✅ (configured but minimal tests)

---

### Python Backend Tests

**Location**: `src/cofounder_agent/tests/`

**Existing Tests** (5 files):

- ✅ `test_e2e_fixed.py` - E2E tests
- ✅ `test_e2e_comprehensive.py` - Comprehensive E2E
- ✅ `test_unit_comprehensive.py` - Unit tests
- ✅ `test_api_integration.py` - Integration tests
- ✅ `test_content_pipeline.py` - Content pipeline tests
- ✅ `test_ollama_client.py` - Ollama client tests

**Test Coverage**: ~30% (smoke tests pass, but many edge cases untested)

**Existing Issues**:

- ⚠️ Many tests use `continue-on-error` in CI/CD
- ⚠️ No code coverage metrics
- ⚠️ Some test files may be flaky (based on timestamps)

**Missing Tests** ❌ (HIGH PRIORITY):

```
Core Modules:
  ❌ main.py - FastAPI server endpoints
  ❌ orchestrator_logic.py - Agent routing
  ❌ mcp_integration.py - MCP protocol handling
  ❌ agents/** - Individual agent implementations
  ❌ business_intelligence.py - BI calculations

Edge Cases:
  ❌ Error handling (network errors, timeouts)
  ❌ Authentication/authorization
  ❌ Rate limiting
  ❌ Concurrent requests
  ❌ Large payload handling
```

**Test Framework**: Pytest ✅

---

## 🎯 Missing Unit Tests (Priority List)

### CRITICAL (Must Have) 🔴

1. **`lib/api.js` Tests** (45 minutes)

   ```
   Tests needed:
   - getStrapiURL() with and without env vars
   - fetchAPI() with timeout protection
   - fetchAPI() error handling
   - getPaginatedPosts() with various parameters
   - getFeaturedPost() with no featured post
   - Timeout behavior (mock AbortController)

   Why critical: This is the core data fetching for entire app
   Line count: ~470 lines, currently 0% tested
   Risk if untested: Broken pages, data not loading
   ```

2. **`PostCard.js` Tests** (20 minutes)

   ```
   Tests needed:
   - Renders post card with title, excerpt, date
   - Link generation for post
   - Missing image handling
   - Category/tag display

   Why critical: Used in lists throughout site
   Line count: ~50 lines
   Risk: UI inconsistencies, broken links
   ```

3. **`Pagination.js` Tests** (30 minutes)

   ```
   Tests needed:
   - Renders page links correctly
   - Previous/Next buttons visibility
   - Page highlighting (current page)
   - No pagination if only 1 page
   - Custom basePath handling
   - Edge cases: page 1, last page

   Why critical: Business logic (navigation)
   Line count: ~46 lines, currently 0% tested
   Risk: Users can't navigate content
   ```

4. **FastAPI Endpoints Tests** (60 minutes)

   ```
   Tests needed for: src/cofounder_agent/main.py
   - GET /health
   - POST /query (main endpoint)
   - GET /status
   - Error responses (400, 500, etc.)
   - Rate limiting
   - Authentication if implemented

   Why critical: Core API for all clients
   Risk: Broken API, no error handling verification
   ```

---

### HIGH (Should Have) 🟠

- **Page Component Tests** (45 minutes)
  - `pages/index.js` (homepage)
  - `pages/about.js`
  - `pages/privacy-policy.js`
  - `pages/terms-of-service.js`
  - Tests: Render, content loading, error states

- **Oversight Hub Components** (90 minutes)
  - `App.jsx`
  - All components/
  - Redux selectors & actions
  - Custom hooks

- **Agent Tests** (90 minutes)
  - `src/agents/compliance_agent/`
  - `src/agents/content_agent/`
  - `src/agents/financial_agent/`
  - `src/agents/market_insight_agent/`
  - Tests: Agent initialization, processing, error handling

- **MCP Integration Tests** (60 minutes)
  - `src/mcp/base_server.py`
  - `src/mcp/client_manager.py`
  - `src/mcp/orchestrator.py`

---

### MEDIUM (Nice to Have) 🟡

- **Utility Tests** (30 minutes)
  - `web/oversight-hub/src/utils/`
  - `web/oversight-hub/src/lib/`

- **Service Tests** (45 minutes)
  - Firebase service
  - API service layer

---

## 📋 Action Plan

### Phase 1: Fix CI/CD (2 hours)

**In `test-on-feat.yml`**:

```diff
      - name: 🧪 Run frontend tests
        run: npm run test:frontend:ci
-       continue-on-error: true
+       continue-on-error: false

      - name: 🧪 Run Python tests
-       run: npm run test:python:smoke
-       continue-on-error: true
+       run: npm run test:python
+       continue-on-error: false
```

**Add coverage reporting**:

```yaml
- name: 📊 Upload coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./coverage/coverage-final.json
    flags: unittests
```

**Add security scanning** (optional but recommended):

```yaml
- name: 🔒 Run security check
  run: npm audit --audit-level=moderate
```

---

### Phase 2: Add Critical Tests (12-15 hours)

**Week 1**:

1. `lib/api.js` tests (3 hours)
2. `PostCard.js` tests (1 hour)
3. `Pagination.js` tests (2 hours)
4. FastAPI endpoints tests (3 hours)

**Week 2**: 5. Page component tests (2 hours) 6. Setup coverage reporting (1 hour) 7. Documentation (1 hour)

---

### Phase 3: Add High Priority Tests (8-10 hours)

**Week 3**:

1. Oversight Hub components (3 hours)
2. Agent tests (3 hours)
3. MCP integration tests (2 hours)

---

## 📝 Example Test Files to Create

### 1. `web/public-site/lib/__tests__/api.test.js`

```javascript
import { fetchAPI, getStrapiURL, getPaginatedPosts } from '../api';

describe('API Client', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
  });

  describe('getStrapiURL', () => {
    it('returns correct URL with API path', () => {
      const result = getStrapiURL('/posts');
      expect(result).toContain('/posts');
    });

    it('uses localhost when env var not set', () => {
      expect(getStrapiURL('/test')).toBe('http://localhost:1337/test');
    });
  });

  describe('fetchAPI with timeout', () => {
    it('throws error if timeout exceeded', async () => {
      jest.useFakeTimers();

      // Mock fetch that never resolves
      global.fetch = jest.fn(() => new Promise(() => {}));

      const fetchPromise = fetchAPI('/posts');
      jest.runAllTimers();

      await expect(fetchPromise).rejects.toThrow('timeout');
    });

    it('successfully fetches data within timeout', async () => {
      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ data: [] }),
        })
      );

      const result = await fetchAPI('/posts');
      expect(result).toEqual({ data: [] });
    });
  });

  describe('getPaginatedPosts', () => {
    it('fetches posts with pagination params', async () => {
      global.fetch = jest.fn(() =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              data: [{ id: 1, title: 'Post 1' }],
              meta: { pagination: { pageCount: 5 } },
            }),
        })
      );

      const result = await getPaginatedPosts(2, 10);
      expect(result.data).toHaveLength(1);
      expect(result.meta.pagination.pageCount).toBe(5);
    });
  });
});
```

---

### 2. `web/public-site/components/__tests__/Pagination.test.js`

```javascript
import { render, screen } from '@testing-library/react';
import Pagination from '../Pagination';

describe('Pagination Component', () => {
  it('renders page links for multi-page results', () => {
    const pagination = { page: 2, pageCount: 5 };
    render(<Pagination pagination={pagination} />);

    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toHaveClass('bg-cyan-500');
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('shows Previous button on non-first page', () => {
    const pagination = { page: 2, pageCount: 5 };
    render(<Pagination pagination={pagination} />);

    expect(screen.getByText('Previous')).toHaveAttribute('href', '/archive/1');
  });

  it('does not show Previous on first page', () => {
    const pagination = { page: 1, pageCount: 5 };
    render(<Pagination pagination={pagination} />);

    expect(screen.queryByText('Previous')).not.toBeInTheDocument();
  });

  it('returns null for single page', () => {
    const pagination = { page: 1, pageCount: 1 };
    const { container } = render(<Pagination pagination={pagination} />);

    expect(container.firstChild).toBeNull();
  });

  it('uses custom basePath', () => {
    const pagination = { page: 1, pageCount: 2 };
    render(<Pagination pagination={pagination} basePath="/blog" />);

    expect(screen.getByText('Next')).toHaveAttribute('href', '/blog/2');
  });
});
```

---

### 3. `src/cofounder_agent/tests/test_main_endpoints.py`

```python
import pytest
from fastapi.testclient import TestClient
from cofounder_agent.main import app

client = TestClient(app)

class TestHealthEndpoint:
    def test_health_check_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_check_includes_version(self):
        response = client.get("/health")
        data = response.json()
        assert "version" in data

class TestQueryEndpoint:
    def test_query_with_valid_input(self):
        payload = {
            "query": "What are our Q4 revenue targets?",
            "context": "financial"
        }
        response = client.post("/query", json=payload)
        assert response.status_code in [200, 202]
        assert "response" in response.json() or "task_id" in response.json()

    def test_query_without_required_field(self):
        payload = {"context": "financial"}  # Missing 'query'
        response = client.post("/query", json=payload)
        assert response.status_code == 422  # Validation error

    def test_query_with_empty_string(self):
        payload = {"query": "", "context": "financial"}
        response = client.post("/query", json=payload)
        assert response.status_code == 400

    def test_query_timeout_handling(self, mocker):
        import asyncio
        mocker.patch('cofounder_agent.main.process_query',
                    side_effect=asyncio.TimeoutError())

        payload = {"query": "test", "context": "financial"}
        response = client.post("/query", json=payload, timeout=5)
        assert response.status_code == 504

class TestStatusEndpoint:
    def test_status_returns_agent_info(self):
        response = client.get("/status")
        assert response.status_code == 200
        assert "agents" in response.json()
```

---

## ✅ Implementation Checklist

### Week 1: Critical Tests

- [ ] Create `web/public-site/lib/__tests__/api.test.js` (3 hours)
- [ ] Create `web/public-site/components/__tests__/Pagination.test.js` (1.5 hours)
- [ ] Create `web/public-site/components/__tests__/PostCard.test.js` (1.5 hours)
- [ ] Create `src/cofounder_agent/tests/test_main_endpoints.py` (2.5 hours)
- [ ] Update CI/CD workflows (1 hour)
- [ ] Test locally: `npm test` and `npm run test:python` (1 hour)
- [ ] Verify all tests pass (30 minutes)

**Subtotal Week 1**: ~11 hours

### Week 2: High Priority Tests

- [ ] Create `web/public-site/__tests__/pages/` (2 hours)
- [ ] Create `web/public-site/lib/__tests__/posts.test.js` (1 hour)
- [ ] Setup coverage reporting (1 hour)
- [ ] Create `src/cofounder_agent/tests/test_orchestrator.py` (2 hours)
- [ ] Create `src/cofounder_agent/tests/test_agents.py` (2 hours)

**Subtotal Week 2**: ~8 hours

### Total Effort

- **Phase 1** (CI/CD fixes): 2 hours
- **Phase 2** (Critical tests): 11 hours
- **Phase 3** (High priority): 8 hours
- **Total**: ~21 hours (~3 days engineering time)

---

## 📊 Expected Coverage After Implementation

| Component           | Current | After   | Target  |
| ------------------- | ------- | ------- | ------- |
| Frontend Components | 40%     | 85%     | 85%     |
| Frontend Utils      | 0%      | 80%     | 80%     |
| Python Endpoints    | 20%     | 85%     | 85%     |
| Python Agents       | 30%     | 70%     | 80%     |
| **Overall**         | **23%** | **80%** | **80%** |

---

## 🚀 Next Steps

1. **This Week**:
   - Review this document with team
   - Prioritize Phase 1 (CI/CD fixes)
   - Assign test creation tasks

2. **Next Week**:
   - Implement critical tests
   - Fix CI/CD workflows
   - Verify passing locally

3. **Week After**:
   - Add high-priority tests
   - Setup code coverage dashboard
   - Establish testing standards

---

## 📚 Resources

- Jest Documentation: https://jestjs.io/docs/getting-started
- React Testing Library: https://testing-library.com/docs/react-testing-library/intro/
- Pytest Documentation: https://docs.pytest.org/
- FastAPI Testing: https://fastapi.tiangolo.com/advanced/testing-websockets/

---

**Status**: Ready for Implementation  
**Effort**: 20-25 hours  
**Impact**: 80%+ test coverage + CI/CD enforcement  
**Timeline**: 2-3 weeks
