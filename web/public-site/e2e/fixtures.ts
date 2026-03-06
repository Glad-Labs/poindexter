/**
 * Shared Playwright Fixtures
 * ==========================
 *
 * Provides reusable fixtures for E2E tests:
 * - API client for backend integration
 * - Authentication helpers
 * - Database state management
 * - Performance metrics
 * - Visual testing utilities
 *
 * Usage:
 * ```
 * import { test, expect } from './fixtures';
 *
 * test('my test', async ({ page, apiClient, metrics }) => {
 *   await page.goto('/');
 *   await metrics.mark('page-load');
 *   const data = await apiClient.get('/api/tasks');
 *   expect(data).toBeTruthy();
 * });
 * ```
 */

import { test as base, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

/**
 * API Client for backend integration
 */
class APIClient {
  private baseUrl: string;
  private page: Page;

  constructor(page: Page, baseUrl: string = 'http://localhost:8000') {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  async request(
    method: string,
    endpoint: string,
    options?: { body?: any; headers?: Record<string, string> }
  ) {
    const url = `${this.baseUrl}${endpoint}`;
    const requestOptions = {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        // Dev auth token - accepted by TokenValidationMiddleware and get_current_user
        Authorization: 'Bearer dev-token',
        ...options?.headers,
      },
    };

    const methodLower = method.toLowerCase();
    let response;

    switch (methodLower) {
      case 'get':
        response = await this.page.request.get(url, requestOptions);
        break;
      case 'post':
        response = await this.page.request.post(url, requestOptions);
        break;
      case 'put':
        response = await this.page.request.put(url, requestOptions);
        break;
      case 'delete':
        response = await this.page.request.delete(url, requestOptions);
        break;
      default:
        throw new Error(`Unsupported HTTP method: ${method}`);
    }

    const data = await response.json().catch(() => null);
    return {
      status: response.status(),
      data,
      ok: response.ok(),
    };
  }

  async get(endpoint: string) {
    const result = await this.request('GET', endpoint);
    return result.data;
  }

  async post(endpoint: string, body: any) {
    const result = await this.request('POST', endpoint, { body });
    return result.data;
  }

  async put(endpoint: string, body: any) {
    const result = await this.request('PUT', endpoint, { body });
    return result.data;
  }

  async delete(endpoint: string) {
    await this.request('DELETE', endpoint);
  }

  async health() {
    return this.get('/health');
  }
}

/**
 * Performance metrics collection
 */
class PerformanceMetrics {
  private page: Page;
  private marks: Map<string, number> = new Map();
  private measurements: Map<string, number> = new Map();

  constructor(page: Page) {
    this.page = page;
  }

  mark(name: string) {
    this.marks.set(name, performance.now());
  }

  measure(name: string, startMark: string, endMark?: string) {
    const start = this.marks.get(startMark);
    const end = endMark ? this.marks.get(endMark) : performance.now();

    if (!start) throw new Error(`Mark ${startMark} not found`);
    if (endMark && !end) throw new Error(`Mark ${endMark} not found`);

    const duration = (end || performance.now()) - start;
    this.measurements.set(name, duration);
    return duration;
  }

  getSummary() {
    return {
      marks: Object.fromEntries(this.marks),
      measurements: Object.fromEntries(this.measurements),
    };
  }

  logSummary() {
    const summary = this.getSummary();
    console.log('\n📊 Performance Metrics:');
    Object.entries(summary.measurements).forEach(([name, duration]) => {
      console.log(`  • ${name}: ${duration.toFixed(2)}ms`);
    });
  }

  /**
   * Collect Web Vitals metrics
   */
  async getWebVitals() {
    return this.page.evaluate(() => {
      return {
        // Navigation Timing
        navigationStart: performance.timing.navigationStart,
        responseEnd: performance.timing.responseEnd,
        domContentLoaded: performance.timing.domContentLoadedEventEnd,
        loadComplete: performance.timing.loadEventEnd,

        // Custom metrics from page
        pageTitle: document.title,
        resourceCount: performance.getEntriesByType('resource').length,
      };
    });
  }
}

/**
 * Database utilities for test data management
 */
class DatabaseUtils {
  private page: Page;
  private apiClient: APIClient;

  constructor(page: Page, apiClient: APIClient) {
    this.page = page;
    this.apiClient = apiClient;
  }

  /**
   * Create test task for integration testing
   */
  async createTestTask(data?: any) {
    return this.apiClient.post('/api/tasks', {
      task_name: 'Test Task',
      topic: 'Automated test topic for E2E testing',
      primary_keyword: 'testing',
      target_audience: 'Developers',
      category: 'general',
      ...data,
    });
  }

  /**
   * Create multiple test tasks
   */
  async createTestTasks(count: number) {
    return Promise.all(
      Array.from({ length: count }).map((_, i) =>
        this.createTestTask({ title: `Test Task ${i + 1}` })
      )
    );
  }

  /**
   * Clean up test data
   */
  async cleanup() {
    // Backend-specific cleanup logic
    console.log('🧹 Cleaning up test data...');
  }
}

/**
 * Request logging utility
 */
class RequestLogger {
  private page: Page;
  private requests: any[] = [];

  constructor(page: Page) {
    this.page = page;

    page.on('request', (request) => {
      this.requests.push({
        url: request.url(),
        method: request.method(),
        timestamp: new Date().toISOString(),
      });
    });
  }

  getRequests(filter?: { url?: RegExp; method?: string }) {
    return this.requests.filter((req) => {
      if (filter?.url && !filter.url.test(req.url)) return false;
      if (filter?.method && req.method !== filter.method) return false;
      return true;
    });
  }

  getAPIRequests() {
    return this.getRequests({
      url: /\/api\//,
    });
  }

  logSummary() {
    const apiRequests = this.getAPIRequests();
    console.log('\n📡 API Requests:');
    apiRequests.forEach((req) => {
      console.log(`  ${req.method} ${req.url}`);
    });
  }
}

/**
 * Visual testing utilities
 */
class VisualTesting {
  private page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  /**
   * Compare current screenshot with baseline
   */
  async compareWithBaseline(name: string) {
    const screenshot = await this.page.screenshot();
    // Implementation would depend on your visual testing setup
    // e.g., Percy, Chromatic, or custom comparison
    return screenshot;
  }

  /**
   * Get page accessibility tree
   */
  async getAccessibilityTree() {
    return this.page.evaluate(() => {
      const tree: any = {};
      const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_ELEMENT,
        null
      );

      let node: any = walker.currentNode;
      while ((node = walker.nextNode())) {
        const element = node as HTMLElement;
        if (element.getAttribute && element.getAttribute('role')) {
          tree[element.id || element.className] = {
            role: element.getAttribute('role'),
            ariaLabel: element.getAttribute('aria-label'),
            text: element.innerText?.substring(0, 50),
          };
        }
      }
      return tree;
    });
  }
}

/**
 * Define fixtures
 */
export const test = base.extend<{
  apiClient: APIClient;
  metrics: PerformanceMetrics;
  database: DatabaseUtils;
  requestLogger: RequestLogger;
  visual: VisualTesting;
}>({
  apiClient: async ({ page }, use) => {
    const client = new APIClient(page);
    await use(client);
  },

  metrics: async ({ page }, use) => {
    const metrics = new PerformanceMetrics(page);
    await use(metrics);
    metrics.logSummary();
  },

  database: async ({ page, apiClient }, use) => {
    const database = new DatabaseUtils(page, apiClient);
    await use(database);
    await database.cleanup();
  },

  requestLogger: async ({ page }, use) => {
    const logger = new RequestLogger(page);
    await use(logger);
    logger.logSummary();
  },

  visual: async ({ page }, use) => {
    const visual = new VisualTesting(page);
    await use(visual);
  },
});

export { expect };
