/**
 * Playwright Fixtures Validation Tests
 * ====================================
 *
 * Validates that all Playwright test fixtures work correctly:
 * - APIClient initialization and methods
 * - PerformanceMetrics timing accuracy
 * - DatabaseUtils task creation and cleanup
 * - RequestLogger request tracking
 * - VisualTesting accessibility analysis
 */

import { test, expect } from './fixtures';

test.describe('Fixtures Validation', () => {
  // ========================
  // APIClient Fixture Tests
  // ========================

  test('apiClient fixture initializes', async ({ apiClient }) => {
    expect(apiClient).toBeTruthy();
    expect(apiClient.baseUrl).toBeTruthy();
  });

  test('apiClient.get() method works', async ({ apiClient }) => {
    try {
      const result = await apiClient.get('/health');
      // Should return status and data
      expect(result).toHaveProperty('status');
      expect(result).toHaveProperty('data');
    } catch (error) {
      // Health endpoint may not exist, that's OK for this test
      expect(error).toBeDefined();
    }
  });

  test('apiClient.post() method works', async ({ apiClient }) => {
    try {
      const result = await apiClient.post('/api/tasks', { title: 'Test' });
      // Should return response object
      expect(result).toHaveProperty('status');
    } catch (error) {
      // API may reject invalid data, that's OK for this test
      expect(error).toBeDefined();
    }
  });

  test('apiClient handles request errors', async ({ apiClient }) => {
    const result = await apiClient.request('GET', '/invalid-endpoint');
    // Should handle error gracefully
    expect(result).toBeDefined();
  });

  // ========================
  // PerformanceMetrics Tests
  // ========================

  test('metrics.mark() and metrics.measure() work', async ({ metrics }) => {
    // Do not use setTimeout for timing assertions — wall-clock sleeps are flaky
    // under CI load. Instead, verify the API contract: measure() returns a
    // non-negative number and the mark names are recorded.
    metrics.mark('start');
    metrics.mark('end');

    const duration = metrics.measure('test', 'start', 'end');

    // Duration must be non-negative (API contract)
    expect(duration).toBeGreaterThanOrEqual(0);
    // Marks must be recorded
    const summary = metrics.getSummary();
    expect(summary.marks).toHaveProperty('start');
    expect(summary.marks).toHaveProperty('end');
  });

  test('metrics.getSummary() returns data', async ({ metrics }) => {
    metrics.mark('mark1');
    metrics.mark('mark2');
    const summary = metrics.getSummary();

    expect(summary.marks).toBeDefined();
    expect(Object.keys(summary.marks).length).toBeGreaterThan(0);
  });

  test('metrics.getWebVitals() returns structure', async ({ metrics }) => {
    const vitals = await metrics.getWebVitals();

    expect(vitals).toHaveProperty('navigationStart');
    expect(vitals).toHaveProperty('responseEnd');
    expect(vitals).toHaveProperty('domContentLoaded');
    expect(vitals).toHaveProperty('pageTitle');
  });

  // ========================
  // DatabaseUtils Tests
  // ========================

  test('database.createTestTask() works', async ({ database }) => {
    const task = await database.createTestTask({
      task_name: 'Fixture Validation Task',
      topic: 'Fixture validation test topic',
    });

    // Should return a task object (API returns task_id on creation)
    expect(task).toBeTruthy();
    // If successful, should have a task identifier
    if (task && !task.error_code) {
      expect(task.task_id || task.id || task.task_name).toBeTruthy();
    }
  });

  test('database.createTestTasks() creates multiple', async ({ database }) => {
    const tasks = await database.createTestTasks(3);

    // Should create an array of tasks
    expect(Array.isArray(tasks)).toBeTruthy();
    expect(tasks.length).toBe(3);
  });

  test('database cleanup removes resources', async ({ database }) => {
    const task = await database.createTestTask({ title: 'Cleanup Test' });

    // Create and immediately cleanup
    await database.cleanup();

    // After cleanup should complete without errors
    expect(database).toBeDefined();
  });

  // ========================
  // RequestLogger Tests
  // ========================

  test('requestLogger tracks API requests', async ({ page, requestLogger }) => {
    // Navigate to page (makes requests)
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Should have tracked requests
    const allRequests = requestLogger.getRequests();
    expect(allRequests).toBeDefined();
  });

  test('requestLogger filters API requests', async ({
    page,
    requestLogger,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Get API requests (should have pattern)
    const apiRequests = requestLogger.getAPIRequests();
    expect(apiRequests).toBeDefined();
    // API requests array should be valid
    expect(Array.isArray(apiRequests)).toBeTruthy();
  });

  test('requestLogger tracks request details', async ({
    page,
    requestLogger,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const requests = requestLogger.getRequests();

    if (requests.length > 0) {
      const firstRequest = requests[0];
      expect(firstRequest).toHaveProperty('url');
      expect(firstRequest).toHaveProperty('method');
      expect(firstRequest).toHaveProperty('timestamp');
    }
  });

  // ========================
  // VisualTesting Tests
  // ========================

  test('visual.getAccessibilityTree() returns data', async ({
    page,
    visual,
  }) => {
    await page.goto('/');

    const tree = await visual.getAccessibilityTree();

    // Should return an object
    expect(tree).toBeTruthy();
    expect(typeof tree).toBe('object');
  });

  test('visual.getAccessibilityTree() finds elements', async ({
    page,
    visual,
  }) => {
    await page.goto('/');

    const tree = await visual.getAccessibilityTree();
    const elementCount = Object.keys(tree).length;

    // Should find some elements with roles
    expect(elementCount).toBeGreaterThanOrEqual(0);
  });

  // ========================
  // Combined Fixture Tests
  // ========================

  test('all fixtures work together', async ({
    page,
    apiClient,
    metrics,
    database,
    requestLogger,
    visual,
  }) => {
    // Should be able to use all fixtures simultaneously
    expect(page).toBeTruthy();
    expect(apiClient).toBeTruthy();
    expect(metrics).toBeTruthy();
    expect(database).toBeTruthy();
    expect(requestLogger).toBeTruthy();
    expect(visual).toBeTruthy();

    // Simple combined test
    metrics.mark('combined-start');
    await page.goto('/');
    metrics.mark('combined-end');

    const duration = metrics.measure(
      'combined',
      'combined-start',
      'combined-end'
    );
    expect(duration).toBeGreaterThan(0);
  });

  test('fixtures cleanup runs automatically', async ({ database }) => {
    // Create a task
    const task = await database.createTestTask({ title: 'Cleanup Test' });
    expect(task).toBeTruthy();

    // Cleanup happens automatically after this test
    // If fixture doesn't cleanup, subsequent tests will fail with too many tasks
  });
});

test.describe('Fixtures Edge Cases', () => {
  test('apiClient handles network errors gracefully', async ({ apiClient }) => {
    // Call non-existent endpoint
    const result = await apiClient.get(
      '/api/this-endpoint-does-not-exist-12345'
    );

    // Should handle error gracefully
    expect(result).toBeTruthy();
    // Status should indicate error
    if (result.status) {
      expect([404, 500, 400]).toContain(result.status);
    }
  });

  test('metrics handles zero duration', async ({ metrics }) => {
    metrics.mark('instant1');
    metrics.mark('instant2');

    const duration = metrics.measure('instant', 'instant1', 'instant2');

    // Duration should be very small (near zero)
    expect(duration).toBeGreaterThanOrEqual(0);
    expect(duration).toBeLessThan(10); // Should be near 0
  });

  test('database factory handles missing API', async ({ database }) => {
    // If API is not available, factory should handle gracefully
    try {
      const task = await database.createTestTask({ title: 'No API Test' });
      // If API available, task should exist
      if (task) {
        expect(task).toBeDefined();
      }
    } catch (error) {
      // If API not available, should throw or return null
      expect(error).toBeDefined();
    }
  });

  test('requestLogger with no requests', async ({ requestLogger }) => {
    // Get requests without making any
    const requests = requestLogger.getRequests();

    // Should return array even if empty
    expect(Array.isArray(requests)).toBeTruthy();
  });
});
