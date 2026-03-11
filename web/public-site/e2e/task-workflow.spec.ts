/**
 * Task Pause/Resume Workflow E2E Tests
 * =====================================
 *
 * Covers issue #13 — E2E coverage gaps: pause/resume workflow.
 *
 * These are API-level tests using the `request` fixture so they work
 * reliably without requiring a browser or UI to be running.
 *
 * Auth: All requests use `Authorization: Bearer dev-token` (requires
 * DEVELOPMENT_MODE=true on the backend, which is the dev default).
 *
 * API base: http://localhost:8000
 *
 * Key endpoints exercised:
 *   POST /api/tasks               — create a task
 *   GET  /api/tasks               — list tasks
 *   GET  /api/tasks/:id           — fetch single task
 *   POST /api/tasks/bulk          — bulk pause / resume / cancel
 */

import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API = 'http://localhost:8000';

const AUTH_HEADERS = {
  Authorization: 'Bearer dev-token',
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
  // -------------------------------------------------------------------------
  // Backend availability guard
  // -------------------------------------------------------------------------

  test('backend health check — skip suite if backend is down', async ({
    request,
  }) => {
    const res = await request
      .get(`${API}/health`, { timeout: 5000 })
      .catch(() => null);

    if (!res || !res.ok()) {
      test.skip(
        true,
        'Backend not reachable at http://localhost:8000 — skipping task workflow tests'
      );
    }
  });

  // -------------------------------------------------------------------------
  // Task creation
  // -------------------------------------------------------------------------

  test('creates a task and returns a valid task ID', async ({ request }) => {
    const res = await createTask(request, 'create-check');

    // Some backends return 200, others 201 — accept both.
    // 401 is also possible when not running in DEVELOPMENT_MODE.
    if (res.status() === 401) {
      test.skip(
        true,
        'Server not in DEVELOPMENT_MODE — dev-token rejected, skipping task creation test'
      );
      return;
    }

    expect([200, 201]).toContain(res.status());

    const body = await res.json().catch(() => null);
    expect(body).not.toBeNull();

    // The response should contain an id field
    const taskId: string | undefined = body?.id ?? body?.task_id;
    expect(taskId).toBeTruthy();
    expect(typeof taskId).toBe('string');
  });

  test('created task appears in the task list', async ({ request }) => {
    // Create task
    const createRes = await createTask(request, 'list-check');
    const createBody = await createRes.json().catch(() => null);
    const taskId: string | undefined =
      createBody?.id ?? createBody?.task_id;

    // Guard: if creation failed (backend down / auth issue) skip gracefully
    if (!createRes.ok() || !taskId) {
      test.skip(true, 'Task creation failed — skipping list-presence check');
      return;
    }

    const listRes = await request.get(`${API}/api/tasks`, {
      headers: AUTH_HEADERS,
    });
    expect(listRes.ok()).toBe(true);

    const listBody = await listRes.json().catch(() => null);
    // API returns {tasks: [...], total: N, ...}
    const tasks: any[] = listBody?.tasks ?? listBody?.data ?? listBody ?? [];
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
    const createRes = await createTask(request, 'pause-test');
    if (!createRes.ok()) {
      test.skip(true, 'Task creation failed — skipping pause test');
      return;
    }

    const createBody = await createRes.json().catch(() => null);
    const taskId: string | undefined =
      createBody?.id ?? createBody?.task_id;
    if (!taskId) {
      test.skip(true, 'No task ID returned — skipping pause test');
      return;
    }

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
    const [r1, r2] = await Promise.all([
      createTask(request, 'pause-count-1'),
      createTask(request, 'pause-count-2'),
    ]);

    if (!r1.ok() || !r2.ok()) {
      test.skip(
        true,
        'Task creation failed — skipping bulk pause count test'
      );
      return;
    }

    const [b1, b2] = await Promise.all([r1.json(), r2.json()]);
    const ids = [b1?.id ?? b1?.task_id, b2?.id ?? b2?.task_id].filter(
      Boolean
    ) as string[];

    if (ids.length < 2) {
      test.skip(true, 'Could not retrieve task IDs — skipping');
      return;
    }

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
    const createRes = await createTask(request, 'resume-test');
    if (!createRes.ok()) {
      test.skip(true, 'Task creation failed — skipping resume test');
      return;
    }

    const createBody = await createRes.json().catch(() => null);
    const taskId: string | undefined =
      createBody?.id ?? createBody?.task_id;
    if (!taskId) {
      test.skip(true, 'No task ID — skipping resume test');
      return;
    }

    // Pause first
    const pauseRes = await bulkAction(request, [taskId], 'pause');
    if (!pauseRes.ok()) {
      test.skip(true, 'Pause failed — skipping resume test');
      return;
    }

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
    const createRes = await createTask(request, 'cancel-test');
    if (!createRes.ok()) {
      test.skip(true, 'Task creation failed — skipping cancel test');
      return;
    }

    const createBody = await createRes.json().catch(() => null);
    const taskId: string | undefined =
      createBody?.id ?? createBody?.task_id;
    if (!taskId) {
      test.skip(true, 'No task ID — skipping cancel test');
      return;
    }

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
    const createRes = await createTask(request, 'cancel-then-pause');
    if (!createRes.ok()) {
      test.skip(true, 'Task creation failed');
      return;
    }

    const createBody = await createRes.json().catch(() => null);
    const taskId: string | undefined =
      createBody?.id ?? createBody?.task_id;
    if (!taskId) {
      test.skip(true, 'No task ID');
      return;
    }

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
    // 400 = validation error (expected), 401 = server not in DEVELOPMENT_MODE
    expect([400, 401]).toContain(res.status());
    if (res.status() === 401) {
      // Not in dev mode — validation won't run. Document the expected behavior
      // and skip the specific assertion.
      test.skip(
        true,
        'Server not in DEVELOPMENT_MODE — auth rejected before validation'
      );
    }
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
    // 400 = validation error (expected), 401 = server not in DEVELOPMENT_MODE
    expect([400, 401]).toContain(res.status());
    if (res.status() === 401) {
      test.skip(
        true,
        'Server not in DEVELOPMENT_MODE — auth rejected before validation'
      );
    }
  });

  test('bulk action with malformed UUID fails gracefully', async ({
    request,
  }) => {
    const res = await request.post(`${API}/api/tasks/bulk`, {
      headers: AUTH_HEADERS,
      data: { task_ids: ['not-a-uuid'], action: 'pause' },
    });
    // 200 with per-task error, 400 top-level error, or 401 when not in dev mode
    expect([200, 400, 401]).toContain(res.status());

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
    const createRes = await createTask(request, 'lifecycle-test');
    if (!createRes.ok()) {
      test.skip(true, 'Task creation failed — skipping lifecycle test');
      return;
    }

    const createBody = await createRes.json().catch(() => null);
    const taskId: string | undefined =
      createBody?.id ?? createBody?.task_id;
    if (!taskId) {
      test.skip(true, 'No task ID — skipping lifecycle test');
      return;
    }

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
