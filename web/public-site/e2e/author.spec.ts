import { test, expect } from '@playwright/test';

/**
 * Author page E2E coverage.
 *
 * Covers: /author/[id]
 *
 * Acceptance criteria (issue #377):
 * - Navigate to known author slugs — assert no 500 error
 * - Assert author bio/name renders
 * - Unknown author gracefully falls back to default profile
 */

const KNOWN_AUTHOR_ID = 'poindexter-ai';
const API_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

test.describe('Author Page', () => {
  test.beforeAll(async ({ request }) => {
    try {
      const resp = await request.get(`${API_URL}/api/health`);
      if (!resp.ok()) test.skip(true, 'Backend API unavailable');
    } catch {
      test.skip(true, 'Backend API unavailable');
    }
  });
  test('loads known author page without error', async ({ page }) => {
    const response = await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    expect(response?.status()).not.toBe(500);
    expect(response?.status()).not.toBe(404);
  });

  test('known author page has page title', async ({ page }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    await expect(page).toHaveTitle(/Poindexter/i);
  });

  test('known author page renders author name', async ({ page }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    await expect(page.locator('body')).toContainText(/Poindexter/i);
  });

  test('known author page renders main content area', async ({ page }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });

  test('known author page renders heading', async ({ page }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    const heading = page.locator('h1, h2').first();
    await expect(heading).toBeVisible();
  });

  test('known author page shows posts list or empty state', async ({
    page,
  }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    const articles = await page.locator('article').count();
    const hasEmptyState = await page
      .locator('text=/no posts|no articles|check back/i')
      .isVisible()
      .catch(() => false);
    expect(articles > 0 || hasEmptyState).toBeTruthy();
  });

  test('unknown author id falls back gracefully (not a 500)', async ({
    page,
  }) => {
    const response = await page.goto('/author/unknown-author-xyz-999');
    expect(response?.status()).not.toBe(500);
  });

  test('unknown author id shows page without Internal Server Error', async ({
    page,
  }) => {
    await page.goto('/author/unknown-author-xyz-999');
    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
  });

  test('author page renders without error boundary triggered', async ({
    page,
  }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    await expect(page.locator('text=Something went wrong')).not.toBeVisible();
  });
});
