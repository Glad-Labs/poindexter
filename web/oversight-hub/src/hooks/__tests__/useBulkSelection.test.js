/**
 * useBulkSelection.test.js
 *
 * Unit tests for the useBulkSelection hook.
 * Covers:
 * - Initial state
 * - handleToggleSelect — adds/removes IDs from selectedIds Set
 * - handleSelectAll — selects all items by itemIdKey
 * - handleClearSelection — empties selectedIds
 * - handleBulkApproveClick — guards against empty selection; opens dialog
 * - handleBulkRejectClick — guards against empty selection; opens dialog
 * - handleBulkApproveSubmit — calls API, fires onSuccess, clears selection
 * - handleBulkRejectSubmit — validates feedback, calls API, fires onSuccess
 * - Error paths: API failure, missing feedback
 */

import { renderHook, act } from '@testing-library/react';
import useBulkSelection from '../useBulkSelection';

// Mock @/lib/logger (imported at top of the module)
vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

// Mock apiConfig
vi.mock('../config/apiConfig', () => ({
  getApiUrl: vi.fn(() => 'http://localhost:8000'),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ITEMS = [
  { task_id: 'a1', name: 'Task A' },
  { task_id: 'b2', name: 'Task B' },
  { task_id: 'c3', name: 'Task C' },
];

function makeHook(overrides = {}) {
  return renderHook(() =>
    useBulkSelection({
      items: ITEMS,
      itemIdKey: 'task_id',
      ...overrides,
    })
  );
}

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('useBulkSelection', () => {
  describe('initial state', () => {
    it('selectedIds is an empty Set', () => {
      const { result } = makeHook();
      expect(result.current.selectedIds.size).toBe(0);
    });

    it('dialogs are closed', () => {
      const { result } = makeHook();
      expect(result.current.bulkApproveDialogOpen).toBe(false);
      expect(result.current.bulkRejectDialogOpen).toBe(false);
    });

    it('bulkOperationLoading is false', () => {
      const { result } = makeHook();
      expect(result.current.bulkOperationLoading).toBe(false);
    });

    it('bulkAllowRevisions defaults to true', () => {
      const { result } = makeHook();
      expect(result.current.bulkAllowRevisions).toBe(true);
    });

    it('bulkRejectReason defaults to "Content quality"', () => {
      const { result } = makeHook();
      expect(result.current.bulkRejectReason).toBe('Content quality');
    });
  });

  // -------------------------------------------------------------------------
  // Selection helpers
  // -------------------------------------------------------------------------

  describe('handleToggleSelect()', () => {
    it('adds an ID when not selected', () => {
      const { result } = makeHook();

      act(() => {
        result.current.handleToggleSelect('a1');
      });

      expect(result.current.selectedIds.has('a1')).toBe(true);
      expect(result.current.selectedIds.size).toBe(1);
    });

    it('removes an ID when already selected', () => {
      const { result } = makeHook();

      act(() => {
        result.current.handleToggleSelect('a1');
      });
      act(() => {
        result.current.handleToggleSelect('a1');
      });

      expect(result.current.selectedIds.size).toBe(0);
    });

    it('handles multiple unique IDs', () => {
      const { result } = makeHook();

      act(() => {
        result.current.handleToggleSelect('a1');
      });
      act(() => {
        result.current.handleToggleSelect('b2');
      });

      expect(result.current.selectedIds.size).toBe(2);
      expect(result.current.selectedIds.has('a1')).toBe(true);
      expect(result.current.selectedIds.has('b2')).toBe(true);
    });
  });

  describe('handleSelectAll()', () => {
    it('selects all items by itemIdKey', () => {
      const { result } = makeHook();

      act(() => {
        result.current.handleSelectAll();
      });

      expect(result.current.selectedIds.size).toBe(3);
      expect(result.current.selectedIds.has('a1')).toBe(true);
      expect(result.current.selectedIds.has('b2')).toBe(true);
      expect(result.current.selectedIds.has('c3')).toBe(true);
    });

    it('works with custom itemIdKey', () => {
      const items = [{ id: 'x1' }, { id: 'x2' }];
      const { result } = renderHook(() =>
        useBulkSelection({ items, itemIdKey: 'id' })
      );

      act(() => {
        result.current.handleSelectAll();
      });

      expect(result.current.selectedIds.size).toBe(2);
      expect(result.current.selectedIds.has('x1')).toBe(true);
    });
  });

  describe('handleClearSelection()', () => {
    it('empties selectedIds', () => {
      const { result } = makeHook();

      act(() => {
        result.current.handleSelectAll();
      });
      expect(result.current.selectedIds.size).toBe(3);

      act(() => {
        result.current.handleClearSelection();
      });
      expect(result.current.selectedIds.size).toBe(0);
    });
  });

  // -------------------------------------------------------------------------
  // handleBulkApproveClick
  // -------------------------------------------------------------------------

  describe('handleBulkApproveClick()', () => {
    it('calls onError if no items selected', () => {
      const onError = vi.fn();
      const { result } = makeHook({ onError });

      act(() => {
        result.current.handleBulkApproveClick();
      });

      expect(onError).toHaveBeenCalledWith('Please select at least one task');
      expect(result.current.bulkApproveDialogOpen).toBe(false);
    });

    it('opens approve dialog when items are selected', () => {
      const { result } = makeHook();

      act(() => {
        result.current.handleToggleSelect('a1');
      });
      act(() => {
        result.current.handleBulkApproveClick();
      });

      expect(result.current.bulkApproveDialogOpen).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // handleBulkRejectClick
  // -------------------------------------------------------------------------

  describe('handleBulkRejectClick()', () => {
    it('calls onError if no items selected', () => {
      const onError = vi.fn();
      const { result } = makeHook({ onError });

      act(() => {
        result.current.handleBulkRejectClick();
      });

      expect(onError).toHaveBeenCalledWith('Please select at least one task');
      expect(result.current.bulkRejectDialogOpen).toBe(false);
    });

    it('opens reject dialog and resets fields when items are selected', () => {
      const { result } = makeHook();

      // Set some fields first
      act(() => {
        result.current.setBulkRejectFeedback('some feedback');
      });
      act(() => {
        result.current.handleToggleSelect('a1');
      });
      act(() => {
        result.current.handleBulkRejectClick();
      });

      expect(result.current.bulkRejectDialogOpen).toBe(true);
      expect(result.current.bulkRejectFeedback).toBe('');
      expect(result.current.bulkRejectReason).toBe('Content quality');
      expect(result.current.bulkAllowRevisions).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // handleBulkApproveSubmit — API calls
  // -------------------------------------------------------------------------

  describe('handleBulkApproveSubmit()', () => {
    beforeEach(() => {
      global.fetch = vi.fn();
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('calls bulk-approve API with selected IDs and closes dialog on success', async () => {
      const onSuccess = vi.fn();
      const onRefresh = vi.fn();
      global.fetch.mockResolvedValue({
        ok: true,
        json: vi
          .fn()
          .mockResolvedValue({ succeeded_count: 2, failed_count: 0 }),
      });

      const { result } = makeHook({ onSuccess, onRefresh });

      act(() => {
        result.current.handleToggleSelect('a1');
      });
      act(() => {
        result.current.handleToggleSelect('b2');
      });

      await act(async () => {
        await result.current.handleBulkApproveSubmit();
      });

      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/tasks/bulk-approve'),
        expect.objectContaining({ method: 'POST' })
      );
      expect(onSuccess).toHaveBeenCalledWith(
        expect.stringContaining('2 approved')
      );
      expect(result.current.bulkApproveDialogOpen).toBe(false);
      expect(result.current.selectedIds.size).toBe(0);
      expect(result.current.bulkOperationLoading).toBe(false);
    });

    it('calls onError when API returns non-ok response', async () => {
      const onError = vi.fn();
      global.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Unauthorized',
      });

      const { result } = makeHook({ onError });
      act(() => {
        result.current.handleToggleSelect('a1');
      });

      await act(async () => {
        await result.current.handleBulkApproveSubmit();
      });

      expect(onError).toHaveBeenCalledWith(
        expect.stringContaining('Failed to approve tasks')
      );
      expect(result.current.bulkOperationLoading).toBe(false);
    });

    it('calls onError when fetch throws', async () => {
      const onError = vi.fn();
      global.fetch.mockRejectedValue(new Error('Network failure'));

      const { result } = makeHook({ onError });
      act(() => {
        result.current.handleToggleSelect('a1');
      });

      await act(async () => {
        await result.current.handleBulkApproveSubmit();
      });

      expect(onError).toHaveBeenCalledWith('Network failure');
    });
  });

  // -------------------------------------------------------------------------
  // handleBulkRejectSubmit — API calls
  // -------------------------------------------------------------------------

  describe('handleBulkRejectSubmit()', () => {
    beforeEach(() => {
      global.fetch = vi.fn();
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('calls onError when feedback is empty', async () => {
      const onError = vi.fn();
      const { result } = makeHook({ onError });

      act(() => {
        result.current.handleToggleSelect('a1');
      });
      // Leave bulkRejectFeedback empty

      await act(async () => {
        await result.current.handleBulkRejectSubmit();
      });

      expect(onError).toHaveBeenCalledWith('Please provide feedback');
      expect(global.fetch).not.toHaveBeenCalled();
    });

    it('calls bulk-reject API with selected IDs and closes dialog on success', async () => {
      const onSuccess = vi.fn();
      global.fetch.mockResolvedValue({
        ok: true,
        json: vi
          .fn()
          .mockResolvedValue({ succeeded_count: 1, failed_count: 0 }),
      });

      const { result } = makeHook({ onSuccess });

      act(() => {
        result.current.handleToggleSelect('c3');
      });
      act(() => {
        result.current.setBulkRejectFeedback('Needs more data');
      });

      await act(async () => {
        await result.current.handleBulkRejectSubmit();
      });

      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/tasks/bulk-reject'),
        expect.objectContaining({ method: 'POST' })
      );
      expect(onSuccess).toHaveBeenCalledWith(
        expect.stringContaining('1 rejected')
      );
      expect(result.current.bulkRejectDialogOpen).toBe(false);
      expect(result.current.selectedIds.size).toBe(0);
    });

    it('calls onError when API returns non-ok response', async () => {
      const onError = vi.fn();
      global.fetch.mockResolvedValue({
        ok: false,
        statusText: 'Server Error',
      });

      const { result } = makeHook({ onError });
      act(() => {
        result.current.handleToggleSelect('a1');
      });
      act(() => {
        result.current.setBulkRejectFeedback('some feedback');
      });

      await act(async () => {
        await result.current.handleBulkRejectSubmit();
      });

      expect(onError).toHaveBeenCalledWith(
        expect.stringContaining('Failed to reject tasks')
      );
    });
  });
});
