/**
 * analyticsService.test.js
 *
 * Unit tests for services/analyticsService.js.
 *
 * Tests cover:
 * - getKPIs — success, response.error throws, network error propagates
 * - getTaskMetrics — success, response.error throws, network error propagates
 * - getCostBreakdown — success, response.error throws
 * - getContentMetrics — success, response.error throws
 * - getSystemMetrics — success, response.error throws
 * - getAgentMetrics — success, response.error throws
 * - getQualityMetrics — success, response.error throws, default range used
 *
 * makeRequest from cofounderAgentClient is mocked; no network calls.
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
  getKPIs,
  getTaskMetrics,
  getCostBreakdown,
  getContentMetrics,
  getSystemMetrics,
  getAgentMetrics,
  getQualityMetrics,
} from '../analyticsService';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _error = (msg) => mockMakeRequest.mockResolvedValue({ error: msg });
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getKPIs
// ---------------------------------------------------------------------------

describe('getKPIs', () => {
  it('returns data on success', async () => {
    _ok({ revenue: 100, tasks: 50 });
    const result = await getKPIs('30d');
    expect(result).toEqual({ revenue: 100, tasks: 50 });
  });

  it('uses default range of 30d', async () => {
    _ok({ revenue: 0 });
    await getKPIs();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=30d');
  });

  it('throws when response contains error field', async () => {
    _error('Unauthorized');
    await expect(getKPIs()).rejects.toThrow('Unauthorized');
  });

  it('propagates network errors', async () => {
    _throw('Network error');
    await expect(getKPIs()).rejects.toThrow('Network error');
  });
});

// ---------------------------------------------------------------------------
// getTaskMetrics
// ---------------------------------------------------------------------------

describe('getTaskMetrics', () => {
  it('returns data on success', async () => {
    _ok({ total: 25, completed: 20 });
    const result = await getTaskMetrics('7d');
    expect(result).toEqual({ total: 25, completed: 20 });
  });

  it('calls correct endpoint with range param', async () => {
    _ok({});
    await getTaskMetrics('90d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/analytics/tasks?range=90d'
    );
  });

  it('throws when response contains error field', async () => {
    _error('Service unavailable');
    await expect(getTaskMetrics()).rejects.toThrow('Service unavailable');
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(getTaskMetrics()).rejects.toThrow('Timeout');
  });
});

// ---------------------------------------------------------------------------
// getCostBreakdown
// ---------------------------------------------------------------------------

describe('getCostBreakdown', () => {
  it('returns breakdown data on success', async () => {
    _ok({ ollama: 0.0, anthropic: 0.05 });
    const result = await getCostBreakdown();
    expect(result).toEqual({ ollama: 0.0, anthropic: 0.05 });
  });

  it('calls correct endpoint', async () => {
    _ok({});
    await getCostBreakdown('7d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/analytics/costs?range=7d'
    );
  });

  it('throws on response error', async () => {
    _error('DB error');
    await expect(getCostBreakdown()).rejects.toThrow('DB error');
  });
});

// ---------------------------------------------------------------------------
// getContentMetrics
// ---------------------------------------------------------------------------

describe('getContentMetrics', () => {
  it('returns content metrics on success', async () => {
    _ok({ posts: 15, engagement: 0.72 });
    const result = await getContentMetrics();
    expect(result.posts).toBe(15);
  });

  it('calls correct endpoint', async () => {
    _ok({});
    await getContentMetrics('all');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/analytics/content?range=all'
    );
  });

  it('throws on response error', async () => {
    _error('Not found');
    await expect(getContentMetrics()).rejects.toThrow('Not found');
  });
});

// ---------------------------------------------------------------------------
// getSystemMetrics
// ---------------------------------------------------------------------------

describe('getSystemMetrics', () => {
  it('returns system metrics on success', async () => {
    _ok({ cpu: 45, memory: 60 });
    const result = await getSystemMetrics();
    expect(result).toEqual({ cpu: 45, memory: 60 });
  });

  it('calls correct endpoint (no range param)', async () => {
    _ok({});
    await getSystemMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/analytics/system');
  });

  it('throws on response error', async () => {
    _error('Internal error');
    await expect(getSystemMetrics()).rejects.toThrow('Internal error');
  });
});

// ---------------------------------------------------------------------------
// getAgentMetrics
// ---------------------------------------------------------------------------

describe('getAgentMetrics', () => {
  it('returns agent metrics on success', async () => {
    _ok({ agents: 4, tasks_completed: 100 });
    const result = await getAgentMetrics();
    expect(result.agents).toBe(4);
  });

  it('calls correct endpoint', async () => {
    _ok({});
    await getAgentMetrics('30d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/analytics/agents?range=30d'
    );
  });

  it('throws on response error', async () => {
    _error('Agent metrics unavailable');
    await expect(getAgentMetrics()).rejects.toThrow(
      'Agent metrics unavailable'
    );
  });
});

// ---------------------------------------------------------------------------
// getQualityMetrics
// ---------------------------------------------------------------------------

describe('getQualityMetrics', () => {
  it('returns quality metrics on success', async () => {
    _ok({ avg_score: 0.82, total: 50 });
    const result = await getQualityMetrics();
    expect(result.avg_score).toBe(0.82);
  });

  it('uses default range of 30d', async () => {
    _ok({});
    await getQualityMetrics();
    expect(mockMakeRequest.mock.calls[0][0]).toContain('range=30d');
  });

  it('calls correct endpoint', async () => {
    _ok({});
    await getQualityMetrics('90d');
    expect(mockMakeRequest.mock.calls[0][0]).toContain(
      '/api/analytics/quality?range=90d'
    );
  });

  it('throws on response error', async () => {
    _error('Quality data unavailable');
    await expect(getQualityMetrics()).rejects.toThrow(
      'Quality data unavailable'
    );
  });

  it('propagates network errors', async () => {
    _throw('Connection refused');
    await expect(getQualityMetrics()).rejects.toThrow('Connection refused');
  });
});
