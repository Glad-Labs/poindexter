import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './web/oversight-hub/e2e',
  fullyParallel: false, // Run sequentially so screenshots are stable
  workers: 1,
  timeout: 30000,
  expect: { timeout: 10000 },

  reporter: [
    ['html', { outputFolder: 'oversight-report', open: 'never' }],
    ['json', { outputFile: 'test-results/oversight-results.json' }],
    ['list'],
  ],

  use: {
    baseURL: 'http://localhost:3001',
    screenshot: 'on', // Always capture screenshots for evaluation
    video: 'off',
    trace: 'off',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
});
