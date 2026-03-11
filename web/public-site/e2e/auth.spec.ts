/**
 * Authentication E2E Tests (#13)
 * ================================
 *
 * Covers auth gaps from issue #13:
 * - Dev token acceptance by TokenValidationMiddleware
 * - Invalid / expired token rejection (401)
 * - Missing token rejection (401/403)
 * - Token refresh simulation (401 → retry with fresh token)
 * - Middleware bypasses for public endpoints
 *
 * Tests hit the real backend at http://localhost:8000.
 * No browser UI needed — these are API-contract tests.
 */

import { test, expect } from './fixtures';

const BACKEND = 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Dev token authentication
// ---------------------------------------------------------------------------

test.describe('Dev token authentication', () => {
  test('dev-token is accepted by protected endpoints', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: 'Bearer dev-token' },
    });

    // 200 success — dev-token is valid in dev mode
    expect(resp.status()).toBe(200);
  });

  test('dev-token works for POST endpoints', async ({ page }) => {
    const resp = await page.request.post(`${BACKEND}/api/tasks`, {
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer dev-token',
      },
      data: {
        task_name: 'Auth Test Task',
        topic: 'Authentication testing for enterprise API security',
      },
    });

    // 202 or 201 — authenticated successfully
    expect([201, 202]).toContain(resp.status());
  });

  test('health endpoint is accessible without token', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/health`);
    // Public endpoint should not require auth
    expect(resp.status()).toBe(200);
  });
});

// ---------------------------------------------------------------------------
// Invalid / expired token rejection
// ---------------------------------------------------------------------------

test.describe('Invalid token rejection', () => {
  test('completely invalid token returns 401 or 403', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: 'Bearer this-is-not-a-valid-token-xyz' },
    });

    expect([401, 403]).toContain(resp.status());
  });

  test('malformed Authorization header returns 401 or 403', async ({
    page,
  }) => {
    const resp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: 'NotBearer abc123' },
    });

    expect([401, 403]).toContain(resp.status());
  });

  test('empty bearer token returns 401 or 403', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: 'Bearer ' },
    });

    expect([401, 403]).toContain(resp.status());
  });

  test('expired-looking JWT returns 401 or 403', async ({ page }) => {
    // A structurally valid JWT but with an expired claim
    // Header: {"alg":"HS256","typ":"JWT"}
    // Payload: {"sub":"test","exp":1000000000} (expired in 2001)
    const expiredJwt =
      'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.' +
      'eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxMDAwMDAwMDAwfQ.' +
      'invalid_signature_here';

    const resp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: `Bearer ${expiredJwt}` },
    });

    expect([401, 403]).toContain(resp.status());
  });
});

// ---------------------------------------------------------------------------
// Missing token
// ---------------------------------------------------------------------------

test.describe('Missing token rejection', () => {
  test('missing Authorization header returns 401 or 403', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/api/tasks`);
    expect([401, 403]).toContain(resp.status());
  });

  test('missing token on POST endpoint returns 401 or 403', async ({
    page,
  }) => {
    const resp = await page.request.post(`${BACKEND}/api/tasks`, {
      headers: { 'Content-Type': 'application/json' },
      data: { task_name: 'No auth task', topic: 'Should be rejected' },
    });

    expect([401, 403]).toContain(resp.status());
  });

  test('error response for missing token has JSON body', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/api/tasks`);

    if ([401, 403].includes(resp.status())) {
      const contentType = resp.headers()['content-type'] ?? '';
      // Should return JSON error detail
      expect(contentType).toContain('application/json');
      const body = await resp.json().catch(() => null);
      expect(body).not.toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// Token refresh simulation
// ---------------------------------------------------------------------------

test.describe('Token refresh flow', () => {
  test('401 response triggers re-authentication (simulated)', async ({
    page,
  }) => {
    // Simulate the scenario: first request fails with 401 (expired token),
    // then retry with a fresh/valid token succeeds.
    // In the real app this is handled by the auth middleware + refresh logic.

    // Step 1: Request with invalid token → 401
    const firstResp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: 'Bearer expired-token-simulation' },
    });
    expect([401, 403]).toContain(firstResp.status());

    // Step 2: Retry with valid dev token → 200
    const retryResp = await page.request.get(`${BACKEND}/api/tasks`, {
      headers: { Authorization: 'Bearer dev-token' },
    });
    expect(retryResp.status()).toBe(200);
  });

  test('OAuth token endpoint responds (if configured)', async ({ page }) => {
    // Check if the OAuth token exchange endpoint exists
    const resp = await page.request.get(`${BACKEND}/auth/token`, {
      headers: { Authorization: 'Bearer dev-token' },
    });

    // Either exists (200/404 with body) or not implemented (404/405)
    // Just verify it doesn't crash the server
    expect(resp.status()).toBeLessThan(500);
  });

  test('auth/me endpoint returns user info with valid token', async ({
    page,
  }) => {
    // Many systems expose /auth/me or /api/auth/me
    const endpoints = ['/auth/me', '/api/auth/me', '/api/users/me'];

    let found = false;
    for (const endpoint of endpoints) {
      const resp = await page.request.get(`${BACKEND}${endpoint}`, {
        headers: { Authorization: 'Bearer dev-token' },
      });

      if (resp.status() === 200) {
        const body = await resp.json();
        expect(body).toBeTruthy();
        found = true;
        break;
      }
    }

    // If none of the common user-info endpoints exist, that's acceptable
    if (!found) {
      test.skip();
    }
  });
});

// ---------------------------------------------------------------------------
// Public endpoints (no auth required)
// ---------------------------------------------------------------------------

test.describe('Public endpoints bypass auth', () => {
  test('GET /health is public', async ({ page }) => {
    const resp = await page.request.get(`${BACKEND}/health`);
    expect(resp.status()).toBe(200);
  });

  test('GET /docs or /openapi.json is accessible', async ({ page }) => {
    // FastAPI exposes these by default
    const docsResp = await page.request.get(`${BACKEND}/openapi.json`);
    // Should be 200 (public API docs)
    expect(docsResp.status()).toBe(200);
  });

  test('protected endpoint blocked without auth but docs are public', async ({
    page,
  }) => {
    const [tasksResp, docsResp] = await Promise.all([
      page.request.get(`${BACKEND}/api/tasks`),
      page.request.get(`${BACKEND}/openapi.json`),
    ]);

    expect([401, 403]).toContain(tasksResp.status());
    expect(docsResp.status()).toBe(200);
  });
});
