/**
 * useApprovalQueue.test.js
 *
 * Unit tests for the useApprovalQueue hook (~400 LOC).
 *
 * Covers:
 * - fetchPendingTasks: success, error, 401 handling
 * - Approve flow: handleApproveClick opens dialog, handleApprovalSubmit calls API
 * - Reject flow: handleRejectClick opens dialog, handleRejectionSubmit calls API,
 *   validates feedback required
 * - Preview flow: handlePreviewOpen / handlePreviewClose
 * - Pagination / filter / sort controls
 * - WebSocket subscriptions: connects per task, closes on unmount
 *
 * Closes #919 (partial).
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import useApprovalQueue from '../useApprovalQueue';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../config/apiConfig', () => ({
  getApiUrl: vi.fn(() => 'http://localhost:8000'),
}));

// Mock WebSocket
const mockWsInstances = [];
class MockWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1; // OPEN
    this.close = vi.fn();
    mockWsInstances.push(this);
  }
}
MockWebSocket.OPEN = 1;

const _origWebSocket = globalThis.WebSocket;
beforeAll(() => {
  globalThis.WebSocket = MockWebSocket;
});
afterAll(() => {
  globalThis.WebSocket = _origWebSocket;
});

// Mock fetch
const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SAMPLE_TASKS = [
  { task_id: 'task-aaa', task_name: 'Blog Post', status: 'pending_approval' },
  { task_id: 'task-bbb', task_name: 'Newsletter', status: 'pending_approval' },
];

function mockFetchSuccess(tasks = SAMPLE_TASKS) {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ tasks }),
  });
}

function mockFetchError(status = 500, statusText = 'Server Error') {
  mockFetch.mockResolvedValue({
    ok: false,
    status,
    statusText,
    json: async () => ({}),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useApprovalQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWsInstances.length = 0;
    mockFetchSuccess();
  });

  // ---- fetchPendingTasks --------------------------------------------------

  describe('fetchPendingTasks', () => {
    it('fetches tasks on mount and populates state', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      expect(result.current.loading).toBe(false);
      expect(result.current.tasks[0].task_id).toBe('task-aaa');
    });

    it('calls the correct endpoint with pagination params', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      const url = mockFetch.mock.calls[0][0];
      expect(url).toContain('/api/tasks/pending-approval');
      expect(url).toContain('limit=10');
      expect(url).toContain('offset=0');
    });

    it('calls onError with message when fetch fails', async () => {
      mockFetch.mockRejectedValue(new Error('Network down'));
      const onError = vi.fn();

      const { result } = renderHook(() => useApprovalQueue({ onError }));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(onError).toHaveBeenCalledWith('Network down');
      expect(result.current.tasks).toEqual([]);
    });

    it('sets specific error for 401 response', async () => {
      mockFetchError(401, 'Unauthorized');
      const onError = vi.fn();

      const { result } = renderHook(() => useApprovalQueue({ onError }));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(onError).toHaveBeenCalledWith('Unauthorized - please log in');
    });

    it('sets error for non-OK non-401 response', async () => {
      mockFetchError(500, 'Internal Server Error');
      const onError = vi.fn();

      const { result } = renderHook(() => useApprovalQueue({ onError }));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(onError).toHaveBeenCalledWith(
        expect.stringContaining('Failed to fetch pending approvals')
      );
    });
  });

  // ---- Approve flow -------------------------------------------------------

  describe('approve flow', () => {
    it('handleApproveClick opens dialog and sets selected task', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handleApproveClick(SAMPLE_TASKS[0]));

      expect(result.current.approveDialogOpen).toBe(true);
      expect(result.current.selectedTask).toEqual(SAMPLE_TASKS[0]);
      expect(result.current.approveFeedback).toBe('');
    });

    it('handleApprovalSubmit calls approve API and fires onSuccess', async () => {
      const onSuccess = vi.fn();
      const { result } = renderHook(() => useApprovalQueue({ onSuccess }));

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      // Open dialog
      act(() => result.current.handleApproveClick(SAMPLE_TASKS[0]));

      // Mock the approval POST
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: 'approved' }),
      });

      await act(async () => {
        await result.current.handleApprovalSubmit();
      });

      // Check it called the approve endpoint
      const approveCall = mockFetch.mock.calls.find(
        (c) => typeof c[0] === 'string' && c[0].includes('/approve')
      );
      expect(approveCall).toBeTruthy();
      expect(onSuccess).toHaveBeenCalledWith(
        expect.stringContaining('Task approved')
      );
      expect(result.current.approveDialogOpen).toBe(false);
    });

    it('handleApprovalSubmit does nothing when no task selected', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      // Don't open dialog, just submit
      await act(async () => {
        await result.current.handleApprovalSubmit();
      });

      // Should not have called any approve endpoint
      const approveCalls = mockFetch.mock.calls.filter(
        (c) => typeof c[0] === 'string' && c[0].includes('/approve')
      );
      expect(approveCalls).toHaveLength(0);
    });
  });

  // ---- Reject flow --------------------------------------------------------

  describe('reject flow', () => {
    it('handleRejectClick opens dialog with defaults', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handleRejectClick(SAMPLE_TASKS[1]));

      expect(result.current.rejectDialogOpen).toBe(true);
      expect(result.current.selectedTask).toEqual(SAMPLE_TASKS[1]);
      expect(result.current.rejectReason).toBe('Content quality');
      expect(result.current.rejectFeedback).toBe('');
      expect(result.current.allowRevisions).toBe(true);
    });

    it('handleRejectionSubmit validates feedback is required', async () => {
      const onError = vi.fn();
      const { result } = renderHook(() => useApprovalQueue({ onError }));

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handleRejectClick(SAMPLE_TASKS[0]));
      // Don't set feedback — it's empty

      await act(async () => {
        await result.current.handleRejectionSubmit();
      });

      expect(onError).toHaveBeenCalledWith('Please provide feedback');
    });

    it('handleRejectionSubmit calls reject API when feedback provided', async () => {
      const onSuccess = vi.fn();
      const { result } = renderHook(() => useApprovalQueue({ onSuccess }));

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handleRejectClick(SAMPLE_TASKS[0]));
      act(() => result.current.setRejectFeedback('Needs better intro'));

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: 'rejected' }),
      });

      await act(async () => {
        await result.current.handleRejectionSubmit();
      });

      const rejectCall = mockFetch.mock.calls.find(
        (c) => typeof c[0] === 'string' && c[0].includes('/reject')
      );
      expect(rejectCall).toBeTruthy();
      expect(onSuccess).toHaveBeenCalledWith(
        expect.stringContaining('Task rejected')
      );
      expect(result.current.rejectDialogOpen).toBe(false);
    });
  });

  // ---- Preview flow -------------------------------------------------------

  describe('preview flow', () => {
    it('handlePreviewOpen sets task and opens preview', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handlePreviewOpen(SAMPLE_TASKS[0]));

      expect(result.current.previewOpen).toBe(true);
      expect(result.current.selectedTask).toEqual(SAMPLE_TASKS[0]);
    });

    it('handlePreviewClose clears task and closes preview', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handlePreviewOpen(SAMPLE_TASKS[0]));
      act(() => result.current.handlePreviewClose());

      expect(result.current.previewOpen).toBe(false);
      expect(result.current.selectedTask).toBeNull();
    });
  });

  // ---- Pagination / filter / sort -----------------------------------------

  describe('pagination / filter / sort', () => {
    it('handlePageChange updates page', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handlePageChange(null, 3));

      expect(result.current.page).toBe(3);
    });

    it('handleSortChange updates sortBy and resets page', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() => result.current.handlePageChange(null, 2)); // go to page 2

      act(() =>
        result.current.handleSortChange({ target: { value: 'priority' } })
      );

      expect(result.current.sortBy).toBe('priority');
      expect(result.current.page).toBe(1); // reset
    });

    it('handleTaskTypeFilterChange sets filter and resets page', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() =>
        result.current.handleTaskTypeFilterChange({
          target: { value: 'blog_post' },
        })
      );

      expect(result.current.taskTypeFilter).toBe('blog_post');
      expect(result.current.page).toBe(1);
    });

    it('handleTaskTypeFilterChange with "all" clears filter', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      act(() =>
        result.current.handleTaskTypeFilterChange({
          target: { value: 'all' },
        })
      );

      expect(result.current.taskTypeFilter).toBe('');
    });

    it('exposes ITEMS_PER_PAGE constant', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      expect(result.current.ITEMS_PER_PAGE).toBe(10);
    });
  });

  // ---- WebSocket ----------------------------------------------------------

  describe('WebSocket subscriptions', () => {
    it('creates WebSocket connections for each task', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      // Allow the second useEffect to run
      await waitFor(() => {
        expect(mockWsInstances.length).toBeGreaterThanOrEqual(2);
      });

      const urls = mockWsInstances.map((ws) => ws.url);
      expect(urls.some((u) => u.includes('task-aaa'))).toBe(true);
      expect(urls.some((u) => u.includes('task-bbb'))).toBe(true);
    });

    it('closes WebSocket connections on unmount', async () => {
      const { result, unmount } = renderHook(() => useApprovalQueue());

      await waitFor(() => {
        expect(result.current.tasks).toHaveLength(2);
      });

      await waitFor(() => {
        expect(mockWsInstances.length).toBeGreaterThanOrEqual(2);
      });

      unmount();

      // Each ws.close should have been called
      for (const ws of mockWsInstances) {
        expect(ws.close).toHaveBeenCalled();
      }
    });
  });

  // ---- Return shape -------------------------------------------------------

  describe('return shape', () => {
    it('returns all expected properties', async () => {
      const { result } = renderHook(() => useApprovalQueue());

      await waitFor(() => expect(result.current.tasks).toHaveLength(2));

      const expected = [
        'tasks',
        'setTasks',
        'loading',
        'page',
        'ITEMS_PER_PAGE',
        'sortBy',
        'taskTypeFilter',
        'processingTaskId',
        'approveDialogOpen',
        'setApproveDialogOpen',
        'approveFeedback',
        'setApproveFeedback',
        'handleApproveClick',
        'handleApprovalSubmit',
        'rejectDialogOpen',
        'setRejectDialogOpen',
        'rejectReason',
        'setRejectReason',
        'rejectFeedback',
        'setRejectFeedback',
        'allowRevisions',
        'setAllowRevisions',
        'handleRejectClick',
        'handleRejectionSubmit',
        'previewOpen',
        'selectedTask',
        'handlePreviewOpen',
        'handlePreviewClose',
        'fetchPendingTasks',
        'handlePageChange',
        'handleSortChange',
        'handleTaskTypeFilterChange',
      ];

      for (const key of expected) {
        expect(result.current).toHaveProperty(key);
      }
    });
  });
});
