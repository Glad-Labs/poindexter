/**
 * GitHub OAuth Authentication Service
 * Handles OAuth flow, token exchange, and user verification
 */

import * as Sentry from '@sentry/react';
import logger from '@/lib/logger';

// mockTokenGenerator is intentionally NOT imported statically.
// Use dynamic import inside dev-only code paths (see calls below) so that
// the file — including its signing secret — is tree-shaken from production
// bundles.  Never add a static import of mockTokenGenerator at module level.

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const PERSIST_KEY = 'oversight-hub-storage';
let devTokenInitPromise = null;

export const clearPersistedAuthState = () => {
  // auth_token and user are stored in sessionStorage (not localStorage) to limit
  // XSS exposure — a stolen token cannot persist across sessions. (#726)
  sessionStorage.removeItem('auth_token');
  sessionStorage.removeItem('refresh_token');
  sessionStorage.removeItem('user');

  const persistedData = localStorage.getItem(PERSIST_KEY);
  if (!persistedData) {
    return;
  }

  try {
    const parsed = JSON.parse(persistedData);
    const currentState = parsed.state || {};

    const updated = {
      ...parsed,
      state: {
        ...currentState,
        accessToken: null,
        auth_token: null,
        refreshToken: null,
        isAuthenticated: false,
        user: null,
      },
    };

    localStorage.setItem(PERSIST_KEY, JSON.stringify(updated));
  } catch (error) {
    logger.warn(
      '[authService] Failed to clear persisted Zustand auth state:',
      error
    );
  }
};

const requestBackendDevToken = async () => {
  const response = await fetch(`${API_BASE_URL}/api/auth/dev-token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include',
  });

  if (!response.ok) {
    throw new Error(`Backend dev token request failed: ${response.status}`);
  }

  const data = await response.json();
  if (!data?.token) {
    throw new Error('Backend dev token response missing token');
  }

  return data;
};

const validateTokenWithBackend = async (token) => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      credentials: 'include',
    });
    return response.ok;
  } catch {
    return false;
  }
};

/**
 * Generate GitHub OAuth authorization URL
 * @param {string} clientId - GitHub OAuth Client ID
 * @returns {string} - Authorization URL
 */
export const generateGitHubAuthURL = (clientId) => {
  const redirectUri = `${window.location.origin}/auth/callback`;
  const scope = 'user:email';
  // Use cryptographically secure random values for CSRF state (replaces Math.random())
  const stateBytes = new Uint8Array(24);
  crypto.getRandomValues(stateBytes);
  const state = Array.from(stateBytes, (b) =>
    b.toString(16).padStart(2, '0')
  ).join('');

  // Store state in session storage for verification
  sessionStorage.setItem('oauth_state', state);

  return `https://github.com/login/oauth/authorize?client_id=${clientId}&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${scope}&state=${state}`;
};

/**
 * Exchange GitHub authorization code for access token
 * @param {string} code - Authorization code from GitHub
 * @returns {Promise<object>} - User data and token
 */
export const exchangeCodeForToken = async (code) => {
  try {
    // Check if this is a mock code (for development)
    if (code && code.startsWith('mock_auth_code_')) {
      // Handle mock auth
      await new Promise((resolve) => setTimeout(resolve, 500)); // Simulate network delay

      const mockUser = {
        id: 'mock_user_12345',
        login: 'dev-user',
        email: 'dev@example.com',
        name: 'Development User',
        avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
      };

      // Dynamic import keeps mockTokenGenerator (and its secret) out of the
      // production bundle — this branch only runs for mock_auth_code_ codes.
      const { createMockJWTToken } =
        await import('../utils/mockTokenGenerator');
      const mockToken = await createMockJWTToken(mockUser);

      // Store token in sessionStorage (not localStorage) to limit XSS exposure (#726)
      sessionStorage.setItem('auth_token', mockToken);
      sessionStorage.setItem('user', JSON.stringify(mockUser));

      return {
        token: mockToken,
        user: mockUser,
      };
    }

    // Real GitHub OAuth
    const state = sessionStorage.getItem('oauth_state');
    if (!state) {
      throw new Error('CSRF state not found - session expired');
    }

    const response = await fetch(`${API_BASE_URL}/api/auth/github/callback`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ code, state }),
      credentials: 'include', // Include cookies for session
    });

    if (!response.ok) {
      throw new Error(`Authentication failed: ${response.statusText}`);
    }

    const data = await response.json();

    // Store token in sessionStorage (not localStorage) to limit XSS exposure (#726)
    if (data.token) {
      sessionStorage.setItem('auth_token', data.token);
      sessionStorage.setItem('user', JSON.stringify(data.user));
    }

    return data;
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'exchangeCodeForToken' } },
    });
    throw error;
  }
};

/**
 * Verify current session is valid
 * @returns {Promise<object|null>} - User data if valid, null otherwise
 */
export const verifySession = async () => {
  try {
    const token = sessionStorage.getItem('auth_token');
    const user = sessionStorage.getItem('user');

    if (!token) {
      return null;
    }

    // For mock tokens (development/testing), trust the stored user if valid format
    if (token.includes('.') && token.split('.').length === 3) {
      // Token has proper JWT format
      try {
        const parsedUser = user ? JSON.parse(user) : null;
        // For development tokens, also verify expiry if possible
        const parts = token.split('.');
        if (parts.length === 3) {
          try {
            const payload = JSON.parse(atob(parts[1]));
            if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) {
              // Token expired
              clearPersistedAuthState();
              return null;
            }
          } catch {
            // Could not parse payload, but JWT format is valid
          }
        }
        return parsedUser;
      } catch {
        return null;
      }
    }

    // Token format is invalid, clear it
    clearPersistedAuthState();
    return null;
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'verifySession' } },
    });
    return null;
  }
};

/**
 * Logout user - clear tokens and user data
 * @returns {Promise<void>}
 */
export const logout = async () => {
  try {
    const token = sessionStorage.getItem('auth_token');

    if (token) {
      await fetch(`${API_BASE_URL}/api/auth/logout`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        credentials: 'include',
      });
    }

    // Clear local storage regardless of API response
    clearPersistedAuthState();
    sessionStorage.removeItem('oauth_state');
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'logout' } },
    });
    // Still clear local storage even if API call fails
    clearPersistedAuthState();
  }
};

/**
 * Get stored user data
 * @returns {object|null} - Parsed user object or null
 */
export const getStoredUser = () => {
  const userStr = sessionStorage.getItem('user');
  try {
    const parsed = userStr ? JSON.parse(userStr) : null;
    if (parsed) {
    }
    return parsed;
  } catch (e) {
    logger.warn('[authService.getStoredUser] Failed to parse user:', e);
    return null;
  }
};

/**
 * Check if JWT token is expired
 * @param {string} token - JWT token
 * @returns {boolean} - True if expired, false otherwise
 */
export const isTokenExpired = (token) => {
  if (!token) {
    return true;
  }

  try {
    const parts = token.split('.');
    if (parts.length !== 3) {
      return true;
    }

    // Decode from base64url to base64 (JWT uses url-safe base64)
    let base64Payload = parts[1];
    base64Payload = base64Payload.replace(/-/g, '+').replace(/_/g, '/');
    // Add padding if needed
    const padding = 4 - (base64Payload.length % 4);
    if (padding !== 4) {
      base64Payload += '='.repeat(padding);
    }

    const payload = JSON.parse(atob(base64Payload));
    if (!payload.exp) {
      return false;
    }

    const expiryTime = payload.exp * 1000; // Convert to milliseconds
    const now = Date.now();
    const isExpired = now > expiryTime;

    return isExpired;
  } catch (e) {
    logger.warn('[authService.isTokenExpired] Error parsing token:', e);
    return true; // If parsing fails, consider expired
  }
};

/**
 * Get stored auth token (with expiry check)
 * @returns {string|null} - Auth token or null if expired
 */
export const getAuthToken = () => {
  let token = null;

  // Try to get token from Zustand persist storage first
  const persistedData = localStorage.getItem(PERSIST_KEY);
  if (persistedData) {
    try {
      const parsed = JSON.parse(persistedData);
      token = parsed.state?.accessToken || parsed.state?.auth_token;
    } catch (e) {
      logger.warn(
        '[authService.getAuthToken] Failed to parse Zustand persist storage:',
        e
      );
    }
  }

  // Fallback to sessionStorage if not found in Zustand persist store
  if (!token) {
    token = sessionStorage.getItem('auth_token');
  }

  if (!token) {
    return null;
  }

  if (isTokenExpired(token)) {
    // Token is expired, remove it
    clearPersistedAuthState();
    return null;
  }

  return token;
};

/**
 * Initialize development token if not present or expired
 * Only used in development/local environment
 * Automatically refreshes token every 14 minutes to prevent expiry (tokens last 15 min)
 * @returns {Promise<string>} - Mock development token
 */
export const initializeDevToken = async (options = {}) => {
  if (devTokenInitPromise) {
    return devTokenInitPromise;
  }

  devTokenInitPromise = (async () => {
    try {
      const { forceRefresh = false, validateWithBackend = true } = options;

      // Check if token exists and is still valid
      const existingToken = sessionStorage.getItem('auth_token');

      if (!forceRefresh && existingToken && !isTokenExpired(existingToken)) {
        if (validateWithBackend) {
          const isValid = await validateTokenWithBackend(existingToken);
          if (!isValid) {
            logger.warn(
              '[authService] Existing token failed backend validation, clearing and regenerating'
            );
            clearPersistedAuthState();
          } else {
            return existingToken;
          }
        } else {
          return existingToken;
        }
      }

      if (forceRefresh) {
        clearPersistedAuthState();
      }

      try {
        const backendAuth = await requestBackendDevToken();
        const backendToken = backendAuth.token;
        const backendUser = backendAuth.user || {
          id: 'dev_user_local',
          email: 'dev@localhost',
          username: 'dev-user',
          login: 'dev-user',
          name: 'Development User',
          avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
          auth_provider: 'mock',
        };

        // Store in sessionStorage (not localStorage) to limit XSS exposure (#726)
        sessionStorage.setItem('auth_token', backendToken);
        sessionStorage.setItem('user', JSON.stringify(backendUser));

        return backendToken;
      } catch (backendError) {
        logger.warn(
          '[authService] Backend dev token generation failed, using local mock token fallback:',
          backendError
        );
      }

      if (existingToken && !isTokenExpired(existingToken)) {
        // Token is still valid
        return existingToken;
      }

      // Token is missing or expired, create a new one
      const mockUser = {
        id: 'dev_user_local',
        email: 'dev@localhost',
        username: 'dev-user',
        login: 'dev-user',
        name: 'Development User',
        avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
        auth_provider: 'mock',
      };

      // Dynamic import — keeps the signing secret out of the production bundle.
      const { createMockJWTToken } =
        await import('../utils/mockTokenGenerator');
      const mockToken = await createMockJWTToken(mockUser);

      // Store token in sessionStorage (not localStorage) to limit XSS exposure (#726)
      sessionStorage.setItem('auth_token', mockToken);
      sessionStorage.setItem('user', JSON.stringify(mockUser));

      // Small delay to ensure sessionStorage is actually persisted
      await new Promise((resolve) => setTimeout(resolve, 50));

      // Verify token was actually stored
      const storedToken = sessionStorage.getItem('auth_token');
      if (!storedToken) {
        Sentry.captureMessage(
          '[authService] Token was not stored in sessionStorage',
          'error'
        );
        // Try to check if it's in Zustand store instead
        try {
          const zustandData = localStorage.getItem(PERSIST_KEY);
          if (zustandData) {
            const parsed = JSON.parse(zustandData);
          }
        } catch {}
        throw new Error('Failed to store token in sessionStorage');
      }

      // Set up auto-refresh every 14 minutes (token expires in 15 minutes)
      // This prevents token expiry during long sessions
      setTimeout(
        () => {
          if (process.env.NODE_ENV === 'development') {
            initializeDevToken().catch((e) => {
              Sentry.captureException(e, {
                contexts: { custom: { action: 'autoRefreshToken' } },
              });
            });
          }
        },
        14 * 60 * 1000
      ); // 14 minutes in milliseconds

      return mockToken;
    } catch (error) {
      Sentry.captureException(error, {
        contexts: { custom: { action: 'initializeDevToken' } },
      });
      // If development token fails, return null - frontend should redirect to login
      return null;
    } finally {
      devTokenInitPromise = null;
    }
  })();

  return devTokenInitPromise;
};

/**
 * Make authenticated API request
 * @param {string} endpoint - API endpoint (relative to API_BASE_URL)
 * @param {object} options - Fetch options
 * @returns {Promise<object>} - Response data
 */
export const authenticatedFetch = async (endpoint, options = {}) => {
  const token = getAuthToken();

  const headers = {
    'Content-Type': 'application/json',
    ...(token && { Authorization: `Bearer ${token}` }),
    ...options.headers,
  };

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
    credentials: 'include',
  });

  if (response.status === 401) {
    // Token expired or invalid
    clearPersistedAuthState();
    await logout();
    window.location.href = '/login';
    throw new Error('Session expired');
  }

  if (!response.ok) {
    throw new Error(`API error: ${response.statusText}`);
  }

  return response.json();
};

const authService = {
  generateGitHubAuthURL,
  exchangeCodeForToken,
  verifySession,
  logout,
  getStoredUser,
  getAuthToken,
  initializeDevToken,
  authenticatedFetch,
};

export default authService;

// ============================================================================
// Enhanced OAuth Functions (FastAPI Backend Compatible)
// ============================================================================

/**
 * Get available OAuth providers from backend
 * @returns {Promise<array>} List of available providers
 */
export async function getAvailableOAuthProviders() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/auth/providers`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
    });

    if (!response.ok) {
      throw new Error(
        `Failed to fetch OAuth providers: ${response.statusText}`
      );
    }

    const data = await response.json();
    return data.providers || [];
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'getOAuthProviders' } },
    });
    return [];
  }
}

/**
 * Get login URL for OAuth provider
 * @param {string} provider - Provider name (github, google, etc)
 * @returns {Promise<string>} OAuth login URL
 */
export async function getOAuthLoginURL(provider) {
  try {
    const response = await fetch(`${API_BASE_URL}/api/auth/${provider}/login`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
    });

    if (!response.ok) {
      throw new Error(`Failed to get OAuth login URL: ${response.statusText}`);
    }

    const data = await response.json();
    return data.login_url;
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'getOAuthLoginURL', provider } },
    });
    throw error;
  }
}

/**
 * Handle OAuth callback - NEW FastAPI endpoint
 * @param {string} provider - OAuth provider
 * @param {string} code - Authorization code
 * @param {string} state - State parameter for CSRF verification
 * @returns {Promise<object>} User data and tokens {user, token, refresh_token}
 */
export async function handleOAuthCallbackNew(provider, code, state) {
  try {
    // Verify CSRF state
    const storedState = sessionStorage.getItem('oauth_state');
    if (storedState && storedState !== state) {
      throw new Error('CSRF state mismatch - potential security breach');
    }

    // Handle mock auth codes for development
    if (code && code.startsWith('mock_auth_code_')) {
      // Generate mock JWT token locally for mock auth
      const mockUser = {
        id: 'mock_user_12345',
        login: 'dev-user',
        email: 'dev@example.com',
        name: 'Development User',
        avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
      };

      // Dynamic import — keeps the signing secret out of the production bundle.
      const { createMockJWTToken } =
        await import('../utils/mockTokenGenerator');
      const mockToken = await createMockJWTToken(mockUser);

      // Store tokens in sessionStorage to limit XSS exposure (#726)
      sessionStorage.setItem('auth_token', mockToken);
      sessionStorage.setItem('user', JSON.stringify(mockUser));

      // Clear state
      sessionStorage.removeItem('oauth_state');

      return {
        token: mockToken,
        user: mockUser,
      };
    }

    const response = await fetch(
      `${API_BASE_URL}/api/auth/${provider}/callback`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, state }),
        credentials: 'include',
      }
    );

    if (!response.ok) {
      throw new Error(`OAuth callback failed: ${response.statusText}`);
    }

    const data = await response.json();

    // Store tokens in sessionStorage to limit XSS exposure (#726)
    if (data.token) {
      sessionStorage.setItem('auth_token', data.token);
    }
    if (data.refresh_token) {
      sessionStorage.setItem('refresh_token', data.refresh_token);
    }
    if (data.user) {
      sessionStorage.setItem('user', JSON.stringify(data.user));
    }

    // Clear state
    sessionStorage.removeItem('oauth_state');

    return data;
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'handleOAuthCallback', provider } },
    });
    throw error;
  }
}

/**
 * Validate token is still valid and get current user
 * @returns {Promise<object>} Current user data
 */
export async function validateAndGetCurrentUser() {
  try {
    const token = getAuthToken();
    if (!token) {
      return null;
    }

    const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      credentials: 'include',
    });

    if (!response.ok) {
      if (response.status === 401) {
        // Token expired
        await logout();
        return null;
      }
      throw new Error(`Failed to validate user: ${response.statusText}`);
    }

    const data = await response.json();
    if (data.user) {
      sessionStorage.setItem('user', JSON.stringify(data.user));
    }
    return data.user;
  } catch (error) {
    Sentry.captureException(error, {
      contexts: { custom: { action: 'validateUser' } },
    });
    return null;
  }
}

/**
 * Clear authentication (alias for logout)
 * @returns {Promise<void>}
 */
export async function clearAuth() {
  return logout();
}

/**
 * Check if user is authenticated
 * @returns {boolean}
 */
export function isAuthenticated() {
  return !!getAuthToken();
}
