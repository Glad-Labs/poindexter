/**
 * Task Pause/Resume Workflow E2E Tests
 * =====================================
 *
 * Covers issue #13 — E2E coverage gaps: pause/resume workflow.
 *
 * These are API-level tests using the `request` fixture so they work
 * reliably without requiring a browser or UI to be running.
 *
 * Auth: every request sends `Authorization: Bearer dev-token`, so this spec
 * needs a DEVELOPMENT_MODE backend, and it writes to it (it creates tasks).
 * Never point it at production. requireBackend({ devToken: true }) checks
 * both before each test; see backend.ts.
 *
 * API base: PLAYWRIGHT_API_URL, else http://localhost:8002 (see backend.ts).
 *
 * Key endpoints exercised:
 *   POST /api/tasks               — create a task
 *   GET  /api/tasks               — list tasks
 *   GET  /api/tasks/:id           — fetch single task
 *   POST /api/tasks/bulk          — bulk pause / resume / cancel
 *
 * Stale: the bulk route no longer exists. bulk_task_routes.py was deleted in
 * d96f43c7a (2026-04-05), so every test that calls it fails against a real
 * backend until it is rewritten against a live route or retired.
 */

import { test, expect } from '@playwright/test';
import { API_URL, DEV_TOKEN_AUTH, requireBackend } from './backend';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API = API_URL;

const AUTH_HEADERS = {
  Authorization: DEV_TOKEN_AUTH,
  'Content-Type': 'application/json',
};

/** Minimal valid task body accepted by POST /api/tasks */
function makeTaskBody(suffix: string) {
  return {
    task_name: `E2E Pause/Resume Test — ${suffix}`,
    topic: `Automated e2e test topic ${suffix}`,
    primary_keyword: 'e2e-testing',
    target_audience: 'QA Engineers',
    category: 'general',
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function createTask(request: any, suffix: string) {
  const res = await request.post(`${API}/api/tasks`, {
    headers: AUTH_HEADERS,
    data: makeTaskBody(suffix),
  });
  return res;
}

/** Create a task, assert the backend accepted it, and return its ID. */
async function createTaskId(request: any, suffix: string): Promise<string> {
  const res = await createTask(request, suffix);
  expect([200, 201]).toContain(res.status());
  const body = await res.json();
  const taskId: string | undefined = body?.id ?? body?.task_id;
  expect(taskId, 'POST /api/tasks returned no task ID').toBeTruthy();
  return taskId as string;
}

async function bulkAction(
  request: any,
  taskIds: string[],
  action: 'pause' | 'resume' | 'cancel' | 'reject' | 'retry'
) {
  return request.post(`${API}/api/tasks/bulk`, {
    headers: AUTH_HEADERS,
    data: { task_ids: taskIds, action },
  });
}

async function getTask(request: any, taskId: string) {
  return request.get(`${API}/api/tasks/${taskId}`, { headers: AUTH_HEADERS });
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Task Pause/Resume Workflow', () => {
  // Fails each test when the backend is unreachable or refuses dev-token, if
  // the run names a target or runs in CI; skips it only on a bare local run.
  // Per test, not beforeAll, so no test is ever recorded as skipped (see
  // backend.ts). Nothing below skips on its own.
  test.beforeEach(async () => {
    await requireBackend({ devToken: true });
  });

  // -------------------------------------------------------------------------
  // Task creation
  // -------------------------------------------------------------------------

  test('creates a task and returns a valid task ID', async ({ request }) => {
    const res = await createTask(request, 'create-check');

    // Some backends return 200, others 201 — accept both.
    expect([200, 201]).toContain(res.status());

    const body = await res.json().catch(() => null);
    expect(body).not.toBeNull();

    // The response should contain an id field
    const taskId: string | undefined = body?.id ?? body?.task_id;
    expect(taskId).toBeTruthy();
    expect(typeof taskId).toBe('string');
  });

  test('created task appears in the task list', async ({ request }) => {
    const taskId = await createTaskId(request, 'list-check');

    const listRes = await request.get(`${API}/api/tasks`, {
      headers: AUTH_HEADERS,
    });
    expect(listRes.ok()).toBe(true);

    const listBody = await listRes.json().catch(() => null);
    // List envelope: {items, total, limit, offset} (poindexter#745), newest first
    const tasks: any[] = listBody?.items;
    expect(Array.isArray(tasks)).toBe(true);

    const found = tasks.some(
      (t: any) => t.id === taskId || t.task_id === taskId
    );
    expect(found).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Pause action
  // -------------------------------------------------------------------------

  test('pauses a task via bulk action and status becomes "paused"', async ({
    request,
  }) => {
    const taskId = await createTaskId(request, 'pause-test');

    // Issue pause
    const pauseRes = await bulkAction(request, [taskId], 'pause');
    expect(pauseRes.ok()).toBe(true);

    const pauseBody = await pauseRes.json().catch(() => null);
    expect(pauseBody).not.toBeNull();
    // Response shape: { message, updated, failed, total, errors? }
    expect(pauseBody.updated).toBeGreaterThanOrEqual(1);
    expect(pauseBody.failed).toBe(0);

    // Verify task status via GET /api/tasks/:id
    const taskRes = await getTask(request, taskId);
    if (taskRes.ok()) {
      const taskBody = await taskRes.json().catch(() => null);
      const status: string = taskBody?.status ?? taskBody?.task_status ?? '';
      expect(status).toBe('paused');
    }
  });

  test('bulk pause response contains correct total count', async ({
    request,
  }) => {
    const ids = await Promise.all([
      createTaskId(request, 'pause-count-1'),
      createTaskId(request, 'pause-count-2'),
    ]);

    const bulkRes = await bulkAction(request, ids, 'pause');
    expect(bulkRes.ok()).toBe(true);

    const bulkBody = await bulkRes.json().catch(() => null);
    expect(bulkBody.total).toBe(ids.length);
    expect(bulkBody.updated).toBe(ids.length);
    expect(bulkBody.failed).toBe(0);
  });

  // -------------------------------------------------------------------------
  // Resume action
  // -------------------------------------------------------------------------

  test('resumes a paused task and status returns to "pending"', async ({
    request,
  }) => {
    // Create
    const taskId = await createTaskId(request, 'resume-test');

    // Pause first
    const pauseRes = await bulkAction(request, [taskId], 'pause');
    expect(pauseRes.ok()).toBe(true);

    // Now resume
    const resumeRes = await bulkAction(request, [taskId], 'resume');
    expect(resumeRes.ok()).toBe(true);

    const resumeBody = await resumeRes.json().catch(() => null);
    expect(resumeBody).not.toBeNull();
    expect(resumeBody.updated).toBeGreaterThanOrEqual(1);
    expect(resumeBody.failed).toBe(0);

    // Verify task status
    const taskRes = await getTask(request, taskId);
    if (taskRes.ok()) {
      const taskBody = await taskRes.json().catch(() => null);
      const status: string = taskBody?.status ?? taskBody?.task_status ?? '';
      // resume maps to "pending"
      expect(status).toBe('pending');
    }
  });

  // -------------------------------------------------------------------------
  // Cancel action
  // -------------------------------------------------------------------------

  test('cancels a task via bulk action and status becomes "cancelled"', async ({
    request,
  }) => {
    const taskId = await createTaskId(request, 'cancel-test');

    const cancelRes = await bulkAction(request, [taskId], 'cancel');
    expect(cancelRes.ok()).toBe(true);

    const cancelBody = await cancelRes.json().catch(() => null);
    expect(cancelBody).not.toBeNull();
    expect(cancelBody.updated).toBeGreaterThanOrEqual(1);
    expect(cancelBody.failed).toBe(0);

    const taskRes = await getTask(request, taskId);
    if (taskRes.ok()) {
      const taskBody = await taskRes.json().catch(() => null);
      const status: string = taskBody?.status ?? taskBody?.task_status ?? '';
      expect(status).toBe('cancelled');
    }
  });

  test('cancelled task cannot be paused (operation may report failure)', async ({
    request,
  }) => {
    const taskId = await createTaskId(request, 'cancel-then-pause');

    // Cancel first
    await bulkAction(request, [taskId], 'cancel');

    // Attempt to pause a cancelled task — backend may allow or deny
    // Either way the request itself should return a 2xx (bulk endpoint
    // handles per-task errors gracefully)
    const pauseRes = await bulkAction(request, [taskId], 'pause');
    expect(pauseRes.ok()).toBe(true);

    const pauseBody = await pauseRes.json().catch(() => null);
    // total should still reflect how many we sent
    expect(pauseBody.total).toBe(1);
    // updated + failed should sum to total
    expect(pauseBody.updated + pauseBody.failed).toBe(pauseBody.total);
  });

  // -------------------------------------------------------------------------
  // Input validation
  // -------------------------------------------------------------------------

  test('bulk action with empty task_ids returns 400', async ({ request }) => {
    const res = await request.post(`${API}/api/tasks/bulk`, {
      headers: AUTH_HEADERS,
      data: { task_ids: [], action: 'pause' },
    });
    // 400 = validation error. requireBackend() already proved dev-token is
    // accepted, so a 401 here is a failure, not a mode to tolerate.
    expect(res.status()).toBe(400);
  });

  test('bulk action with invalid action string returns 400', async ({
    request,
  }) => {
    const res = await request.post(`${API}/api/tasks/bulk`, {
      headers: AUTH_HEADERS,
      data: {
        task_ids: ['550e8400-e29b-41d4-a716-446655440000'],
        action: 'fly',
      },
    });
    // 400 = validation error
    expect(res.status()).toBe(400);
  });

  test('bulk action with malformed UUID fails gracefully', async ({
    request,
  }) => {
    const res = await request.post(`${API}/api/tasks/bulk`, {
      headers: AUTH_HEADERS,
      data: { task_ids: ['not-a-uuid'], action: 'pause' },
    });
    // 200 with per-task error, or 400 top-level error
    expect([200, 400]).toContain(res.status());

    if (res.status() === 200) {
      const body = await res.json().catch(() => null);
      // The malformed UUID should be counted as failed
      expect(body.failed).toBeGreaterThanOrEqual(1);
    }
  });

  // -------------------------------------------------------------------------
  // Full pause → resume → cancel lifecycle
  // -------------------------------------------------------------------------

  test('full task lifecycle: create → pause → resume → cancel', async ({
    request,
  }) => {
    const taskId = await createTaskId(request, 'lifecycle-test');

    // --- Pause ---
    const pauseRes = await bulkAction(request, [taskId], 'pause');
    expect(pauseRes.ok()).toBe(true);
    const pauseBody = await pauseRes.json();
    expect(pauseBody.updated).toBe(1);

    // Verify paused
    let taskRes = await getTask(request, taskId);
    if (taskRes.ok()) {
      const t = await taskRes.json().catch(() => null);
      expect(t?.status ?? t?.task_status).toBe('paused');
    }

    // --- Resume ---
    const resumeRes = await bulkAction(request, [taskId], 'resume');
    expect(resumeRes.ok()).toBe(true);
    const resumeBody = await resumeRes.json();
    expect(resumeBody.updated).toBe(1);

    // Verify pending
    taskRes = await getTask(request, taskId);
    if (taskRes.ok()) {
      const t = await taskRes.json().catch(() => null);
      expect(t?.status ?? t?.task_status).toBe('pending');
    }

    // --- Cancel ---
    const cancelRes = await bulkAction(request, [taskId], 'cancel');
    expect(cancelRes.ok()).toBe(true);
    const cancelBody = await cancelRes.json();
    expect(cancelBody.updated).toBe(1);

    // Verify cancelled
    taskRes = await getTask(request, taskId);
    if (taskRes.ok()) {
      const t = await taskRes.json().catch(() => null);
      expect(t?.status ?? t?.task_status).toBe('cancelled');
    }
  });
});
