/**
 * Manual Publish Pipeline — Full E2E Test
 * ========================================
 *
 * MVP Milestone: Covers the complete blog post lifecycle from task creation
 * through public-site display:
 *
 *   1. Create task via API (POST /api/tasks)
 *   2. Wait for content generation to complete (poll task status)
 *   3. Approve the task (POST /api/tasks/:id/approve)
 *   4. Publish the task (POST /api/tasks/:id/publish)
 *   5. Verify publish response contains post_id, post_slug, published_url
 *   6. Verify the post is accessible on the public site (/posts/:slug)
 *   7. Verify homepage lists the new post
 *
 * Prerequisites:
 *   - Backend running on port 8000 (DEVELOPMENT_MODE=true)
 *   - Public site running on port 3000
 *   - At least one LLM provider configured (Ollama, Anthropic, OpenAI, etc.)
 *   - PostgreSQL database accessible
 *
 * Run:
 *   SKIP_SERVER_START=true npx playwright test manual-publish-pipeline --project=chromium
 */

import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const API = process.env.PLAYWRIGHT_API_URL || 'http://localhost:8000';
const PUBLIC_SITE =
  process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:3000';

const AUTH_HEADERS = {
  Authorization: 'Bearer dev-token',
  'Content-Type': 'application/json',
};

// Content generation can take 30-120s depending on the LLM provider.
// Poll every 5s, give up after 3 minutes.
const GENERATION_POLL_INTERVAL_MS = 5_000;
const GENERATION_TIMEOUT_MS = 180_000;

// Unique suffix so parallel runs don't collide
const RUN_ID = Date.now().toString(36);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Create a blog post task and return the task ID. */
async function createBlogTask(request: any): Promise<string> {
  const response = await request.post(`${API}/api/tasks`, {
    headers: AUTH_HEADERS,
    data: {
      task_type: 'blog_post',
      topic: `E2E Pipeline Test — The Future of Automated Testing ${RUN_ID}`,
      style: 'technical',
      tone: 'professional',
      target_length: 500, // Short to speed up generation
      target_audience: 'Software Engineers',
      primary_keyword: 'automated testing',
      category: 'technology',
      quality_preference: 'fast', // Cheapest/fastest models
      generate_featured_image: false, // Skip image search to speed up
    },
  });

  expect(response.status()).toBe(201);
  const body = await response.json();

  // The endpoint returns { id, task_id, status, ... }
  const taskId = body.id || body.task_id;
  expect(taskId).toBeTruthy();
  return taskId;
}

/** Poll task status until it reaches a terminal or target state. */
async function waitForTaskStatus(
  request: any,
  taskId: string,
  targetStatuses: string[],
  timeoutMs = GENERATION_TIMEOUT_MS
): Promise<any> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const response = await request.get(`${API}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });
    expect(response.ok()).toBeTruthy();

    const task = await response.json();
    const status = task.status;

    if (targetStatuses.includes(status)) {
      return task;
    }

    // If the task failed, bail out early with useful info
    if (status === 'failed') {
      throw new Error(
        `Task ${taskId} failed during generation: ${task.error_message || JSON.stringify(task.result || {})}`
      );
    }

    // Wait before polling again
    await new Promise((r) => setTimeout(r, GENERATION_POLL_INTERVAL_MS));
  }

  throw new Error(
    `Task ${taskId} did not reach ${targetStatuses.join('/')} within ${timeoutMs / 1000}s`
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Manual Publish Pipeline (full E2E)', () => {
  // Generous timeout — content generation is the bottleneck
  test.setTimeout(240_000);

  let taskId: string;
  let postSlug: string;

  test('Step 1: Create a blog post task', async ({ request }) => {
    taskId = await createBlogTask(request);
    expect(taskId).toBeTruthy();
  });

  test('Step 2: Content generation completes', async ({ request }) => {
    // The task may go through: pending → generating → completed/awaiting_approval
    const task = await waitForTaskStatus(request, taskId, [
      'completed',
      'awaiting_approval',
      'approved', // Some flows auto-advance
    ]);

    // Verify content was actually generated
    const result = task.result || task.task_metadata || {};
    const content =
      result.draft_content ||
      result.content ||
      result.body ||
      result.article ||
      task.content ||
      '';

    expect(content.length).toBeGreaterThan(50);
  });

  test('Step 3: Approve the task', async ({ request }) => {
    const response = await request.post(`${API}/api/tasks/${taskId}/approve`, {
      headers: AUTH_HEADERS,
      data: {
        approved: true,
        auto_publish: false, // Manual publish — separate step
        human_feedback: 'Approved by E2E test pipeline',
      },
    });

    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.status).toBe('approved');
  });

  test('Step 4: Publish the task', async ({ request }) => {
    const response = await request.post(`${API}/api/tasks/${taskId}/publish`, {
      headers: AUTH_HEADERS,
      data: {},
    });

    expect(response.ok()).toBeTruthy();
    const body = await response.json();

    // #954: Verify publish response contract
    expect(body.status).toBe('published');
    expect(body.post_id).toBeTruthy();
    expect(body.post_slug).toBeTruthy();
    expect(body.published_url).toBeTruthy();
    expect(body.published_url).toContain('/posts/');

    postSlug = body.post_slug;
  });

  test('Step 5: Post is accessible via CMS API', async ({ request }) => {
    // Give the DB a moment to be consistent
    await new Promise((r) => setTimeout(r, 1000));

    const response = await request.get(`${API}/api/posts/${postSlug}`, {
      headers: { 'Content-Type': 'application/json' },
    });

    expect(response.ok()).toBeTruthy();
    const body = await response.json();

    const post = body.data || body;
    expect(post.title).toBeTruthy();
    expect(post.slug).toBe(postSlug);
    expect(post.content).toBeTruthy();
    expect(post.content.length).toBeGreaterThan(50);
    expect(post.status).toBe('published');
  });

  test('Step 6: Post renders on the public site', async ({ page }) => {
    // Navigate to the published post on the public site
    const postUrl = `${PUBLIC_SITE}/posts/${postSlug}`;
    const response = await page.goto(postUrl, {
      waitUntil: 'domcontentloaded',
    });

    // Should not be a 404
    expect(response?.status()).toBe(200);

    // Page should have a visible heading with the post title
    const heading = page.locator('h1');
    await expect(heading).toBeVisible({ timeout: 10_000 });

    // Article content should be present
    const article = page.locator('article');
    await expect(article).toBeVisible({ timeout: 10_000 });

    // Content should have meaningful text (not just whitespace)
    const articleText = await article.textContent();
    expect((articleText || '').trim().length).toBeGreaterThan(50);
  });

  test('Step 7: Post appears on the homepage', async ({ page }) => {
    // The homepage fetches from /api/posts — with ISR cache, we hit the
    // backend API directly to confirm the post is in the listing.
    const response = await page.request.get(
      `${API}/api/posts?offset=0&limit=20&published_only=true`,
      { headers: { 'Content-Type': 'application/json' } }
    );

    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    const posts = body.posts || body.data || [];

    // Our newly published post should be in the list
    const found = posts.find((p: any) => p.slug === postSlug);
    expect(found).toBeTruthy();
    expect(found.title).toBeTruthy();
  });

  test('Step 8: Cleanup — delete the test post task', async ({ request }) => {
    // Best-effort cleanup: delete the task so test data doesn't accumulate.
    // Failure here doesn't invalidate the pipeline test.
    const response = await request.delete(`${API}/api/tasks/${taskId}`, {
      headers: AUTH_HEADERS,
    });

    // 200 or 204 both fine; 404 means already cleaned up
    expect([200, 204, 404]).toContain(response.status());
  });
});

// ---------------------------------------------------------------------------
// Standalone smoke test: can also run without waiting for generation
// ---------------------------------------------------------------------------

test.describe('Publish Contract Smoke Tests', () => {
  test('Publish endpoint rejects non-approved tasks', async ({ request }) => {
    // Create a fresh task
    const createRes = await request.post(`${API}/api/tasks`, {
      headers: AUTH_HEADERS,
      data: {
        task_type: 'blog_post',
        topic: `Publish guard test ${RUN_ID}`,
        target_length: 500,
        quality_preference: 'fast',
      },
    });
    expect(createRes.status()).toBe(201);
    const { id: newTaskId } = await createRes.json();

    // Try to publish without approving — should fail
    const publishRes = await request.post(
      `${API}/api/tasks/${newTaskId}/publish`,
      {
        headers: AUTH_HEADERS,
        data: {},
      }
    );

    expect(publishRes.status()).toBe(400);
    const err = await publishRes.json();
    expect(err.detail).toContain('approved');

    // Cleanup
    await request.delete(`${API}/api/tasks/${newTaskId}`, {
      headers: AUTH_HEADERS,
    });
  });

  test('Approve endpoint returns correct status', async ({ request }) => {
    const createRes = await request.post(`${API}/api/tasks`, {
      headers: AUTH_HEADERS,
      data: {
        task_type: 'blog_post',
        topic: `Approve test ${RUN_ID}`,
        target_length: 500,
        quality_preference: 'fast',
      },
    });
    expect(createRes.status()).toBe(201);
    const task = await createRes.json();
    const newTaskId = task.id || task.task_id;

    // Wait for generation to finish (or just try approving — many statuses accepted)
    await new Promise((r) => setTimeout(r, 2000));

    const approveRes = await request.post(
      `${API}/api/tasks/${newTaskId}/approve`,
      {
        headers: AUTH_HEADERS,
        data: { approved: true, auto_publish: false },
      }
    );

    // The approve endpoint accepts tasks in many statuses
    expect(approveRes.ok()).toBeTruthy();
    const body = await approveRes.json();
    expect(['approved', 'published']).toContain(body.status);

    // Cleanup
    await request.delete(`${API}/api/tasks/${newTaskId}`, {
      headers: AUTH_HEADERS,
    });
  });

  test('Revalidation endpoint requires authentication', async ({ request }) => {
    // Call without auth header
    const res = await request.post(`${API}/api/revalidate-cache`, {
      headers: { 'Content-Type': 'application/json' },
      data: { paths: ['/'] },
    });

    // Should be 401 or 403 — not 200
    expect([401, 403]).toContain(res.status());
  });
});
