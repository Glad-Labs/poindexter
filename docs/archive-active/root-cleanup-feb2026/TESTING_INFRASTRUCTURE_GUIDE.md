# Enhanced Testing Infrastructure Guide

## Overview

This document describes the improved testing infrastructure for Glad Labs, combining:

- **Playwright** for Frontend E2E testing (UI/Browser automation)
- **Pytest** for Backend integration testing (API validation)
- **Jest** for React component testing
- **Unified test runner** for orchestrating all suites

## 📋 Quick Start

### Run All Tests (Recommended)
```bash
npm run test:unified
```

### Backend (Python) Tests
```bash
npm run test:python                    # All backend tests
npm run test:python:integration        # Integration tests only
npm run test:python:performance        # Performance tests only
npm run test:python:concurrent         # Concurrency tests only
npm run test:python:coverage           # With coverage report
```

### Frontend (Playwright) Tests
```bash
npm run test:playwright                # Run in headless mode
npm run test:playwright:headed          # Run with visible browser
npm run test:playwright:debug           # Interactive debug mode
npm run test:playwright:report          # View HTML report
```

### API Tests (Python + Playwright)
```bash
npm run test:api                       # Python API integration tests
npm run test:unified                   # All tests including API
```

---

## 🎯 Architecture Overview

### File Structure

```
glad-labs-website/
├── playwright.config.ts              # NEW: Unified Playwright config
├── scripts/
│   └── test-runner.js               # NEW: Unified test runner
├── web/public-site/e2e/
│   ├── fixtures.ts                  # NEW: Shared Playwright fixtures
│   ├── global-setup.ts              # NEW: Global setup/teardown
│   ├── global-teardown.ts           # NEW: Cleanup logic
│   ├── integration-tests.spec.ts    # NEW: UI/API integration tests
│   ├── accessibility.spec.js        # EXISTING
│   ├── home.spec.js                 # EXISTING
│   └── ...
├── tests/
│   ├── conftest_enhanced.py         # NEW: Enhanced pytest fixtures
│   ├── conftest.py                  # EXISTING: Original fixtures
│   └── integration/
│       ├── test_api_integration.py  # NEW: API integration tests
│       └── ...
```

---

## 🔧 Configuration

### Playwright Configuration (`playwright.config.ts`)

**Key Features:**
- Multi-browser testing (Chrome, Firefox, WebKit, mobile)
- Parallel execution with configurable workers
- Automatic failure screenshots/videos
- HTML report generation
- Performance metrics collection
- Global setup/teardown hooks

**Customize in `playwright.config.ts`:**
```typescript
export default defineConfig({
  timeout: 30000,              // Change timeout (ms)
  workers: 4,                  // Change parallel workers
  projects: [                  // Add/remove browsers
    { name: 'chromium' },
    { name: 'firefox' },
  ],
  webServer: [{                // Update server config
    command: 'npm run dev:public',
    url: 'http://localhost:3000',
  }],
});
```

### Pytest Configuration (`pyproject.toml`)

**Existing Markers (Keep Using):**
```python
@pytest.mark.unit           # Unit tests
@pytest.mark.integration    # Integration tests
@pytest.mark.e2e           # End-to-end tests
@pytest.mark.api           # API tests
@pytest.mark.slow          # Slow tests
@pytest.mark.asyncio       # Async tests
@pytest.mark.performance   # Performance tests
@pytest.mark.websocket     # WebSocket tests
```

**New Markers Available:**
```python
@pytest.mark.concurrent     # Concurrency tests
```

---

## 📚 Fixtures & Utilities

### Playwright Fixtures (`web/public-site/e2e/fixtures.ts`)

Extend Playwright's `test` with enhanced fixtures:

```typescript
import { test, expect } from './fixtures';

test('example', async ({ page, apiClient, metrics, database, visual }) => {
  // page: Standard Playwright page object
  
  // apiClient: REST API client for backend integration
  const tasks = await apiClient.get('/api/tasks');
  expect(tasks).toBeTruthy();
  
  // metrics: Performance measurement utilities
  metrics.mark('start');
  await page.goto('/');
  metrics.mark('end');
  const duration = metrics.measure('load', 'start', 'end');
  
  // database: Test data creation & cleanup
  const task = await database.createTestTask({ title: 'Test' });
  
  // visual: Visual testing & accessibility
  const tree = await visual.getAccessibilityTree();
});
```

**Available Methods:**

| Fixture | Method | Purpose |
|---------|--------|---------|
| `apiClient` | `get(endpoint)` | GET request |
| | `post(endpoint, body)` | POST request |
| | `put(endpoint, body)` | PUT request |
| | `delete(endpoint)` | DELETE request |
| `metrics` | `mark(name)` | Mark time point |
| | `measure(name, start, end)` | Measure duration |
| | `getWebVitals()` | Get page performance metrics |
| `database` | `createTestTask(data)` | Create test task |
| | `createTestTasks(count)` | Create multiple |
| | `cleanup()` | Clean up resources |
| `visual` | `getAccessibilityTree()` | Get a11y tree |
| `requestLogger` | `getAPIRequests()` | Get API calls made |

### Pytest Fixtures (`tests/conftest_enhanced.py`)

Enhanced fixtures for Python testing:

```python
import pytest
from tests.conftest_enhanced import (
    http_client,
    api_tester,
    test_data_factory,
    performance_timer,
    concurrency_tester,
)

@pytest.mark.integration
async def test_api(api_tester, test_data_factory, performance_timer):
    # api_tester: Helper for API testing
    await api_tester.get('/api/tasks')
    api_tester.assert_status(200)
    data = api_tester.get_json()
    
    # test_data_factory: Create/cleanup test data
    task = await test_data_factory.create_task(title='Test')
    
    # performance_timer: Measure execution time
    with performance_timer() as timer:
        await api_tester.post('/api/tasks', json={'title': 'New'})
    print(f'Duration: {timer.duration:.2f}ms')
```

**Key Classes:**

| Class | Purpose | Usage |
|-------|---------|-------|
| `APITester` | Simplify API testing | `await api_tester.get(path)` |
| `TestDataFactory` | Create test fixtures | `await factory.create_task()` |
| `PerformanceTimer` | Measure execution | `with timer as t: ...` |
| `ConcurrencyTester` | Test concurrent ops | `await tester.stress_test()` |
| `WebSocketTester` | WebSocket testing | `await ws.connect(endpoint)` |

---

## 📖 Example Tests

### Frontend Integration Test (Playwright)

```typescript
// web/public-site/e2e/integration-tests.spec.ts

test('Complete task workflow', async ({ page, apiClient, database, metrics }) => {
  // Step 1: Create task via API
  metrics.mark('test-start');
  const task = await database.createTestTask({ title: 'Test Task' });
  
  // Step 2: Navigate to tasks page
  await page.goto('/tasks');
  await page.waitForLoadState('networkidle');
  
  // Step 3: Verify task appears
  const taskElement = page.locator(`text=${task.title}`);
  await expect(taskElement).toBeVisible();
  
  // Step 4: Measure performance
  metrics.mark('test-end');
  const duration = metrics.measure('workflow', 'test-start', 'test-end');
  console.log(`Workflow completed in ${duration.toFixed(2)}ms`);
  
  // Cleanup happens automatically
});
```

### Backend Integration Test (Pytest)

```python
# tests/integration/test_api_integration.py

@pytest.mark.integration
@pytest.mark.api
async def test_full_workflow(api_tester, test_data_factory, performance_timer):
    """Test create -> list -> get workflow"""
    
    with performance_timer() as timer:
        # Create task
        task = await test_data_factory.create_task(title='Workflow Test')
        task_id = task['id']
        
        # List tasks
        await api_tester.get('/api/tasks')
        api_tester.assert_status(200)
        
        # Get single task
        await api_tester.get(f'/api/tasks/{task_id}')
        api_tester.assert_status(200)
    
    assert timer.duration < 2000  # Should be fast
```

### Performance Test

```python
@pytest.mark.performance
async def test_api_performance(api_tester, performance_timer):
    """Test that API responds quickly"""
    
    with performance_timer() as timer:
        await api_tester.get('/api/tasks')
        api_tester.assert_status(200)
    
    # Assert response time
    assert timer.duration < 500  # ms
    print(f'API response: {timer.duration:.2f}ms')
```

### Stress Test (Concurrency)

```python
@pytest.mark.concurrent
@pytest.mark.slow
async def test_stress(http_client, concurrency_tester):
    """Stress test with concurrent requests"""
    
    stats = await concurrency_tester.stress_test(
        lambda: http_client.get('/api/tasks'),
        iterations=100,
        concurrent_workers=10,
    )
    
    print(f'''
    Success Rate: {stats['success_rate']:.2f}%
    Avg Duration: {stats['avg_duration']:.2f}ms
    Failures: {stats['failure']}
    ''')
    
    assert stats['success_rate'] >= 95
```

---

## 🚀 Running Tests

### Run All Tests
```bash
npm run test:unified
```

**Output:**
```
🧪 Running Unified Test Suite

============================================================

✓ Playwright E2E Tests                    [CRITICAL]    12.45s
✓ Pytest Backend Tests                    [CRITICAL]    8.92s
✓ Jest Component Tests                    [OPTIONAL]    5.31s

📈 Overall: 3/3 passed in 26.68s

✅ All tests passed!

📁 Test results saved to: test-results/test-summary.json
```

### Run Specific Test Suite

```bash
# Frontend only
npm run test:playwright

# Backend only
npm run test:python:integration

# API tests only
npm run test:api

# With debugging
npm run test:unified -- --debug

# With coverage
npm run test:unified -- --coverage
```

### Filter Tests by Marker

```bash
# Run only performance tests
poetry run pytest tests/ -m performance

# Run only integration tests
poetry run pytest tests/ -m integration

# Run only API tests
poetry run pytest tests/ -m api

# Skip slow tests
poetry run pytest tests/ -m "not slow"

# Run only tests matching pattern
poetry run pytest tests/ -k "task"
```

---

## 📊 Test Organization

### Test Markers (Pytest)

Use markers to organize and filter tests:

```python
@pytest.mark.integration         # Integration tests
@pytest.mark.api                 # API/REST tests
@pytest.mark.performance         # Performance benchmarks
@pytest.mark.concurrent          # Concurrency tests
@pytest.mark.slow                # Slow tests (skip with -m "not slow")
@pytest.mark.asyncio             # Async tests
```

### Test File Organization

```
tests/
├── conftest.py                  # Original fixtures
├── conftest_enhanced.py         # NEW: Enhanced fixtures
├── unit/
│   └── test_*.py               # Unit tests
├── integration/
│   ├── test_api_integration.py # NEW: API integration tests
│   └── test_*.py               # Integration tests
└── e2e/
    └── test_*.py               # E2E tests

web/public-site/e2e/
├── fixtures.ts                  # NEW: Playwright fixtures
├── global-setup.ts              # NEW: Setup hook
├── global-teardown.ts           # NEW: Teardown hook
├── integration-tests.spec.ts    # NEW: Full integration tests
└── *.spec.js                    # Individual E2E specs
```

---

## 🔍 Debugging Tests

### Playwright Debug Mode
```bash
npm run test:playwright:debug
```
Opens interactive debugger with Playwright Inspector.

### Run Single Test
```bash
# Playwright
npx playwright test integration-tests -g "Complete task workflow"

# Pytest
poetry run pytest tests/integration/test_api_integration.py::test_api_integration -v
```

### View Playwright Report
```bash
npm run test:playwright:report
```
Opens HTML report with screenshots, videos, traces.

### Print Debug Info

**JavaScript:**
```typescript
test('debug example', async ({ page, apiClient }) => {
  console.log('📍 Debugging:', {
    url: page.url(),
    title: await page.title(),
  });
});
```

**Python:**
```python
async def test_debug(api_tester):
    await api_tester.get('/api/tasks')
    print(f'Status: {api_tester.last_response.status_code}')
    print(f'Data: {api_tester.get_json()}')
```

---

## 📈 Coverage Reports

### Generate Coverage

```bash
# Python
npm run test:python:coverage

# All tests
npm run test:unified:coverage
```

### View Coverage Report
```bash
# Python coverage
open htmlcov/index.html

# Playwright report
npm run test:playwright:report
```

---

## 🔄 CI/CD Integration

### GitHub Actions Example

```yaml
name: Test Suite

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: npm run install:all
      
      - name: Run tests
        run: npm run test:unified
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./htmlcov/coverage.xml
      
      - name: Upload Playwright report
        uses: actions/upload-artifact@v3
        if: always()
        with:
          name: playwright-report
          path: test-results/playwright-report/
```

---

## 🎓 Best Practices

### ✅ DO

- ✅ Use fixtures for setup/teardown
- ✅ Clean up test data automatically
- ✅ Use meaningful test names
- ✅ Group related tests with `describe` (Playwright) or classes (Pytest)
- ✅ Mark slow tests with `@pytest.mark.slow`
- ✅ Use parametrized tests for multiple scenarios
- ✅ Test full workflows, not just individual operations

### ❌ DON'T

- ❌ Create interdependent tests
- ❌ Hardcode test data
- ❌ Skip cleanup
- ❌ Ignore performance regressions
- ❌ Test implementation details
- ❌ Use `sleep()` for waiting (Use `waitFor` instead)
- ❌ Leave browser/server instances running

---

## 📚 Additional Resources

- [Playwright Documentation](https://playwright.dev/)
- [Pytest Documentation](https://docs.pytest.org/)
- [Testing Best Practices](./TESTING_BEST_PRACTICES.md)
- [Test Failures & Solutions](./TESTING_TROUBLESHOOTING.md)

---

## 🤝 Contributing

When adding new tests:

1. **Choose the right location:**
   - UI tests → `web/public-site/e2e/`
   - Integration tests → `tests/integration/`
   - Performance tests → Use `@pytest.mark.performance` marker

2. **Use appropriate fixtures:**
   - Frontend → Use Playwright fixtures from `fixtures.ts`
   - Backend → Use pytest fixtures from `conftest_enhanced.py`

3. **Follow naming conventions:**
   - `test_[feature]_[scenario].spec.ts` (Playwright)
   - `test_[feature]_[scenario].py` (Pytest)

4. **Mark appropriately:**
   - Slow tests: Add `@pytest.mark.slow`
   - API tests: Add `@pytest.mark.api`
   - Others as appropriate

5. **Ensure cleanup:**
   - Use fixtures for automatic cleanup
   - Don't manually delete test data in tests

---

## ❓ FAQ

**Q: How do I run tests without starting services?**  
A: Tests expect services running on default ports. Start with `npm run dev` first.

**Q: Can I run tests in parallel?**  
A: Yes! Playwright and Pytest both support parallelization by default.

**Q: How do I add a new Playwright fixture?**  
A: Edit `web/public-site/e2e/fixtures.ts` and extend the `test` object.

**Q: How do I add a new Pytest fixture?**  
A: Edit `tests/conftest_enhanced.py` and add a `@pytest.fixture` decorated function.

**Q: What's the difference between performance and slow tests?**  
A: `@slow` = long duration, `@performance` = testing performance metrics/benchmarks.

---

Last Updated: 2025-02-20
