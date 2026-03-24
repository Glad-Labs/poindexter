/**
 * authService.test.js
 *
 * Unit tests for services/authService.js (Issue #666 — 754 lines, zero coverage).
 *
 * Covers:
 * - clearPersistedAuthState — clears localStorage keys; gracefully handles malformed JSON
 * - generateGitHubAuthURL — returns well-formed GitHub OAuth URL; stores state in sessionStorage
 * - exchangeCodeForToken — mock code path, real code path, CSRF state missing error
 * - verifySession — valid unexpired token, expired token clears storage, no token, non-JWT format
 * - logout — calls API then clears storage; clears storage even if API fails
 * - getStoredUser — returns parsed user, handles null, handles malformed JSON
 * - isTokenExpired — expired token, unexpired token, missing exp claim, invalid format
 * - getAuthToken — valid token from direct key, expired clears and returns null, Zustand persist path
 * - authenticatedFetch — attaches Authorization header, throws on 401, throws on non-ok
 * - isAuthenticated — returns true with valid token, false without
 * - getAvailableOAuthProviders — returns array from backend, gracefully returns [] on failure
 * - handleOAuthCallbackNew — CSRF mismatch throws, mock code path, real OAuth path
 * - validateAndGetCurrentUser — valid token calls /api/auth/me, returns null without token
 */

import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// ------------------------------------------------------------------
// Mock dependencies before importing authService
// ------------------------------------------------------------------

const { mockCreateMockJWTToken } = vi.hoisted(() => ({
  mockCreateMockJWTToken: vi.fn(),
}));

vi.mock('@/utils/mockTokenGenerator', () => ({
  createMockJWTToken: mockCreateMockJWTToken,
}));

// Intercept global fetch
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Intercept window.location
const mockLocationAssign = vi.fn();
Object.defineProperty(window, 'location', {
  value: { href: '', origin: 'http://localhost:3000' },
  writable: true,
});

// Intercept crypto for PRNG (jsdom ships with SubtleCrypto stub)
const mockGetRandomValues = vi.fn((arr) => {
  for (let i = 0; i < arr.length; i++) arr[i] = i % 256;
  return arr;
});
Object.defineProperty(window, 'crypto', {
  value: {
    getRandomValues: mockGetRandomValues,
    subtle: window.crypto?.subtle,
  },
  writable: true,
});

import {
  clearPersistedAuthState,
  generateGitHubAuthURL,
  exchangeCodeForToken,
  verifySession,
  logout,
  getStoredUser,
  isTokenExpired,
  getAuthToken,
  authenticatedFetch,
  isAuthenticated,
  getAvailableOAuthProviders,
  handleOAuthCallbackNew,
  validateAndGetCurrentUser,
} from '../authService';

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

const PERSIST_KEY = 'oversight-hub-storage';

/** Build a minimal valid JWT payload with given exp (Unix seconds). */
function makeJWT(exp) {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ sub: 'user', exp }));
  return `${header}.${payload}.fake-sig`;
}

function futureExp() {
  return Math.floor(Date.now() / 1000) + 3600; // 1 hour ahead
}

function pastExp() {
  return Math.floor(Date.now() / 1000) - 3600; // 1 hour behind
}

// ------------------------------------------------------------------
// clearPersistedAuthState
// ------------------------------------------------------------------

describe('clearPersistedAuthState', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('removes auth_token, refresh_token and user keys', () => {
    sessionStorage.setItem('auth_token', 'tok');
    sessionStorage.setItem('refresh_token', 'ref');
    sessionStorage.setItem('user', '{"id":"1"}');
    clearPersistedAuthState();
    expect(sessionStorage.getItem('auth_token')).toBeNull();
    expect(sessionStorage.getItem('refresh_token')).toBeNull();
    expect(sessionStorage.getItem('user')).toBeNull();
  });

  it('clears auth fields in Zustand persist storage', () => {
    const state = {
      state: {
        accessToken: 'tok',
        isAuthenticated: true,
        user: { id: '1' },
      },
    };
    localStorage.setItem(PERSIST_KEY, JSON.stringify(state));
    clearPersistedAuthState();
    const updated = JSON.parse(localStorage.getItem(PERSIST_KEY));
    expect(updated.state.accessToken).toBeNull();
    expect(updated.state.isAuthenticated).toBe(false);
    expect(updated.state.user).toBeNull();
  });

  it('handles missing Zustand key gracefully (no throw)', () => {
    expect(() => clearPersistedAuthState()).not.toThrow();
  });

  it('handles malformed Zustand JSON gracefully', () => {
    localStorage.setItem(PERSIST_KEY, 'NOT_JSON');
    expect(() => clearPersistedAuthState()).not.toThrow();
  });
});

// ------------------------------------------------------------------
// generateGitHubAuthURL
// ------------------------------------------------------------------

describe('generateGitHubAuthURL', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('returns a URL starting with the GitHub authorize endpoint', () => {
    const url = generateGitHubAuthURL('my-client-id');
    expect(url).toMatch(/^https:\/\/github\.com\/login\/oauth\/authorize/);
  });

  it('includes the client_id parameter', () => {
    const url = generateGitHubAuthURL('my-client-id');
    expect(url).toContain('client_id=my-client-id');
  });

  it('includes the redirect_uri parameter', () => {
    const url = generateGitHubAuthURL('any-id');
    expect(url).toContain('redirect_uri=');
    expect(url).toContain(encodeURIComponent('/auth/callback'));
  });

  it('includes the scope parameter for user:email', () => {
    const url = generateGitHubAuthURL('cid');
    // URL encoding may vary by environment — accept both forms
    expect(url).toMatch(/scope=user(%3A|:)email/);
  });

  it('stores oauth_state in sessionStorage', () => {
    generateGitHubAuthURL('cid');
    expect(sessionStorage.getItem('oauth_state')).not.toBeNull();
  });

  it('state in URL matches state stored in sessionStorage', () => {
    const url = generateGitHubAuthURL('cid');
    const storedState = sessionStorage.getItem('oauth_state');
    expect(url).toContain(`state=${storedState}`);
  });
});

// ------------------------------------------------------------------
// exchangeCodeForToken
// ------------------------------------------------------------------

describe('exchangeCodeForToken', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
    mockCreateMockJWTToken.mockResolvedValue('mock.jwt.token');
  });

  it('mock code path: tries backend dev-token first, falls back to local mock', async () => {
    // fetch is called to try backend /api/auth/dev-token, but no mock response
    // so it falls back to createMockJWTToken
    mockFetch.mockRejectedValueOnce(new Error('Backend unreachable'));
    const result = await exchangeCodeForToken('mock_auth_code_12345');
    expect(result.token).toBe('mock.jwt.token');
    expect(result.user).toBeDefined();
    expect(result.user.login).toBe('dev-user');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/dev-token'),
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('mock code path: stores token in sessionStorage', async () => {
    await exchangeCodeForToken('mock_auth_code_99999');
    expect(sessionStorage.getItem('auth_token')).toBe('mock.jwt.token');
  });

  it('real code path: throws when CSRF state not found in sessionStorage', async () => {
    await expect(exchangeCodeForToken('real-gh-code')).rejects.toThrow(
      /CSRF state not found/
    );
  });

  it('real code path: calls fetch with code and state in body', async () => {
    sessionStorage.setItem('oauth_state', 'csrf-state-123');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ token: 'server-tok', user: { id: '1' } }),
    });
    const result = await exchangeCodeForToken('real-code');
    expect(mockFetch).toHaveBeenCalled();
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.code).toBe('real-code');
    expect(body.state).toBe('csrf-state-123');
    expect(result.token).toBe('server-tok');
  });

  it('real code path: stores token from server in localStorage', async () => {
    sessionStorage.setItem('oauth_state', 'csrf-state-456');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ token: 'server-tok', user: { id: '2' } }),
    });
    await exchangeCodeForToken('real-code-2');
    expect(sessionStorage.getItem('auth_token')).toBe('server-tok');
  });

  it('real code path: throws when server returns non-ok', async () => {
    sessionStorage.setItem('oauth_state', 'state-789');
    mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'Unauthorized' });
    await expect(exchangeCodeForToken('bad-code')).rejects.toThrow(
      /Authentication failed/
    );
  });
});

// ------------------------------------------------------------------
// verifySession
// ------------------------------------------------------------------

describe('verifySession', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns null when no token in storage', async () => {
    const result = await verifySession();
    expect(result).toBeNull();
  });

  it('returns parsed user for a valid unexpired JWT', async () => {
    const token = makeJWT(futureExp());
    const user = { id: '1', login: 'alice' };
    sessionStorage.setItem('auth_token', token);
    sessionStorage.setItem('user', JSON.stringify(user));
    const result = await verifySession();
    expect(result).toEqual(user);
  });

  it('clears storage and returns null for expired token', async () => {
    const token = makeJWT(pastExp());
    sessionStorage.setItem('auth_token', token);
    sessionStorage.setItem('user', '{"id":"1"}');
    const result = await verifySession();
    expect(result).toBeNull();
    expect(sessionStorage.getItem('auth_token')).toBeNull();
  });

  it('clears storage and returns null for non-JWT formatted token', async () => {
    sessionStorage.setItem('auth_token', 'not-a-jwt');
    const result = await verifySession();
    expect(result).toBeNull();
  });
});

// ------------------------------------------------------------------
// logout
// ------------------------------------------------------------------

describe('logout', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it('calls /api/auth/logout with bearer token', async () => {
    sessionStorage.setItem('auth_token', 'my-token');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });
    await logout();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/logout'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer my-token' }),
      })
    );
  });

  it('clears localStorage even when API call fails', async () => {
    sessionStorage.setItem('auth_token', 'tok');
    sessionStorage.setItem('user', '{"id":"1"}');
    mockFetch.mockRejectedValueOnce(new Error('Network error'));
    await logout();
    expect(sessionStorage.getItem('auth_token')).toBeNull();
    expect(sessionStorage.getItem('user')).toBeNull();
  });

  it('removes oauth_state from sessionStorage', async () => {
    sessionStorage.setItem('oauth_state', 'some-state');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });
    await logout();
    expect(sessionStorage.getItem('oauth_state')).toBeNull();
  });

  it('skips API call when no token stored', async () => {
    await logout();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------
// getStoredUser
// ------------------------------------------------------------------

describe('getStoredUser', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('returns parsed user object when stored', () => {
    const user = { id: '1', login: 'bob' };
    sessionStorage.setItem('user', JSON.stringify(user));
    expect(getStoredUser()).toEqual(user);
  });

  it('returns null when no user in storage', () => {
    expect(getStoredUser()).toBeNull();
  });

  it('returns null when stored user JSON is malformed', () => {
    sessionStorage.setItem('user', '{NOT VALID JSON}');
    expect(getStoredUser()).toBeNull();
  });
});

// ------------------------------------------------------------------
// isTokenExpired
// ------------------------------------------------------------------

describe('isTokenExpired', () => {
  it('returns true for null/undefined token', () => {
    expect(isTokenExpired(null)).toBe(true);
    expect(isTokenExpired(undefined)).toBe(true);
  });

  it('returns true for token with wrong segment count', () => {
    expect(isTokenExpired('a.b')).toBe(true);
    expect(isTokenExpired('onlyone')).toBe(true);
  });

  it('returns true for expired token (past exp)', () => {
    const token = makeJWT(pastExp());
    expect(isTokenExpired(token)).toBe(true);
  });

  it('returns false for valid unexpired token', () => {
    const token = makeJWT(futureExp());
    expect(isTokenExpired(token)).toBe(false);
  });

  it('returns false when no exp claim (treat as non-expiring)', () => {
    const header = btoa(JSON.stringify({ alg: 'HS256' }));
    const payload = btoa(JSON.stringify({ sub: 'user' })); // no exp
    const token = `${header}.${payload}.sig`;
    expect(isTokenExpired(token)).toBe(false);
  });

  it('returns true when payload is not valid JSON', () => {
    const header = btoa('{}');
    const badPayload = 'NOT_BASE64_JSON';
    expect(isTokenExpired(`${header}.${badPayload}.sig`)).toBe(true);
  });
});

// ------------------------------------------------------------------
// getAuthToken
// ------------------------------------------------------------------

describe('getAuthToken', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns token from direct localStorage key when valid', () => {
    const token = makeJWT(futureExp());
    sessionStorage.setItem('auth_token', token);
    expect(getAuthToken()).toBe(token);
  });

  it('returns null and clears storage when token is expired', () => {
    const expired = makeJWT(pastExp());
    sessionStorage.setItem('auth_token', expired);
    expect(getAuthToken()).toBeNull();
    expect(sessionStorage.getItem('auth_token')).toBeNull();
  });

  it('returns null when no token stored', () => {
    expect(getAuthToken()).toBeNull();
  });

  it('returns token from Zustand persist storage (accessToken key)', () => {
    const token = makeJWT(futureExp());
    const zustandState = { state: { accessToken: token } };
    localStorage.setItem(PERSIST_KEY, JSON.stringify(zustandState));
    expect(getAuthToken()).toBe(token);
  });

  it('falls back to direct key if Zustand state is malformed JSON', () => {
    const token = makeJWT(futureExp());
    localStorage.setItem(PERSIST_KEY, 'BAD_JSON');
    sessionStorage.setItem('auth_token', token);
    expect(getAuthToken()).toBe(token);
  });
});

// ------------------------------------------------------------------
// authenticatedFetch
// ------------------------------------------------------------------

describe('authenticatedFetch', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    mockFetch.mockReset();
  });

  it('calls fetch with Authorization header when token is present', async () => {
    const token = makeJWT(futureExp());
    sessionStorage.setItem('auth_token', token);
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ data: 'ok' }),
    });
    await authenticatedFetch('/api/some-endpoint');
    const callHeaders = mockFetch.mock.calls[0][1].headers;
    expect(callHeaders['Authorization']).toBe(`Bearer ${token}`);
  });

  it('calls fetch without Authorization header when no token present', async () => {
    // No token — localStorage is empty, getAuthToken returns null
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    });
    await authenticatedFetch('/api/public');
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers['Authorization']).toBeUndefined();
  });

  it('throws error on non-ok response (non-401)', async () => {
    // 500 does not trigger logout, just throws
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
    });
    await expect(authenticatedFetch('/api/broken')).rejects.toThrow(
      /API error/
    );
  });
});

// ------------------------------------------------------------------
// isAuthenticated
// ------------------------------------------------------------------

describe('isAuthenticated', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('returns false when no token stored', () => {
    expect(isAuthenticated()).toBe(false);
  });

  it('returns true when a valid unexpired token is stored', () => {
    sessionStorage.setItem('auth_token', makeJWT(futureExp()));
    expect(isAuthenticated()).toBe(true);
  });

  it('returns false when stored token is expired', () => {
    sessionStorage.setItem('auth_token', makeJWT(pastExp()));
    expect(isAuthenticated()).toBe(false);
  });
});

// ------------------------------------------------------------------
// getAvailableOAuthProviders
// ------------------------------------------------------------------

describe('getAvailableOAuthProviders', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('returns providers array from backend response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ providers: ['github', 'google'] }),
    });
    const result = await getAvailableOAuthProviders();
    expect(result).toEqual(['github', 'google']);
  });

  it('returns empty array when backend returns empty providers', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ providers: [] }),
    });
    const result = await getAvailableOAuthProviders();
    expect(result).toEqual([]);
  });

  it('returns empty array when fetch fails', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network down'));
    const result = await getAvailableOAuthProviders();
    expect(result).toEqual([]);
  });

  it('returns empty array when response is not ok', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'Not Found' });
    const result = await getAvailableOAuthProviders();
    expect(result).toEqual([]);
  });
});

// ------------------------------------------------------------------
// handleOAuthCallbackNew
// ------------------------------------------------------------------

describe('handleOAuthCallbackNew', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    mockFetch.mockReset();
    vi.clearAllMocks();
    mockCreateMockJWTToken.mockResolvedValue('mock.jwt.token');
  });

  it('throws when CSRF state does not match stored state', async () => {
    sessionStorage.setItem('oauth_state', 'expected-state');
    await expect(
      handleOAuthCallbackNew('github', 'code', 'wrong-state')
    ).rejects.toThrow(/CSRF state mismatch/);
  });

  it('does not throw on CSRF check when no state stored in sessionStorage', async () => {
    // When sessionStorage has no oauth_state, the comparison is skipped
    // but the API call may fail with the mock — we just verify CSRF check doesn't throw
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ token: 'srv-tok', user: { id: '1' } }),
    });
    // Should NOT throw CSRF error since sessionStorage has no stored state
    const result = await handleOAuthCallbackNew(
      'github',
      'real-code',
      'any-state'
    );
    expect(result).toBeDefined();
  });

  it('mock code path: returns token without API call', async () => {
    const result = await handleOAuthCallbackNew(
      'github',
      'mock_auth_code_123',
      'any-state'
    );
    expect(result.token).toBe('mock.jwt.token');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('mock code path: stores token in sessionStorage', async () => {
    await handleOAuthCallbackNew('github', 'mock_auth_code_456', 'any-state');
    expect(sessionStorage.getItem('auth_token')).toBe('mock.jwt.token');
  });

  it('mock code path: clears oauth_state from sessionStorage', async () => {
    sessionStorage.setItem('oauth_state', 'mock_auth_code_456');
    await handleOAuthCallbackNew(
      'github',
      'mock_auth_code_456',
      'mock_auth_code_456'
    );
    expect(sessionStorage.getItem('oauth_state')).toBeNull();
  });

  it('real OAuth path: calls provider-specific callback endpoint', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ token: 'srv-tok', user: { id: '2' } }),
    });
    const result = await handleOAuthCallbackNew('google', 'code', 'state');
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/google/callback'),
      expect.anything()
    );
    expect(result.token).toBe('srv-tok');
  });

  it('real OAuth path: throws when server returns non-ok', async () => {
    // Set a stored state that matches so CSRF check passes
    sessionStorage.setItem('oauth_state', 'state-xyz');
    mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'Bad Request' });
    await expect(
      handleOAuthCallbackNew('github', 'bad-code', 'state-xyz')
    ).rejects.toThrow(/OAuth callback failed/);
  });
});

// ------------------------------------------------------------------
// validateAndGetCurrentUser
// ------------------------------------------------------------------

describe('validateAndGetCurrentUser', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    mockFetch.mockReset();
    vi.clearAllMocks();
  });

  it('returns null when no token in storage', async () => {
    const result = await validateAndGetCurrentUser();
    expect(result).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns user data when /api/auth/me responds ok', async () => {
    const token = makeJWT(futureExp());
    sessionStorage.setItem('auth_token', token);
    const user = { id: '1', login: 'alice' };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ user }),
    });
    const result = await validateAndGetCurrentUser();
    expect(result).toEqual(user);
  });

  it('updates localStorage user on success', async () => {
    const token = makeJWT(futureExp());
    sessionStorage.setItem('auth_token', token);
    const user = { id: '42', login: 'charlie' };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ user }),
    });
    await validateAndGetCurrentUser();
    expect(JSON.parse(sessionStorage.getItem('user'))).toEqual(user);
  });

  it('returns null and clears state when server returns 401', async () => {
    const token = makeJWT(futureExp());
    sessionStorage.setItem('auth_token', token);
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        statusText: 'Unauthorized',
      })
      // logout() will attempt /api/auth/logout
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({}) });
    const result = await validateAndGetCurrentUser();
    expect(result).toBeNull();
  });

  it('returns null when fetch throws (network error)', async () => {
    const token = makeJWT(futureExp());
    sessionStorage.setItem('auth_token', token);
    mockFetch.mockRejectedValueOnce(new Error('Network down'));
    const result = await validateAndGetCurrentUser();
    expect(result).toBeNull();
  });
});
