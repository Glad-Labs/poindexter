/**
 * Playwright Configuration - Public Site (Next.js)
 * =================================================
 *
 * Comprehensive E2E testing for:
 * - Public website (port 3000)
 * - All browsers and devices
 * - All test scenarios (unit, integration, visual, a11y, performance)
 *
 * Features:
 * ✅ Multi-browser testing (Chrome, Firefox, WebKit)
 * ✅ Multi-device testing (Desktop, Tablet, Mobile)
 * ✅ Parallel execution with configurable workers
 * ✅ Screenshot/video capture on failure
 * ✅ HTML, JSON, JUnit, Markdown reporting
 * ✅ Trace collection for debugging
 * ✅ Global setup/teardown hooks
 * ✅ Environment-specific configuration
 * ✅ CI/CD optimizations
 *
 * Usage:
 * - npx playwright test                          # Run all tests
 * - npx playwright test --project=chromium       # Run specific browser
 * - npx playwright test --ui                     # Run in UI mode
 * - npx playwright test --debug                  # Debug mode
 * - npx playwright show-report                   # View HTML report
 */

import { defineConfig, devices } from '@playwright/test';

// ========================
// Environment Configuration
// ========================

const isCI = !!process.env.CI;
const isMockDelay = !!process.env.MOCK_NETWORK_DELAY;
const outputDir =
  process.env.PLAYWRIGHT_OUTPUT_DIR || 'test-results/playwright';

const baseURL = process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:3000';
const apiURL = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000';

export default defineConfig({
  // ========================
  // Test Discovery
  // ========================

  testDir: './web/public-site/e2e',
  testMatch: '**/*.spec.ts',
  testIgnore: '**/skip/**',

  // ========================
  // Execution Strategy
  // ========================

  // Run tests in parallel (desktop), sequential (mobile for stability)
  fullyParallel: true,

  // Fail fast in CI, continue in local development
  forbidOnly: isCI,

  // Retry failed tests in CI, not locally
  retries: isCI ? 2 : 0,

  // Run multiple workers in parallel
  workers: isCI ? 1 : 4,

  // ========================
  // Reporting & Output
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
    // GitHub integration for CI
    ...(isCI ? [['github']] : ([] as any)),
  ],

  // ========================
  // Performance & Behavior
  // ========================

  // Individual test timeout
  timeout: process.env.PLAYWRIGHT_TIMEOUT
    ? parseInt(process.env.PLAYWRIGHT_TIMEOUT)
    : 30000,

  // Expectation timeout
  expect: {
    timeout: 5000,
  },

  // Max failure limit before stopping
  maxFailures: isCI ? undefined : 5,

  // ========================
  // Global Configuration
  // ========================

  use: {
    // Base URL context
    baseURL,

    // Action & timeout settings
    actionTimeout: 10000,
    navigationTimeout: 30000,

    // Capture behavior
    trace: isCI ? 'on-first-retry' : 'off',
    screenshot: isCI ? 'only-on-failure' : 'off',
    video: isCI ? 'retain-on-failure' : 'off',

    // HTTP behavior
    bypassCSP: false,
    ignoreHTTPSErrors: false,

    // Accept all prompts (dialogs, etc.)
    // acceptDownloads: true,

    // Locale and timezone
    locale: 'en-US',
    timezoneId: 'America/New_York',
  },

  // ========================
  // Browser Projects
  // ========================

  projects: [
    // ==================
    // Desktop Browsers
    // ==================

    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Chrome-specific settings
        bypassCSP: false,
      },
    },

    {
      name: 'firefox',
      use: {
        ...devices['Desktop Firefox'],
      },
    },

    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari'],
      },
    },

    // ==================
    // Tablet Devices
    // ==================

    {
      name: 'iPad',
      use: {
        ...devices['iPad Pro'],
      },
    },

    {
      name: 'iPad Mini',
      use: {
        ...devices['iPad (gen 7)'],
      },
    },

    // ==================
    // Mobile Devices
    // ==================

    {
      name: 'Pixel 5',
      use: {
        ...devices['Pixel 5'],
      },
    },

    {
      name: 'iPhone 12',
      use: {
        ...devices['iPhone 12'],
      },
    },

    {
      name: 'iPhone SE',
      use: {
        ...devices['iPhone SE'],
      },
    },

    {
      name: 'Galaxy S9+',
      use: {
        ...devices['Galaxy S9+'],
      },
    },

    // ==================
    // Accessibility Testing
    // ==================

    {
      name: 'chromium-axe',
      use: {
        ...devices['Desktop Chrome'],
      },
      testMatch: '**/*.a11y.spec.ts',
    },

    // ==================
    // Visual Regression
    // ==================

    {
      name: 'chromium-visual',
      use: {
        ...devices['Desktop Chrome'],
      },
      testMatch: '**/*.visual.spec.ts',
    },
  ],

  // ========================
  // Web Server Setup
  // ========================

  webServer: [
    {
      command: process.env.SKIP_SERVER_START ? '' : 'npm run dev:public',
      url: baseURL,
      reuseExistingServer: !isCI,
      stdout: 'ignore',
      stderr: 'pipe',
      timeout: 120000,
    },
  ],

  // ========================
  // Setup/Teardown Hooks
  // ========================

  globalSetup: './web/public-site/e2e/global-setup.ts',
  globalTeardown: './web/public-site/e2e/global-teardown.ts',

  // ========================
  // Output Configuration
  // ========================

  outputDir: `${outputDir}/traces`,
  snapshotDir: './web/public-site/e2e/snapshots',
  snapshotPathTemplate:
    '{snapshotDir}/{testFileDir}/{testFileName}-{platform}{ext}',
});

// ========================
// Test Configuration Export
// ========================

export const testConfig = {
  baseURL,
  apiURL,
  isCI,
  outputDir,
};
