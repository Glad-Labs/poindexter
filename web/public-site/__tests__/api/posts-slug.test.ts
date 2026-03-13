/**
 * @jest-environment node
 *
 * Tests for /api/posts/[slug] route handler
 *
 * Uses jest-environment-node so that the Web Fetch API globals
 * (Request, Response, Headers) are available via Node 18+ built-ins,
 * which NextRequest depends on.
 */

import { NextRequest } from 'next/server';
import { GET } from '../../app/api/posts/[slug]/route';

jest.mock('@/lib/logger', () => ({
  error: jest.fn(),
  log: jest.fn(),
  warn: jest.fn(),
  info: jest.fn(),
}));

function makeRequest(
  slug: string
): [NextRequest, { params: Promise<{ slug: string }> }] {
  const req = new NextRequest(`http://localhost/api/posts/${slug}`);
  const params = Promise.resolve({ slug });
  return [req, { params }];
}

describe('GET /api/posts/[slug]', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('returns post body with 200 for a valid slug', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ id: '1', slug: 'my-post', title: 'My Post' }),
    } as Response);

    const [req, ctx] = makeRequest('my-post');
    const res = await GET(req, ctx);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.slug).toBe('my-post');
  });

  it('returns 404 when backend returns 404', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
    } as Response);

    const [req, ctx] = makeRequest('unknown-slug');
    const res = await GET(req, ctx);
    const body = await res.json();

    expect(res.status).toBe(404);
    expect(body.error).toBe('Post not found');
  });

  it('returns 500 when backend returns non-ok, non-404 status', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 503,
    } as Response);

    const [req, ctx] = makeRequest('my-post');
    const res = await GET(req, ctx);

    expect(res.status).toBe(500);
  });

  it('returns 500 when fetch throws (network error)', async () => {
    global.fetch = jest
      .fn()
      .mockRejectedValue(new Error('Network unavailable'));

    const [req, ctx] = makeRequest('my-post');
    const res = await GET(req, ctx);
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body.error).toBe('Failed to fetch post');
  });

  it('URL-encodes the slug before forwarding to backend', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: '1', slug: 'hello world' }),
    } as Response);

    const [req, ctx] = makeRequest('hello world');
    await GET(req, ctx);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain('hello%20world');
  });
});
