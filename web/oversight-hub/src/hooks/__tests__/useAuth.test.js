/**
 * useAuth.test.js
 *
 * Unit tests for the useAuth hook.
 *
 * Covers:
 * - Returns user, isAuthenticated, loading, error, accessToken from store
 * - loading = !authInitialized
 * - error is always null
 * - logout() calls authService.logout then store.logout
 * - logout() calls store.logout even if authService throws
 * - setUser / setIsAuthenticated / setAccessToken / setAuthInitialized
 *
 * Closes #919 (partial).
 */

import { renderHook, act } from '@testing-library/react';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const { mockAuthServiceLogout } = vi.hoisted(() => ({
  mockAuthServiceLogout: vi.fn(),
}));

vi.mock('@/lib/logger', () => ({
  default: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../services/authService', () => ({
  logout: mockAuthServiceLogout,
}));

// We need the real Zustand store for this hook test
// but clear it between runs
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  mockAuthServiceLogout.mockResolvedValue(undefined);
});

// Import after mocks
import { useAuth } from '../useAuth';
import useStore from '../../store/useStore';

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useAuth', () => {
  // ---- State reads --------------------------------------------------------

  it('returns user from store', () => {
    act(() => useStore.getState().setUser({ id: 'u1', name: 'Alice' }));

    const { result } = renderHook(() => useAuth());
    expect(result.current.user).toEqual({ id: 'u1', name: 'Alice' });
  });

  it('returns isAuthenticated from store', () => {
    act(() => useStore.getState().setIsAuthenticated(true));

    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(true);
  });

  it('returns accessToken from store', () => {
    act(() => useStore.getState().setAccessToken('tok-123'));

    const { result } = renderHook(() => useAuth());
    expect(result.current.accessToken).toBe('tok-123');
  });

  it('loading is true when authInitialized is false', () => {
    act(() => useStore.getState().setAuthInitialized(false));

    const { result } = renderHook(() => useAuth());
    expect(result.current.loading).toBe(true);
  });

  it('loading is false when authInitialized is true', () => {
    act(() => useStore.getState().setAuthInitialized(true));

    const { result } = renderHook(() => useAuth());
    expect(result.current.loading).toBe(false);
  });

  it('error is always null', () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.error).toBeNull();
  });

  // ---- Actions ------------------------------------------------------------

  it('setUser updates user in store', () => {
    const { result } = renderHook(() => useAuth());

    act(() => result.current.setUser({ id: 'u2' }));
    expect(useStore.getState().user).toEqual({ id: 'u2' });
  });

  it('setIsAuthenticated updates store', () => {
    const { result } = renderHook(() => useAuth());

    act(() => result.current.setIsAuthenticated(true));
    expect(useStore.getState().isAuthenticated).toBe(true);
  });

  it('setAccessToken updates store', () => {
    const { result } = renderHook(() => useAuth());

    act(() => result.current.setAccessToken('new-tok'));
    expect(useStore.getState().accessToken).toBe('new-tok');
  });

  it('setAuthInitialized updates store', () => {
    const { result } = renderHook(() => useAuth());

    act(() => result.current.setAuthInitialized(true));
    expect(useStore.getState().authInitialized).toBe(true);
  });

  // ---- logout() -----------------------------------------------------------

  it('logout calls authService.logout then clears store', async () => {
    act(() => {
      useStore.getState().setUser({ id: 'u1' });
      useStore.getState().setIsAuthenticated(true);
      useStore.getState().setAccessToken('tok');
    });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.logout();
    });

    expect(mockAuthServiceLogout).toHaveBeenCalled();
    expect(useStore.getState().user).toBeNull();
    expect(useStore.getState().isAuthenticated).toBe(false);
    expect(useStore.getState().accessToken).toBeNull();
  });

  it('logout clears store even if authService.logout throws', async () => {
    mockAuthServiceLogout.mockRejectedValue(new Error('service down'));

    act(() => {
      useStore.getState().setUser({ id: 'u1' });
      useStore.getState().setIsAuthenticated(true);
    });

    const { result } = renderHook(() => useAuth());

    await act(async () => {
      await result.current.logout();
    });

    // Store still cleared despite error
    expect(useStore.getState().user).toBeNull();
    expect(useStore.getState().isAuthenticated).toBe(false);
  });

  // ---- Return shape -------------------------------------------------------

  it('returns expected properties', () => {
    const { result } = renderHook(() => useAuth());

    expect(result.current).toHaveProperty('user');
    expect(result.current).toHaveProperty('isAuthenticated');
    expect(result.current).toHaveProperty('loading');
    expect(result.current).toHaveProperty('error');
    expect(result.current).toHaveProperty('accessToken');
    expect(result.current).toHaveProperty('logout');
    expect(result.current).toHaveProperty('setUser');
    expect(result.current).toHaveProperty('setIsAuthenticated');
    expect(result.current).toHaveProperty('setAccessToken');
    expect(result.current).toHaveProperty('setAuthInitialized');
    expect(typeof result.current.logout).toBe('function');
  });
});
