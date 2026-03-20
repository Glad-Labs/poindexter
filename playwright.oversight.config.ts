/**
 * Playwright Configuration - Oversight Hub (React Admin)
 * =========================================================
 *
 * E2E testing for admin dashboard:
 * - Oversight Hub (React @ port 3001)
 * - Authenticated user workflows
 * - Admin-specific features (workflows, models, monitoring)
 *
 * Features:
 * ✅ Pre-authenticated state management
 * ✅ Sequential execution for stability
 * ✅ Screenshot capture for visual verification
 * ✅ Global auth setup
 * ✅ Admin-specific timeout handling
 * ✅ Comprehensive error reporting
 *
 * Usage:
 * - npx playwright test -c playwright.oversight.config.ts
 * - npx playwright test -c playwright.oversight.config.ts --ui
 * - npx playwright test -c playwright.oversight.config.ts --debug
 */

import { defineConfig, devices } from '@playwright/test';

// ========================
// Environment Variables
// ========================

const isCI = !!process.env.CI;
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR || 'test-results/oversight';

const adminURL = process.env.PLAYWRIGHT_ADMIN_URL || 'http://localhost:3001';
const authStorageState =
  process.env.PLAYWRIGHT_AUTH_STATE || 'playwright/.auth/admin-user.json';

export default defineConfig({
  // ========================
  // Test Configuration
  // ========================

  testDir: './web/oversight-hub/e2e',
  testMatch: '**/*.spec.ts',
  testIgnore: '**/skip/**',

  // ========================
  // Execution Strategy
  // ========================

  // Run sequentially for admin tests (auth state, data consistency)
  fullyParallel: false,
  workers: 1,

  // Fail fast in CI
  forbidOnly: isCI,

  // Retry failed tests
  retries: isCI ? 2 : 0,

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
  // Timeouts (Longer for Admin)
  // ========================

  // Admin workflows may take longer
  timeout: isCI ? 45000 : 30000,

  expect: {
    timeout: 10000, // Longer for admin complex interactions
  },

  // ========================
  // Global Settings
  // ========================

  use: {
    baseURL: adminURL,

    // Use pre-authenticated storage state
    storageState: authStorageState,

    // Timeouts for complex admin operations
    actionTimeout: 15000,
    navigationTimeout: 30000,

    // Always capture screenshots for admin verification
    screenshot: 'only-on-failure',

    // Capture traces for debugging auth issues
    trace: isCI ? 'on-first-retry' : 'off',

    // Videos for complex workflows
    video: isCI ? 'retain-on-failure' : 'off',

    // Locale/timezone
    locale: 'en-US',
    timezoneId: 'America/New_York',

    // Accept confirmation dialogs
    acceptDownloads: true,
  },

  // ========================
  // Browser Projects
  // ========================

  projects: [
    // ==================
    // Chrome (Primary)
    // ==================
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
      },
    },

    // ==================
    // Firefox (Compatibility)
    // ==================
    {
      name: 'firefox',
      use: {
        ...devices['Desktop Firefox'],
        viewport: { width: 1440, height: 900 },
      },
    },

    // ==================
    // Safari (Compatibility)
    // ==================
    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari'],
        viewport: { width: 1440, height: 900 },
      },
    },

    // ==================
    // Tablet Admin
    // ==================
    {
      name: 'iPad-admin',
      use: {
        ...devices['iPad Pro'],
      },
    },

    // ==================
    // Accessibility Testing
    // ==================
    {
      name: 'chromium-a11y',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
      },
      testMatch: '**/*.a11y.spec.ts',
    },
  ],

  // ========================
  // Web Server
  // ========================

  webServer: [
    {
      command: process.env.SKIP_SERVER_START ? '' : 'npm run dev:oversight',
      url: adminURL,
      reuseExistingServer: !isCI,
      stdout: 'ignore',
      stderr: 'pipe',
      timeout: 120000,
    },
  ],

  // ========================
  // Global Setup/Teardown
  // ========================

  globalSetup: './web/oversight-hub/e2e/global-setup.ts',
  globalTeardown: './web/oversight-hub/e2e/global-teardown.ts',

  // ========================
  // Output Configuration
  // ========================

  outputDir: `${outputDir}/traces`,
  snapshotDir: './web/oversight-hub/e2e/snapshots',
  snapshotPathTemplate:
    '{snapshotDir}/{testFileDir}/{testFileName}-{platform}{ext}',
});

// ========================
// Test Configuration Export
// ========================

export const adminTestConfig = {
  adminURL,
  authStorageState,
  isCI,
  outputDir,
};
