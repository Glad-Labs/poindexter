/**
 * ollamaService.test.js
 *
 * Unit tests for services/ollamaService.js.
 *
 * Tests cover:
 * - getOllamaModels — success with models array, not ok returns [], fetch throws returns []
 * - isOllamaAvailable — connected: true returns true, connected: false returns false, not ok returns false, throws returns false
 * - generateWithOllamaModel — success returns response text, not ok throws, AbortError rethrowed with timeout message
 * - getOllamaModelInfo — success, not ok throws
 *
 * fetch is mocked globally; no network calls.
 */

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../config/apiConfig', () => ({
  getApiUrl: () => 'http://localhost:8000',
}));

import {
  getOllamaModels,
  isOllamaAvailable,
  generateWithOllamaModel,
  getOllamaModelInfo,
} from '../ollamaService';

const _mockFetch = (opts = {}) => {
  const { ok = true, json = {}, status = 200, throws = null } = opts;
  if (throws) {
    global.fetch = vi.fn().mockRejectedValue(throws);
  } else {
    global.fetch = vi.fn().mockResolvedValue({
      ok,
      status,
      json: async () => json,
    });
  }
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// getOllamaModels
// ---------------------------------------------------------------------------

describe('getOllamaModels', () => {
  it('returns models array on success', async () => {
    _mockFetch({ json: { models: ['mistral:7b', 'llama2:13b'] } });
    const result = await getOllamaModels();
    expect(result).toEqual(['mistral:7b', 'llama2:13b']);
  });

  it('returns empty array when response not ok', async () => {
    _mockFetch({ ok: false, status: 503 });
    const result = await getOllamaModels();
    expect(result).toEqual([]);
  });

  it('returns empty array when fetch throws', async () => {
    _mockFetch({ throws: new Error('ECONNREFUSED') });
    const result = await getOllamaModels();
    expect(result).toEqual([]);
  });

  it('returns empty array when response has no models key', async () => {
    _mockFetch({ json: {} });
    const result = await getOllamaModels();
    expect(result).toEqual([]);
  });

  it('calls the /api/ollama/tags endpoint', async () => {
    _mockFetch({ json: { models: [] } });
    await getOllamaModels();
    expect(global.fetch.mock.calls[0][0]).toContain('/api/ollama/tags');
  });
});

// ---------------------------------------------------------------------------
// isOllamaAvailable
// ---------------------------------------------------------------------------

describe('isOllamaAvailable', () => {
  it('returns true when connected: true', async () => {
    _mockFetch({ json: { connected: true } });
    const result = await isOllamaAvailable();
    expect(result).toBe(true);
  });

  it('returns false when connected: false', async () => {
    _mockFetch({ json: { connected: false } });
    const result = await isOllamaAvailable();
    expect(result).toBe(false);
  });

  it('returns false when response not ok', async () => {
    _mockFetch({ ok: false, status: 503 });
    const result = await isOllamaAvailable();
    expect(result).toBe(false);
  });

  it('returns false when fetch throws', async () => {
    _mockFetch({ throws: new Error('Connection refused') });
    const result = await isOllamaAvailable();
    expect(result).toBe(false);
  });

  it('calls the /api/ollama/health endpoint', async () => {
    _mockFetch({ json: { connected: true } });
    await isOllamaAvailable();
    expect(global.fetch.mock.calls[0][0]).toContain('/api/ollama/health');
  });
});

// ---------------------------------------------------------------------------
// generateWithOllamaModel
// ---------------------------------------------------------------------------

describe('generateWithOllamaModel', () => {
  it('returns response text on success', async () => {
    _mockFetch({ json: { response: 'Generated text here' } });
    const result = await generateWithOllamaModel('mistral', 'Say hello');
    expect(result).toBe('Generated text here');
  });

  it('returns empty string when response has no response key', async () => {
    _mockFetch({ json: {} });
    const result = await generateWithOllamaModel('mistral', 'Prompt');
    expect(result).toBe('');
  });

  it('throws when response not ok', async () => {
    _mockFetch({ ok: false, status: 500 });
    await expect(generateWithOllamaModel('mistral', 'Prompt')).rejects.toThrow(
      'Generation failed'
    );
  });

  it('rethrows AbortError with timeout message', async () => {
    const abortError = new DOMException(
      'The operation was aborted',
      'AbortError'
    );
    _mockFetch({ throws: abortError });
    await expect(generateWithOllamaModel('mistral', 'Prompt')).rejects.toThrow(
      'Generation timed out'
    );
  });

  it('propagates other network errors', async () => {
    _mockFetch({ throws: new Error('Network failure') });
    await expect(generateWithOllamaModel('mistral', 'Prompt')).rejects.toThrow(
      'Network failure'
    );
  });

  it('calls the /api/ollama/generate endpoint via POST', async () => {
    _mockFetch({ json: { response: 'ok' } });
    await generateWithOllamaModel('mistral', 'Test');
    expect(global.fetch.mock.calls[0][0]).toContain('/api/ollama/generate');
    expect(global.fetch.mock.calls[0][1].method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// getOllamaModelInfo
// ---------------------------------------------------------------------------

describe('getOllamaModelInfo', () => {
  it('returns model info on success', async () => {
    const info = { name: 'mistral', size: '7b', parameters: {} };
    _mockFetch({ json: info });
    const result = await getOllamaModelInfo('mistral:7b');
    expect(result).toEqual(info);
  });

  it('throws when response not ok', async () => {
    _mockFetch({ ok: false, status: 404 });
    await expect(getOllamaModelInfo('nonexistent')).rejects.toThrow(
      'Could not fetch model info'
    );
  });

  it('calls /api/ollama/show endpoint via POST', async () => {
    _mockFetch({ json: {} });
    await getOllamaModelInfo('llama2');
    expect(global.fetch.mock.calls[0][0]).toContain('/api/ollama/show');
    expect(global.fetch.mock.calls[0][1].method).toBe('POST');
  });
});
