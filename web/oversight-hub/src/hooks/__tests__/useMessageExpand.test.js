/**
 * useMessageExpand.test.js
 *
 * Unit tests for the useMessageExpand hook.
 * Covers:
 * - Default state (collapsed)
 * - defaultOpen=true initialization
 * - toggle() simple state flip
 * - handleToggle() fires onExpand/onCollapse callbacks
 * - setExpanded() controlled setter with callbacks
 */

import { renderHook, act } from '@testing-library/react';
import { useMessageExpand } from '../useMessageExpand';

describe('useMessageExpand', () => {
  // -------------------------------------------------------------------------
  // Initial state
  // -------------------------------------------------------------------------

  describe('initial state', () => {
    it('defaults to collapsed (expanded=false)', () => {
      const { result } = renderHook(() => useMessageExpand());
      expect(result.current.expanded).toBe(false);
    });

    it('respects defaultOpen=true', () => {
      const { result } = renderHook(() => useMessageExpand(true));
      expect(result.current.expanded).toBe(true);
    });

    it('exposes toggle, setExpanded, handleToggle functions', () => {
      const { result } = renderHook(() => useMessageExpand());
      expect(typeof result.current.toggle).toBe('function');
      expect(typeof result.current.setExpanded).toBe('function');
      expect(typeof result.current.handleToggle).toBe('function');
    });
  });

  // -------------------------------------------------------------------------
  // toggle()
  // -------------------------------------------------------------------------

  describe('toggle()', () => {
    it('flips collapsed to expanded', () => {
      const { result } = renderHook(() => useMessageExpand(false));

      act(() => {
        result.current.toggle();
      });

      expect(result.current.expanded).toBe(true);
    });

    it('flips expanded to collapsed', () => {
      const { result } = renderHook(() => useMessageExpand(true));

      act(() => {
        result.current.toggle();
      });

      expect(result.current.expanded).toBe(false);
    });

    it('does NOT call onExpand/onCollapse callbacks', () => {
      const onExpand = vi.fn();
      const onCollapse = vi.fn();
      const { result } = renderHook(() =>
        useMessageExpand(false, onExpand, onCollapse)
      );

      act(() => {
        result.current.toggle();
      });

      // toggle() is the simple version — no callbacks
      expect(onExpand).not.toHaveBeenCalled();
      expect(onCollapse).not.toHaveBeenCalled();
    });
  });

  // -------------------------------------------------------------------------
  // handleToggle()
  // -------------------------------------------------------------------------

  describe('handleToggle()', () => {
    it('expands from collapsed and calls onExpand', () => {
      const onExpand = vi.fn();
      const onCollapse = vi.fn();
      const { result } = renderHook(() =>
        useMessageExpand(false, onExpand, onCollapse)
      );

      act(() => {
        result.current.handleToggle();
      });

      expect(result.current.expanded).toBe(true);
      expect(onExpand).toHaveBeenCalledTimes(1);
      expect(onCollapse).not.toHaveBeenCalled();
    });

    it('collapses from expanded and calls onCollapse', () => {
      const onExpand = vi.fn();
      const onCollapse = vi.fn();
      const { result } = renderHook(() =>
        useMessageExpand(true, onExpand, onCollapse)
      );

      act(() => {
        result.current.handleToggle();
      });

      expect(result.current.expanded).toBe(false);
      expect(onCollapse).toHaveBeenCalledTimes(1);
      expect(onExpand).not.toHaveBeenCalled();
    });

    it('works without callbacks (no crash when callbacks are undefined)', () => {
      const { result } = renderHook(() => useMessageExpand(false));

      expect(() => {
        act(() => {
          result.current.handleToggle();
        });
      }).not.toThrow();

      expect(result.current.expanded).toBe(true);
    });

    it('alternates state on each call', () => {
      const onExpand = vi.fn();
      const onCollapse = vi.fn();
      const { result } = renderHook(() =>
        useMessageExpand(false, onExpand, onCollapse)
      );

      act(() => {
        result.current.handleToggle();
      });
      expect(result.current.expanded).toBe(true);

      act(() => {
        result.current.handleToggle();
      });
      expect(result.current.expanded).toBe(false);

      expect(onExpand).toHaveBeenCalledTimes(1);
      expect(onCollapse).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // setExpanded() — controlled setter
  // -------------------------------------------------------------------------

  describe('setExpanded() controlled setter', () => {
    it('sets to true and calls onExpand', () => {
      const onExpand = vi.fn();
      const onCollapse = vi.fn();
      const { result } = renderHook(() =>
        useMessageExpand(false, onExpand, onCollapse)
      );

      act(() => {
        result.current.setExpanded(true);
      });

      expect(result.current.expanded).toBe(true);
      expect(onExpand).toHaveBeenCalledTimes(1);
      expect(onCollapse).not.toHaveBeenCalled();
    });

    it('sets to false and calls onCollapse', () => {
      const onExpand = vi.fn();
      const onCollapse = vi.fn();
      const { result } = renderHook(() =>
        useMessageExpand(true, onExpand, onCollapse)
      );

      act(() => {
        result.current.setExpanded(false);
      });

      expect(result.current.expanded).toBe(false);
      expect(onCollapse).toHaveBeenCalledTimes(1);
      expect(onExpand).not.toHaveBeenCalled();
    });

    it('works without callbacks', () => {
      const { result } = renderHook(() => useMessageExpand(false));

      expect(() => {
        act(() => {
          result.current.setExpanded(true);
        });
      }).not.toThrow();

      expect(result.current.expanded).toBe(true);
    });
  });
});
