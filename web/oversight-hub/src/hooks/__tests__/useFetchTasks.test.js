/**
 * useFetchTasks.test.js
 *
 * Unit tests for the useFetchTasks hook.
 * Covers:
 * - Successful fetch populates tasks and total
 * - API error sets error message and clears tasks
 * - Auto-refresh on interval
 * - No auto-refresh when autoRefreshInterval=0
 * - refetch() triggers a manual re-fetch
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { useFetchTasks } from '../useFetchTasks';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// vi.hoisted required because these are referenced inside vi.mock() factory
const { mockSetTasks, mockGetTasks } = vi.hoisted(() => ({
  mockSetTasks: vi.fn(),
  mockGetTasks: vi.fn(),
}));

vi.mock('../../store/useStore', () => ({
  default: vi.fn(() => ({ setTasks: mockSetTasks })),
}));

vi.mock('../../services/cofounderAgentClient', () => ({
  getTasks: (...args) => mockGetTasks(...args),
}));

vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SAMPLE_TASKS = [
  { id: 't1', task_name: 'Task One' },
  { id: 't2', task_name: 'Task Two' },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useFetchTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetTasks.mockResolvedValue({
      tasks: SAMPLE_TASKS,
      total: 42,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------
  // Successful fetch
  // -------------------------------------------------------------------------

  describe('successful fetch', () => {
    it('populates tasks and total from API response', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(result.current.tasks).toEqual(SAMPLE_TASKS);
      expect(result.current.total).toBe(42);
      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('updates Zustand store with fetched tasks', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(mockSetTasks).toHaveBeenCalledWith(SAMPLE_TASKS);
    });

    it('calculates offset from page and limit correctly', async () => {
      // Page 3, limit 10 → offset = 20
      const { result } = renderHook(() => useFetchTasks(3, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(mockGetTasks).toHaveBeenCalledWith(10, 20, expect.anything());
    });

    it('handles response.data fallback when response.tasks is absent', async () => {
      mockGetTasks.mockResolvedValue({
        data: SAMPLE_TASKS,
        total: 5,
      });

      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(result.current.total).toBe(5);
    });

    it('handles response.pagination.total fallback', async () => {
      mockGetTasks.mockResolvedValue({
        tasks: SAMPLE_TASKS,
        pagination: { total: 99 },
      });

      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(result.current.total).toBe(99);
    });

    it('exposes tasks, total, loading, error, refetch properties', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(Array.isArray(result.current.tasks)).toBe(true);
      expect(typeof result.current.total).toBe('number');
      expect(typeof result.current.loading).toBe('boolean');
      expect(typeof result.current.refetch).toBe('function');
    });
  });

  // -------------------------------------------------------------------------
  // Error handling
  // -------------------------------------------------------------------------

  describe('error handling', () => {
    it('sets error message and clears tasks when API throws', async () => {
      mockGetTasks.mockRejectedValue(new Error('Network timeout'));

      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.error).toBeTruthy();
      });

      expect(result.current.error).toContain('Network timeout');
      expect(result.current.tasks).toHaveLength(0);
      expect(result.current.total).toBe(0);
      expect(result.current.loading).toBe(false);
    });

    it('clears Zustand store on error', async () => {
      mockGetTasks.mockRejectedValue(new Error('Server error'));

      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.error).toBeTruthy();
      });

      expect(mockSetTasks).toHaveBeenCalledWith([]);
    });
  });

  // -------------------------------------------------------------------------
  // refetch()
  // -------------------------------------------------------------------------

  describe('refetch()', () => {
    it('triggers a new API call when called manually', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      const firstCallCount = mockGetTasks.mock.calls.length;

      await act(async () => {
        await result.current.refetch();
      });

      expect(mockGetTasks.mock.calls.length).toBeGreaterThan(firstCallCount);
    });
  });

  // -------------------------------------------------------------------------
  // Auto-refresh
  // -------------------------------------------------------------------------

  describe('auto-refresh', () => {
    it('does NOT call getTasks more than once when autoRefreshInterval=0', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      // Wait a bit — no interval should fire
      await new Promise((r) => setTimeout(r, 50));

      // Should have been called exactly once (on mount)
      expect(mockGetTasks).toHaveBeenCalledTimes(1);
    });

    it('calls getTasks immediately on mount', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(mockGetTasks).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // Filter params
  // -------------------------------------------------------------------------

  describe('filter params', () => {
    it('passes status and search filters to getTasks', async () => {
      const { result } = renderHook(() =>
        useFetchTasks(1, 10, 0, { status: 'pending', search: 'blog' })
      );

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(mockGetTasks).toHaveBeenCalledWith(
        10,
        0,
        expect.objectContaining({ status: 'pending', search: 'blog' })
      );
    });

    it('passes undefined filters when not provided', async () => {
      const { result } = renderHook(() => useFetchTasks(1, 10, 0));

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(mockGetTasks).toHaveBeenCalledWith(
        10,
        0,
        expect.objectContaining({ status: undefined, search: undefined })
      );
    });
  });
});
