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
    // The gate protects LOCAL runs only: a dev-server page render fetches from
    // the backend per request, so a half-up local stack would fail every test
    // here for the wrong reason. Against an external already-running target
    // (SKIP_SERVER_START — the weekly scheduled run against production) the
    // pages are served by that target itself and the operator backend is not
    // publicly reachable, so gating on localhost:8000 skipped the entire spec
    // on every scheduled fire. There, an unreachable TARGET should fail loud.
    if (process.env.SKIP_SERVER_START) return;
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
    // .first(): the empty-state copy ("NO ARTICLES YET" badge + "hasn't
    // published anything yet" body) matches TWICE, and strict-mode
    // isVisible() then THROWS — which the .catch turned into false, failing
    // the test against a correctly-rendering page. The regex also names the
    // page's real copy; the old one only knew phrasings the page never used.
    const hasEmptyState = await page
      .locator("text=/no posts|no articles|check back|hasn't published/i")
      .first()
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
