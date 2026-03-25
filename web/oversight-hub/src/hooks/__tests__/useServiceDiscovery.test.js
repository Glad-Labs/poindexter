/**
 * useServiceDiscovery.test.js
 *
 * Unit tests for the useServiceDiscovery hook.
 * Covers:
 * - Initial state
 * - loadServices happy path (transforms agent data)
 * - loadServices error path (sets error, calls onError)
 * - Derived values: allCapabilities, allPhases
 * - filteredServices: search, capability filter, phase filter
 * - clearError resets error
 * - setSelectedCapabilities / setSelectedPhases / setSearchQuery
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Mock logger
vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

// Mock phase4Client
const mockHealthCheck = vi.fn();
const mockListServices = vi.fn();

vi.mock('../../services/phase4Client', () => ({
  default: {
    healthCheck: (...args) => mockHealthCheck(...args),
    serviceRegistryClient: {
      listServices: (...args) => mockListServices(...args),
    },
  },
}));

import useServiceDiscovery from '../useServiceDiscovery';

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const MOCK_HEALTH = { status: 'healthy', uptime: 12345 };

const MOCK_AGENTS = [
  {
    name: 'content-agent',
    category: 'content',
    description: 'Generates blog posts',
    phases: ['research', 'draft'],
    capabilities: ['writing', 'seo'],
    version: '2.1.0',
    actions: ['create', 'update'],
  },
  {
    name: 'market-agent',
    category: 'analytics',
    description: 'Market analysis',
    phases: ['research', 'analysis'],
    capabilities: ['data-analysis', 'reporting'],
    version: '1.5.0',
    actions: ['analyze'],
  },
  {
    name: 'compliance-agent',
    description: 'Compliance checking',
    // Missing optional fields to test defaults
  },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useServiceDiscovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockHealthCheck.mockResolvedValue(MOCK_HEALTH);
    mockListServices.mockResolvedValue({ agents: MOCK_AGENTS });
  });

  it('returns initial state', () => {
    const { result } = renderHook(() => useServiceDiscovery());

    expect(result.current.services).toEqual([]);
    expect(result.current.loadingServices).toBe(true);
    expect(result.current.error).toBeNull();
    expect(result.current.selectedCapabilities).toEqual([]);
    expect(result.current.selectedPhases).toEqual([]);
    expect(result.current.searchQuery).toBe('');
    expect(result.current.healthStatus).toBeNull();
    expect(typeof result.current.loadServices).toBe('function');
    expect(typeof result.current.clearError).toBe('function');
  });

  it('loadServices fetches and transforms agent data', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(mockHealthCheck).toHaveBeenCalled();
    expect(mockListServices).toHaveBeenCalled();
    expect(result.current.loadingServices).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.healthStatus).toEqual(MOCK_HEALTH);
    expect(result.current.services).toHaveLength(3);

    // Check first service is transformed correctly
    const first = result.current.services[0];
    expect(first.id).toBe('content-agent');
    expect(first.name).toBe('content-agent');
    expect(first.category).toBe('content');
    expect(first.description).toBe('Generates blog posts');
    expect(first.phases).toEqual(['research', 'draft']);
    expect(first.capabilities).toEqual(['writing', 'seo']);
    expect(first.version).toBe('2.1.0');
    expect(first.actions).toEqual(['create', 'update']);
  });

  it('applies defaults for missing agent fields', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    const compliance = result.current.services[2];
    expect(compliance.category).toBe('general');
    expect(compliance.phases).toEqual([]);
    expect(compliance.capabilities).toEqual([]);
    expect(compliance.version).toBe('1.0.0');
    expect(compliance.actions).toEqual([]);
  });

  it('handles empty agents list', async () => {
    mockListServices.mockResolvedValue({ agents: [] });
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.services).toEqual([]);
    expect(result.current.loadingServices).toBe(false);
  });

  it('handles missing agents key in response', async () => {
    mockListServices.mockResolvedValue({});
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.services).toEqual([]);
    expect(result.current.loadingServices).toBe(false);
  });

  it('sets error and calls onError on failure', async () => {
    const onError = vi.fn();
    mockHealthCheck.mockRejectedValue(new Error('Network timeout'));

    const { result } = renderHook(() => useServiceDiscovery({ onError }));

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.loadingServices).toBe(false);
    expect(result.current.error).toBe(
      'Error loading services: Network timeout'
    );
    expect(onError).toHaveBeenCalledWith(
      'Error loading services: Network timeout'
    );
  });

  it('sets error with default message when error has no message', async () => {
    mockHealthCheck.mockRejectedValue({});

    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.error).toBe(
      'Error loading services: Failed to load services'
    );
  });

  it('does not throw when onError is not provided', async () => {
    mockHealthCheck.mockRejectedValue(new Error('fail'));

    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.error).toContain('fail');
  });

  it('computes allCapabilities as sorted unique set', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.allCapabilities).toEqual([
      'data-analysis',
      'reporting',
      'seo',
      'writing',
    ]);
  });

  it('computes allPhases as sorted unique set', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.allPhases).toEqual(['analysis', 'draft', 'research']);
  });

  it('filters services by search query (name)', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    act(() => {
      result.current.setSearchQuery('content');
    });

    expect(result.current.filteredServices).toHaveLength(1);
    expect(result.current.filteredServices[0].name).toBe('content-agent');
  });

  it('filters services by search query (description)', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    act(() => {
      result.current.setSearchQuery('blog');
    });

    expect(result.current.filteredServices).toHaveLength(1);
    expect(result.current.filteredServices[0].name).toBe('content-agent');
  });

  it('search is case-insensitive', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    act(() => {
      result.current.setSearchQuery('MARKET');
    });

    expect(result.current.filteredServices).toHaveLength(1);
    expect(result.current.filteredServices[0].name).toBe('market-agent');
  });

  it('filters services by selected capabilities', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    act(() => {
      result.current.setSelectedCapabilities(['seo']);
    });

    expect(result.current.filteredServices).toHaveLength(1);
    expect(result.current.filteredServices[0].name).toBe('content-agent');
  });

  it('filters services by selected phases', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    act(() => {
      result.current.setSelectedPhases(['analysis']);
    });

    expect(result.current.filteredServices).toHaveLength(1);
    expect(result.current.filteredServices[0].name).toBe('market-agent');
  });

  it('combines search and capability filters (intersection)', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    act(() => {
      result.current.setSearchQuery('agent');
      result.current.setSelectedCapabilities(['writing']);
    });

    // 'agent' matches all three by name, but only content-agent has 'writing'
    expect(result.current.filteredServices).toHaveLength(1);
    expect(result.current.filteredServices[0].name).toBe('content-agent');
  });

  it('returns all services when no filters are active', async () => {
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.filteredServices).toHaveLength(3);
  });

  it('clearError resets error to null', async () => {
    mockHealthCheck.mockRejectedValue(new Error('fail'));
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.error).not.toBeNull();

    act(() => {
      result.current.clearError();
    });

    expect(result.current.error).toBeNull();
  });

  it('clears previous error on subsequent loadServices call', async () => {
    mockHealthCheck.mockRejectedValueOnce(new Error('fail'));
    const { result } = renderHook(() => useServiceDiscovery());

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.error).not.toBeNull();

    // Second call succeeds
    mockHealthCheck.mockResolvedValue(MOCK_HEALTH);

    await act(async () => {
      await result.current.loadServices();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.services).toHaveLength(3);
  });
});
