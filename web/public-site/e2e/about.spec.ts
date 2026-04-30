import { test, expect } from '@playwright/test';

/**
 * About page E2E coverage.
 *
 * Covers: /about
 *
 * Acceptance criteria (issue #377):
 * - Navigate to /about — assert status 200
 * - Assert main content renders
 * - Assert key content visible (author name or lab name)
 */

test.describe('About Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/about');
  });

  test('loads successfully (status not 404)', async ({ page }) => {
    const response = await page.goto('/about');
    expect(response?.status()).not.toBe(404);
    expect(response?.status()).toBeLessThan(400);
  });

  test('has expected page title', async ({ page }) => {
    await expect(page).toHaveTitle(/About/i);
  });

  test('renders a main heading', async ({ page }) => {
    const heading = page.locator('h1, h2').first();
    await expect(heading).toBeVisible();
  });

  test('renders main content area', async ({ page }) => {
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });

  test('contains "Glad Labs" text somewhere on the page', async ({ page }) => {
    await expect(page.locator('body')).toContainText(/Glad Labs/i);
  });

  test('renders without error boundary', async ({ page }) => {
    await expect(page.locator('text=Something went wrong')).not.toBeVisible();
    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
  });

  test('does not redirect to 404', async ({ page }) => {
    await expect(page).not.toHaveURL(/404/);
    await expect(page).not.toHaveURL(/not-found/);
  });
});
