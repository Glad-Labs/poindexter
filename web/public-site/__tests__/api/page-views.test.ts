/**
 * @jest-environment node
 *
 * Tests for /api/page-views route handler — the same-origin proxy that
 * forwards beacon hits to the backend's /api/track/view. This file
 * pins the contract that fixes the silent-since-2026-04-09 regression.
 *
 * Mocks global.fetch so no real HTTP calls are made to the backend.
 */

import { NextRequest } from 'next/server';
import { POST } from '../../app/api/page-views/route';

function makeRequest(body: unknown, headers?: Record<string, string>): NextRequest {
  return new NextRequest('http://localhost/api/page-views', {
    method: 'POST',
    body: typeof body === 'string' ? body : JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(headers || {}),
    },
  });
}

describe('POST /api/page-views', () => {
  let fetchMock: jest.SpyInstance;

  beforeEach(() => {
    fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 204,
      text: () => Promise.resolve(''),
    } as Response);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('returns 204 on a valid beacon payload', async () => {
    const req = makeRequest({
      path: '/posts/my-slug',
      slug: 'my-slug',
      referrer: 'https://google.com',
    });

    const res = await POST(req);

    expect(res.status).toBe(204);
  });

  it('forwards the payload to the backend /api/track/view', async () => {
    const req = makeRequest({
      path: '/posts/x',
      slug: 'x',
      referrer: 'https://example.com',
    });

    await POST(req);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/track\/view$/);
    expect((init as RequestInit).method).toBe('POST');
    const forwarded = JSON.parse((init as RequestInit).body as string);
    expect(forwarded).toEqual({
      path: '/posts/x',
      slug: 'x',
      referrer: 'https://example.com',
    });
  });

  it('forwards the original User-Agent header', async () => {
    const req = makeRequest(
      { path: '/posts/x', slug: 'x' },
      { 'User-Agent': 'Mozilla/5.0 (PageViewsBeacon)' }
    );

    await POST(req);

    const [, init] = fetchMock.mock.calls[0];
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers['User-Agent']).toBe('Mozilla/5.0 (PageViewsBeacon)');
  });

  it('returns 204 even when the backend is unreachable', async () => {
    fetchMock.mockRejectedValueOnce(new Error('ECONNREFUSED'));

    const req = makeRequest({ path: '/posts/x', slug: 'x' });

    const res = await POST(req);

    expect(res.status).toBe(204);
  });

  it('returns 204 when the backend returns 5xx', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      text: () => Promise.resolve(''),
    } as Response);

    const req = makeRequest({ path: '/posts/x', slug: 'x' });

    const res = await POST(req);

    // Beacon is fire-and-forget — backend failure must not surface to the reader
    expect(res.status).toBe(204);
  });

  it('handles malformed JSON gracefully', async () => {
    const req = new NextRequest('http://localhost/api/page-views', {
      method: 'POST',
      body: 'not-valid-json',
      headers: { 'Content-Type': 'application/json' },
    });

    const res = await POST(req);

    expect(res.status).toBe(204);
    // Still forwards (with empty fields) so the backend sees the hit count
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('normalizes missing payload fields to empty strings', async () => {
    const req = makeRequest({ path: '/posts/no-slug' });

    await POST(req);

    const [, init] = fetchMock.mock.calls[0];
    const forwarded = JSON.parse((init as RequestInit).body as string);
    expect(forwarded.path).toBe('/posts/no-slug');
    expect(forwarded.slug).toBe('');
    expect(forwarded.referrer).toBe('');
  });
});
