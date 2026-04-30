/**
 * @jest-environment node
 *
 * Tests for /api/posts route handler
 *
 * The route now reads from static JSON on R2/CDN (posts/index.json),
 * slices locally for offset/limit, and returns { items, total }.
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

// Helper: build a mock fetch response for static JSON
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

  it('returns items and total from static JSON posts array', async () => {
    global.fetch = mockFetchOk({
      posts: [{ id: '1', title: 'Post A' }],
      total: 1,
      exported_at: '2024-01-15T00:00:00Z',
    });

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.items).toHaveLength(1);
    expect(body.total).toBe(1);
  });

  it('returns empty array and zero total when static JSON has no posts', async () => {
    global.fetch = mockFetchOk({
      posts: [],
      total: 0,
      exported_at: '2024-01-15T00:00:00Z',
    });

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.items).toHaveLength(0);
    expect(body.total).toBe(0);
  });

  it('slices posts locally using offset and limit query params', async () => {
    const allPosts = Array.from({ length: 30 }, (_, i) => ({
      id: String(i),
      title: `Post ${i}`,
    }));

    global.fetch = mockFetchOk({
      posts: allPosts,
      total: 30,
      exported_at: '2024-01-15T00:00:00Z',
    });

    const req = makeRequest('http://localhost/api/posts?offset=20&limit=5');
    const res = await GET(req);
    const body = await res.json();

    expect(body.items).toHaveLength(5);
    expect(body.items[0].id).toBe('20');
    expect(body.total).toBe(30);
  });

  it('fetches from posts/index.json on R2', async () => {
    global.fetch = mockFetchOk({
      posts: [],
      total: 0,
      exported_at: '2024-01-15T00:00:00Z',
    });

    const req = makeRequest('http://localhost/api/posts');
    await GET(req);

    const calledUrl = (global.fetch as jest.Mock).mock.calls[0][0] as string;
    expect(calledUrl).toContain('/posts/index.json');
  });

  it('returns 500 and error body when static JSON is unavailable', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('Connection refused'));

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);
    const body = await res.json();

    expect(res.status).toBe(500);
    expect(body.error).toBe('Failed to fetch posts');
    expect(body.items).toEqual([]);
    expect(body.total).toBe(0);
  });

  it('returns 500 when static JSON responds with non-ok status', async () => {
    global.fetch = mockFetchError(503);

    const req = makeRequest('http://localhost/api/posts');
    const res = await GET(req);

    expect(res.status).toBe(500);
  });
});
