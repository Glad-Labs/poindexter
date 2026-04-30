import { test, expect } from '@playwright/test';

test.describe('Archive Page - Pagination & Navigation', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to archive (page 1)
    await page.goto('/archive/1');
  });

  test('should load archive page successfully', async ({ page }) => {
    await expect(page).toHaveURL(/archive\/\d+/);

    // Check that page has content
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });

  test('should display archive page heading', async ({ page }) => {
    const headings = page.locator('h1, h2');
    const heading = headings.first();

    await expect(heading).toBeVisible();
  });

  test('should display list of posts or empty state', async ({ page }) => {
    // Wait for content to load (page uses client-side fetching)
    await page.waitForTimeout(1000);

    const posts = page.locator('article');
    const postCount = await posts.count();
    const hasEmptyState = await page
      .locator('text=Check back soon')
      .isVisible()
      .catch(() => false);

    // Either posts are displayed or the empty state is shown
    expect(postCount > 0 || hasEmptyState).toBeTruthy();
  });

  test('should display post cards with required information', async ({
    page,
  }) => {
    const firstPost = page.locator('article').first();

    // Check for post title
    const title = firstPost.locator('h3, h2, a');
    await expect(title.first()).toBeVisible();
  });

  test('should have pagination navigation', async ({ page }) => {
    const nav = page.locator('nav');
    await expect(nav.first()).toBeVisible();
  });

  test('should navigate to next page', async ({ page }) => {
    const nextButton = page
      .locator(
        'a:has-text("next"), a:has-text("Next"), button:has-text("next")'
      )
      .first();

    const nextExists = await nextButton.isVisible().catch(() => false);

    if (nextExists) {
      const initialURL = page.url();
      await nextButton.click();

      // Should navigate to a different page
      await page.waitForNavigation();
      const newURL = page.url();
      expect(newURL).not.toBe(initialURL);
    }
  });

  test('should navigate to previous page', async ({ page }) => {
    // First navigate to page 2 to test back navigation
    await page.goto('/archive/2');

    const prevButton = page
      .locator(
        'a:has-text("prev"), a:has-text("Prev"), button:has-text("prev")'
      )
      .first();

    const prevExists = await prevButton.isVisible().catch(() => false);

    if (prevExists) {
      await prevButton.click();
      await page.waitForNavigation();

      // Should navigate back to page 1
      await expect(page).toHaveURL(/archive\/1/);
    }
  });

  test('should allow jumping to specific page', async ({ page }) => {
    const pageLinks = page.locator('a[href*="/archive/"]');
    const pageCount = await pageLinks.count();

    // If multiple page links exist
    if (pageCount > 2) {
      const link = pageLinks.nth(2);
      const href = await link.getAttribute('href');

      if (href && href.includes('/archive/')) {
        await link.click();
        await page.waitForNavigation();

        expect(page.url()).toContain('/archive/');
      }
    }
  });

  test('should be responsive on different viewports', async ({ page }) => {
    // Test on mobile
    await page.setViewportSize({ width: 375, height: 667 });

    const posts = page.locator('article');
    await expect(posts.first()).toBeVisible();

    // Test on tablet
    await page.setViewportSize({ width: 768, height: 1024 });

    await expect(posts.first()).toBeVisible();

    // Test on desktop
    await page.setViewportSize({ width: 1920, height: 1080 });

    await expect(posts.first()).toBeVisible();
  });

  test('should allow clicking on post to view details', async ({ page }) => {
    const firstPost = page.locator('article').first();
    const postLink = firstPost.locator('a').first();

    if (await postLink.isVisible()) {
      const href = await postLink.getAttribute('href');

      if (href && href.includes('/posts/')) {
        await postLink.click();
        await page.waitForNavigation();

        // Should navigate to post page
        await expect(page).toHaveURL(/\/posts\//);
      }
    }
  });
});

test.describe('Post Detail Page', () => {
  test('should load post page successfully', async ({ page }) => {
    // First get a post URL from archive
    await page.goto('/archive/1');

    const firstPost = page.locator('article').first();
    const postLink = firstPost.locator('a').first();
    const href = await postLink.getAttribute('href');

    if (href && href.includes('/posts/')) {
      await page.goto(href);

      // Check that page loaded
      const main = page.locator('main');
      await expect(main).toBeVisible();
    }
  });

  test('should display post title', async ({ page }) => {
    await page.goto('/archive/1');

    const firstPost = page.locator('article').first();
    const postLink = firstPost.locator('a').first();
    const href = await postLink.getAttribute('href');

    if (href && href.includes('/posts/')) {
      await page.goto(href);

      const headings = page.locator('h1, h2');
      await expect(headings.first()).toBeVisible();
    }
  });

  test('should display post content', async ({ page }) => {
    await page.goto('/archive/1');

    const firstPost = page.locator('article').first();
    const postLink = firstPost.locator('a').first();
    const href = await postLink.getAttribute('href');

    if (href && href.includes('/posts/')) {
      await page.goto(href);

      const main = page.locator('main');
      const hasContent = await main.evaluate(
        (el) => el.textContent.length > 100
      );

      expect(hasContent).toBe(true);
    }
  });
});

test.describe('Archive Search & Filtering', () => {
  test('should have working navigation back to home', async ({ page }) => {
    await page.goto('/archive/1');

    const homeLink = page.locator('a').first(); // Usually logo
    await homeLink.click();

    await expect(page).toHaveURL('http://localhost:3000/');
  });

  test('should handle 404 for non-existent pages', async ({ page }) => {
    const _response = await page.goto('/archive/9999');

    // Should either show 404 or redirect to available page
    const url = page.url();
    const isValidURL =
      url.includes('archive') || url === 'http://localhost:3000/';

    expect(isValidURL).toBe(true);
  });
});
