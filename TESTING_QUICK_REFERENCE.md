# Testing Quick Reference

## 🚀 Start Here

### Run All Tests
```bash
npm run test:unified
```

### Common Commands
```bash
npm run test:python                 # Python tests only
npm run test:playwright             # Playwright E2E tests
npm run test:unified:coverage       # With coverage report
npm run test:unified:debug          # Debug mode
```

---

## 🧪 Writing Tests

### Playwright Test Template

**File: `web/public-site/e2e/my-test.spec.ts`**

```typescript
import { test, expect } from './fixtures';

test.describe('Feature Name', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should do something', async ({ page, apiClient }) => {
    // Arrange
    const data = await apiClient.get('/api/tasks');
    
    // Act
    await page.click('[data-testid="button"]');
    
    // Assert
    await expect(page.locator('[data-testid="result"]')).toBeVisible();
  });
});
```

### Pytest Test Template

**File: `tests/integration/test_my_feature.py`**

```python
import pytest

@pytest.mark.integration
async def test_api_endpoint(api_tester, test_data_factory):
    """Test a single API endpoint"""
    # Arrange
    task = await test_data_factory.create_task(title='Test')
    
    # Act
    await api_tester.get(f'/api/tasks/{task["id"]}')
    
    # Assert
    api_tester.assert_status(200)
    data = api_tester.get_json()
    assert data is not None
```

---

## 📊 Available Fixtures

### Playwright Fixtures

| Fixture | Use For |
|---------|---------|
| `page` | Browser automation |
| `apiClient` | REST API calls |
| `metrics` | Performance measurement |
| `database` | Create test tasks |
| `visual` | Accessibility testing |
| `requestLogger` | Track API calls |

### Pytest Fixtures

| Fixture | Use For |
|---------|---------|
| `http_client` | HTTP requests |
| `api_tester` | Simplified API testing |
| `test_data_factory` | Create test fixtures |
| `performance_timer` | Measure execution |
| `concurrency_tester` | Concurrent tests |

---

## 🎯 Test Markers (Pytest)

```python
@pytest.mark.integration        # Integration test
@pytest.mark.api                # REST API test
@pytest.mark.performance        # Performance test
@pytest.mark.concurrent         # Concurrency test
@pytest.mark.slow               # Slow running test
@pytest.mark.asyncio            # Async test
```

### Run Tests by Marker
```bash
# Only integration tests
pytest tests/ -m integration

# Skip slow tests
pytest tests/ -m "not slow"

# Run performance tests
pytest tests/ -m performance
```

---

## ⚡ Common Patterns

### API Testing
```python
async def test_api(api_tester):
    await api_tester.get('/api/tasks')
    api_tester.assert_status(200)
    data = api_tester.get_json()
```

### Performance Measurement
```python
def test_performance(performance_timer):
    with performance_timer() as timer:
        # Your code here
        pass
    assert timer.duration < 1000  # ms
```

### Creating Test Data
```python
async def test_with_data(test_data_factory):
    task = await test_data_factory.create_task(title='Test')
    # Automatically cleaned up after test
```

### UI/API Integration
```typescript
test('full workflow', async ({ page, apiClient, database }) => {
  const task = await database.createTestTask();
  await page.goto('/tasks');
  await expect(page.locator(`text=${task.title}`)).toBeVisible();
});
```

---

## 🔍 Debug Commands

```bash
# Interactive Playwright debugger
npm run test:playwright:debug

# View Playwright test report
npm run test:playwright:report

# Run single test
pytest tests/ -k "test_name"

# Verbose output
pytest tests/ -v --tb=short

# Stop on first failure
pytest tests/ -x
```

---

## 📈 Performance Testing

```python
@pytest.mark.performance
async def test_api_speed(api_tester, performance_timer):
    with performance_timer() as timer:
        await api_tester.get('/api/tasks')
    
    # Performance assertion
    assert timer.duration < 500  # milliseconds
```

---

## 🔄 CI/CD Integration

Tests run automatically on:
- Push to branches
- Pull requests
- Scheduled nightly runs

View results in GitHub Actions.

---

## 🆘 Troubleshooting

| Issue | Solution |
|-------|----------|
| Tests timeout | Increase timeout in config or use `.wait_for_timeout()` |
| Port already in use | Kill existing process: `lsof -i :3000` then `kill -9 <PID>` |
| Fixture not found | Check import path and fixture spelling |
| API connection fails | Ensure backend running: `npm run dev:cofounder` |
| Flaky tests | Use explicit waits, not `sleep()` |

---

## 📚 Full Documentation

See [TESTING_INFRASTRUCTURE_GUIDE.md](./TESTING_INFRASTRUCTURE_GUIDE.md) for:
- Detailed architecture
- All available methods
- Best practices
- CI/CD setup

---

## 🚀 Next Steps

1. **Run existing tests:** `npm run test:unified`
2. **Check failing tests:** Review output and logs
3. **Write a new test:** Use templates above
4. **Run specific test:** `pytest tests/ -k "your_test_name"`

---

Last Updated: 2025-02-20
