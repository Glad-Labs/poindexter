/**
 * Global Test Setup
 * =================
 *
 * Runs once before all tests across all browsers
 * Perfect for:
 * - Database initialization
 * - Test data seeding
 * - Service health checks
 * - Authentication setup
 */

import { chromium, FullConfig } from '@playwright/test';

async function globalSetup(config: FullConfig) {
  console.log('🚀 Global Test Setup Started');

  const browser = await chromium.launch();
  const page = await browser.newPage();

  try {
    // Check if backend is running
    console.log('✓ Checking backend health...');
    const response = await page
      .goto('http://localhost:8000/health', {
        waitUntil: 'domcontentloaded',
      })
      .catch(() => null);

    if (!response) {
      console.warn('⚠️  Backend health check failed, continuing anyway...');
    }

    // Check if frontend is running
    console.log('✓ Checking frontend availability...');
    await page
      .goto('http://localhost:3000', {
        waitUntil: 'domcontentloaded',
      })
      .catch(() => console.warn('⚠️  Frontend not ready yet'));

    // Optional: Setup test data
    console.log('✓ Test environment ready');
  } catch (error) {
    console.warn('⚠️  Global setup warning:', error);
  } finally {
    await browser.close();
  }

  console.log('✅ Global Test Setup Complete\n');
}

export default globalSetup;
