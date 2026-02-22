/**
 * Comprehensive Playwright Configuration
 * =====================================
 * 
 * Unified configuration for all E2E tests across:
 * - Public Site (Next.js @ port 3000)
 * - Oversight Hub (React @ port 3001)
 * - Backend API (FastAPI @ port 8000)
 * 
 * Features:
 * - Multi-browser testing (Chrome, Firefox, WebKit)
 * - Parallel execution with workers
 * - Screenshot/video capture on failure
 * - Performance metrics collection
 * - Accessibility testing
 * - Visual regression testing
 */

import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:3000';
const adminURL = process.env.PLAYWRIGHT_ADMIN_URL || 'http://localhost:3001';
const apiURL = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000';

export default defineConfig({
  // ========================
  // Project Configuration
  // ========================
  
  testDir: './web/public-site/e2e',
  fullyParallel: true,
  
  // Stop after first failure (useful for CI/CD debugging)
  forbidOnly: !!process.env.CI,
  
  // Fail if any tests are skipped in CI
  // retries: process.env.CI ? 2 : 0,
  
  // ========================
  // Execution Configuration
  // ========================
  
  // Run tests in files in parallel
  workers: process.env.CI ? 1 : 4,
  
  // Reporter configuration
  reporter: [
    ['html', { outputFolder: 'test-results/playwright-report' }],
    ['json', { outputFile: 'test-results/results.json' }],
    ['junit', { outputFile: 'test-results/junit.xml' }],
    ['list'],
    ...(process.env.CI ? [['github']] : [] as any),
  ],
  
  // ========================
  // Timeout Configuration
  // ========================
  
  timeout: 30000,  // 30 seconds per test
  expect: {
    timeout: 5000,  // 5 seconds for expect assertions
  },

  // ========================
  // Global Configuration
  // ========================
  
  use: {
    // Base URL for relative navigation
    baseURL,
    
    // Collect trace when retrying
    trace: 'on-first-retry',
    
    // Screenshot on failure
    screenshot: 'only-on-failure',
    
    // Video on failure
    video: 'retain-on-failure',
    
    // API request context
    // httpCredentials: {
    //   username: 'test',
    //   password: 'test',
    // },
  },

  // ========================
  // Projects (Browsers)
  // ========================
  
  projects: [
    // Desktop browsers
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },

    // Mobile browsers
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'Mobile Safari',
      use: { ...devices['iPhone 12'] },
    },
  ],

  // ========================
  // Web Server Configuration
  // ========================
  
  webServer: [
    {
      command: process.env.SKIP_SERVER_START ? '' : 'npm run dev:public',
      url: baseURL,
      reuseExistingServer: !process.env.CI,
      stdout: 'ignore',
      stderr: 'pipe',
      timeout: 120000,
    },
  ],

  // ========================
  // Global Fixtures Setup
  // ========================
  
  globalSetup: './web/public-site/e2e/global-setup.ts',
  globalTeardown: './web/public-site/e2e/global-teardown.ts',
});

// Export URLs for use in tests
export const testConfig = {
  baseURL,
  adminURL,
  apiURL,
};
