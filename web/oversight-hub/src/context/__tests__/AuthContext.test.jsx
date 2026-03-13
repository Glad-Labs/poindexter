/**
 * Tests for AuthContext.jsx — auth state provider (Issue #573)
 *
 * Covers:
 * - Initial loading state
 * - Successful session restore via validateAndGetCurrentUser
 * - Fallback to stored user when session check fails
 * - Unauthenticated initial state when no user exists
 * - logout() clears state and calls Zustand store
 * - setAuthUser() syncs user to both context and Zustand store
 * - handleOAuthCallback() updates auth state on success
 * - validateCurrentUser() re-checks the session
 * - Error during initialization sets error state
 */

import React from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { AuthContext, AuthProvider } from '../AuthContext';

// Mock logger before any imports that might use it
vi.mock('@/lib/logger', () => ({
  default: {
    log: vi.fn(),
    error: vi.fn(),
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

// Mock Zustand store
const mockSetUser = vi.fn();
const mockSetIsAuthenticated = vi.fn();
const mockSetAuthInitialized = vi.fn();
const mockStoreLogout = vi.fn();

vi.mock('../../store/useStore', () => ({
  default: vi.fn((selector) =>
    selector({
      setUser: mockSetUser,
      setIsAuthenticated: mockSetIsAuthenticated,
      setAuthInitialized: mockSetAuthInitialized,
      logout: mockStoreLogout,
    })
  ),
}));

// Mock authService functions
const mockValidateAndGetCurrentUser = vi.fn();
const mockGetStoredUser = vi.fn();
const mockAuthLogout = vi.fn();
const mockHandleOAuthCallbackNew = vi.fn();

vi.mock('../../services/authService', () => ({
  validateAndGetCurrentUser: (...args) =>
    mockValidateAndGetCurrentUser(...args),
  getStoredUser: (...args) => mockGetStoredUser(...args),
  logout: (...args) => mockAuthLogout(...args),
  handleOAuthCallbackNew: (...args) => mockHandleOAuthCallbackNew(...args),
}));

const MOCK_USER = {
  id: 'user-1',
  login: 'testuser',
  email: 'test@example.com',
};

// Helper: render AuthProvider and expose context value
function renderAuthProvider(ui = null) {
  let contextValue;
  const Spy = () => {
    contextValue = React.useContext(AuthContext);
    return null;
  };
  const wrapper = render(
    <AuthProvider>
      <Spy />
      {ui}
    </AuthProvider>
  );
  return { wrapper, getContext: () => contextValue };
}

describe('AuthContext — initialization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStoredUser.mockReturnValue(null);
  });

  it('starts with loading=true before async init completes', () => {
    // Never resolve — hold in loading state
    mockValidateAndGetCurrentUser.mockReturnValue(new Promise(() => {}));

    const { getContext } = renderAuthProvider();
    expect(getContext().loading).toBe(true);
  });

  it('sets isAuthenticated=true when session is valid', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);

    const { getContext } = renderAuthProvider();

    await waitFor(() => expect(getContext().loading).toBe(false));
    expect(getContext().isAuthenticated).toBe(true);
    expect(getContext().user).toEqual(MOCK_USER);
  });

  it('calls Zustand setUser with valid user on session restore', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    renderAuthProvider();

    await waitFor(() => expect(mockSetUser).toHaveBeenCalledWith(MOCK_USER));
    expect(mockSetIsAuthenticated).toHaveBeenCalledWith(true);
  });

  it('sets isAuthenticated=false when no active session', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(null);

    const { getContext } = renderAuthProvider();

    await waitFor(() => expect(getContext().loading).toBe(false));
    expect(getContext().isAuthenticated).toBe(false);
    expect(getContext().user).toBeNull();
  });

  it('falls back to storedUser for UI continuity when session check fails', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(null);
    mockGetStoredUser.mockReturnValue(MOCK_USER);

    const { getContext } = renderAuthProvider();

    await waitFor(() => expect(getContext().loading).toBe(false));
    // user is populated from storage but isAuthenticated stays false
    expect(getContext().user).toEqual(MOCK_USER);
    expect(getContext().isAuthenticated).toBe(false);
  });

  it('sets error state when initialization throws', async () => {
    mockValidateAndGetCurrentUser.mockRejectedValue(new Error('Network error'));

    const { getContext } = renderAuthProvider();

    await waitFor(() => expect(getContext().loading).toBe(false));
    expect(getContext().error).toBe('Network error');
    expect(getContext().isAuthenticated).toBe(false);
  });

  it('calls setAuthInitialized(true) after init completes', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(null);
    renderAuthProvider();

    await waitFor(() =>
      expect(mockSetAuthInitialized).toHaveBeenCalledWith(true)
    );
  });
});

describe('AuthContext — logout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStoredUser.mockReturnValue(null);
  });

  it('clears user and isAuthenticated on logout', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    mockAuthLogout.mockResolvedValue(undefined);

    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    await act(async () => {
      await getContext().logout();
    });

    expect(getContext().user).toBeNull();
    expect(getContext().isAuthenticated).toBe(false);
  });

  it('calls Zustand storeLogout on logout', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    mockAuthLogout.mockResolvedValue(undefined);

    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    await act(async () => {
      await getContext().logout();
    });

    expect(mockStoreLogout).toHaveBeenCalled();
  });

  it('sets error state when logout throws', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    mockAuthLogout.mockRejectedValue(new Error('Logout failed'));

    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    await act(async () => {
      await getContext().logout();
    });

    expect(getContext().error).toBe('Logout failed');
  });
});

describe('AuthContext — setAuthUser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStoredUser.mockReturnValue(null);
    mockValidateAndGetCurrentUser.mockResolvedValue(null);
  });

  it('sets user and isAuthenticated=true when called with a user', async () => {
    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    act(() => {
      getContext().setAuthUser(MOCK_USER);
    });

    expect(getContext().user).toEqual(MOCK_USER);
    expect(getContext().isAuthenticated).toBe(true);
  });

  it('sets isAuthenticated=false when called with null', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    act(() => {
      getContext().setAuthUser(null);
    });

    expect(getContext().isAuthenticated).toBe(false);
  });

  it('syncs Zustand store when setAuthUser is called', async () => {
    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    vi.clearAllMocks(); // clear init calls

    act(() => {
      getContext().setAuthUser(MOCK_USER);
    });

    expect(mockSetUser).toHaveBeenCalledWith(MOCK_USER);
    expect(mockSetIsAuthenticated).toHaveBeenCalledWith(true);
  });
});

describe('AuthContext — handleOAuthCallback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStoredUser.mockReturnValue(null);
    mockValidateAndGetCurrentUser.mockResolvedValue(null);
  });

  it('sets user on successful OAuth callback', async () => {
    mockHandleOAuthCallbackNew.mockResolvedValue({ user: MOCK_USER });

    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    let returned;
    await act(async () => {
      returned = await getContext().handleOAuthCallback(
        'github',
        'code-123',
        'state-abc'
      );
    });

    expect(returned).toEqual(MOCK_USER);
    expect(getContext().isAuthenticated).toBe(true);
  });

  it('throws when OAuth returns no user', async () => {
    mockHandleOAuthCallbackNew.mockResolvedValue({ user: null });

    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    await expect(
      act(async () => {
        await getContext().handleOAuthCallback('github', 'bad-code', 'state');
      })
    ).rejects.toThrow();
  });
});

describe('AuthContext — validateCurrentUser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStoredUser.mockReturnValue(null);
  });

  it('returns user and updates state when session is still valid', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(null);
    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    let result;
    await act(async () => {
      result = await getContext().validateCurrentUser();
    });

    expect(result).toEqual(MOCK_USER);
    expect(getContext().isAuthenticated).toBe(true);
  });

  it('clears auth state when re-validation fails', async () => {
    mockValidateAndGetCurrentUser.mockResolvedValue(MOCK_USER);
    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));
    expect(getContext().isAuthenticated).toBe(true);

    mockValidateAndGetCurrentUser.mockResolvedValue(null);
    let result;
    await act(async () => {
      result = await getContext().validateCurrentUser();
    });

    expect(result).toBeNull();
    expect(getContext().isAuthenticated).toBe(false);
  });
});

describe('AuthContext — context value shape', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStoredUser.mockReturnValue(null);
    mockValidateAndGetCurrentUser.mockResolvedValue(null);
  });

  it('exposes all required properties', async () => {
    const { getContext } = renderAuthProvider();
    await waitFor(() => expect(getContext().loading).toBe(false));

    const ctx = getContext();
    expect(typeof ctx.user).toBeDefined();
    expect(typeof ctx.loading).toBe('boolean');
    expect(typeof ctx.isAuthenticated).toBe('boolean');
    expect(typeof ctx.logout).toBe('function');
    expect(typeof ctx.setAuthUser).toBe('function');
    expect(typeof ctx.handleOAuthCallback).toBe('function');
    expect(typeof ctx.validateCurrentUser).toBe('function');
  });
});
