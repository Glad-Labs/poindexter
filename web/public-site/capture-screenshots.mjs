#!/usr/bin/env node

/**
 * Screenshot verification script - captures pages and verifies header/footer
 */

import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const screenshotDir = path.join(__dirname, 'screenshots');

// Ensure screenshots directory exists
if (!fs.existsSync(screenshotDir)) {
  fs.mkdirSync(screenshotDir, { recursive: true });
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
  });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 720 },
  });

  const baseURL = 'http://localhost:3000';
  const pages = [
    { name: 'Home', url: '/', file: '01-home' },
    { name: 'Archive', url: '/archive/1', file: '02-archive' },
  ];

  console.log('🎯 Starting Screenshot Verification\n');
  console.log('='.repeat(60));

  try {
    for (const pageConfig of pages) {
      console.log(`\n📄 Testing: ${pageConfig.name}`);
      console.log('-'.repeat(60));

      try {
        // Navigate to page
        const response = await page.goto(`${baseURL}${pageConfig.url}`, {
          waitUntil: 'networkidle',
          timeout: 10000,
        });

        console.log(`   HTTP Status: ${response.status()}`);

        // Check for header
        const headerVisible = await page
          .locator('header')
          .isVisible()
          .catch(() => false);
        console.log(
          `   Header: ${headerVisible ? '✅ VISIBLE' : '❌ NOT FOUND'}`
        );

        // Check for footer
        const footerVisible = await page
          .locator('footer')
          .isVisible()
          .catch(() => false);
        console.log(
          `   Footer: ${footerVisible ? '✅ VISIBLE' : '❌ NOT FOUND'}`
        );

        // Get page content info
        const title = await page.title();
        console.log(`   Title: ${title}`);

        // Take full page screenshot
        const screenshotPath = path.join(
          screenshotDir,
          `${pageConfig.file}-full.png`
        );
        await page.screenshot({ path: screenshotPath, fullPage: true });
        console.log(`   📸 Full page: ${pageConfig.file}-full.png`);

        // Take header screenshot
        const header = page.locator('header');
        if (await header.isVisible().catch(() => false)) {
          const headerScreenshot = path.join(
            screenshotDir,
            `${pageConfig.file}-header.png`
          );
          await header.screenshot({ path: headerScreenshot });
          console.log(`   📸 Header only: ${pageConfig.file}-header.png`);
        }

        // Take footer screenshot
        const footer = page.locator('footer');
        if (await footer.isVisible().catch(() => false)) {
          const footerScreenshot = path.join(
            screenshotDir,
            `${pageConfig.file}-footer.png`
          );
          await footer.screenshot({ path: footerScreenshot });
          console.log(`   📸 Footer only: ${pageConfig.file}-footer.png`);
        }

        // Verify specific elements
        if (headerVisible) {
          const navLinks = header.locator('nav a');
          const linkCount = await navLinks.count();
          console.log(`   Navigation Links: ${linkCount}`);
        }

        if (footerVisible) {
          const footerLinks = footer.locator('a');
          const footerLinkCount = await footerLinks.count();
          console.log(`   Footer Links: ${footerLinkCount}`);
        }

        console.log(`   ✅ ${pageConfig.name} page captured successfully`);
      } catch (error) {
        console.error(
          `   ❌ Error capturing ${pageConfig.name}: ${error.message}`
        );
      }
    }

    console.log('\n' + '='.repeat(60));
    console.log(`\n✅ Screenshot verification complete!`);
    console.log(`📁 Screenshots saved to: ${screenshotDir}\n`);

    // List generated files
    const files = fs.readdirSync(screenshotDir);
    console.log('Generated files:');
    files.forEach((file) => {
      const fullPath = path.join(screenshotDir, file);
      const stats = fs.statSync(fullPath);
      console.log(`   • ${file} (${(stats.size / 1024).toFixed(1)} KB)`);
    });
  } catch (error) {
    console.error('❌ Fatal error:', error);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main().catch(console.error);
