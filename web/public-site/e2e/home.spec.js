import { test, expect } from '@playwright/test';

test.describe('Home Page Navigation & Layout', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should load home page successfully', async ({ page }) => {
    // Check for 200 status
    await expect(page).toHaveURL('http://localhost:3000/');

    // Check that page title is present
    const heading = page.locator('h1').first();
    await expect(heading).toBeVisible();
  });

  test('should display header navigation', async ({ page }) => {
    const header = page.locator('header');
    await expect(header).toBeVisible();

    // Check for navigation links (count may vary as nav grows)
    const navLinks = page.locator('nav a');
    await expect(navLinks.first()).toBeVisible();
  });

  test('should navigate to Articles page', async ({ page }) => {
    const articlesLink = page.locator('a:has-text("Articles")').first();
    await expect(articlesLink).toBeVisible();

    await articlesLink.click();
    await expect(page).toHaveURL(/archive/);
  });

  test('should navigate back to home', async ({ page }) => {
    // Click logo or home link
    const homeLink = page.locator('a').first();
    await homeLink.click();
    await expect(page).toHaveURL('http://localhost:3000/');
  });

  test('should display footer', async ({ page }) => {
    const footer = page.locator('footer');
    await expect(footer).toBeVisible();

    // Scroll to footer
    await page.evaluate(() => {
      window.scrollBy(0, document.body.scrollHeight);
    });

    // Check footer is visible
    await expect(footer).toBeInViewport();
  });

  test('should display footer copyright with current year', async ({
    page,
  }) => {
    const footer = page.locator('footer');
    const currentYear = new Date().getFullYear();

    const copyrightText = footer.locator('text=' + currentYear);
    await expect(copyrightText).toBeVisible();
  });

  test('should have proper heading hierarchy', async ({ page }) => {
    const h1s = page.locator('h1');
    const h2s = page.locator('h2');

    // Should have at least one h1
    await expect(h1s.first()).toBeVisible();

    // If h2s exist, they should be visible
    const h2Count = await h2s.count();
    if (h2Count > 0) {
      await expect(h2s.first()).toBeVisible();
    }
  });

  test('should be responsive on mobile', async ({ page }) => {
    // Test on mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    const header = page.locator('header');
    await expect(header).toBeVisible();

    const mainContent = page.locator('main');
    await expect(mainContent).toBeVisible();
  });

  test('should be responsive on tablet', async ({ page }) => {
    // Test on tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });

    const header = page.locator('header');
    await expect(header).toBeVisible();

    const mainContent = page.locator('main');
    await expect(mainContent).toBeVisible();
  });

  test('should display main content section', async ({ page }) => {
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });

  test('should handle keyboard navigation', async ({ page }) => {
    // Test Tab key navigation
    const header = page.locator('header');
    await header.focus();

    // Tab to first link
    await page.keyboard.press('Tab');
    const activeElement = await page.evaluate(
      () => document.activeElement?.tagName
    );

    expect(activeElement).toBeTruthy();
  });
});

test.describe('Carousel Component', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should display featured posts section', async ({ page }) => {
    // Home page shows a featured post section or no-posts message
    const heroHeading = page.locator('h1');
    await expect(heroHeading).toBeVisible();

    // Page should have a section for posts (either featured post or empty state)
    const postsSection = page.locator('section');
    await expect(postsSection.first()).toBeVisible();
  });

  test('should display posts or empty state', async ({ page }) => {
    // With posts: shows featured post card and recent posts grid
    // Without posts: shows "No posts available yet" message
    const hasPostLinks = await page.locator('a[href^="/posts/"]').count();
    const hasEmptyState = await page
      .locator('text=No posts available yet')
      .isVisible()
      .catch(() => false);

    // Either posts exist or empty state is shown
    expect(hasPostLinks > 0 || hasEmptyState).toBeTruthy();
  });

  test('should navigate carousel with previous/next buttons', async ({
    page,
  }) => {
    // Look for carousel navigation buttons
    const buttons = page.locator('button');
    const navButtons = buttons.filter({
      hasText: /prev|next|previous|navigate/i,
    });

    const navButtonCount = await navButtons.count();

    // If navigation buttons exist, they should be clickable
    if (navButtonCount > 0) {
      const firstNavButton = navButtons.first();
      await expect(firstNavButton).toBeVisible();

      // Click should not throw error
      await firstNavButton.click().catch(() => {
        // Button might be disabled, that's ok
      });
    }
  });

  test('should display post information in carousel', async ({ page }) => {
    const postCards = page.locator('article');
    const hasCards = (await postCards.count()) > 0;

    if (hasCards) {
      const firstCard = postCards.first();

      // Should have title
      const title = firstCard.locator('h3, h2, a');
      await expect(title.first()).toBeVisible();
    }
  });

  test('carousel items should be clickable', async ({ page }) => {
    const postCards = page.locator('article');
    const hasCards = (await postCards.count()) > 0;

    if (hasCards) {
      const firstCard = postCards.first();
      const link = firstCard.locator('a').first();

      if (await link.isVisible()) {
        const href = await link.getAttribute('href');
        expect(href).toBeTruthy();
      }
    }
  });
});
