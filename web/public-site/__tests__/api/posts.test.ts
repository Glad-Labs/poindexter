/**
 * @jest-environment node
 *
 * Tests for /api/posts route handler
 *
 * Uses jest-environment-node so that the Web Fetch API globals
 * (Request, Response, Headers) are available via Node 18+ built-ins,
 * which NextRequest depends on.
 *
 * Mocks global.fetch so no real HTTP calls are made.
 */

import { NextRequest } from 'next/server';
import { GET } from '../../app/api/posts/route';

// Mock the logger so test output is not polluted
jest.mock('@/lib/logger', () => ({
  error: jest.fn(),
  log: jest.fn(),
  warn: jest.fn(),
  info: jest.fn(),
}));

// Helper: build a NextRequest from a URL string
function makeRequest(url: string): NextRequest {
  return new NextRequest(url);
}

// Helper: build a mock fetch response
function mockFetchOk(data: object): jest.MockedFunction<typeof fetch> {
  return jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  } as Response);
}

function mockFetchError(status: number): jest.MockedFunction<typeof fetch> {
  return jest.fn().mockResolvedValue({
    ok: false,
    status,
    json: () => Promise.resolve({ detail: 'error' }),
  } as Response);
}

describe('GET /api/posts', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('returns items and total from backend posts array', async () => {
    global.fetch = mockFetchOk({
      posts: [{ id: '1', title: 'Post A' }],
      total: 1,
    });

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.items).toHaveLength(1);
    expect(body.total).toBe(1);
  });

  it('accepts data.data field as items fallback', async () => {
    global.fetch = mockFetchOk({
      data: [{ id: '2', title: 'Post B' }],
      total: 1,
    });

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(body.items).toHaveLength(1);
  });

  it('returns empty array and zero total when backend returns no posts', async () => {
    global.fetch = mockFetchOk({ posts: [], total: 0 });

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.items).toHaveLength(0);
    expect(body.total).toBe(0);
  });

  it('passes offset and limit query params to backend', async () => {
    global.fetch = mockFetchOk({ posts: [], total: 0 });

    const req = makeRequest('http://localhost/api/posts?offset=20&limit=5');
    await GET(req);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain('offset=20');
    expect(calledUrl).toContain('limit=5');
  });

  it('returns 500 and error body when backend is unavailable', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('Connection refused'));

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body.error).toBe('Failed to fetch posts');
    expect(body.items).toEqual([]);
    expect(body.total).toBe(0);
  });

  it('returns 500 when backend responds with non-ok status', async () => {
    global.fetch = mockFetchError(503);

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);

    expect(res.status).toBe(500);
  });

  it('passes published_only=true for status=published (default)', async () => {
    global.fetch = mockFetchOk({ posts: [], total: 0 });

    const req = makeRequest('http://localhost/api/posts');
    await GET(req);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain('published_only=true');
  });

  it('passes published_only=false for status=draft', async () => {
    global.fetch = mockFetchOk({ posts: [], total: 0 });

    const req = makeRequest('http://localhost/api/posts?status=draft');
    await GET(req);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain('published_only=false');
  });
});
