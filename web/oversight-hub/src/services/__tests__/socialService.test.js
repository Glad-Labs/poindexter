/**
 * socialService.test.js
 *
 * Unit tests for services/socialService.js.
 *
 * Tests cover:
 * - getPlatforms — success, response.error throws, network error propagates
 * - connectPlatform — success, response.error throws, network error propagates
 * - disconnectPlatform — success with platform name, response.error throws
 * - getPosts — success with options (status, platform, limit), no filters, response.error throws
 * - createPost — success, response.error throws
 * - updatePost — success, response.error throws
 * - deletePost — success, response.error throws
 * - getSocialAnalytics — success, default range used, response.error throws
 * - getPostAnalytics — success, response.error throws
 *
 * makeRequest is mocked; no network calls.
 */

import { vi } from 'vitest';

const { mockMakeRequest } = vi.hoisted(() => ({
  mockMakeRequest: vi.fn(),
}));

vi.mock('@/services/cofounderAgentClient', () => ({
  makeRequest: mockMakeRequest,
}));

vi.mock('@/lib/logger', () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

import {
  getPlatforms,
  connectPlatform,
  disconnectPlatform,
  getPosts,
  createPost,
  updatePost,
  deletePost,
  getSocialAnalytics,
  getPostAnalytics,
} from '../socialService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _error = (msg) => mockMakeRequest.mockResolvedValue({ error: msg });
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getPlatforms
// ---------------------------------------------------------------------------

describe('getPlatforms', () => {
  it('returns platform data on success', async () => {
    _ok({ twitter: { connected: true }, instagram: { connected: false } });
    const result = await getPlatforms();
    expect(result.twitter.connected).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Auth required');
    await expect(getPlatforms()).rejects.toThrow('Auth required');
  });

  it('propagates network errors', async () => {
    _throw('Connection refused');
    await expect(getPlatforms()).rejects.toThrow('Connection refused');
  });

  it('calls GET /api/social/platforms', async () => {
    _ok({});
    await getPlatforms();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/platforms');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// connectPlatform
// ---------------------------------------------------------------------------

describe('connectPlatform', () => {
  it('returns connection result on success', async () => {
    _ok({ connected: true, platform: 'twitter' });
    const result = await connectPlatform({ platform: 'twitter' });
    expect(result.connected).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Invalid credentials');
    await expect(connectPlatform({ platform: 'twitter' })).rejects.toThrow(
      'Invalid credentials'
    );
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(connectPlatform({ platform: 'instagram' })).rejects.toThrow(
      'Timeout'
    );
  });

  it('calls POST /api/social/connect with params', async () => {
    _ok({ connected: true });
    const params = { platform: 'twitter', credentials: { token: 'abc' } };
    await connectPlatform(params);
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/connect');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual(params);
  });
});

// ---------------------------------------------------------------------------
// disconnectPlatform
// ---------------------------------------------------------------------------

describe('disconnectPlatform', () => {
  it('returns disconnection result on success', async () => {
    _ok({ disconnected: true });
    const result = await disconnectPlatform('twitter');
    expect(result.disconnected).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Platform not connected');
    await expect(disconnectPlatform('twitter')).rejects.toThrow(
      'Platform not connected'
    );
  });

  it('sends platform name in request body', async () => {
    _ok({});
    await disconnectPlatform('instagram');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual({ platform: 'instagram' });
  });

  it('calls POST /api/social/disconnect', async () => {
    _ok({});
    await disconnectPlatform('facebook');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/disconnect');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// getPosts
// ---------------------------------------------------------------------------

describe('getPosts', () => {
  it('returns posts list on success', async () => {
    _ok({ posts: [{ id: 'p1' }, { id: 'p2' }] });
    const result = await getPosts();
    expect(result.posts).toHaveLength(2);
  });

  it('includes status filter in URL when provided', async () => {
    _ok({ posts: [] });
    await getPosts({ status: 'published' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('status=published');
  });

  it('includes platform filter in URL when provided', async () => {
    _ok({ posts: [] });
    await getPosts({ platform: 'twitter' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('platform=twitter');
  });

  it('includes limit in URL when provided', async () => {
    _ok({ posts: [] });
    await getPosts({ limit: 10 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('limit=10');
  });

  it('omits query string when no options provided', async () => {
    _ok({ posts: [] });
    await getPosts();
    // No query string appended
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/posts');
  });

  it('throws when response contains error field', async () => {
    _error('Not found');
    await expect(getPosts()).rejects.toThrow('Not found');
  });

  it('propagates network errors', async () => {
    _throw('Network error');
    await expect(getPosts({ status: 'draft' })).rejects.toThrow(
      'Network error'
    );
  });

  it('calls GET /api/social/posts', async () => {
    _ok({});
    await getPosts();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('/api/social/posts');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// createPost
// ---------------------------------------------------------------------------

describe('createPost', () => {
  it('returns created post on success', async () => {
    _ok({ id: 'post-1', status: 'scheduled' });
    const result = await createPost({
      content: 'Hello world',
      platforms: ['twitter'],
    });
    expect(result.id).toBe('post-1');
  });

  it('throws when response contains error field', async () => {
    _error('Content too long');
    await expect(createPost({ content: 'x'.repeat(1000) })).rejects.toThrow(
      'Content too long'
    );
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(createPost({})).rejects.toThrow('Timeout');
  });

  it('calls POST /api/social/posts with post data', async () => {
    _ok({ id: 'p2' });
    const postData = { content: 'Test post', platforms: ['instagram'] };
    await createPost(postData);
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/posts');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual(postData);
  });
});

// ---------------------------------------------------------------------------
// updatePost
// ---------------------------------------------------------------------------

describe('updatePost', () => {
  it('returns updated post on success', async () => {
    _ok({ id: 'post-1', content: 'Updated content' });
    const result = await updatePost('post-1', { content: 'Updated content' });
    expect(result.content).toBe('Updated content');
  });

  it('throws when response contains error field', async () => {
    _error('Post not found');
    await expect(updatePost('bad-id', {})).rejects.toThrow('Post not found');
  });

  it('calls PUT /api/social/posts/:id with updates', async () => {
    _ok({ id: 'post-7' });
    const updates = { content: 'New text' };
    await updatePost('post-7', updates);
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/posts/post-7');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('PUT');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual(updates);
  });
});

// ---------------------------------------------------------------------------
// deletePost
// ---------------------------------------------------------------------------

describe('deletePost', () => {
  it('returns deletion result on success', async () => {
    _ok({ deleted: true });
    const result = await deletePost('post-1');
    expect(result.deleted).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Cannot delete published post');
    await expect(deletePost('post-1')).rejects.toThrow(
      'Cannot delete published post'
    );
  });

  it('calls DELETE /api/social/posts/:id', async () => {
    _ok({});
    await deletePost('post-99');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/social/posts/post-99');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// getSocialAnalytics
// ---------------------------------------------------------------------------

describe('getSocialAnalytics', () => {
  it('returns analytics data on success', async () => {
    _ok({ impressions: 10000, engagements: 500 });
    const result = await getSocialAnalytics();
    expect(result.impressions).toBe(10000);
  });

  it('uses default range of 30d', async () => {
    _ok({});
    await getSocialAnalytics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=30d');
  });

  it('passes custom range in URL', async () => {
    _ok({});
    await getSocialAnalytics('7d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=7d');
  });

  it('throws when response contains error field', async () => {
    _error('Analytics unavailable');
    await expect(getSocialAnalytics()).rejects.toThrow('Analytics unavailable');
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(getSocialAnalytics('90d')).rejects.toThrow('Timeout');
  });

  it('calls GET /api/social/analytics', async () => {
    _ok({});
    await getSocialAnalytics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('/api/social/analytics');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getPostAnalytics
// ---------------------------------------------------------------------------

describe('getPostAnalytics', () => {
  it('returns post analytics on success', async () => {
    _ok({ likes: 42, shares: 7, reach: 1500 });
    const result = await getPostAnalytics('post-5');
    expect(result.likes).toBe(42);
  });

  it('throws when response contains error field', async () => {
    _error('Post not found');
    await expect(getPostAnalytics('bad-id')).rejects.toThrow('Post not found');
  });

  it('propagates network errors', async () => {
    _throw('Network failure');
    await expect(getPostAnalytics('post-1')).rejects.toThrow('Network failure');
  });

  it('calls GET /api/social/posts/:id/analytics', async () => {
    _ok({});
    await getPostAnalytics('post-12');
    expect(mockMakeRequest.mock.calls[0][0]).toBe(
      '/api/social/posts/post-12/analytics'
    );
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});
