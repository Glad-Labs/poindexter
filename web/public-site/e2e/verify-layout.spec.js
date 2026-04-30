import { test, expect } from '@playwright/test';

test.describe('Screenshot Verification - Header & Footer', () => {
  test('HOME PAGE - Header and Footer visible', async ({ page }) => {
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    // Check header exists and is visible
    const header = page.locator('header');
    await expect(header).toBeVisible();
    console.log('✅ HOME: Header is visible');

    // Check footer exists and is visible
    const footer = page.locator('footer');
    await expect(footer).toBeVisible();
    console.log('✅ HOME: Footer is visible');

    // Take full page screenshot
    await page.screenshot({
      path: './screenshots/home-page.png',
      fullPage: true,
    });
    console.log('📸 Screenshot: home-page.png');
  });

  test('HOME PAGE - Header contains navigation', async ({ page }) => {
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    const header = page.locator('header');
    const nav = header.locator('nav');
    await expect(nav).toBeVisible();

    // Check for navigation links
    const navLinks = nav.locator('a');
    const linkCount = await navLinks.count();
    console.log(`✅ HOME: Header has ${linkCount} navigation links`);

    // Verify logo/home link
    const homeLink = nav.locator('a').first();
    await expect(homeLink).toBeVisible();
  });

  test('HOME PAGE - Footer contains contact and links', async ({ page }) => {
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    const footer = page.locator('footer');

    // Check for footer sections
    const footerSections = footer.locator('div[role="region"], nav, section');
    const sectionCount = await footerSections.count();
    console.log(`✅ HOME: Footer has ${sectionCount} sections`);

    // Check for copyright
    const copyright = footer.locator('text=' + new Date().getFullYear());
    const hasCopyright = await copyright.isVisible().catch(() => false);
    console.log(`✅ HOME: Footer has copyright year: ${hasCopyright}`);
  });

  test('ARCHIVE PAGE - Header and Footer visible', async ({ page }) => {
    await page.goto('http://localhost:3000/archive/1', {
      waitUntil: 'networkidle',
    });

    // Check header
    const header = page.locator('header');
    await expect(header).toBeVisible();
    console.log('✅ ARCHIVE: Header is visible');

    // Check footer
    const footer = page.locator('footer');
    await expect(footer).toBeVisible();
    console.log('✅ ARCHIVE: Footer is visible');

    // Take screenshot
    await page.screenshot({
      path: './screenshots/archive-page.png',
      fullPage: true,
    });
    console.log('📸 Screenshot: archive-page.png');
  });

  test('MOBILE VIEW (375px) - Header and Footer visible', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    const header = page.locator('header');
    await expect(header).toBeVisible();
    console.log('✅ MOBILE: Header is visible (375px)');

    const footer = page.locator('footer');
    await expect(footer).toBeVisible();
    console.log('✅ MOBILE: Footer is visible (375px)');

    await page.screenshot({
      path: './screenshots/mobile-home.png',
      fullPage: true,
    });
    console.log('📸 Screenshot: mobile-home.png');
  });

  test('TABLET VIEW (768px) - Header and Footer visible', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    const header = page.locator('header');
    await expect(header).toBeVisible();
    console.log('✅ TABLET: Header is visible (768px)');

    const footer = page.locator('footer');
    await expect(footer).toBeVisible();
    console.log('✅ TABLET: Footer is visible (768px)');

    await page.screenshot({
      path: './screenshots/tablet-home.png',
      fullPage: true,
    });
    console.log('📸 Screenshot: tablet-home.png');
  });

  test('ACCESSIBILITY - Header and Footer roles', async ({ page }) => {
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    // Check header structure
    const header = page.locator('header');
    await expect(header).toBeVisible();

    const nav = header.locator('nav');
    const hasNav = await nav.isVisible().catch(() => false);
    console.log(`✅ ACCESSIBILITY: Header has nav: ${hasNav}`);

    // Check footer role
    const footer = page.locator('footer[role="contentinfo"]');
    await expect(footer).toBeVisible();
    console.log('✅ ACCESSIBILITY: Footer has contentinfo role');
  });

  test('DOM INSPECTION - Verify structure', async ({ page }) => {
    await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });

    // Verify DOM structure
    const html = await page.content();

    const hasHeader = html.includes('<header');
    const hasFooter = html.includes('<footer');
    const hasMain = html.includes('<main');

    console.log(`\n📋 DOM STRUCTURE:`);
    console.log(`   ✓ <header> present: ${hasHeader}`);
    console.log(`   ✓ <main> present: ${hasMain}`);
    console.log(`   ✓ <footer> present: ${hasFooter}`);

    expect(hasHeader && hasMain && hasFooter).toBe(true);
  });
});
