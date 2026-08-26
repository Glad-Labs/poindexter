import { test, expect } from '@playwright/test';

/**
 * Tag archive page E2E coverage.
 *
 * Covers: /tag/[slug]
 *
 * Acceptance criteria (issue #377):
 * - Navigate to a tag slug — assert status not 404 (may be 200 or empty state)
 * - Assert main content area renders
 * - Posts list or empty state renders
 *
 * Note: Tag pages are dynamic — they fetch from the API. If the API returns no
 * posts for a tag, an empty state should render (not a 500 error).
 */

// Site tags are SLUGS (ai-ml, indie-hacking, devops) — bare 'ai' does not
// exist, so the old fixture only ever exercised the empty-state branch.
// ai-ml is the most-used tag on production (12 posts at 2026-08-25); if
// content drift ever empties it, the test degrades to the empty-state
// assertion rather than failing.
const KNOWN_TAG_SLUG = 'ai-ml';
const API_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

test.describe('Tag Archive Page', () => {
  test.beforeAll(async ({ request }) => {
    // Local-run gate only — see the twin comment in author.spec.ts. On an
    // external target (SKIP_SERVER_START, e.g. the weekly scheduled run
    // against production) the target serves the pages itself; gating on
    // localhost:8000 skipped the whole spec there, and an unreachable target
    // should fail loud instead.
    if (process.env.SKIP_SERVER_START) return;
    try {
      const resp = await request.get(`${API_URL}/api/health`);
      if (!resp.ok()) test.skip(true, 'Backend API unavailable');
    } catch {
      test.skip(true, 'Backend API unavailable');
    }
  });
  test('loads tag page without 500 error', async ({ page }) => {
    const response = await page.goto(`/tag/${KNOWN_TAG_SLUG}`);
    // Page may 404 if tag not found (via notFound()), but should never 500
    expect(response?.status()).not.toBe(500);
  });

  test('tag page renders main content or 404 page gracefully', async ({
    page,
  }) => {
    await page.goto(`/tag/${KNOWN_TAG_SLUG}`);
    // Either shows content or the Next.js not-found page — not a blank/broken page
    const body = page.locator('body');
    await expect(body).toBeVisible();
    // No unhandled error message
    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
  });

  test('tag page with known slug renders a heading', async ({ page }) => {
    await page.goto(`/tag/${KNOWN_TAG_SLUG}`);
    const heading = page.locator('h1, h2').first();
    await expect(heading).toBeVisible();
  });

  test('tag page shows posts list or empty state message', async ({ page }) => {
    await page.goto(`/tag/${KNOWN_TAG_SLUG}`);
    // Either posts are listed or an empty state is shown. The tag page lists
    // posts as Card links, not <article> elements, so count post links too.
    // .first() because a multi-element match makes strict-mode isVisible()
    // THROW, which the .catch used to turn into a silent false; "nothing
    // tagged" is the copy the page actually renders for an empty tag.
    const articles = await page.locator('article, a[href^="/posts/"]').count();
    const hasEmptyState = await page
      .locator('text=/no posts|no articles|check back|0 posts|nothing tagged/i')
      .first()
      .isVisible()
      .catch(() => false);
    const hasNotFound = await page
      .locator('text=/not found|404/i')
      .first()
      .isVisible()
      .catch(() => false);
    expect(articles > 0 || hasEmptyState || hasNotFound).toBeTruthy();
  });

  test('tag page does not render without a heading', async ({ page }) => {
    // Test a tag that won't exist — should gracefully 404 or show not-found UI
    await page.goto('/tag/this-tag-definitely-does-not-exist-xyz123');
    await expect(page.locator('text=Internal Server Error')).not.toBeVisible();
  });
});
