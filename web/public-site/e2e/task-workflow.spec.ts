/**
 * Task creation E2E tests
 * =======================
 *
 * API-level tests using the `request` fixture so they work without a browser
 * or UI running.
 *
 * Auth: every request sends `Authorization: Bearer dev-token`, so this spec
 * needs a DEVELOPMENT_MODE backend, and it writes to it (it creates tasks).
 * Never point it at production. requireBackend({ devToken: true }) checks
 * both before each test; see backend.ts.
 *
 * API base: PLAYWRIGHT_API_URL, else http://localhost:8002 (see backend.ts).
 *
 * Endpoints exercised:
 *   POST   /api/tasks       — create a task
 *   GET    /api/tasks       — list tasks
 *   DELETE /api/tasks/:id   — soft-cancel, used to clean up
 *
 * This spec used to be "Task Pause/Resume Workflow": nine of its eleven tests
 * drove POST /api/tasks/bulk, whose route (bulk_task_routes.py) was deleted in
 * d96f43c7a (2026-04-05), so they could only fail against a real backend. They
 * were retired rather than rewritten (glad-labs-stack#4237). No live route
 * pauses or resumes a task, and a rewrite against the status route couldn't be
 * verified without a DEVELOPMENT_MODE backend. The tasks it creates are
 * cancelled after the run, so a dev stack's content pipeline never picks them
 * up.
 */

import { test, expect } from '@playwright/test';
import { API_URL, DEV_TOKEN_AUTH, requireBackend } from './backend';

const API = API_URL;

const AUTH_HEADERS = {
  Authorization: DEV_TOKEN_AUTH,
  'Content-Type': 'application/json',
};

/** Every task this spec created, cancelled in afterAll. */
const created: string[] = [];

/** Minimal valid task body accepted by POST /api/tasks */
function makeTaskBody(suffix: string) {
  return {
    task_name: `E2E task creation test — ${suffix}`,
    topic: `Automated e2e test topic ${suffix}`,
    primary_keyword: 'e2e-testing',
    target_audience: 'QA Engineers',
    category: 'general',
  };
}

async function createTask(request: any, suffix: string) {
  const res = await request.post(`${API}/api/tasks`, {
    headers: AUTH_HEADERS,
    data: makeTaskBody(suffix),
  });
  const body = await res.json().catch(() => null);
  const taskId: string | undefined = body?.id ?? body?.task_id;
  if (taskId) created.push(taskId);
  return { res, body, taskId };
}

test.describe('Task creation', () => {
  // Fails each test when the backend is unreachable or refuses dev-token, if
  // the run names a target or runs in CI; skips it only on a bare local run.
  // Per test, not beforeAll, so no test is ever recorded as skipped (see
  // backend.ts). Nothing below skips on its own.
  test.beforeEach(async () => {
    await requireBackend({ devToken: true });
  });

  test.afterAll(async ({ request }) => {
    for (const taskId of created.splice(0)) {
      await request.delete(`${API}/api/tasks/${taskId}`, {
        headers: AUTH_HEADERS,
      });
    }
  });

  test('creates a task and returns a valid task ID', async ({ request }) => {
    const { res, body, taskId } = await createTask(request, 'create-check');

    // Some backends return 200, others 201 — accept both.
    expect([200, 201]).toContain(res.status());
    expect(body).not.toBeNull();
    expect(taskId).toBeTruthy();
    expect(typeof taskId).toBe('string');
  });

  test('created task appears in the task list', async ({ request }) => {
    const { res, taskId } = await createTask(request, 'list-check');
    expect([200, 201]).toContain(res.status());
    expect(taskId, 'POST /api/tasks returned no task ID').toBeTruthy();

    const listRes = await request.get(`${API}/api/tasks`, {
      headers: AUTH_HEADERS,
    });
    expect(listRes.ok()).toBe(true);

    const listBody = await listRes.json().catch(() => null);
    // List envelope: {items, total, limit, offset} (poindexter#745), newest first
    const tasks: any[] = listBody?.items;
    expect(Array.isArray(tasks)).toBe(true);
    expect(
      tasks.some((t: any) => t.id === taskId || t.task_id === taskId)
    ).toBe(true);
  });
});
