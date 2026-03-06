import { defineConfig, devices } from 'playwright';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
});

// Test file content
const pagesToTest = [
  '/',
  '/oversight-hub-home',
  '/dashboard-after-task',
  '/poindexter-chat-prepared',
];

describe('Review website', () => {
  pagesToTest.forEach((page) => {
    it(`should load ${page} correctly`, async ({ page }) => {
      await page.goto(page);
      // Take screenshot
      await page.screenshot({ path: `screenshots${page}.png` });
      // Basic checks
      await expect(page).toHaveTitle();
      await expect(page).not.toContainText(/Error|Not Found/);
    });
  });
});
