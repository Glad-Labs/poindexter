/**
 * mediaService.test.js
 *
 * Unit tests for services/mediaService.js.
 *
 * Tests cover:
 * - generateImages — success, response.error throws, network error propagates
 * - listMedia — success, options (limit/offset/type), no options, response.error throws
 * - getMediaHealth — success, response.error throws
 * - deleteMedia — success, response.error throws
 * - getMediaMetrics — success, default range, custom range, response.error throws
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
  generateImages,
  listMedia,
  getMediaHealth,
  deleteMedia,
  getMediaMetrics,
} from '../mediaService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _error = (msg) => mockMakeRequest.mockResolvedValue({ error: msg });
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// generateImages
// ---------------------------------------------------------------------------

describe('generateImages', () => {
  it('returns image data on success', async () => {
    _ok({ url: 'https://example.com/image.jpg', source: 'pexels' });
    const result = await generateImages({
      prompt: 'Tech industry',
      use_pexels: true,
    });
    expect(result.url).toBe('https://example.com/image.jpg');
  });

  it('throws when response contains error field', async () => {
    _error('Image generation failed');
    await expect(generateImages({ prompt: 'Test' })).rejects.toThrow(
      'Image generation failed'
    );
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(generateImages({ prompt: 'Test' })).rejects.toThrow('Timeout');
  });

  it('calls POST /api/media/generate-image with params', async () => {
    _ok({ url: 'https://example.com/img.jpg' });
    const params = { prompt: 'AI future', use_pexels: true };
    await generateImages(params);
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/media/generate-image');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('POST');
    expect(mockMakeRequest.mock.calls[0][2]).toEqual(params);
  });
});

// ---------------------------------------------------------------------------
// listMedia
// ---------------------------------------------------------------------------

describe('listMedia', () => {
  it('returns media list on success', async () => {
    _ok({ items: [{ id: 'm1' }, { id: 'm2' }], total: 2 });
    const result = await listMedia();
    expect(result.total).toBe(2);
  });

  it('includes limit in URL when provided', async () => {
    _ok({});
    await listMedia({ limit: 25 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('limit=25');
  });

  it('includes offset in URL when provided', async () => {
    _ok({});
    await listMedia({ offset: 10 });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('offset=10');
  });

  it('includes type filter in URL when provided', async () => {
    _ok({});
    await listMedia({ type: 'generated' });
    expect(mockMakeRequest.mock.calls[0][0]).toContain('type=generated');
  });

  it('omits query string when no options provided', async () => {
    _ok({});
    await listMedia();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/media/list');
  });

  it('throws when response contains error field', async () => {
    _error('Unauthorized');
    await expect(listMedia()).rejects.toThrow('Unauthorized');
  });

  it('propagates network errors', async () => {
    _throw('Connection refused');
    await expect(listMedia({ type: 'uploaded' })).rejects.toThrow(
      'Connection refused'
    );
  });

  it('calls GET /api/media/list', async () => {
    _ok({});
    await listMedia();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('/api/media/list');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// getMediaHealth
// ---------------------------------------------------------------------------

describe('getMediaHealth', () => {
  it('returns health status on success', async () => {
    _ok({ status: 'healthy', pexels_connected: true });
    const result = await getMediaHealth();
    expect(result.status).toBe('healthy');
  });

  it('throws when response contains error field', async () => {
    _error('Service unavailable');
    await expect(getMediaHealth()).rejects.toThrow('Service unavailable');
  });

  it('propagates network errors', async () => {
    _throw('Network error');
    await expect(getMediaHealth()).rejects.toThrow('Network error');
  });

  it('calls GET /api/media/health', async () => {
    _ok({});
    await getMediaHealth();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/media/health');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});

// ---------------------------------------------------------------------------
// deleteMedia
// ---------------------------------------------------------------------------

describe('deleteMedia', () => {
  it('returns deletion result on success', async () => {
    _ok({ deleted: true, id: 'media-1' });
    const result = await deleteMedia('media-1');
    expect(result.deleted).toBe(true);
  });

  it('throws when response contains error field', async () => {
    _error('Media not found');
    await expect(deleteMedia('bad-id')).rejects.toThrow('Media not found');
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(deleteMedia('media-1')).rejects.toThrow('Timeout');
  });

  it('calls DELETE /api/media/:id', async () => {
    _ok({});
    await deleteMedia('media-42');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/media/media-42');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// getMediaMetrics
// ---------------------------------------------------------------------------

describe('getMediaMetrics', () => {
  it('returns metrics on success', async () => {
    _ok({ total_images: 150, generated: 30, uploaded: 120 });
    const result = await getMediaMetrics();
    expect(result.total_images).toBe(150);
  });

  it('uses default range of 30d', async () => {
    _ok({});
    await getMediaMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=30d');
  });

  it('passes custom range in URL', async () => {
    _ok({});
    await getMediaMetrics('7d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=7d');
  });

  it('throws when response contains error field', async () => {
    _error('Metrics unavailable');
    await expect(getMediaMetrics()).rejects.toThrow('Metrics unavailable');
  });

  it('propagates network errors', async () => {
    _throw('Server error');
    await expect(getMediaMetrics('90d')).rejects.toThrow('Server error');
  });

  it('calls GET /api/media/metrics', async () => {
    _ok({});
    await getMediaMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('/api/media/metrics');
    expect(mockMakeRequest.mock.calls[0][1]).toBe('GET');
  });
});
