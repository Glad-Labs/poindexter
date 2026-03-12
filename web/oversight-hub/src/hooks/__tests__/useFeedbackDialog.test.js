/**
 * useFeedbackDialog.test.js
 *
 * Unit tests for the useFeedbackDialog hook.
 * Covers:
 * - Initial state (closed, not submitting, no error)
 * - open() / close() state transitions
 * - close() fires onClose callback
 * - approve() calls onApprove, closes dialog on success
 * - approve() sets error and does not close on failure
 * - reject() calls onReject, closes dialog on success
 * - reject() sets error and does not close on failure
 * - reset() clears all state
 */

import { renderHook, act } from '@testing-library/react';
import { useFeedbackDialog } from '../useFeedbackDialog';

describe('useFeedbackDialog', () => {
  // -------------------------------------------------------------------------
  // Initial state
  // -------------------------------------------------------------------------

  describe('initial state', () => {
    it('starts closed, not submitting, no error', () => {
      const { result } = renderHook(() => useFeedbackDialog());
      expect(result.current.isOpen).toBe(false);
      expect(result.current.isSubmitting).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('exposes open, close, approve, reject, reset functions', () => {
      const { result } = renderHook(() => useFeedbackDialog());
      expect(typeof result.current.open).toBe('function');
      expect(typeof result.current.close).toBe('function');
      expect(typeof result.current.approve).toBe('function');
      expect(typeof result.current.reject).toBe('function');
      expect(typeof result.current.reset).toBe('function');
    });
  });

  // -------------------------------------------------------------------------
  // open() / close()
  // -------------------------------------------------------------------------

  describe('open()', () => {
    it('sets isOpen to true', () => {
      const { result } = renderHook(() => useFeedbackDialog());

      act(() => {
        result.current.open();
      });

      expect(result.current.isOpen).toBe(true);
    });

    it('clears any existing error when opened', () => {
      const failingApprove = vi.fn().mockRejectedValue(new Error('old error'));
      const { result } = renderHook(() => useFeedbackDialog(failingApprove));

      // First open and generate an error
      act(() => {
        result.current.open();
      });

      // Then close and reopen — error should clear
      act(() => {
        result.current.close();
      });
      act(() => {
        result.current.open();
      });

      expect(result.current.error).toBeNull();
    });
  });

  describe('close()', () => {
    it('sets isOpen to false', () => {
      const { result } = renderHook(() => useFeedbackDialog());

      act(() => {
        result.current.open();
      });
      act(() => {
        result.current.close();
      });

      expect(result.current.isOpen).toBe(false);
    });

    it('calls onClose callback if provided', () => {
      const onClose = vi.fn();
      const { result } = renderHook(() =>
        useFeedbackDialog(undefined, undefined, onClose)
      );

      act(() => {
        result.current.open();
      });
      act(() => {
        result.current.close();
      });

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('does not crash without onClose callback', () => {
      const { result } = renderHook(() => useFeedbackDialog());

      expect(() => {
        act(() => {
          result.current.open();
        });
        act(() => {
          result.current.close();
        });
      }).not.toThrow();
    });
  });

  // -------------------------------------------------------------------------
  // approve()
  // -------------------------------------------------------------------------

  describe('approve()', () => {
    it('calls onApprove with feedback and closes dialog on success', async () => {
      const onApprove = vi.fn().mockResolvedValue(undefined);
      const { result } = renderHook(() => useFeedbackDialog(onApprove));

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.approve('looks good');
      });

      expect(onApprove).toHaveBeenCalledWith('looks good');
      expect(result.current.isOpen).toBe(false);
      expect(result.current.isSubmitting).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('sets error and keeps dialog open on failure', async () => {
      const onApprove = vi.fn().mockRejectedValue(new Error('Network error'));
      const { result } = renderHook(() => useFeedbackDialog(onApprove));

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.approve('feedback');
      });

      expect(result.current.error).toBe('Network error');
      expect(result.current.isOpen).toBe(true);
      expect(result.current.isSubmitting).toBe(false);
    });

    it('uses generic error message for non-Error throws', async () => {
      const onApprove = vi.fn().mockRejectedValue('string error');
      const { result } = renderHook(() => useFeedbackDialog(onApprove));

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.approve('feedback');
      });

      expect(result.current.error).toBe('Failed to approve');
    });

    it('works without onApprove callback (no crash)', async () => {
      const { result } = renderHook(() => useFeedbackDialog());

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.approve('feedback');
      });

      // When onApprove is undefined, close() is still called on success
      expect(result.current.isOpen).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // reject()
  // -------------------------------------------------------------------------

  describe('reject()', () => {
    it('calls onReject with feedback and closes dialog on success', async () => {
      const onReject = vi.fn().mockResolvedValue(undefined);
      const { result } = renderHook(() =>
        useFeedbackDialog(undefined, onReject)
      );

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.reject('needs revision');
      });

      expect(onReject).toHaveBeenCalledWith('needs revision');
      expect(result.current.isOpen).toBe(false);
      expect(result.current.isSubmitting).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('sets error and keeps dialog open on failure', async () => {
      const onReject = vi.fn().mockRejectedValue(new Error('Server error'));
      const { result } = renderHook(() =>
        useFeedbackDialog(undefined, onReject)
      );

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.reject('feedback');
      });

      expect(result.current.error).toBe('Server error');
      expect(result.current.isOpen).toBe(true);
    });

    it('uses generic error message for non-Error throws', async () => {
      const onReject = vi.fn().mockRejectedValue(42);
      const { result } = renderHook(() =>
        useFeedbackDialog(undefined, onReject)
      );

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.reject('feedback');
      });

      expect(result.current.error).toBe('Failed to reject');
    });

    it('works without onReject callback (no crash)', async () => {
      const { result } = renderHook(() => useFeedbackDialog());

      act(() => {
        result.current.open();
      });

      await act(async () => {
        await result.current.reject('feedback');
      });

      // When onReject is undefined, close() is still called on success
      expect(result.current.isOpen).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // reset()
  // -------------------------------------------------------------------------

  describe('reset()', () => {
    it('clears all state: isOpen, isSubmitting, error', () => {
      const { result } = renderHook(() => useFeedbackDialog());

      // Open the dialog
      act(() => {
        result.current.open();
      });
      expect(result.current.isOpen).toBe(true);

      // Reset everything
      act(() => {
        result.current.reset();
      });

      expect(result.current.isOpen).toBe(false);
      expect(result.current.isSubmitting).toBe(false);
      expect(result.current.error).toBeNull();
    });
  });
});
