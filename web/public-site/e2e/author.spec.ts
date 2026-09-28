import { test, expect, type Page } from '@playwright/test';

/**
 * Author page E2E coverage.
 *
 * Covers: /author/[id]
 *
 * Acceptance criteria (issue #377):
 * - Navigate to known author slugs — assert no 500 error
 * - Assert author bio/name renders
 * - Unknown author gracefully falls back to default profile
 * - The known author's page lists their posts; an unknown author's page
 *   shows the empty state instead (glad-labs-stack#3339)
 */

const KNOWN_AUTHOR_ID = 'poindexter-ai';
const API_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';

// The two states of the page's post list, matched on the markup it renders.
// A post card is a brand <Card> (a div.gl-card), not an <article>, so a post
// is found by its title link to /posts/<slug>. The empty state is a Card too,
// so neither `article` nor `.gl-card` can tell the two states apart.
const POST_LINKS = 'a[href^="/posts/"]';
const EMPTY_STATE = /hasn't published anything yet/i;

/**
 * The "Articles by <name>" section. Scoping to it keeps the header's
 * "← All articles" button and the site chrome from standing in for either
 * list state.
 */
function articlesSection(page: Page) {
  return page.locator('section').filter({
    has: page.getByRole('heading', { level: 2, name: /^Articles by/i }),
  });
}

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

  // Every published post is attributed to the Poindexter AI author row
  // (src/cofounder_agent/poindexter/services/default_author.py), so on any
  // target that has posts, an empty state on this page is a bug. It is what
  // #3339 looked like in production.
  // This test used to accept "posts OR empty state" and counted <article>
  // elements, which this page has never rendered. It could only pass through
  // the empty state, so it was green while #3339 hid every post (2026-08-31,
  // 09-07) and turned red once #3599 fixed the page and listed them.
  test('known author page lists their posts, not the empty state', async ({
    page,
  }) => {
    await page.goto(`/author/${KNOWN_AUTHOR_ID}`);
    const articles = articlesSection(page);
    await expect(articles.locator(POST_LINKS).first()).toBeVisible();
    await expect(articles.getByText(EMPTY_STATE)).toHaveCount(0);
  });

  // The empty state belongs here: an unknown slug gets the default profile
  // and no posts. The no-posts check also pins #3599's rule that an unknown
  // slug never falls back to listing somebody else's posts.
  test('unknown author page shows the empty state and lists no posts', async ({
    page,
  }) => {
    await page.goto('/author/unknown-author-xyz-999');
    const articles = articlesSection(page);
    await expect(articles.getByText(EMPTY_STATE)).toBeVisible();
    await expect(articles.locator(POST_LINKS)).toHaveCount(0);
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
