/**
 * Playwright Global Setup
 * Initializes dev-mode auth token for all tests
 */
import { chromium, FullConfig } from '@playwright/test';

async function globalSetup(config: FullConfig) {
  const { baseURL } = config.projects[0].use;
  const browser = await chromium.launch();
  const page = await browser.newPage();

  // Navigate to the app
  await page.goto(baseURL || 'http://localhost:3001');

  // Set dev-mode auth token (matches what initializeDevToken() sets)
  await page.evaluate(() => {
    const mockUser = {
      id: 'dev_user_local',
      email: 'dev@localhost',
      username: 'playwright-test',
      login: 'playwright-test',
      name: 'Playwright Test User',
      avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
      auth_provider: 'mock',
    };

    localStorage.setItem('user', JSON.stringify(mockUser));
    localStorage.setItem(
      'auth_token',
      process.env.E2E_DEV_TOKEN ?? 'dev-token'
    ); // Backend recognizes this
  });

  // Save storage state
  await page.context().storageState({ path: 'playwright/.auth/user.json' });
  await browser.close();
}

export default globalSetup;
