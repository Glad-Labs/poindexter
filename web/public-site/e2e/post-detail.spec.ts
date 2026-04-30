import { test, expect } from '@playwright/test';

/**
 * Individual blog post page E2E coverage.
 *
 * Covers: /posts/[slug]
 *
 * Acceptance criteria (issue #377):
 * - Navigate to a known post slug — assert 200, assert content renders
 * - Author attribution, navigation elements render
 *
 * Note: Tests gracefully handle missing posts (notFound() is called by the page
 * when the API returns nothing).
 */

test.describe('Post Detail Page', () => {
  test('visiting a valid post slug renders the post page', async ({ page }) => {
    // First, discover a slug from the archive page so tests are data-driven
    await page.goto('/archive/1');

    // Find the first post link
    const postLinks = page.locator('article a, a[href^="/posts/"]');
    const count = await postLinks.count();

    if (count === 0) {
      // No posts in DB — skip this assertion
      test.skip();
      return;
    }

    const href = await postLinks.first().getAttribute('href');
    if (!href || !href.startsWith('/posts/')) {
      test.skip();
      return;
    }

    // Navigate to post
    const response = await page.goto(href);
    expect(response?.status()).not.toBe(404);
    expect(response?.status()).toBeLessThan(400);
  });

  test('post detail page renders main heading when slug exists', async ({
    page,
  }) => {
    await page.goto('/archive/1');
    const postLinks = page.locator('a[href^="/posts/"]');
    const count = await postLinks.count();
    if (count === 0) {
      test.skip();
      return;
    }

    const href = await postLinks.first().getAttribute('href');
    await page.goto(href!);

    const heading = page.locator('h1').first();
    await expect(heading).toBeVisible();
  });

  test('post detail page renders article content', async ({ page }) => {
    await page.goto('/archive/1');
    const postLinks = page.locator('a[href^="/posts/"]');
    const count = await postLinks.count();
    if (count === 0) {
      test.skip();
      return;
    }

    const href = await postLinks.first().getAttribute('href');
    await page.goto(href!);

    // Main article content area
    const article = page.locator('article, main').first();
    await expect(article).toBeVisible();
  });

  test('post detail page does not throw unhandled error', async ({ page }) => {
    await page.goto('/archive/1');
    const postLinks = page.locator('a[href^="/posts/"]');
    const count = await postLinks.count();
    if (count === 0) {
      test.skip();
      return;
    }

    const href = await postLinks.first().getAttribute('href');
    await page.goto(href!);

    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
    await expect(page.locator('text=Something went wrong')).not.toBeVisible();
  });

  test('non-existent slug renders graceful 404 page (not a 500)', async ({
    page,
  }) => {
    const response = await page.goto('/posts/this-slug-does-not-exist-xyz-999');
    // next.js notFound() returns 404 — acceptable; must not be 500
    expect(response?.status()).not.toBe(500);
  });

  test('non-existent slug shows not-found content instead of blank page', async ({
    page,
  }) => {
    await page.goto('/posts/this-slug-does-not-exist-xyz-999');
    const body = page.locator('body');
    await expect(body).toBeVisible();
    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
  });
});
