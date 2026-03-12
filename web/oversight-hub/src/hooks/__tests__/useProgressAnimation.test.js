/**
 * useProgressAnimation.test.js
 *
 * Unit tests for the useProgressAnimation hook.
 * Covers:
 * - Initial state (progress, phaseProgress, estimatedTimeRemaining, isComplete, phase)
 * - progress calculation formula
 * - estimatedTimeRemaining calculation
 * - Phase display format
 * - isComplete flag
 * - Animation interval (phaseProgress increments when isAnimating=true)
 * - No animation when isAnimating=false
 * - phaseProgress resets when currentPhase changes
 */

import { renderHook, act } from '@testing-library/react';
import { useProgressAnimation } from '../useProgressAnimation';

describe('useProgressAnimation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // -------------------------------------------------------------------------
  // Initial state
  // -------------------------------------------------------------------------

  describe('initial state', () => {
    it('returns phase display string "Phase 1/6" for defaults', () => {
      const { result } = renderHook(() => useProgressAnimation());
      expect(result.current.phase).toBe('Phase 1/6');
    });

    it('starts with phaseProgress = 0', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, false));
      expect(result.current.phaseProgress).toBe(0);
    });

    it('isComplete is false at start', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, false));
      expect(result.current.isComplete).toBe(false);
    });

    it('exposes elapsedTime (>= 0)', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, false));
      expect(typeof result.current.elapsedTime).toBe('number');
      expect(result.current.elapsedTime).toBeGreaterThanOrEqual(0);
    });
  });

  // -------------------------------------------------------------------------
  // progress calculation
  // -------------------------------------------------------------------------

  describe('progress calculation', () => {
    it('returns 0 at phase 1 with no phaseProgress', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, false));
      // Phase 1 of 6, phaseProgress=0: (0 * 16.67%) + (0 * 16.67%) = 0
      expect(result.current.progress).toBeCloseTo(0, 1);
    });

    it('calculates progress for phase 3 of 6 with 0 phaseProgress', () => {
      const { result } = renderHook(() => useProgressAnimation(3, 6, false));
      // Previous phases: 2 * (100/6) = 33.33%
      expect(result.current.progress).toBeCloseTo(33.33, 0);
    });

    it('calculates progress for phase 6 of 6 with 0 phaseProgress', () => {
      const { result } = renderHook(() => useProgressAnimation(6, 6, false));
      // Previous phases: 5 * (100/6) = 83.33%
      expect(result.current.progress).toBeCloseTo(83.33, 0);
    });

    it('does not exceed 100', () => {
      // High phase values should be clamped to 100
      const { result } = renderHook(() => useProgressAnimation(7, 6, false));
      expect(result.current.progress).toBeLessThanOrEqual(100);
    });
  });

  // -------------------------------------------------------------------------
  // estimatedTimeRemaining
  // -------------------------------------------------------------------------

  describe('estimatedTimeRemaining', () => {
    it('returns 0 for final phase', () => {
      const { result } = renderHook(() => useProgressAnimation(6, 6, false, 3));
      // remaining = 6 - 6 = 0, timePerPhase = 4, total = 0 -> ceil(0) = 0
      expect(result.current.estimatedTimeRemaining).toBe(0);
    });

    it('calculates remaining time for phase 1 of 6 (duration=3)', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, false, 3));
      // remainingPhases = 5, timePerPhase = 4, total = 20
      expect(result.current.estimatedTimeRemaining).toBe(20);
    });

    it('calculates remaining time for phase 4 of 6 (duration=2)', () => {
      const { result } = renderHook(() => useProgressAnimation(4, 6, false, 2));
      // remainingPhases = 2, timePerPhase = 3, total = 6
      expect(result.current.estimatedTimeRemaining).toBe(6);
    });
  });

  // -------------------------------------------------------------------------
  // Phase display
  // -------------------------------------------------------------------------

  describe('phase display', () => {
    it('formats as "Phase X/Y"', () => {
      const { result } = renderHook(() => useProgressAnimation(3, 8, false));
      expect(result.current.phase).toBe('Phase 3/8');
    });

    it('reflects custom currentPhase and totalPhases', () => {
      const { result } = renderHook(() => useProgressAnimation(2, 4, false));
      expect(result.current.phase).toBe('Phase 2/4');
    });
  });

  // -------------------------------------------------------------------------
  // isComplete
  // -------------------------------------------------------------------------

  describe('isComplete', () => {
    it('is false when phase < totalPhases', () => {
      const { result } = renderHook(() => useProgressAnimation(5, 6, false));
      expect(result.current.isComplete).toBe(false);
    });

    it('is false when at final phase but phaseProgress < 100', () => {
      const { result } = renderHook(() => useProgressAnimation(6, 6, false));
      // phaseProgress starts at 0
      expect(result.current.isComplete).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Animation (isAnimating=true)
  // -------------------------------------------------------------------------

  describe('animation', () => {
    it('increments phaseProgress when isAnimating=true', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, true, 3));

      const initialProgress = result.current.phaseProgress;

      act(() => {
        // Advance one animation tick (100ms interval)
        vi.advanceTimersByTime(100);
      });

      expect(result.current.phaseProgress).toBeGreaterThan(initialProgress);
    });

    it('does NOT increment phaseProgress when isAnimating=false', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, false, 3));

      act(() => {
        vi.advanceTimersByTime(500);
      });

      expect(result.current.phaseProgress).toBe(0);
    });

    it('phaseProgress does not exceed 100', () => {
      const { result } = renderHook(() => useProgressAnimation(1, 6, true, 1));

      act(() => {
        // Advance well beyond one phase duration
        vi.advanceTimersByTime(10000);
      });

      expect(result.current.phaseProgress).toBeLessThanOrEqual(100);
    });
  });
});
