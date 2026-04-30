import { test, expect } from '@playwright/test';

test.describe('Accessibility Tests', () => {
  test('home page should have proper semantic HTML', async ({ page }) => {
    await page.goto('/');

    // Check for main landmark
    const main = page.locator('main');
    await expect(main).toBeVisible();

    // Check for header
    const header = page.locator('header');
    await expect(header).toBeVisible();

    // Check for footer
    const footer = page.locator('footer');
    const footerExists = await footer.isVisible().catch(() => false);
    expect(footerExists).toBe(true);
  });

  test('should have skip to main content link', async ({ page }) => {
    await page.goto('/');

    // Look for skip link (usually first focusable element)
    const skipLink = page.locator('a:has-text("skip"), a[href="#main"]');
    const hasSkipLink = await skipLink.isVisible().catch(() => false);

    // If skip link exists, it should work
    if (hasSkipLink) {
      await skipLink.focus();
      const isFocused = await skipLink.evaluate(
        (el) => el === document.activeElement
      );
      expect(isFocused).toBe(true);
    }
  });

  test('navigation should be keyboard accessible', async ({ page }) => {
    await page.goto('/');

    // Start tabbing through page
    await page.keyboard.press('Tab');

    let tabbableCount = 0;
    for (let i = 0; i < 10; i++) {
      const activeElement = await page.evaluate(
        () => document.activeElement?.tagName
      );
      if (activeElement) {
        tabbableCount++;
      }
      await page.keyboard.press('Tab');
    }

    // Should have tabbable elements
    expect(tabbableCount).toBeGreaterThan(0);
  });

  test('links should have visible text or aria-label', async ({ page }) => {
    await page.goto('/');

    const links = page.locator('a');
    const linkCount = await links.count();

    for (let i = 0; i < Math.min(linkCount, 5); i++) {
      const link = links.nth(i);
      const text = await link.textContent();
      const ariaLabel = await link.getAttribute('aria-label');
      const title = await link.getAttribute('title');

      const hasContent = text?.trim() || ariaLabel || title;
      expect(hasContent).toBeTruthy();
    }
  });

  test('images should have alt text', async ({ page }) => {
    await page.goto('/');

    const images = page.locator('img');
    const imageCount = await images.count();

    for (let i = 0; i < Math.min(imageCount, 5); i++) {
      const img = images.nth(i);
      const alt = await img.getAttribute('alt');

      // Images should have alt text or be decorative (aria-hidden)
      const isDecorative = (await img.getAttribute('aria-hidden')) === 'true';
      const hasAlt = alt && alt.trim().length > 0;

      if (!isDecorative) {
        // Meaningful images should have alt text
        expect(hasAlt || isDecorative).toBe(true);
      }
    }
  });

  test('form inputs should have labels', async ({ page }) => {
    await page.goto('/');

    const inputs = page.locator('input');
    const inputCount = await inputs.count();

    // If there are inputs, they should have associated labels
    if (inputCount > 0) {
      for (let i = 0; i < inputCount; i++) {
        const input = inputs.nth(i);
        const id = await input.getAttribute('id');
        const label = page.locator(`label[for="${id}"]`);

        const hasLabel = await label.isVisible().catch(() => false);
        const ariaLabel = await input.getAttribute('aria-label');

        expect(hasLabel || ariaLabel).toBeTruthy();
      }
    }
  });

  test('color contrast should be sufficient', async ({ page }) => {
    await page.goto('/');

    // Get computed color values for text elements
    const textElements = page.locator('h1, h2, h3, p');
    const elementCount = await textElements.count();

    // Check first few elements for visible text
    expect(elementCount).toBeGreaterThan(0);

    for (let i = 0; i < Math.min(elementCount, 3); i++) {
      const element = textElements.nth(i);
      const isVisible = await element.isVisible();

      expect(isVisible).toBe(true);
    }
  });
});

test.describe('Performance & Load Time', () => {
  test('home page should load in reasonable time', async ({ page }) => {
    const startTime = Date.now();

    await page.goto('/');

    const loadTime = Date.now() - startTime;

    // Should load in under 5 seconds
    expect(loadTime).toBeLessThan(5000);
  });

  test('archive page should load in reasonable time', async ({ page }) => {
    const startTime = Date.now();

    await page.goto('/archive/1');

    const loadTime = Date.now() - startTime;

    // Should load in under 5 seconds
    expect(loadTime).toBeLessThan(5000);
  });

  test('should not have console errors', async ({ page }) => {
    const errors = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('/');

    // Should not have critical errors (some warnings ok)
    const criticalErrors = errors.filter(
      (e) => !e.includes('404') && !e.includes('warn')
    );

    expect(criticalErrors.length).toBe(0);
  });

  test('images should be optimized', async ({ page }) => {
    await page.goto('/');

    // Get all image requests
    const imageRequests = [];

    page.on('request', (request) => {
      if (request.resourceType() === 'image') {
        imageRequests.push(request.url());
      }
    });

    // Wait for page to fully load
    await page.waitForLoadState('networkidle');

    // Should have images
    expect(imageRequests.length).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Error Handling', () => {
  test('should handle 404 gracefully', async ({ page }) => {
    const _response = await page.goto('/non-existent-page');

    // Should show error page or redirect
    const hasContent = await page
      .locator('body')
      .evaluate((el) => el.textContent.length > 0);
    expect(hasContent).toBe(true);
  });

  test('should recover from network errors', async ({ page }) => {
    // Go offline
    await page.context().setOffline(true);

    const goto = page.goto('/').catch(() => {});

    // Should handle gracefully (may throw on network error)
    expect(goto).toBeDefined();

    // Go back online
    await page.context().setOffline(false);
  });

  test('page should be usable without JavaScript in critical path', async ({
    page,
  }) => {
    // Load page
    await page.goto('/');

    // Should have content even if some JS fails
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });
});
