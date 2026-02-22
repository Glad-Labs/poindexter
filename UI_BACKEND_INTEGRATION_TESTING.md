# UI/Backend Integration Testing Guide

## Overview

This guide focuses on testing the **real interaction** between your frontend (Playwright) and backend (FastAPI) components with full database integration.

---

## 🏗️ Testing Patterns

### Pattern 1: UI Actions Trigger API Calls

**Scenario:** User fills form → API call → Database update → UI updates

```typescript
// web/public-site/e2e/task-creation.spec.ts
import { test, expect } from './fixtures';

test('Create task from UI', async ({ page, apiClient, requestLogger }) => {
  // Navigate to task creation page
  await page.goto('/create-task');
  
  // Fill out form
  await page.fill('[name="title"]', 'My New Task');
  await page.fill('[name="description"]', 'Task description');
  
  // Log all API calls that happen next
  const apiCallsBefore = requestLogger.getAPIRequests().length;
  
  // Submit form
  await page.click('[type="submit"]');
  
  // Wait for success message
  await expect(page.locator('text=Task created')).toBeVisible({ timeout: 5000 });
  
  // Verify POST request was made
  const apiCallsAfter = requestLogger.getAPIRequests().length;
  expect(apiCallsAfter).toBeGreaterThan(apiCallsBefore);
  
  // Verify database via API
  const tasks = await apiClient.get('/api/tasks');
  const createdTask = tasks.find((t) => t.title === 'My New Task');
  expect(createdTask).toBeTruthy();
});
```

### Pattern 2: Database State → UI Display

**Scenario:** Create data in database → Navigate UI → Verify data appears

```typescript
test('Display tasks from database', async ({ page, database, metrics }) => {
  // Create test data via API (writes to database)
  metrics.mark('data-create-start');
  const task1 = await database.createTestTask({ title: 'Task 1' });
  const task2 = await database.createTestTask({ title: 'Task 2' });
  metrics.mark('data-create-end');
  
  // Navigate to list page
  metrics.mark('nav-start');
  await page.goto('/tasks');
  await page.waitForLoadState('networkidle');
  metrics.mark('nav-end');
  
  // Verify both tasks appear in UI
  await expect(page.locator('text=Task 1')).toBeVisible();
  await expect(page.locator('text=Task 2')).toBeVisible();
  
  // Log performance
  const setupTime = metrics.measure('setup', 'data-create-start', 'data-create-end');
  const navTime = metrics.measure('navigation', 'nav-start', 'nav-end');
  
  console.log(`Setup: ${setupTime.toFixed(2)}ms, Navigation: ${navTime.toFixed(2)}ms`);
});
```

### Pattern 3: Real-time Updates via WebSocket

**Scenario:** Another client updates data → WebSocket notifies → UI updates

```typescript
test('Real-time task updates', async ({ page, database, apiClient }) => {
  // Create initial task
  const task = await database.createTestTask({ title: 'Original Title' });
  
  // Render UI with task
  await page.goto(`/tasks/${task.id}`);
  await expect(page.locator(`text=Original Title`)).toBeVisible();
  
  // Simulate another client updating the task
  await apiClient.put(`/api/tasks/${task.id}`, {
    title: 'Updated Title',
  });
  
  // Verify UI updates in real-time
  // (assumes your app subscribes to updates via WebSocket)
  await expect(page.locator(`text=Updated Title`)).toBeVisible({ timeout: 5000 });
});
```

### Pattern 4: Error Handling (API Error → UI Message)

**Scenario:** Backend returns error → App shows user-friendly message

```typescript
test('Handle API errors gracefully', async ({ page, apiClient }) => {
  // Try to access non-existent task
  const response = await apiClient.get('/api/tasks/invalid-id-12345');
  
  // Should return error
  expect(response).toBeTruthy();
  
  // Or navigate to invalid URL in UI
  await page.goto('/tasks/invalid-id');
  
  // Verify error message appears
  const errorMsg = page.locator('[role="alert"], .error-message');
  const isVisible = await errorMsg.isVisible().catch(() => false);
  
  if (isVisible) {
    expect(errorMsg).toBeVisible();
  }
});
```

### Pattern 5: Full End-to-End Workflow

**Scenario:** Complete user journey with multiple steps

```typescript
test('Complete task lifecycle: create → view → edit → delete', async ({
  page,
  database,
  apiClient,
  metrics,
}) => {
  metrics.mark('workflow-start');
  
  // Step 1: Create task
  console.log('📝 Creating task...');
  const task = await database.createTestTask({
    title: 'Lifecycle Test Task',
    description: 'Testing full workflow',
    status: 'pending',
  });
  
  // Step 2: Navigate to task detail page
  console.log('🔍 Navigating to task detail...');
  await page.goto(`/tasks/${task.id}`);
  await page.waitForLoadState('networkidle');
  
  // Verify task content displays
  await expect(page.locator('text=Lifecycle Test Task')).toBeVisible();
  await expect(page.locator('text=Testing full workflow')).toBeVisible();
  
  // Step 3: Edit task
  console.log('✏️  Editing task...');
  await page.click('[data-testid="edit-button"]');
  await page.fill('[name="description"]', 'Updated description');
  await page.click('[type="submit"]');
  
  // Wait for update to complete
  await expect(page.locator('text=Task updated')).toBeVisible();
  
  // Verify database was updated via API
  const updated = await apiClient.get(`/api/tasks/${task.id}`);
  expect(updated.description).toBe('Updated description');
  
  // Step 4: Delete task
  console.log('🗑️  Deleting task...');
  await page.click('[data-testid="delete-button"]');
  
  // Confirm deletion
  await expect(page.locator('text=Are you sure')).toBeVisible();
  await page.click('button:has-text("Delete")');
  
  // Verify task is gone
  await expect(page.locator(`text=Lifecycle Test Task`)).not.toBeVisible();
  
  // Verify API returns 404
  const deleted = await apiClient.get(`/api/tasks/${task.id}`);
  // Should be null, error, or 404
  expect(!deleted || deleted.error).toBeTruthy();
  
  metrics.mark('workflow-end');
  const total = metrics.measure('workflow', 'workflow-start', 'workflow-end');
  
  console.log(`✅ Workflow completed in ${(total / 1000).toFixed(2)}s`);
});
```

---

## 🐍 Python Integration Tests

### Pattern 1: API Endpoint Testing

```python
# tests/integration/test_task_api.py
import pytest

@pytest.mark.integration
@pytest.mark.api
async def test_create_task_endpoint(api_tester, test_data_factory):
    """Test the task creation endpoint"""
    # Create task via API
    task = await test_data_factory.create_task(
        title='Integration Test Task',
        description='Testing create endpoint',
        priority=1,
    )
    
    # Verify creation was successful
    api_tester.assert_status(200)
    assert task['id'] is not None
    assert task['title'] == 'Integration Test Task'
```

### Pattern 2: Performance & Reliability

```python
@pytest.mark.performance
async def test_list_endpoint_performance(api_tester, test_data_factory, performance_timer):
    """Test that list endpoint performs well"""
    # Create test data
    await test_data_factory.create_multiple_tasks(count=50)
    
    # Measure list performance
    with performance_timer() as timer:
        await api_tester.get('/api/tasks?limit=50')
        api_tester.assert_status(200)
    
    # Should be fast
    assert timer.duration < 500  # 500ms
    print(f'List 50 tasks: {timer.duration:.2f}ms')
```

### Pattern 3: Data Consistency

```python
@pytest.mark.integration
async def test_data_consistency(api_tester, test_data_factory):
    """Verify data consistency between create and read"""
    
    # Create task with specific data
    original = await test_data_factory.create_task(
        title='Consistency Test',
        description='Should not change',
        status='pending',
        priority=3,
    )
    
    # Fetch it back
    await api_tester.get(f'/api/tasks/{original["id"]}')
    api_tester.assert_status(200)
    fetched = api_tester.get_json()
    
    # Verify data consistency
    assert fetched['title'] == original['title']
    assert fetched['description'] == original['description']
    assert fetched['status'] == original['status']
    assert fetched['priority'] == original['priority']
```

### Pattern 4: Concurrent Operations

```python
@pytest.mark.concurrent
@pytest.mark.slow
async def test_concurrent_task_creation(http_client, concurrency_tester):
    """Test creating multiple tasks concurrently"""
    
    async def create_task():
        return await http_client.post('/api/tasks', json={
            'title': 'Concurrent Task',
            'description': 'Created concurrently',
        })
    
    # Create 20 tasks concurrently
    results = await concurrency_tester.run_concurrent(
        create_task,
        [() for _ in range(20)],
    )
    
    # All should succeed
    assert all(r.status_code == 200 for r in results)
    print(f'✓ Created 20 tasks concurrently')
```

### Pattern 5: Error Handling & Validation

```python
@pytest.mark.integration
@pytest.mark.api
async def test_validation_errors(api_tester):
    """Test that validation errors are handled properly"""
    
    # Missing required field
    response = await api_tester.client.post('/api/tasks', json={
        'description': 'Missing title',
    })
    
    # Should fail validation
    assert response.status_code >= 400
    error = response.json()
    assert 'title' in str(error).lower() or 'required' in str(error).lower()
```

---

## 🔗 Connecting UI Tests to API Tests

### Shared Test Data

```typescript
// web/public-site/e2e/shared-workflow.spec.ts

test('Create and manage task in full stack', async ({
  page,
  apiClient,
  database,
}) => {
  // 1. Create via database (API)
  const task = await database.createTestTask({
    title: 'Full Stack Test',
    description: 'Testing all layers',
    status: 'pending',
  });
  
  // 2. Verify via API
  const fromApi = await apiClient.get(`/api/tasks/${task.id}`);
  expect(fromApi).toEqual(task);
  
  // 3. Display in UI
  await page.goto(`/tasks/${task.id}`);
  
  // 4. Update in UI
  await page.fill('[name="title"]', 'Updated Title');
  await page.click('[type="submit"]');
  
  // 5. Verify update via API
  const updated = await apiClient.get(`/api/tasks/${task.id}`);
  expect(updated.title).toBe('Updated Title');
  
  // 6. Verify update displays in UI (reload)
  await page.reload();
  await expect(page.locator('text=Updated Title')).toBeVisible();
});
```

---

## 📊 Performance Testing

### Measure Page Load Time

```typescript
test('Page load performance', async ({ page, metrics }) => {
  metrics.mark('nav-start');
  
  await page.goto('/tasks');
  await page.waitForLoadState('networkidle');
  
  metrics.mark('nav-end');
  
  const duration = metrics.measure('page-load', 'nav-start', 'nav-end');
  const vitals = await metrics.getWebVitals();
  
  console.log(`
    Load Time: ${duration.toFixed(2)}ms
    DOM Content Loaded: ${vitals.domContentLoaded - vitals.navigationStart}ms
    Full Load: ${vitals.loadComplete - vitals.navigationStart}ms
    Resources: ${vitals.resourceCount}
  `);
  
  // Performance assertion
  expect(duration).toBeLessThan(3000);  // 3 seconds
});
```

### Measure API Response Time

```python
@pytest.mark.performance
async def test_api_latency(api_tester, performance_timer):
    """Measure API response latency"""
    
    with performance_timer() as timer:
        await api_tester.get('/api/tasks?limit=100')
    
    print(f'API latency: {timer.duration:.2f}ms')
    
    # Should be under 500ms for list endpoint
    assert timer.duration < 500
```

---

## 🎯 Test Isolation & Cleanup

### Automatic Cleanup

```typescript
test.describe('Task Management', () => {
  test.beforeEach(async ({ page }) => {
    // Setup before each test
    await page.goto('/');
  });

  test('Create and delete', async ({ page, database }) => {
    const task = await database.createTestTask();
    // ... test code ...
    // database.cleanup() happens automatically after test
  });
});
```

### Manual Cleanup If Needed

```python
@pytest.mark.integration
async def test_cleanup(test_data_factory):
    """Test with explicit cleanup"""
    
    tasks = await test_data_factory.create_multiple_tasks(count=5)
    
    # ... test code ...
    
    # Manual cleanup
    await test_data_factory.cleanup()
```

---

## 📋 Checklist for Integration Tests

When writing integration tests, ensure:

- ✅ Tests are independent (can run in any order)
- ✅ Test data is created automatically (fixtures)
- ✅ Cleanup happens automatically
- ✅ Use explicit waits (not `sleep()`)
- ✅ API endpoints are tested independently
- ✅ UI is tested with real data
- ✅ Full workflows are tested end-to-end
- ✅ Performance is monitored
- ✅ Error cases are covered
- ✅ Concurrent operations are tested

---

## 🚀 Example: Full Task Management Test

```typescript
// web/public-site/e2e/task-management-full.spec.ts
import { test, expect } from './fixtures';

test.describe('Task Management Full Stack', () => {
  test('Complete workflow: create, list, detail, edit, delete', async ({
    page,
    apiClient,
    database,
    metrics,
    requestLogger,
  }) => {
    metrics.mark('start');
    
    // 1. CREATE
    console.log('1️⃣  Creating task...');
    const task = await database.createTestTask({
      title: 'Complete Workflow Test',
      description: 'Testing all operations',
    });
    expect(task.id).toBeTruthy();
    
    // 2. LIST with API
    console.log('2️⃣  Listing tasks from API...');
    const tasks = await apiClient.get('/api/tasks');
    expect(Array.isArray(tasks) || Array.isArray(tasks.data)).toBeTruthy();
    
    // 3. DETAIL PAGE
    console.log('3️⃣  Viewing detail page...');
    await page.goto(`/tasks/${task.id}`);
    await expect(page.locator(`text=${task.title}`)).toBeVisible();
    
    // 4. EDIT
    console.log('4️⃣  Editing task...');
    await page.click('[data-testid="edit"]');
    await page.fill('[name="description"]', 'Updated in UI');
    await page.click('[type="submit"]');
    
    // Verify edit via API
    const updated = await apiClient.get(`/api/tasks/${task.id}`);
    expect(updated.description).toContain('Updated');
    
    // 5. VERIFY IN LIST
    console.log('5️⃣  Verifying in list...');
    await page.goto('/tasks');
    await expect(page.locator(`text=Complete Workflow Test`)).toBeVisible();
    
    // 6. DELETE
    console.log('6️⃣  Deleting task...');
    await page.click(`[data-testid="delete-${task.id}"]`);
    await page.click('button:has-text("Confirm")');
    
    // Verify deletion via API
    const deleted = await apiClient.get(`/api/tasks/${task.id}`);
    expect(!deleted || deleted.error).toBeTruthy();
    
    // 7. SUMMARY
    metrics.mark('end');
    const duration = metrics.measure('workflow', 'start', 'end');
    const apiCalls = requestLogger.getAPIRequests();
    
    console.log(`
      ✅ Workflow Complete
      Duration: ${(duration / 1000).toFixed(2)}s
      API Calls: ${apiCalls.length}
      Methods: ${[...new Set(apiCalls.map(r => r.method))].join(', ')}
    `);
  });
});
```

---

## 📚 Best Practices

### ✅ DO

- Test user workflows, not implementation
- Use fixtures for setup/teardown
- Verify data consistency across layers
- Measure performance
- Test error cases
- Use explicit waits

### ❌ DON'T

- Create test interdependencies
- Use hardcoded IDs/data
- Skip cleanup
- Test implementation details
- Use `sleep()` for waiting
- Ignore performance

---

## 🔍 Debugging Tips

```bash
# Run single test with debug output
npx playwright test integration-tests -g "workflow" --debug

# Run with headed browser (see what's happening)
npm run test:playwright:headed

# View detailed logs
npm run test:unified -- --debug

# Check API requests made
# (requestLogger.getAPIRequests() in test)
```

---

Last Updated: 2025-02-20
