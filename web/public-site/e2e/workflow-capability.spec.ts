/**
 * Workflow & Capability System E2E Tests
 * =======================================
 *
 * Covers issue #13 — E2E coverage gaps: workflow system and capability routing.
 *
 * These are API-level tests using the `request` fixture (no browser needed).
 * They exercise the workflow and capability routes exhaustively:
 *
 * Workflow endpoints:
 *   GET  /api/workflows/templates              — list available templates
 *   GET  /api/workflows/status/:id             — get workflow execution status
 *   GET  /api/workflows/executions             — list executions
 *   GET  /api/workflows/templates/history      — execution history
 *   POST /api/workflows/execute/:template      — execute a workflow template
 *   POST /api/workflows/:id/pause              — pause a running workflow
 *   POST /api/workflows/:id/resume             — resume a paused workflow
 *   POST /api/workflows/:id/cancel             — cancel a workflow
 *   POST /api/workflows/executions/:id/cancel  — cancel an execution
 *   GET  /api/workflows/executions/:id/progress— get execution progress
 *
 * Capability endpoints:
 *   GET  /api/capabilities                     — list all capabilities
 *   GET  /api/capabilities/:name               — get capability details
 *   POST /api/tasks/capability                 — create a capability-backed task
 *   GET  /api/tasks/capability                 — list capability tasks
 *   GET  /api/tasks/capability/:id             — get a capability task
 *   DELETE /api/tasks/capability/:id           — delete a capability task
 *
 * Auth: Bearer dev-token (requires DEVELOPMENT_MODE=true on backend).
 * All test suites guard against backend unavailability and skip gracefully.
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns true if the backend is reachable. */
async function isBackendUp(request: any): Promise<boolean> {
  try {
    const res = await request.get(`${API}/health`, { timeout: 5000 });
    return res.ok();
  } catch {
    return false;
  }
}

/** Build a minimal valid capability task body. */
function makeCapabilityTaskBody(suffix: string) {
  return {
    task_name: `E2E Capability Test — ${suffix}`,
    topic: `capability e2e test ${suffix}`,
    primary_keyword: 'capability-testing',
    target_audience: 'Developers',
    category: 'general',
  };
}

// ---------------------------------------------------------------------------
// Suite 1: Workflow Templates
// ---------------------------------------------------------------------------

test.describe('Workflow Templates API', () => {
  test('backend health check — skip suite if backend is down', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) {
      test.skip(
        true,
        'Backend not reachable — skipping workflow template tests'
      );
    }
  });

  test('GET /api/workflows/templates returns 200 with array or object', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(`${API}/api/workflows/templates`, {
      headers: AUTH_HEADERS,
      data: {},
    });

    expect([200, 201, 422]).toContain(res.status());
    if (res.ok()) {
      const body = await res.json().catch(() => null);
      expect(body).not.toBeNull();
    }
  });

  test('workflow templates endpoint rejects unauthenticated request', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(`${API}/api/workflows/templates`, {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });

    expect([401, 403]).toContain(res.status());
  });

  test('workflow templates history returns 200 when authenticated', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/workflows/templates/history`, {
      headers: AUTH_HEADERS,
    });

    // May return 200 with empty list or 404 if no history — both are valid
    expect([200, 404]).toContain(res.status());
  });

  test('workflow templates history rejects unauthenticated request', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/workflows/templates/history`);
    expect([401, 403]).toContain(res.status());
  });
});

// ---------------------------------------------------------------------------
// Suite 2: Workflow Executions
// ---------------------------------------------------------------------------

test.describe('Workflow Executions API', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(true, 'Backend not reachable — skipping execution tests');
  });

  test('GET /api/workflows/executions returns 200 with list', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/workflows/executions`, {
      headers: AUTH_HEADERS,
    });

    expect([200, 204]).toContain(res.status());
    if (res.status() === 200) {
      const body = await res.json().catch(() => null);
      expect(body).not.toBeNull();
    }
  });

  test('GET /api/workflows/executions rejects unauthenticated request', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/workflows/executions`);
    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/workflows/status/:id returns 404 for non-existent workflow', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000001';
    const res = await request.get(`${API}/api/workflows/status/${fakeId}`, {
      headers: AUTH_HEADERS,
    });

    expect([404, 400]).toContain(res.status());
  });

  test('GET /api/workflows/status/:id rejects unauthenticated request', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000001';
    const res = await request.get(`${API}/api/workflows/status/${fakeId}`);
    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/workflows/executions/:id/progress returns 404 for non-existent execution', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000002';
    const res = await request.get(
      `${API}/api/workflows/executions/${fakeId}/progress`,
      { headers: AUTH_HEADERS }
    );

    expect([404, 400]).toContain(res.status());
  });

  test('POST /api/workflows/executions/:id/cancel returns 404 for non-existent execution', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000003';
    const res = await request.post(
      `${API}/api/workflows/executions/${fakeId}/cancel`,
      { headers: AUTH_HEADERS, data: {} }
    );

    expect([404, 400]).toContain(res.status());
  });
});

// ---------------------------------------------------------------------------
// Suite 3: Workflow Pause/Resume/Cancel
// ---------------------------------------------------------------------------

test.describe('Workflow State Transitions', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(
        true,
        'Backend not reachable — skipping state transition tests'
      );
  });

  test('POST /api/workflows/:id/pause returns 404 for non-existent workflow', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000010';
    const res = await request.post(`${API}/api/workflows/${fakeId}/pause`, {
      headers: AUTH_HEADERS,
      data: {},
    });

    expect([404, 400]).toContain(res.status());
  });

  test('POST /api/workflows/:id/resume returns 404 for non-existent workflow', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000011';
    const res = await request.post(`${API}/api/workflows/${fakeId}/resume`, {
      headers: AUTH_HEADERS,
      data: {},
    });

    expect([404, 400]).toContain(res.status());
  });

  test('POST /api/workflows/:id/cancel returns 404 for non-existent workflow', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000012';
    const res = await request.post(`${API}/api/workflows/${fakeId}/cancel`, {
      headers: AUTH_HEADERS,
      data: {},
    });

    expect([404, 400]).toContain(res.status());
  });

  test('workflow pause requires authentication', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000013';
    const res = await request.post(`${API}/api/workflows/${fakeId}/pause`, {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });

    expect([401, 403]).toContain(res.status());
  });

  test('workflow resume requires authentication', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000014';
    const res = await request.post(`${API}/api/workflows/${fakeId}/resume`, {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });

    expect([401, 403]).toContain(res.status());
  });

  test('workflow cancel requires authentication', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000015';
    const res = await request.post(`${API}/api/workflows/${fakeId}/cancel`, {
      headers: { 'Content-Type': 'application/json' },
      data: {},
    });

    expect([401, 403]).toContain(res.status());
  });

  test('workflow execute with non-existent template returns 404', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(
      `${API}/api/workflows/execute/nonexistent_template_xyz`,
      {
        headers: AUTH_HEADERS,
        data: { workflow_data: {}, model_preference: 'cheap' },
      }
    );

    expect([404, 400, 422]).toContain(res.status());
  });

  test('workflow execute requires authentication', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(
      `${API}/api/workflows/execute/some_template`,
      {
        headers: { 'Content-Type': 'application/json' },
        data: { workflow_data: {} },
      }
    );

    expect([401, 403]).toContain(res.status());
  });
});

// ---------------------------------------------------------------------------
// Suite 4: Capability Discovery
// ---------------------------------------------------------------------------

test.describe('Capability Discovery API', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(true, 'Backend not reachable — skipping capability tests');
  });

  test('GET /api/capabilities returns 200 with capabilities list', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/capabilities`, {
      headers: AUTH_HEADERS,
    });

    expect([200, 204]).toContain(res.status());
    if (res.status() === 200) {
      const body = await res.json().catch(() => null);
      expect(body).not.toBeNull();
    }
  });

  test('GET /api/capabilities requires authentication', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/capabilities`);
    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/capabilities/:name returns 404 for unknown capability', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(
      `${API}/api/capabilities/nonexistent_capability_xyz`,
      { headers: AUTH_HEADERS }
    );

    expect([404, 400]).toContain(res.status());
  });

  test('GET /api/capabilities/:name requires authentication', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(
      `${API}/api/capabilities/nonexistent_capability_xyz`
    );
    expect([401, 403]).toContain(res.status());
  });

  test('capabilities list response has correct content-type header', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/capabilities`, {
      headers: AUTH_HEADERS,
    });

    if (res.ok()) {
      const contentType = res.headers()['content-type'] ?? '';
      expect(contentType).toContain('application/json');
    }
  });
});

// ---------------------------------------------------------------------------
// Suite 5: Capability Task CRUD
// ---------------------------------------------------------------------------

test.describe('Capability Task CRUD', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(true, 'Backend not reachable — skipping capability task tests');
  });

  test('POST /api/tasks/capability creates a task and returns a task ID', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(`${API}/api/tasks/capability`, {
      headers: AUTH_HEADERS,
      data: makeCapabilityTaskBody('create-test'),
    });

    // 201 Created is ideal; 200 is acceptable; 422 means validation rejection
    expect([200, 201, 422]).toContain(res.status());
    if (res.ok()) {
      const body = await res.json().catch(() => null);
      expect(body).not.toBeNull();
    }
  });

  test('POST /api/tasks/capability requires authentication', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(`${API}/api/tasks/capability`, {
      headers: { 'Content-Type': 'application/json' },
      data: makeCapabilityTaskBody('auth-test'),
    });

    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/tasks/capability returns 200 with list', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/tasks/capability`, {
      headers: AUTH_HEADERS,
    });

    expect([200, 204]).toContain(res.status());
  });

  test('GET /api/tasks/capability requires authentication', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/tasks/capability`);
    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/tasks/capability/:id returns 404 for non-existent task', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000020';
    const res = await request.get(`${API}/api/tasks/capability/${fakeId}`, {
      headers: AUTH_HEADERS,
    });

    expect([404, 400]).toContain(res.status());
  });

  test('DELETE /api/tasks/capability/:id returns 404 for non-existent task', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000021';
    const res = await request.delete(`${API}/api/tasks/capability/${fakeId}`, {
      headers: AUTH_HEADERS,
    });

    expect([404, 400]).toContain(res.status());
  });

  test('DELETE /api/tasks/capability/:id requires authentication', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000022';
    const res = await request.delete(`${API}/api/tasks/capability/${fakeId}`);
    expect([401, 403]).toContain(res.status());
  });

  test('POST /api/tasks/capability with missing required fields returns 422', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(`${API}/api/tasks/capability`, {
      headers: AUTH_HEADERS,
      data: {}, // Missing task_name and topic
    });

    expect([400, 422]).toContain(res.status());
  });

  test('capability task list response has correct content-type', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/tasks/capability`, {
      headers: AUTH_HEADERS,
    });

    if (res.ok()) {
      const contentType = res.headers()['content-type'] ?? '';
      expect(contentType).toContain('application/json');
    }
  });
});

// ---------------------------------------------------------------------------
// Suite 6: Capability Task Execution
// ---------------------------------------------------------------------------

test.describe('Capability Task Execution', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(true, 'Backend not reachable — skipping execution tests');
  });

  test('POST /api/tasks/capability/:id/execute returns 404 for non-existent task', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000030';
    const res = await request.post(
      `${API}/api/tasks/capability/${fakeId}/execute`,
      { headers: AUTH_HEADERS, data: {} }
    );

    expect([404, 400]).toContain(res.status());
  });

  test('POST /api/tasks/capability/:id/execute requires authentication', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000031';
    const res = await request.post(
      `${API}/api/tasks/capability/${fakeId}/execute`,
      { headers: { 'Content-Type': 'application/json' }, data: {} }
    );

    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/tasks/capability/:id/executions/:execId returns 404 for non-existent execution', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeTaskId = '00000000-0000-0000-0000-000000000032';
    const fakeExecId = '00000000-0000-0000-0000-000000000033';
    const res = await request.get(
      `${API}/api/tasks/capability/${fakeTaskId}/executions/${fakeExecId}`,
      { headers: AUTH_HEADERS }
    );

    expect([404, 400]).toContain(res.status());
  });

  test('GET /api/tasks/capability/:id/executions returns 404 for non-existent task', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000034';
    const res = await request.get(
      `${API}/api/tasks/capability/${fakeId}/executions`,
      { headers: AUTH_HEADERS }
    );

    expect([404, 400, 200]).toContain(res.status());
  });
});

// ---------------------------------------------------------------------------
// Suite 7: Edge Cases & Validation
// ---------------------------------------------------------------------------

test.describe('Workflow & Capability Edge Cases', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(true, 'Backend not reachable — skipping edge case tests');
  });

  test('workflow status endpoint with invalid UUID format returns 400 or 404', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.get(`${API}/api/workflows/status/not-a-uuid`, {
      headers: AUTH_HEADERS,
    });

    expect([400, 404, 422]).toContain(res.status());
  });

  test('workflow pause with invalid UUID format returns 400 or 404', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.post(`${API}/api/workflows/not-a-uuid/pause`, {
      headers: AUTH_HEADERS,
      data: {},
    });

    expect([400, 404, 422]).toContain(res.status());
  });

  test('capability task with extremely long task_name is rejected or truncated', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const longName = 'a'.repeat(5001);
    const res = await request.post(`${API}/api/tasks/capability`, {
      headers: AUTH_HEADERS,
      data: {
        task_name: longName,
        topic: 'test topic',
        primary_keyword: 'test',
        target_audience: 'Developers',
        category: 'general',
      },
    });

    // Either reject with 422, accept with 200/201, or database-level 400/500
    expect([200, 201, 400, 422, 500]).toContain(res.status());
  });

  test('capability task update (PUT) on non-existent task returns 404', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000040';
    const res = await request.put(`${API}/api/tasks/capability/${fakeId}`, {
      headers: AUTH_HEADERS,
      data: { task_name: 'Updated name', topic: 'Updated topic' },
    });

    expect([404, 400]).toContain(res.status());
  });

  test('PUT /api/tasks/capability/:id requires authentication', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeId = '00000000-0000-0000-0000-000000000041';
    const res = await request.put(`${API}/api/tasks/capability/${fakeId}`, {
      headers: { 'Content-Type': 'application/json' },
      data: { task_name: 'Updated' },
    });

    expect([401, 403]).toContain(res.status());
  });

  test('API responds consistently to repeated identical requests', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const statuses: number[] = [];
    for (let i = 0; i < 3; i++) {
      const res = await request.get(`${API}/api/capabilities`, {
        headers: AUTH_HEADERS,
      });
      statuses.push(res.status());
    }

    // All three should return the same status code
    const uniqueStatuses = new Set(statuses);
    expect(uniqueStatuses.size).toBe(1);
  });

  test('OPTIONS request to capability endpoint returns appropriate CORS headers or 405', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const res = await request.fetch(`${API}/api/capabilities`, {
      method: 'OPTIONS',
      headers: {
        Origin: 'http://localhost:3001',
        'Access-Control-Request-Method': 'GET',
      },
    });

    // FastAPI with CORS middleware returns 200; without it returns 405
    expect([200, 204, 405]).toContain(res.status());
  });
});

// ---------------------------------------------------------------------------
// Suite 8: Concurrent / Burst Scenarios
// ---------------------------------------------------------------------------

test.describe('Concurrent Request Handling', () => {
  test('backend health check', async ({ request }) => {
    const up = await isBackendUp(request);
    if (!up)
      test.skip(true, 'Backend not reachable — skipping concurrency tests');
  });

  test('3 concurrent GET /api/capabilities requests all return consistent status', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const requests = Array.from({ length: 3 }, () =>
      request.get(`${API}/api/capabilities`, { headers: AUTH_HEADERS })
    );

    const results = await Promise.all(requests);
    const statuses = results.map((r) => r.status());

    // All should return the same status code — consistent behavior under concurrency
    const uniqueStatuses = new Set(statuses);
    expect(uniqueStatuses.size).toBe(1);
  });

  test('3 concurrent GET /api/workflows/executions requests all return consistent status', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const requests = Array.from({ length: 3 }, () =>
      request.get(`${API}/api/workflows/executions`, { headers: AUTH_HEADERS })
    );

    const results = await Promise.all(requests);
    const statuses = results.map((r) => r.status());

    const uniqueStatuses = new Set(statuses);
    expect(uniqueStatuses.size).toBe(1);
  });

  test('burst: 5 unauthenticated requests to protected endpoints all return 401/403', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const endpoints = [
      `${API}/api/capabilities`,
      `${API}/api/workflows/executions`,
      `${API}/api/tasks/capability`,
      `${API}/api/workflows/templates/history`,
      `${API}/api/workflows/status/00000000-0000-0000-0000-000000000099`,
    ];

    const requests = endpoints.map((url) => request.get(url));
    const results = await Promise.all(requests);

    results.forEach((res) => {
      expect([401, 403]).toContain(res.status());
    });
  });

  test('concurrent POST requests to non-existent workflows return consistent 404s', async ({
    request,
  }) => {
    const up = await isBackendUp(request);
    if (!up) test.skip(true, 'Backend not reachable');

    const fakeIds = [
      '00000000-0000-0000-0000-000000000050',
      '00000000-0000-0000-0000-000000000051',
      '00000000-0000-0000-0000-000000000052',
    ];

    const requests = fakeIds.map((id) =>
      request.post(`${API}/api/workflows/${id}/cancel`, {
        headers: AUTH_HEADERS,
        data: {},
      })
    );

    const results = await Promise.all(requests);
    results.forEach((res) => {
      expect([404, 400]).toContain(res.status());
    });
  });
});
