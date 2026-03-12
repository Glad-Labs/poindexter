/**
 * capabilityService.test.js
 *
 * Unit tests for services/capabilityService.js.
 *
 * Tests cover:
 * - getServiceRegistry — success, response.error throws, network error propagates
 * - listServices — success with array response, success with {services:[]} shape, empty, network error
 * - getServiceMetadata — success, response.error throws, network error propagates
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
  getServiceRegistry,
  listServices,
  getServiceMetadata,
} from '../capabilityService';

const _ok = (data) => mockMakeRequest.mockResolvedValue(data);
const _error = (msg) => mockMakeRequest.mockResolvedValue({ error: msg });
const _throw = (msg) => mockMakeRequest.mockRejectedValue(new Error(msg));

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getServiceRegistry
// ---------------------------------------------------------------------------

describe('getServiceRegistry', () => {
  it('returns registry data on success', async () => {
    _ok({ services: { content_service: { actions: [] } } });
    const result = await getServiceRegistry();
    expect(result.services).toBeDefined();
  });

  it('calls correct endpoint', async () => {
    _ok({});
    await getServiceRegistry();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/services/registry');
  });

  it('throws when response contains error field', async () => {
    _error('Service unavailable');
    await expect(getServiceRegistry()).rejects.toThrow('Service unavailable');
  });

  it('propagates network errors', async () => {
    _throw('Connection refused');
    await expect(getServiceRegistry()).rejects.toThrow('Connection refused');
  });
});

// ---------------------------------------------------------------------------
// listServices
// ---------------------------------------------------------------------------

describe('listServices', () => {
  it('returns array directly when response is an array', async () => {
    _ok(['content_service', 'model_router', 'workflow_engine']);
    const result = await listServices();
    expect(result).toEqual([
      'content_service',
      'model_router',
      'workflow_engine',
    ]);
  });

  it('returns response.services when response has services property', async () => {
    _ok({ services: ['svc1', 'svc2'] });
    const result = await listServices();
    expect(result).toEqual(['svc1', 'svc2']);
  });

  it('returns empty array when response has no services key', async () => {
    _ok({});
    const result = await listServices();
    expect(result).toEqual([]);
  });

  it('calls correct endpoint', async () => {
    _ok([]);
    await listServices();
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/services/list');
  });

  it('propagates network errors', async () => {
    _throw('Timeout');
    await expect(listServices()).rejects.toThrow('Timeout');
  });
});

// ---------------------------------------------------------------------------
// getServiceMetadata
// ---------------------------------------------------------------------------

describe('getServiceMetadata', () => {
  it('returns metadata for a service', async () => {
    _ok({ name: 'content_service', version: '1.0.0', actions: [] });
    const result = await getServiceMetadata('content_service');
    expect(result.name).toBe('content_service');
  });

  it('calls correct endpoint with service name', async () => {
    _ok({});
    await getServiceMetadata('model_router');
    expect(mockMakeRequest.mock.calls[0][0]).toBe('/api/services/model_router');
  });

  it('throws when response contains error field', async () => {
    _error('Service not found');
    await expect(getServiceMetadata('unknown')).rejects.toThrow(
      'Service not found'
    );
  });

  it('propagates network errors', async () => {
    _throw('Network error');
    await expect(getServiceMetadata('content_service')).rejects.toThrow(
      'Network error'
    );
  });
});
