/**
 * useCopyToClipboard.test.js
 *
 * Unit tests for the useCopyToClipboard hook.
 * Covers:
 * - Initial state (copied=false, copying=false, error=null)
 * - Successful copy via Clipboard API
 * - Fallback to execCommand when Clipboard API unavailable
 * - Error state when copy fails
 * - Auto-dismiss feedback via setTimeout
 * - reset() clears all state
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { useCopyToClipboard } from '../useCopyToClipboard';

describe('useCopyToClipboard', () => {
  // -------------------------------------------------------------------------
  // Setup / teardown
  // -------------------------------------------------------------------------
  let originalClipboard;
  let originalIsSecureContext;

  beforeEach(() => {
    vi.useFakeTimers();
    originalClipboard = navigator.clipboard;
    originalIsSecureContext = window.isSecureContext;
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, 'isSecureContext', {
      value: originalIsSecureContext,
      configurable: true,
      writable: true,
    });
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------
  // Initial state
  // -------------------------------------------------------------------------

  describe('initial state', () => {
    it('returns copied=false, copying=false, error=null', () => {
      const { result } = renderHook(() => useCopyToClipboard());
      expect(result.current.copied).toBe(false);
      expect(result.current.copying).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('exposes copyToClipboard and reset functions', () => {
      const { result } = renderHook(() => useCopyToClipboard());
      expect(typeof result.current.copyToClipboard).toBe('function');
      expect(typeof result.current.reset).toBe('function');
    });
  });

  // -------------------------------------------------------------------------
  // Successful copy via Clipboard API
  // -------------------------------------------------------------------------

  describe('Clipboard API (modern browser)', () => {
    beforeEach(() => {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: vi.fn().mockResolvedValue(undefined) },
        configurable: true,
        writable: true,
      });
      Object.defineProperty(window, 'isSecureContext', {
        value: true,
        configurable: true,
        writable: true,
      });
    });

    it('sets copied=true after successful copy', async () => {
      const { result } = renderHook(() => useCopyToClipboard());

      await act(async () => {
        await result.current.copyToClipboard('hello world');
      });

      expect(result.current.copied).toBe(true);
      expect(result.current.copying).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('calls navigator.clipboard.writeText with the provided text', async () => {
      const { result } = renderHook(() => useCopyToClipboard());

      await act(async () => {
        await result.current.copyToClipboard('test text');
      });

      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('test text');
    });

    it('auto-dismisses copied state after feedbackDuration', async () => {
      const { result } = renderHook(() => useCopyToClipboard(1000));

      await act(async () => {
        await result.current.copyToClipboard('text');
      });

      expect(result.current.copied).toBe(true);

      act(() => {
        vi.advanceTimersByTime(1000);
      });

      expect(result.current.copied).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Fallback to execCommand
  // -------------------------------------------------------------------------

  describe('execCommand fallback (no Clipboard API)', () => {
    beforeEach(() => {
      Object.defineProperty(navigator, 'clipboard', {
        value: undefined,
        configurable: true,
        writable: true,
      });
      Object.defineProperty(window, 'isSecureContext', {
        value: false,
        configurable: true,
        writable: true,
      });
      // Mock execCommand
      document.execCommand = vi.fn().mockReturnValue(true);
    });

    it('sets copied=true via execCommand fallback', async () => {
      const { result } = renderHook(() => useCopyToClipboard());

      await act(async () => {
        await result.current.copyToClipboard('fallback text');
      });

      expect(result.current.copied).toBe(true);
      expect(result.current.error).toBeNull();
    });

    it('calls document.execCommand("copy")', async () => {
      const { result } = renderHook(() => useCopyToClipboard());

      await act(async () => {
        await result.current.copyToClipboard('text');
      });

      expect(document.execCommand).toHaveBeenCalledWith('copy');
    });
  });

  // -------------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------------

  describe('error handling', () => {
    it('sets error message when copy throws', async () => {
      Object.defineProperty(navigator, 'clipboard', {
        value: {
          writeText: vi.fn().mockRejectedValue(new Error('Permission denied')),
        },
        configurable: true,
        writable: true,
      });
      Object.defineProperty(window, 'isSecureContext', {
        value: true,
        configurable: true,
        writable: true,
      });

      const { result } = renderHook(() => useCopyToClipboard());

      await act(async () => {
        await result.current.copyToClipboard('text');
      });

      expect(result.current.error).toBe('Permission denied');
      expect(result.current.copied).toBe(false);
      expect(result.current.copying).toBe(false);
    });

    it('auto-dismisses error after feedbackDuration', async () => {
      Object.defineProperty(navigator, 'clipboard', {
        value: {
          writeText: vi.fn().mockRejectedValue(new Error('Denied')),
        },
        configurable: true,
        writable: true,
      });
      Object.defineProperty(window, 'isSecureContext', {
        value: true,
        configurable: true,
        writable: true,
      });

      const { result } = renderHook(() => useCopyToClipboard(500));

      await act(async () => {
        await result.current.copyToClipboard('text');
      });

      expect(result.current.error).toBe('Denied');

      act(() => {
        vi.advanceTimersByTime(500);
      });

      expect(result.current.error).toBeNull();
    });

    it('uses generic message for non-Error throws', async () => {
      Object.defineProperty(navigator, 'clipboard', {
        value: {
          writeText: vi.fn().mockRejectedValue('string error'),
        },
        configurable: true,
        writable: true,
      });
      Object.defineProperty(window, 'isSecureContext', {
        value: true,
        configurable: true,
        writable: true,
      });

      const { result } = renderHook(() => useCopyToClipboard());

      await act(async () => {
        await result.current.copyToClipboard('text');
      });

      expect(result.current.error).toBe('Failed to copy to clipboard');
    });
  });

  // -------------------------------------------------------------------------
  // reset()
  // -------------------------------------------------------------------------

  describe('reset()', () => {
    it('clears copied, error, and copying state', async () => {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: vi.fn().mockResolvedValue(undefined) },
        configurable: true,
        writable: true,
      });
      Object.defineProperty(window, 'isSecureContext', {
        value: true,
        configurable: true,
        writable: true,
      });

      const { result } = renderHook(() => useCopyToClipboard(60000));

      await act(async () => {
        await result.current.copyToClipboard('text');
      });

      expect(result.current.copied).toBe(true);

      act(() => {
        result.current.reset();
      });

      expect(result.current.copied).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.copying).toBe(false);
    });
  });
});
