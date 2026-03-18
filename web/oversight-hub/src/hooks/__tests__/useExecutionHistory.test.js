/**
 * useExecutionHistory.test.js
 *
 * Unit tests for the useExecutionHistory hook.
 *
 * Covers:
 * - No fetch when workflowId is null
 * - Fetches history on mount when workflowId provided
 * - Re-fetches when executionId changes
 * - Handles API errors
 * - loadExecutionHistory with override workflowId
 * - Return shape
 *
 * Closes #919 (partial).
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import useExecutionHistory from '../useExecutionHistory';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const { mockGetWorkflowExecutions } = vi.hoisted(() => ({
  mockGetWorkflowExecutions: vi.fn(),
}));

vi.mock('../../services/workflowBuilderService', () => ({
  getWorkflowExecutions: (...args) => mockGetWorkflowExecutions(...args),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SAMPLE_EXECUTIONS = [
  { id: 'exec-1', status: 'completed' },
  { id: 'exec-2', status: 'failed' },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useExecutionHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWorkflowExecutions.mockResolvedValue({
      executions: SAMPLE_EXECUTIONS,
    });
  });

  it('does not fetch when workflowId is null', async () => {
    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: null })
    );

    // Wait a tick for the effect to run
    await act(async () => {});

    expect(mockGetWorkflowExecutions).not.toHaveBeenCalled();
    expect(result.current.executionHistory).toEqual([]);
    expect(result.current.executionHistoryError).toBe('');
  });

  it('fetches history on mount when workflowId is provided', async () => {
    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: 'wf-1' })
    );

    await waitFor(() => {
      expect(result.current.executionHistory).toHaveLength(2);
    });

    expect(mockGetWorkflowExecutions).toHaveBeenCalledWith('wf-1', {
      limit: 10,
      offset: 0,
    });
    expect(result.current.executionHistoryLoading).toBe(false);
    expect(result.current.executionHistoryError).toBe('');
  });

  it('re-fetches when executionId changes', async () => {
    const { result, rerender } = renderHook(
      ({ workflowId, executionId }) =>
        useExecutionHistory({ workflowId, executionId }),
      { initialProps: { workflowId: 'wf-1', executionId: 'exec-old' } }
    );

    await waitFor(() => {
      expect(result.current.executionHistory).toHaveLength(2);
    });

    const callsBefore = mockGetWorkflowExecutions.mock.calls.length;

    rerender({ workflowId: 'wf-1', executionId: 'exec-new' });

    await waitFor(() => {
      expect(mockGetWorkflowExecutions.mock.calls.length).toBeGreaterThan(
        callsBefore
      );
    });
  });

  it('handles API error', async () => {
    mockGetWorkflowExecutions.mockRejectedValue(new Error('API down'));

    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: 'wf-1' })
    );

    await waitFor(() => {
      expect(result.current.executionHistoryError).toBeTruthy();
    });

    expect(result.current.executionHistoryError).toContain('API down');
    expect(result.current.executionHistory).toEqual([]);
    expect(result.current.executionHistoryLoading).toBe(false);
  });

  it('handles error with no message', async () => {
    mockGetWorkflowExecutions.mockRejectedValue({});

    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: 'wf-1' })
    );

    await waitFor(() => {
      expect(result.current.executionHistoryError).toBeTruthy();
    });

    expect(result.current.executionHistoryError).toContain(
      'Failed to load execution history'
    );
  });

  it('loadExecutionHistory with override workflowId', async () => {
    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: 'wf-1' })
    );

    await waitFor(() => {
      expect(result.current.executionHistory).toHaveLength(2);
    });

    mockGetWorkflowExecutions.mockResolvedValue({ executions: [] });

    await act(async () => {
      await result.current.loadExecutionHistory('wf-override');
    });

    expect(mockGetWorkflowExecutions).toHaveBeenLastCalledWith('wf-override', {
      limit: 10,
      offset: 0,
    });
  });

  it('handles response without executions key', async () => {
    mockGetWorkflowExecutions.mockResolvedValue({});

    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: 'wf-1' })
    );

    await waitFor(() => {
      expect(result.current.executionHistoryLoading).toBe(false);
    });

    expect(result.current.executionHistory).toEqual([]);
  });

  it('returns expected shape', () => {
    const { result } = renderHook(() =>
      useExecutionHistory({ workflowId: null })
    );

    expect(result.current).toHaveProperty('executionHistory');
    expect(result.current).toHaveProperty('executionHistoryLoading');
    expect(result.current).toHaveProperty('executionHistoryError');
    expect(result.current).toHaveProperty('loadExecutionHistory');
    expect(typeof result.current.loadExecutionHistory).toBe('function');
  });
});
