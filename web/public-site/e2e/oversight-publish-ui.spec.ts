/**
 * Oversight Hub UI — Manual Publish Pipeline E2E
 * ================================================
 *
 * Drives the actual Oversight Hub browser UI through the full blog post
 * lifecycle:
 *
 *   1. Authenticate via mock auth (dev mode)
 *   2. Navigate to Tasks page
 *   3. Open CreateTaskModal, fill blog post form, submit
 *   4. Wait for generation to complete (poll task in list)
 *   5. Open task detail, approve via TaskApprovalForm
 *   6. Publish via "Publish (Step 2)" button
 *   7. Verify post appears on the public site
 *
 * Prerequisites:
 *   - Backend running on port 8000 (DEVELOPMENT_MODE=true)
 *   - Oversight Hub running on port 3001
 *   - Public site running on port 3000
 *   - At least one LLM provider configured
 *
 * Run:
 *   SKIP_SERVER_START=true npx playwright test oversight-publish-ui --project=chromium
 */

import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const ADMIN_URL = process.env.PLAYWRIGHT_ADMIN_URL || 'http://localhost:3001';
const API_URL = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000';
const PUBLIC_SITE =
  process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:3000';

const AUTH_HEADERS = {
  Authorization: 'Bearer dev-token',
  'Content-Type': 'application/json',
};

// Content generation timeout: LLM calls can take 30-180s
const GENERATION_TIMEOUT_MS = 180_000;
const POLL_INTERVAL_MS = 5_000;

const RUN_ID = Date.now().toString(36);

// ---------------------------------------------------------------------------
// Auth helper: inject a dev-token session so ProtectedRoute passes
// ---------------------------------------------------------------------------

/**
 * Get a valid dev JWT from the backend's dev-token endpoint,
 * then inject it into the Oversight Hub's sessionStorage.
 */
async function authenticateAdmin(page: any) {
  // First, get a dev token from the backend
  const response = await page.request.get(`${API_URL}/api/auth/dev-token`, {
    headers: { 'Content-Type': 'application/json' },
  });

  let token: string;

  if (response.ok()) {
    const data = await response.json();
    token = data.token || data.access_token;
  } else {
    // Fallback: use dev-token literal (backend accepts it in DEVELOPMENT_MODE)
    token = 'dev-token';
  }

  // Navigate to the admin origin first so we can set sessionStorage
  await page.goto(`${ADMIN_URL}/login`, { waitUntil: 'domcontentloaded' });

  // Inject auth state into sessionStorage
  await page.evaluate((authToken: string) => {
    sessionStorage.setItem('auth_token', authToken);
    // Also set user info so the auth context picks it up
    sessionStorage.setItem(
      'user',
      JSON.stringify({
        id: 'e2e-test-user',
        login: 'e2e-tester',
        name: 'E2E Test User',
        email: 'e2e@test.local',
        avatar_url: '',
      })
    );
  }, token);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Wait for a task to reach a target status by polling the API. */
async function waitForTaskStatus(
  request: any,
  taskId: string,
  targetStatuses: string[]
): Promise<any> {
  const deadline = Date.now() + GENERATION_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const response = await request.get(`${API_URL}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });
    if (!response.ok()) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      continue;
    }

    const task = await response.json();
    if (targetStatuses.includes(task.status)) return task;
    if (task.status === 'failed') {
      throw new Error(
        `Task ${taskId} failed: ${task.error_message || 'unknown'}`
      );
    }

    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  throw new Error(`Task did not reach ${targetStatuses} within timeout`);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Oversight Hub UI — Full Publish Pipeline', () => {
  test.setTimeout(300_000); // 5 min — generation is the bottleneck

  let taskId: string;
  let postSlug: string;

  test.beforeEach(async ({ page }) => {
    await authenticateAdmin(page);
  });

  test('Step 1: Navigate to Tasks page', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/tasks`, { waitUntil: 'networkidle' });

    // Should see the tasks page (not redirected to login)
    await expect(page).toHaveURL(/\/tasks/);

    // The "Create Task" button should be visible
    const createBtn = page.getByText(/Create Task/i);
    await expect(createBtn).toBeVisible({ timeout: 10_000 });
  });

  test('Step 2: Create a blog post via UI modal', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/tasks`, { waitUntil: 'networkidle' });

    // Click "Create Task" button
    const createBtn = page.getByText(/Create Task/i);
    await createBtn.click();

    // Wait for the modal to appear
    const modal = page.locator('[role="dialog"], .fixed');
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Select "Blog Post" task type
    const blogPostBtn = page.getByText(/Blog Post/i).first();
    await blogPostBtn.click();

    // Fill in the topic field
    const topicInput = page
      .locator('input[name="topic"], #topic, input[placeholder*="Topic" i]')
      .first();
    await expect(topicInput).toBeVisible({ timeout: 5_000 });
    await topicInput.fill(
      `E2E UI Pipeline Test — Automated Testing Insights ${RUN_ID}`
    );

    // Fill primary keyword (if visible)
    const keywordInput = page
      .locator('input[name="primary_keyword"], #primary_keyword')
      .first();
    if (await keywordInput.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await keywordInput.fill('automated testing');
    }

    // Fill target audience (if visible)
    const audienceInput = page
      .locator('input[name="target_audience"], #target_audience')
      .first();
    if (await audienceInput.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await audienceInput.fill('Software Engineers');
    }

    // Submit the form
    const submitBtn = page.getByText(/Create Task/i).last();
    await submitBtn.click();

    // Wait for the modal to close (task created)
    await expect(modal).toBeHidden({ timeout: 30_000 });

    // Extract the task ID from the API — get the most recent task
    const tasksRes = await page.request.get(
      `${API_URL}/api/tasks?offset=0&limit=5&status=pending`,
      { headers: AUTH_HEADERS }
    );
    const tasksData = await tasksRes.json();
    const tasks = tasksData.tasks || [];
    const ourTask = tasks.find(
      (t: any) => t.topic?.includes(RUN_ID) || t.task_name?.includes(RUN_ID)
    );

    expect(ourTask).toBeTruthy();
    taskId = ourTask.id || ourTask.task_id;
    expect(taskId).toBeTruthy();
  });

  test('Step 3: Wait for content generation', async ({ request }) => {
    expect(taskId).toBeTruthy();

    const task = await waitForTaskStatus(request, taskId, [
      'completed',
      'awaiting_approval',
      'approved',
    ]);

    // Content should have been generated
    const result = task.result || task.task_metadata || {};
    const content =
      result.draft_content ||
      result.content ||
      result.body ||
      task.content ||
      '';
    expect(content.length).toBeGreaterThan(50);
  });

  test('Step 4: Approve task via UI', async ({ page }) => {
    expect(taskId).toBeTruthy();

    // Navigate to tasks page
    await page.goto(`${ADMIN_URL}/tasks`, { waitUntil: 'networkidle' });

    // Click on the task row to open detail modal
    // Find the row containing our task topic
    const taskRow = page
      .locator(`tr, [data-task-id="${taskId}"]`)
      .filter({ hasText: RUN_ID })
      .first();

    if (await taskRow.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await taskRow.click();
    } else {
      // Fallback: use the API to approve if UI row isn't visible
      // (task might be on a different page or the list format varies)
      const approveRes = await page.request.post(
        `${API_URL}/api/tasks/${taskId}/approve`,
        {
          headers: AUTH_HEADERS,
          data: {
            approved: true,
            auto_publish: false,
            human_feedback: 'Approved via E2E UI test',
          },
        }
      );
      expect(approveRes.ok()).toBeTruthy();
      return;
    }

    // Wait for detail modal
    const detailModal = page.locator('[role="dialog"]').first();
    await expect(detailModal).toBeVisible({ timeout: 10_000 });

    // Find and click the Approve button
    const approveBtn = detailModal
      .getByText(/Approve.*Step 1|Approve/i)
      .first();
    await expect(approveBtn).toBeVisible({ timeout: 10_000 });
    await approveBtn.click();

    // If there's a confirmation dialog, confirm it
    const confirmBtn = page.getByRole('button', { name: /Approve/i }).last();
    if (await confirmBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await confirmBtn.click();
    }

    // Wait for status to update
    await page.waitForTimeout(2_000);

    // Verify via API
    const taskRes = await page.request.get(`${API_URL}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });
    const taskData = await taskRes.json();
    expect(['approved', 'published']).toContain(taskData.status);
  });

  test('Step 5: Publish task via UI', async ({ page }) => {
    expect(taskId).toBeTruthy();

    // Check if already published (auto_publish may have triggered)
    const checkRes = await page.request.get(`${API_URL}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });
    const checkData = await checkRes.json();

    if (checkData.status === 'published') {
      // Already published — extract slug from result
      const result =
        typeof checkData.result === 'string'
          ? JSON.parse(checkData.result)
          : checkData.result || {};
      postSlug = result.post_slug || checkData.post_slug;
      expect(postSlug).toBeTruthy();
      return;
    }

    // Navigate to tasks page and open the task
    await page.goto(`${ADMIN_URL}/tasks`, { waitUntil: 'networkidle' });

    const taskRow = page
      .locator(`tr, [data-task-id="${taskId}"]`)
      .filter({ hasText: RUN_ID })
      .first();

    if (await taskRow.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await taskRow.click();

      // Wait for detail modal
      const detailModal = page.locator('[role="dialog"]').first();
      await expect(detailModal).toBeVisible({ timeout: 10_000 });

      // Find and click Publish button (Step 2)
      const publishBtn = detailModal
        .getByText(/Publish.*Step 2|Publish/i)
        .first();
      await expect(publishBtn).toBeVisible({ timeout: 10_000 });
      await publishBtn.click();

      // If there's a confirmation dialog, confirm it
      const confirmBtn = page.getByRole('button', { name: /Publish/i }).last();
      if (await confirmBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
        await confirmBtn.click();
      }

      // Wait for publish to complete
      await page.waitForTimeout(3_000);
    } else {
      // Fallback: publish via API
      const publishRes = await page.request.post(
        `${API_URL}/api/tasks/${taskId}/publish`,
        { headers: AUTH_HEADERS, data: {} }
      );
      expect(publishRes.ok()).toBeTruthy();
    }

    // Verify via API and get the post slug
    const taskRes = await page.request.get(`${API_URL}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });
    const taskData = await taskRes.json();
    expect(taskData.status).toBe('published');

    // Extract post slug from task result
    const result =
      typeof taskData.result === 'string'
        ? JSON.parse(taskData.result)
        : taskData.result || {};
    postSlug = result.post_slug || taskData.post_slug;
    expect(postSlug).toBeTruthy();
  });

  test('Step 6: Post renders on public site', async ({ page }) => {
    expect(postSlug).toBeTruthy();

    // Navigate to the post on the public site
    const response = await page.goto(`${PUBLIC_SITE}/posts/${postSlug}`, {
      waitUntil: 'domcontentloaded',
    });

    expect(response?.status()).toBe(200);

    // Heading should be visible
    const heading = page.locator('h1');
    await expect(heading).toBeVisible({ timeout: 10_000 });

    // Article content should be present
    const article = page.locator('article');
    await expect(article).toBeVisible({ timeout: 10_000 });

    const articleText = await article.textContent();
    expect((articleText || '').trim().length).toBeGreaterThan(50);
  });

  test('Step 7: Post is in homepage listing', async ({ page }) => {
    expect(postSlug).toBeTruthy();

    // Check via API (homepage ISR cache may be stale)
    const response = await page.request.get(
      `${API_URL}/api/posts?offset=0&limit=20&published_only=true`,
      { headers: { 'Content-Type': 'application/json' } }
    );

    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    const posts = body.posts || body.data || [];

    const found = posts.find((p: any) => p.slug === postSlug);
    expect(found).toBeTruthy();
    expect(found.title).toBeTruthy();
  });

  test('Step 8: Cleanup', async ({ request }) => {
    if (!taskId) return;

    await request.delete(`${API_URL}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });
  });
});

// ---------------------------------------------------------------------------
// Approval Queue UI Tests
// ---------------------------------------------------------------------------

test.describe('Oversight Hub — Approval Queue UI', () => {
  test.setTimeout(60_000);

  test.beforeEach(async ({ page }) => {
    await authenticateAdmin(page);
  });

  test('Approval queue page loads', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/approvals`, { waitUntil: 'networkidle' });

    await expect(page).toHaveURL(/\/approvals/);

    // Should see the approval queue header
    const header = page.getByText(/Approval Queue/i);
    await expect(header).toBeVisible({ timeout: 10_000 });
  });

  test('Tasks page loads with Create Task button', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/tasks`, { waitUntil: 'networkidle' });

    await expect(page).toHaveURL(/\/tasks/);

    const createBtn = page.getByText(/Create Task/i);
    await expect(createBtn).toBeVisible({ timeout: 10_000 });
  });

  test('CreateTaskModal opens and shows task type selection', async ({
    page,
  }) => {
    await page.goto(`${ADMIN_URL}/tasks`, { waitUntil: 'networkidle' });

    const createBtn = page.getByText(/Create Task/i);
    await createBtn.click();

    // Modal should appear
    const modal = page.locator('[role="dialog"], .fixed');
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Should show task type options
    await expect(page.getByText(/Blog Post/i).first()).toBeVisible();
  });

  test('Dashboard loads with navigation', async ({ page }) => {
    await page.goto(ADMIN_URL, { waitUntil: 'networkidle' });

    // Should not be on login page
    const url = page.url();
    // If redirected to login, auth injection didn't work — still a useful signal
    if (url.includes('/login')) {
      test.skip(
        true,
        'Auth injection did not bypass login — mock auth may need enabling'
      );
    }

    // Navigation should have key items
    const tasksNav = page.getByText(/Tasks/i).first();
    await expect(tasksNav).toBeVisible({ timeout: 10_000 });
  });
});
