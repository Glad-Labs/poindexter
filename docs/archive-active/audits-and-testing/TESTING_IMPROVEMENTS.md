# Testing Improvements & Fixes

## Current Status
- **Python Tests**: 141 passed, 55 skipped, 1 failed, 2 errors
- **JavaScript Tests**: 13 test suites failed, 17 tests failed  
- **Core Issues**: Missing dependencies, unpaired fixtures, module resolution, backend not running

---

## Critical Fixes (Do First)

### 1. Install Missing Testing Dependencies ⚠️ BLOCKING
```bash
npm install --save-dev @testing-library/dom @testing-library/user-event --workspaces
```
**Fixes**: 5 failing React component test suites

---

### 2. Fix pytest Fixture Dependencies
**File**: `conftest.py` - Tests are dependent but fixtures aren't wired

**Problem**: `test_websocket_endpoint(request_id)` expects fixture but none exists
**Solution**: Make tests work independently or use proper pytest fixture pattern

---

### 3. Fix Jest Module Resolution  
**File**: `web/public-site/jest.config.js`

**Problem**: 
```
Could not locate module @/components/Carousel mapped as:
C:\Users\mattm\glad-labs-website\web\public-site\components\$1
```

**Solution**: Fix moduleNameMapper path format (Windows path issue)

---

### 4. Add Backend Health Check
**File**: Start backend before running integration tests

```bash
npm run dev:cofounder &  # Start backend
sleep 5  # Wait for startup
npm run test:all  # Run tests
```

---

## Recommended Test Strategy

### Tier 1: Unit Tests (No Dependencies)
```bash
npm run test:python:unit
npm run test -- --testPathPattern="unit"
```
✅ Should pass in isolation

### Tier 2: Integration Tests (Requires Services)
```bash
# Start backend first
npm run dev:cofounder &
sleep 5
npm run test:python:integration
npm run test -- --testPathPattern="integration|e2e"
```
Requires: FastAPI backend running

### Tier 3: E2E Tests (Full Stack)
Requires: Backend + Public Site + Oversight Hub running

---

## Issues Summary

| Issue | Type | Impact | Fix Priority |
|-------|------|--------|--------------|
| `@testing-library/dom` missing | Dependency | Blocks 5 React test suites | **HIGH** |
| WebSocket fixture dependency | pytest | 1 crashed test | **HIGH** |
| Backend not running | Service | 40+ skipped integration tests | **MEDIUM** |
| Jest moduleNameMapper Windows path | Config | 1 failing test (Carousel) | **MEDIUM** |
| CrewAIToolsFactory.reset_instances() | Code | Teardown error | **MEDIUM** |
| URL double-slash normalization | Logic | 1 failing test | **LOW** |
| TTL cache boundary timing | Logic | 1 flaky test | **LOW** |

---

## Implementation Steps

### Step 1: Install Dependencies (5 min)
```bash
npm install --save-dev @testing-library/dom @testing-library/user-event --workspaces
```

### Step 2: Fix Pytest Fixtures (15 min)
Update `tests/integration/test_langgraph_integration.py` to properly handle dependent tests

### Step 3: Fix Jest moduleNameMapper (5 min)
Update `web/public-site/jest.config.js` - use relative paths instead of Windows absolute paths

### Step 4: Add CI/CD Test Script (10 min)
Create `.github/workflows/test.yml` that:
1. Starts backend
2. Waits for health check
3. Runs tests in proper order
4. Reports results

### Step 5: Document Test Requirements (5 min)
Update README with test tier information

---

## Post-Fix Expected Results

### Python Tests
- ✅ 141 passed (baseline)
- ✅ ~20-30 additional passing (integration tests that require backend)
- ❌ 1-2 failing (known issues to be addressed)
- ⏭️ ~30 skipped (require external packages)

### JavaScript Tests  
- ✅ All unit tests pass
- ✅ Component tests pass
- ⏭️ Some E2E tests skipped (requires services)

---

## Next Actions

1. [ ] Run: `npm install --save-dev @testing-library/dom --workspaces`
2. [ ] Check Jest moduleNameMapper in jest.config.js
3. [ ] Verify backend test fixture pattern
4. [ ] Start backend: `npm run dev:cofounder`
5. [ ] Run tests: `npm run test:all`
