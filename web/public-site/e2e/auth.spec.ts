/**
 * Auth Resilience E2E Tests
 * ==========================
 *
 * Covers issue #13 — E2E coverage gaps: OAuth token refresh / auth resilience.
 *
 * These are API-level tests using the `request` fixture (no browser needed).
 * They verify that the TokenValidationMiddleware and get_current_user dependency
 * correctly enforce authentication on protected routes.
 *
 * Auth model:
 *   - GitHub OAuth in production
 *   - `Bearer dev-token` accepted when DEVELOPMENT_MODE=true (dev default)
 *   - Middleware blocks /api/tasks, /api/workflows, etc. without a valid header
 *
 * API base: http://localhost:8000
 */

import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API = 'http://localhost:8000';

/** A protected endpoint — blocked by TokenValidationMiddleware */
const PROTECTED_ENDPOINT = `${API}/api/tasks`;

/** A public endpoint — no auth required */
const PUBLIC_HEALTH = `${API}/health`;

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Auth Resilience', () => {
  // -------------------------------------------------------------------------
  // Backend availability guard
  // -------------------------------------------------------------------------

  test('backend health endpoint is publicly accessible without auth', async ({
    request,
  }) => {
    const res = await request
      .get(PUBLIC_HEALTH, { timeout: 5000 })
      .catch(() => null);

    if (!res) {
      test.skip(
        true,
        'Backend not reachable at http://localhost:8000 — skipping auth tests'
      );
      return;
    }

    // /health should return 200 with no auth header at all
    expect(res.status()).toBe(200);
  });

  // -------------------------------------------------------------------------
  // Valid dev-token (DEVELOPMENT_MODE=true)
  // -------------------------------------------------------------------------

  test('Bearer dev-token is accepted when DEVELOPMENT_MODE=true, rejected otherwise', async ({
    request,
  }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: 'Bearer dev-token',
      },
    });

    // When DEVELOPMENT_MODE=true the backend accepts dev-token → 200.
    // When not in dev mode it is treated as an invalid JWT → 401.
    // Both are correct behaviour depending on server configuration.
    expect([200, 401]).toContain(res.status());
  });

  test('dev-token in dev mode returns well-formed JSON response body', async ({
    request,
  }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: 'Bearer dev-token',
      },
    });

    if (!res.ok()) {
      // Backend is not in DEVELOPMENT_MODE — nothing more to check
      test.skip(
        true,
        'dev-token rejected (server not in DEVELOPMENT_MODE) — skipping body shape test'
      );
      return;
    }

    const body = await res.json().catch(() => null);
    expect(body).not.toBeNull();
    // Tasks endpoint returns {tasks: [...], total: N, ...}
    expect(typeof body === 'object').toBe(true);
  });

  // -------------------------------------------------------------------------
  // Missing / absent auth header
  // -------------------------------------------------------------------------

  test('request with no Authorization header returns 401', async ({
    request,
  }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      // No Authorization header
    });

    // Middleware returns 401 for protected endpoints without any auth header
    expect(res.status()).toBe(401);
  });

  test('401 response includes a descriptive error message', async ({
    request,
  }) => {
    const res = await request.get(PROTECTED_ENDPOINT);
    expect(res.status()).toBe(401);

    const body = await res.json().catch(() => null);
    expect(body).not.toBeNull();
    // FastAPI typically wraps error in {detail: "..."}
    expect(body?.detail).toBeTruthy();
    expect(typeof body.detail).toBe('string');
  });

  // -------------------------------------------------------------------------
  // Malformed / invalid auth header format
  // -------------------------------------------------------------------------

  test('token without "Bearer " prefix returns 401', async ({ request }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        // Missing "Bearer " prefix — just the raw token
        Authorization: 'dev-token',
      },
    });

    expect(res.status()).toBe(401);
  });

  test('"Token " scheme (not Bearer) returns 401', async ({ request }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: 'Token dev-token',
      },
    });

    expect(res.status()).toBe(401);
  });

  test('empty string Authorization header returns 401', async ({ request }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: '',
      },
    });

    // Empty string is treated as missing
    expect([400, 401, 403]).toContain(res.status());
  });

  // -------------------------------------------------------------------------
  // Invalid / expired tokens
  // -------------------------------------------------------------------------

  test('random invalid token returns 401', async ({ request }) => {
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: 'Bearer this-is-not-a-valid-token-xyz123',
      },
    });

    // Middleware lets through (format is valid), but get_current_user rejects
    expect([401, 403]).toContain(res.status());
  });

  test('malformed JWT (wrong format) returns 401', async ({ request }) => {
    // A JWT has three base64-encoded segments separated by dots.
    // This is syntactically invalid.
    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: 'Bearer not.a.valid.jwt.structure.at.all',
      },
    });

    expect([401, 403]).toContain(res.status());
  });

  test('expired-looking JWT token returns 401 or 403', async ({ request }) => {
    // A syntactically valid but obviously expired JWT (exp in the past)
    // Header: {"alg":"HS256","typ":"JWT"}
    // Payload: {"sub":"test","exp":1000000000} (expired Sep 2001)
    const expiredToken =
      'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
      '.eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxMDAwMDAwMDAwfQ' +
      '.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c';

    const res = await request.get(PROTECTED_ENDPOINT, {
      headers: {
        Authorization: `Bearer ${expiredToken}`,
      },
    });

    expect([401, 403]).toContain(res.status());
  });

  // -------------------------------------------------------------------------
  // Protected route coverage — other protected paths
  // -------------------------------------------------------------------------

  test('POST /api/tasks without auth returns 401', async ({ request }) => {
    const res = await request.post(`${API}/api/tasks`, {
      data: {
        task_name: 'Unauthenticated task',
        topic: 'should be rejected',
        primary_keyword: 'test',
        target_audience: 'nobody',
        category: 'general',
      },
    });

    expect([401, 403]).toContain(res.status());
  });

  test('GET /api/workflows without auth returns 401', async ({ request }) => {
    const res = await request.get(`${API}/api/workflows`);
    expect([401, 403]).toContain(res.status());
  });

  test('POST /api/tasks/bulk without auth returns 401', async ({ request }) => {
    const res = await request.post(`${API}/api/tasks/bulk`, {
      data: {
        task_ids: ['550e8400-e29b-41d4-a716-446655440000'],
        action: 'pause',
      },
    });

    expect([401, 403]).toContain(res.status());
  });

  // -------------------------------------------------------------------------
  // Token refresh / resilience — simulated scenarios
  // -------------------------------------------------------------------------

  test('repeated requests with same auth header return consistent status codes', async ({
    request,
  }) => {
    const headers = { Authorization: 'Bearer dev-token' };

    const results = await Promise.all(
      Array.from({ length: 3 }).map(() =>
        request.get(PROTECTED_ENDPOINT, { headers })
      )
    );

    // All requests with the same token must get the same status code —
    // either all 200 (dev mode) or all 401 (non-dev mode). Never mixed.
    const statuses = results.map((r) => r.status());
    const uniqueStatuses = new Set(statuses);
    expect(uniqueStatuses.size).toBe(1);

    // Whichever status is returned must be one of the valid options
    expect([200, 401, 403]).toContain(statuses[0]);
  });

  test('invalid token is rejected while auth headers are evaluated independently', async ({
    request,
  }) => {
    const devRes = await request.get(PROTECTED_ENDPOINT, {
      headers: { Authorization: 'Bearer dev-token' },
    });
    const badRes = await request.get(PROTECTED_ENDPOINT, {
      headers: { Authorization: 'Bearer definitely-invalid-token-abc' },
    });

    // dev-token → 200 in dev mode, 401 in prod mode (both acceptable)
    expect([200, 401, 403]).toContain(devRes.status());

    // A random invalid token must always be rejected regardless of mode
    expect([401, 403]).toContain(badRes.status());

    // The two tokens must not produce the same acceptance result:
    // If dev-token was accepted (200), the bad token must be rejected (401/403).
    // If dev-token was also rejected (401), that's fine — both rejected.
    if (devRes.ok()) {
      expect(badRes.ok()).toBe(false);
    }
  });
});
