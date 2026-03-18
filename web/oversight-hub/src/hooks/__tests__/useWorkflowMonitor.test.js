/**
 * useWorkflowMonitor.test.js
 *
 * Unit tests for the useWorkflowMonitor hook.
 *
 * Covers:
 * - Initial state
 * - loadWorkflowMonitorData: success populates all three state slices
 * - loadWorkflowMonitorData: handles API error
 * - Calls onError callback on failure
 * - Return shape
 *
 * Closes #919 (partial).
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import useWorkflowMonitor from '../useWorkflowMonitor';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const {
  mockGetWorkflowHistory,
  mockGetWorkflowStatistics,
  mockGetPerformanceMetrics,
} = vi.hoisted(() => ({
  mockGetWorkflowHistory: vi.fn(),
  mockGetWorkflowStatistics: vi.fn(),
  mockGetPerformanceMetrics: vi.fn(),
}));

vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../services/workflowManagementService', () => ({
  getWorkflowHistory: (...args) => mockGetWorkflowHistory(...args),
  getWorkflowStatistics: (...args) => mockGetWorkflowStatistics(...args),
  getPerformanceMetrics: (...args) => mockGetPerformanceMetrics(...args),
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useWorkflowMonitor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWorkflowHistory.mockResolvedValue({
      executions: [{ id: 'exec-1' }, { id: 'exec-2' }],
    });
    mockGetWorkflowStatistics.mockResolvedValue({
      statistics: { total: 50, completed: 45, failed: 5 },
    });
    mockGetPerformanceMetrics.mockResolvedValue({
      metrics: { avg_duration: 120, p95: 300 },
    });
  });

  it('has correct initial state', () => {
    const { result } = renderHook(() => useWorkflowMonitor());

    expect(result.current.monitorLoading).toBe(false);
    expect(result.current.monitorError).toBeNull();
    expect(result.current.executionHistory).toEqual([]);
    expect(result.current.statistics).toBeNull();
    expect(result.current.performanceMetrics).toBeNull();
  });

  it('loadWorkflowMonitorData populates all state slices', async () => {
    const { result } = renderHook(() => useWorkflowMonitor());

    await act(async () => {
      await result.current.loadWorkflowMonitorData();
    });

    expect(result.current.executionHistory).toEqual([
      { id: 'exec-1' },
      { id: 'exec-2' },
    ]);
    expect(result.current.statistics).toEqual({
      total: 50,
      completed: 45,
      failed: 5,
    });
    expect(result.current.performanceMetrics).toEqual({
      avg_duration: 120,
      p95: 300,
    });
    expect(result.current.monitorLoading).toBe(false);
    expect(result.current.monitorError).toBeNull();
  });

  it('passes limit=20 to getWorkflowHistory', async () => {
    const { result } = renderHook(() => useWorkflowMonitor());

    await act(async () => {
      await result.current.loadWorkflowMonitorData();
    });

    expect(mockGetWorkflowHistory).toHaveBeenCalledWith({ limit: 20 });
  });

  it('handles fallback response shapes (no nested keys)', async () => {
    mockGetWorkflowHistory.mockResolvedValue([{ id: 'raw-exec' }]);
    mockGetWorkflowStatistics.mockResolvedValue({ total: 10 });
    mockGetPerformanceMetrics.mockResolvedValue({ avg: 5 });

    const { result } = renderHook(() => useWorkflowMonitor());

    await act(async () => {
      await result.current.loadWorkflowMonitorData();
    });

    // Falls back to the raw response
    expect(result.current.executionHistory).toEqual([{ id: 'raw-exec' }]);
    expect(result.current.statistics).toEqual({ total: 10 });
    expect(result.current.performanceMetrics).toEqual({ avg: 5 });
  });

  it('sets monitorError on API failure', async () => {
    mockGetWorkflowHistory.mockRejectedValue(new Error('DB timeout'));

    const { result } = renderHook(() => useWorkflowMonitor());

    await act(async () => {
      await result.current.loadWorkflowMonitorData();
    });

    expect(result.current.monitorError).toContain('Monitor Error');
    expect(result.current.monitorError).toContain('DB timeout');
    expect(result.current.monitorLoading).toBe(false);
  });

  it('calls onError callback on failure', async () => {
    mockGetWorkflowStatistics.mockRejectedValue(new Error('Stats failed'));
    const onError = vi.fn();

    const { result } = renderHook(() => useWorkflowMonitor({ onError }));

    await act(async () => {
      await result.current.loadWorkflowMonitorData();
    });

    expect(onError).toHaveBeenCalledWith(
      expect.stringContaining('Monitor Error')
    );
  });

  it('returns expected shape', () => {
    const { result } = renderHook(() => useWorkflowMonitor());

    const expected = [
      'monitorLoading',
      'monitorError',
      'executionHistory',
      'statistics',
      'performanceMetrics',
      'loadWorkflowMonitorData',
    ];

    for (const key of expected) {
      expect(result.current).toHaveProperty(key);
    }
    expect(typeof result.current.loadWorkflowMonitorData).toBe('function');
  });
});
