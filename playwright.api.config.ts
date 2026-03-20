/**
 * Playwright Configuration - API Testing
 * ========================================
 *
 * E2E testing for backend API:
 * - FastAPI @ port 8000
 * - API endpoints and workflows
 * - Error handling and edge cases
 * - Integration between frontend and API
 *
 * Features:
 * ✅ Pure API testing (no UI)
 * ✅ Request/response validation
 * ✅ Auth token management
 * ✅ Database state verification
 * ✅ API error scenario coverage
 * ✅ Performance baselines
 *
 * Usage:
 * - npx playwright test -c playwright.api.config.ts
 * - npx playwright test -c playwright.api.config.ts --reporter=list
 */

import { defineConfig } from '@playwright/test';

// ========================
// Environment Variables
// ========================

const isCI = !!process.env.CI;
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR || 'test-results/api';

const apiURL = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000';
const apiKey = process.env.PLAYWRIGHT_API_KEY || '';

export default defineConfig({
  // ========================
  // Test Configuration
  // ========================

  testDir: './playwright/api',
  testMatch: '**/*.spec.ts',
  testIgnore: '**/skip/**',

  // ========================
  // Execution Strategy
  // ========================

  // API tests can run in parallel
  fullyParallel: true,
  workers: isCI ? 1 : 4,

  // Fail fast
  forbidOnly: isCI,

  // Retry on network errors
  retries: isCI ? 3 : 1,

  // ========================
  // Reporting
  // ========================

  reporter: [
    [
      'html',
      {
        outputFolder: `${outputDir}/html-report`,
        open: isCI ? 'never' : 'on-failure',
      },
    ],
    [
      'json',
      {
        outputFile: `${outputDir}/results.json`,
      },
    ],
    [
      'junit',
      {
        outputFile: `${outputDir}/junit.xml`,
      },
    ],
    ['list'],
    ...(isCI ? [['github']] : ([] as any)),
  ],

  // ========================
  // Timeouts
  // ========================

  timeout: 20000, // API calls typically faster
  expect: {
    timeout: 5000,
  },

  // ========================
  // Global Settings
  // ========================

  use: {
    // Base URL for API
    baseURL: apiURL,

    // HTTP client settings
    actionTimeout: 10000,
    navigationTimeout: 20000,

    // Don't capture for API tests (no UI)
    screenshot: 'off',
    trace: 'on-first-retry',
    video: 'off',

    // Locale for timestamps
    locale: 'en-US',
    timezoneId: 'America/New_York',

    // Accept all responses
    ignoreHTTPSErrors: false,
  },

  // ========================
  // Projects
  // ========================

  projects: [
    // ==================
    // API Test Suites
    // ==================

    {
      name: 'api',
      use: {},
    },

    {
      name: 'api-performance',
      use: {},
      testMatch: '**/*.perf.spec.ts',
      timeout: 30000, // Allow more time for performance metrics
    },

    {
      name: 'api-security',
      use: {},
      testMatch: '**/*.security.spec.ts',
    },

    {
      name: 'api-smoke',
      use: {},
      testMatch: '**/*.smoke.spec.ts',
    },
  ],

  // ========================
  // Web Server
  // ========================

  webServer: isCI
    ? undefined
    : {
        command: process.env.SKIP_SERVER_START ? '' : 'npm run dev:cofounder',
        url: apiURL,
        reuseExistingServer: true,
        stdout: 'ignore',
        stderr: 'pipe',
        timeout: 120000,
      },

  // ========================
  // Global Setup/Teardown
  // ========================

  globalSetup: './playwright/api/global-setup.ts',
  globalTeardown: './playwright/api/global-teardown.ts',

  // ========================
  // Output Configuration
  // ========================

  outputDir: `${outputDir}/traces`,
});

// ========================
// Test Configuration Export
// ========================

export const apiTestConfig = {
  apiURL,
  apiKey,
  isCI,
  outputDir,
};
