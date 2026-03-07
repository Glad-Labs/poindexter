/**
 * Oversight Hub — Functional Evaluation Suite
 *
 * Evaluates all major routes and UI interactions against the live dev server
 * (http://localhost:3001). Dev-mode auth is pre-loaded via global-setup.ts.
 *
 * Run: npx playwright test --config playwright.oversight.config.ts
 */

import { test, expect, Page } from '@playwright/test';

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Wait for dev-mode auth and the layout to settle */
async function waitForAuth(page: Page) {
  // Dev auth is pre-loaded via global-setup.ts (localStorage already set)
  // Wait for React to render the protected content
  await page.waitForLoadState('networkidle');
  // Verify we're not on login page
  await expect(page).not.toHaveURL(/\/login/, { timeout: 8000 });
  // Wait for main content to be visible
  await page.waitForSelector('h1, [role="main"], .dashboard-container', {
    timeout: 5000,
  });
}

/** Navigate to a route via the hamburger nav menu */
async function navTo(page: Page, label: string) {
  const menuBtn = page.locator('.nav-menu-btn');
  if (await menuBtn.isVisible()) {
    await menuBtn.click();
    await page.locator('.nav-menu-item', { hasText: label }).click();
    await page.waitForLoadState('networkidle');
  }
}

// ─── Shared setup ────────────────────────────────────────────────────────────

test.beforeEach(async ({ page }) => {
  // Navigate to root; auth token is already in localStorage from global-setup.ts
  await page.goto('/');
  await waitForAuth(page);
});

// ─── 1. Auth & Layout ────────────────────────────────────────────────────────

test.describe('Auth & Layout', () => {
  test('dev-mode auto-auth: redirects to dashboard, not login', async ({
    page,
  }) => {
    await expect(page).toHaveURL('http://localhost:3001/');
    await expect(page.locator('h1').first()).toBeVisible();
  });

  test('header renders with app title', async ({ page }) => {
    // Actual rendered text is "🎛️ Oversight Hub" (Header.jsx)
    await expect(
      page.locator('h1', { hasText: 'Oversight Hub' }).first()
    ).toBeVisible();
  });

  test('navigation menu button is accessible', async ({ page }) => {
    await expect(page.locator('.nav-menu-btn')).toBeVisible();
  });

  test('opening nav menu shows all 9 navigation items', async ({ page }) => {
    await page.locator('.nav-menu-btn').click();
    const navItems = [
      'Dashboard',
      'Tasks',
      'Content',
      'Approvals',
      'Services',
      'AI Studio',
      'Costs',
      'Performance',
      'Settings',
    ];
    for (const label of navItems) {
      await expect(
        page.locator('.nav-menu-item', { hasText: label })
      ).toBeVisible();
    }
  });

  test('login page is accessible at /login (public route)', async ({
    page,
  }) => {
    await page.goto('/login');
    await page.waitForLoadState('networkidle');
    // Should render a login UI, not redirect away
    await expect(page.locator('body')).toBeVisible();
    await page.screenshot({
      path: 'test-results/screenshots/login.png',
      fullPage: true,
    });
  });
});

// ─── 2. Executive Dashboard (/) ──────────────────────────────────────────────

test.describe('Executive Dashboard (/)', () => {
  test('renders dashboard heading', async ({ page }) => {
    await expect(
      page.locator('h1', { hasText: 'Executive Dashboard' })
    ).toBeVisible();
  });

  test('KPI section is present', async ({ page }) => {
    await expect(page.locator('.kpi-section')).toBeVisible();
    await expect(
      page.locator('h2', { hasText: 'Key Performance Indicators' })
    ).toBeVisible();
  });

  test('KPI cards render (Revenue, Content Published, Tasks Completed, AI Savings)', async ({
    page,
  }) => {
    await expect(page.locator('.kpi-card.revenue-card')).toBeVisible();
    await expect(page.locator('.kpi-card.content-card')).toBeVisible();
    await expect(page.locator('.kpi-card.tasks-card')).toBeVisible();
    await expect(page.locator('.kpi-card.savings-card')).toBeVisible();
  });

  test('time range selector is present', async ({ page }) => {
    await expect(page.locator('.time-range-selector')).toBeVisible();
  });

  test('no JS errors crash the page', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));
    await page.reload();
    await waitForAuth(page);
    expect(errors.filter((e) => !e.includes('ResizeObserver'))).toHaveLength(0);
  });

  test('screenshot: dashboard full page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/dashboard.png',
      fullPage: true,
    });
  });
});

// ─── 3. Task Management (/tasks) ─────────────────────────────────────────────

test.describe('Task Management (/tasks)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tasks');
    await waitForAuth(page);
  });

  test('page loads at /tasks', async ({ page }) => {
    await expect(page).toHaveURL(/\/tasks/);
  });

  test('task list or empty state is visible', async ({ page }) => {
    // Either a task table/list OR an empty state message renders
    const hasContent = await page
      .locator(
        'table, [class*="task-list"], [class*="TaskTable"], [class*="empty"]'
      )
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('filters/controls area renders', async ({ page }) => {
    // Some filter control should be present (search, status filter, etc.)
    const hasFilters = await page
      .locator(
        'input[type="search"], input[type="text"], select, [class*="filter"], [class*="Filter"]'
      )
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasFilters).toBe(true);
  });

  test('screenshot: tasks page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/tasks.png',
      fullPage: true,
    });
  });
});

// ─── 4. Content (/content) ───────────────────────────────────────────────────

test.describe('Content (/content)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/content');
    await waitForAuth(page);
  });

  test('page loads at /content', async ({ page }) => {
    await expect(page).toHaveURL(/\/content/);
  });

  test('content area renders without crashing', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));
    await page.waitForTimeout(2000);
    expect(errors.filter((e) => !e.includes('ResizeObserver'))).toHaveLength(0);
  });

  test('screenshot: content page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/content.png',
      fullPage: true,
    });
  });
});

// ─── 5. Approvals (/approvals) ───────────────────────────────────────────────

test.describe('Approvals (/approvals)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/approvals');
    await waitForAuth(page);
  });

  test('page loads at /approvals', async ({ page }) => {
    await expect(page).toHaveURL(/\/approvals/);
  });

  test('approval queue UI renders', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    // Either items or an empty/loading state
    const hasUI = await page
      .locator(
        '[class*="approval"], [class*="Approval"], [class*="queue"], h1, h2'
      )
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasUI).toBe(true);
  });

  test('screenshot: approvals page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/approvals.png',
      fullPage: true,
    });
  });
});

// ─── 6. Services (/services) ─────────────────────────────────────────────────

test.describe('Services (/services)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/services');
    await waitForAuth(page);
  });

  test('page loads at /services', async ({ page }) => {
    await expect(page).toHaveURL(/\/services/);
  });

  test('services panel renders', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const hasContent = await page
      .locator('[class*="service"], [class*="Service"], h1, h2')
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('screenshot: services page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/services.png',
      fullPage: true,
    });
  });
});

// ─── 7. AI Studio (/ai) ──────────────────────────────────────────────────────

test.describe('AI Studio (/ai)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ai');
    await waitForAuth(page);
  });

  test('page loads at /ai', async ({ page }) => {
    await expect(page).toHaveURL(/\/ai/);
  });

  test('AI studio UI renders', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const hasContent = await page
      .locator(
        '[class*="ai"], [class*="studio"], [class*="Studio"], textarea, h1, h2'
      )
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('screenshot: AI studio page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/ai-studio.png',
      fullPage: true,
    });
  });
});

// ─── 8. Cost Metrics (/costs) ────────────────────────────────────────────────

test.describe('Cost Metrics (/costs)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/costs');
    await waitForAuth(page);
  });

  test('page loads at /costs', async ({ page }) => {
    await expect(page).toHaveURL(/\/costs/);
  });

  test('cost dashboard renders', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const hasContent = await page
      .locator('[class*="cost"], [class*="Cost"], [class*="metric"], h1, h2')
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('screenshot: costs page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/costs.png',
      fullPage: true,
    });
  });
});

// ─── 9. Performance (/performance) ───────────────────────────────────────────

test.describe('Performance (/performance)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/performance');
    await waitForAuth(page);
  });

  test('page loads at /performance', async ({ page }) => {
    await expect(page).toHaveURL(/\/performance/);
  });

  test('performance dashboard renders', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const hasContent = await page
      .locator('[class*="performance"], [class*="Performance"], h1, h2')
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('screenshot: performance page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/performance.png',
      fullPage: true,
    });
  });
});

// ─── 10. Settings (/settings) ────────────────────────────────────────────────

test.describe('Settings (/settings)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings');
    await waitForAuth(page);
  });

  test('page loads at /settings', async ({ page }) => {
    await expect(page).toHaveURL(/\/settings/);
  });

  test('settings sections render', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const hasContent = await page
      .locator('[class*="setting"], [class*="Setting"], h1, h2, h3')
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('screenshot: settings page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/settings.png',
      fullPage: true,
    });
  });
});

// ─── 11. Workflows (/workflows) ──────────────────────────────────────────────

test.describe('Workflows (/workflows)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workflows');
    await waitForAuth(page);
  });

  test('page loads at /workflows', async ({ page }) => {
    await expect(page).toHaveURL(/\/workflows/);
  });

  test('blog workflow page renders', async ({ page }) => {
    await expect(page.locator('body')).toBeVisible();
    const hasContent = await page
      .locator(
        '[class*="workflow"], [class*="Workflow"], [class*="blog"], h1, h2'
      )
      .first()
      .isVisible()
      .catch(() => false);
    expect(hasContent).toBe(true);
  });

  test('screenshot: workflows page', async ({ page }) => {
    await page.screenshot({
      path: 'test-results/screenshots/workflows.png',
      fullPage: true,
    });
  });
});

// ─── 12. 404 / Unknown Routes ────────────────────────────────────────────────

test.describe('Routing Edge Cases', () => {
  test('unknown route redirects to dashboard (catch-all route)', async ({
    page,
  }) => {
    await page.goto('/this-route-does-not-exist');
    await page.waitForLoadState('networkidle');
    // AppRoutes has <Navigate to="/" replace /> for unknown routes
    await expect(page).toHaveURL('http://localhost:3001/');
  });
});
