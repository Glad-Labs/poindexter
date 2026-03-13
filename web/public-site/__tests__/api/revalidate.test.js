/**
 * @jest-environment node
 *
 * Tests for /api/revalidate route handler
 *
 * Verifies secret-based auth and path revalidation logic.
 * Mocks next/cache so no real ISR operations occur.
 *
 * The revalidate route uses the native Request/Headers Web API.
 * Jest jsdom does not provide these globals, so we build a lightweight
 * mock that satisfies the handler's usage:
 *   - request.headers.get(name)
 *   - await request.json()
 */

import { POST } from '../../app/api/revalidate/route';

// Mock next/cache before importing the handler
jest.mock('next/cache', () => ({
  revalidatePath: jest.fn(),
}));

jest.mock('@/lib/logger', () => ({
  error: jest.fn(),
  log: jest.fn(),
  warn: jest.fn(),
  info: jest.fn(),
}));

const { revalidatePath } = require('next/cache');

// Set a deterministic secret for all tests
process.env.REVALIDATE_SECRET = 'test-secret-123';

/** Minimal Request mock used only within this test file */
function makeRequest({ secret, body } = {}) {
  const headersMap = { 'content-type': 'application/json' };
  if (secret !== undefined) {
    headersMap['x-revalidate-secret'] = secret;
  }
  return {
    headers: {
      get: (name) => headersMap[name.toLowerCase()] ?? null,
    },
    json: () => Promise.resolve(body ?? {}),
  };
}

describe('POST /api/revalidate', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('authorization', () => {
    it('returns 401 when no secret header is provided', async () => {
      const req = makeRequest({}); // no secret property → header returns null
      const res = await POST(req);
      expect(res.status).toBe(401);
    });

    it('returns 401 when secret is wrong', async () => {
      const req = makeRequest({ secret: 'wrong-secret' });
      const res = await POST(req);
      expect(res.status).toBe(401);
    });

    it('does not call revalidatePath when auth fails', async () => {
      const req = makeRequest({ secret: 'wrong-secret' });
      await POST(req);
      expect(revalidatePath).not.toHaveBeenCalled();
    });
  });

  describe('successful revalidation', () => {
    it('returns 200 with success=true for valid secret', async () => {
      const req = makeRequest({ secret: 'test-secret-123' });
      const res = await POST(req);
      const body = await res.json();

      expect(res.status).toBe(200);
      expect(body.success).toBe(true);
    });

    it('revalidates default paths when no paths provided', async () => {
      const req = makeRequest({ secret: 'test-secret-123', body: {} });
      await POST(req);

      // Should revalidate /, /archive, /posts
      expect(revalidatePath).toHaveBeenCalledWith('/', 'page');
      expect(revalidatePath).toHaveBeenCalledWith('/archive', 'page');
      expect(revalidatePath).toHaveBeenCalledWith('/posts', 'page');
    });

    it('revalidates only provided paths when paths array is given', async () => {
      const req = makeRequest({
        secret: 'test-secret-123',
        body: { paths: ['/blog/my-post'] },
      });
      await POST(req);

      expect(revalidatePath).toHaveBeenCalledWith('/blog/my-post', 'page');
      expect(revalidatePath).toHaveBeenCalledTimes(1);
    });

    it('returns the revalidated paths in response body', async () => {
      const req = makeRequest({
        secret: 'test-secret-123',
        body: { paths: ['/custom-page'] },
      });
      const res = await POST(req);
      const body = await res.json();

      expect(body.paths).toContain('/custom-page');
    });

    it('response includes message with path count', async () => {
      const req = makeRequest({
        secret: 'test-secret-123',
        body: { paths: ['/a', '/b'] },
      });
      const res = await POST(req);
      const body = await res.json();

      expect(body.message).toContain('2');
    });
  });

  describe('error handling', () => {
    it('returns 500 when revalidatePath throws', async () => {
      revalidatePath.mockRejectedValue(new Error('Cache error'));

      const req = makeRequest({ secret: 'test-secret-123' });
      const res = await POST(req);

      expect(res.status).toBe(500);
    });
  });
});
