import { test, expect } from '@playwright/test';

/**
 * Legal pages E2E coverage.
 *
 * Covers: /legal/privacy, /legal/terms, /legal/cookie-policy, /legal/data-requests
 *
 * Minimum acceptance criteria (issue #377):
 * - Navigate to each URL — assert no 404 redirect
 * - Assert page title contains expected heading text
 * - Assert main content area renders
 */

const LEGAL_PAGES = [
  {
    path: '/legal/privacy',
    expectedTitle: /Privacy/i,
    expectedHeading: /Privacy/i,
  },
  {
    path: '/legal/terms',
    expectedTitle: /Terms/i,
    expectedHeading: /Terms/i,
  },
  {
    path: '/legal/cookie-policy',
    expectedTitle: /Cookie/i,
    expectedHeading: /Cookie/i,
  },
  {
    path: '/legal/data-requests',
    expectedTitle: /Data/i,
    expectedHeading: /Data/i,
  },
];

test.describe('Legal Pages — render and navigation', () => {
  for (const { path, expectedTitle, expectedHeading } of LEGAL_PAGES) {
    test(`${path} loads with status 200 and renders content`, async ({
      page,
    }) => {
      let responseStatus: number | undefined;

      page.on('response', (response) => {
        if (response.url().includes(path) || response.url().endsWith('/')) {
          // Track the main document response
        }
      });

      // Navigate and assert no redirect to 404
      const response = await page.goto(path);
      expect(response?.status()).not.toBe(404);
      expect(response?.status()).toBeLessThan(400);

      // URL should not redirect away
      await expect(page).not.toHaveURL(/404/);
      await expect(page).not.toHaveURL(/not-found/);
    });

    test(`${path} has expected page title`, async ({ page }) => {
      await page.goto(path);

      // Check browser tab title
      await expect(page).toHaveTitle(expectedTitle);
    });

    test(`${path} renders main heading`, async ({ page }) => {
      await page.goto(path);

      // Heading should appear in the main content
      const heading = page.locator('h1, h2').first();
      await expect(heading).toBeVisible();
      await expect(heading).toHaveText(expectedHeading);
    });

    test(`${path} renders main content area`, async ({ page }) => {
      await page.goto(path);

      // A <main> or substantial content container should be visible
      const main = page.locator('main');
      await expect(main).toBeVisible();
    });
  }
});

test.describe('Legal pages — navigation from layout', () => {
  test('legal layout renders without error', async ({ page }) => {
    await page.goto('/legal/privacy');
    // No error boundary or 500 error page
    await expect(page.locator('text=Something went wrong')).not.toBeVisible();
    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
  });

  test('cookie-policy page has cookie-related content', async ({ page }) => {
    await page.goto('/legal/cookie-policy');
    const body = page.locator('body');
    await expect(body).toContainText(/cookie/i);
  });

  test('data-requests page has a form or instructions', async ({ page }) => {
    await page.goto('/legal/data-requests');
    // Should have some form of action content (form, instructions, email link)
    const hasForm = await page.locator('form').count();
    const hasLink = await page.locator('a[href^="mailto"]').count();
    const hasInstructions = await page
      .locator('text=/request|submit|contact/i')
      .count();
    expect(hasForm + hasLink + hasInstructions).toBeGreaterThan(0);
  });
});
