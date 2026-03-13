/**
 * GitHub OAuth Authentication Service
 * Handles OAuth flow, token exchange, and user verification
 */

import { createMockJWTToken } from '../utils/mockTokenGenerator';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const PERSIST_KEY = 'oversight-hub-storage';

export const clearPersistedAuthState = () => {
  localStorage.removeItem('auth_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');

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
    console.warn(
      '[authService] Failed to clear persisted Zustand auth state:',
      error
    );
  }
};

const requestBackendDevToken = async () => {
  const response = await fetch(`${API_BASE_URL}/api/auth/github/callback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      code: `mock_auth_code_${Date.now()}`,
      state: `dev_state_${Date.now()}`,
    }),
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
  const state = Math.random().toString(36).substring(7); // Simple state for CSRF protection

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

      // Generate a proper JWT token for development (awaiting async signing)
      const mockToken = await createMockJWTToken(mockUser);

      // Store token and user data
      localStorage.setItem('auth_token', mockToken);
      localStorage.setItem('user', JSON.stringify(mockUser));

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

    // Store token and user data
    if (data.token) {
      localStorage.setItem('auth_token', data.token);
      localStorage.setItem('user', JSON.stringify(data.user));
    }

    return data;
  } catch (error) {
    console.error('Error exchanging code for token:', error);
    throw error;
  }
};

/**
 * Verify current session is valid
 * @returns {Promise<object|null>} - User data if valid, null otherwise
 */
export const verifySession = async () => {
  try {
    const token = localStorage.getItem('auth_token');
    const user = localStorage.getItem('user');

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
    console.error('Error verifying session:', error);
    return null;
  }
};

/**
 * Logout user - clear tokens and user data
 * @returns {Promise<void>}
 */
export const logout = async () => {
  try {
    const token = localStorage.getItem('auth_token');

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
    console.error('Error during logout:', error);
    // Still clear local storage even if API call fails
    clearPersistedAuthState();
  }
};

/**
 * Get stored user data
 * @returns {object|null} - Parsed user object or null
 */
export const getStoredUser = () => {
  const userStr = localStorage.getItem('user');
  console.log(
    '[authService.getStoredUser] Looking for user...',
    userStr ? 'FOUND' : 'NOT FOUND'
  );
  try {
    const parsed = userStr ? JSON.parse(userStr) : null;
    if (parsed) {
      console.log('[authService.getStoredUser] Parsed user:', parsed.login);
    }
    return parsed;
  } catch (e) {
    console.error('[authService.getStoredUser] Failed to parse user:', e);
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
      console.log(
        '[authService.isTokenExpired] Invalid token format (not 3 parts)'
      );
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
      console.log(
        '[authService.isTokenExpired] No expiry in token, assuming valid'
      );
      return false;
    }

    const expiryTime = payload.exp * 1000; // Convert to milliseconds
    const now = Date.now();
    const isExpired = now > expiryTime;

    console.log('[authService.isTokenExpired]', {
      expiryTime: new Date(expiryTime).toISOString(),
      now: new Date(now).toISOString(),
      isExpired,
    });

    return isExpired;
  } catch (e) {
    console.error('[authService.isTokenExpired] Error parsing token:', e);
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
      console.warn(
        '[authService.getAuthToken] Failed to parse Zustand persist storage:',
        e
      );
    }
  }

  // Fallback to direct localStorage key if not found in Zustand
  if (!token) {
    token = localStorage.getItem('auth_token');
  }

  console.log(
    '[authService.getAuthToken] Looking for token...',
    token ? 'FOUND' : 'NOT FOUND'
  );

  if (!token) {
    console.log(
      '[authService.getAuthToken] No token in localStorage or Zustand'
    );
    return null;
  }

  if (isTokenExpired(token)) {
    console.log('[authService.getAuthToken] Token is expired, removing');
    // Token is expired, remove it
    clearPersistedAuthState();
    return null;
  }

  console.log('[authService.getAuthToken] Token is valid');
  return token;
};

/**
 * Initialize development token if not present or expired
 * Only used in development/local environment
 * Automatically refreshes token every 14 minutes to prevent expiry (tokens last 15 min)
 * @returns {Promise<string>} - Mock development token
 */
export const initializeDevToken = async (options = {}) => {
  try {
    const { forceRefresh = false, validateWithBackend = true } = options;

    // Check if token exists and is still valid
    const existingToken = localStorage.getItem('auth_token');

    if (!forceRefresh && existingToken && !isTokenExpired(existingToken)) {
      if (validateWithBackend) {
        const isValid = await validateTokenWithBackend(existingToken);
        if (!isValid) {
          console.warn(
            '[authService] Existing token failed backend validation, clearing and regenerating'
          );
          clearPersistedAuthState();
        } else {
          console.log('[authService] Using existing backend-validated token');
          return existingToken;
        }
      } else {
        console.log('[authService] Using existing valid token');
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

      localStorage.setItem('auth_token', backendToken);
      localStorage.setItem('user', JSON.stringify(backendUser));

      console.log(
        '[authService] Development token initialized from backend signer'
      );
      return backendToken;
    } catch (backendError) {
      console.warn(
        '[authService] Backend dev token generation failed, using local mock token fallback:',
        backendError
      );
    }

    if (existingToken && !isTokenExpired(existingToken)) {
      // Token is still valid
      console.log('[authService] Using existing valid token');
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

    // Generate proper JWT token for development (awaiting async signing)
    console.log('[authService] Creating new mock JWT token...');
    const mockToken = await createMockJWTToken(mockUser);
    console.log(
      '[authService] Mock token created successfully, storing in localStorage...'
    );

    // Store token and user BEFORE anything else
    localStorage.setItem('auth_token', mockToken);
    localStorage.setItem('user', JSON.stringify(mockUser));

    console.log(
      '[authService] Items set in localStorage, waiting for persistence...'
    );

    // Small delay to ensure localStorage is actually persisted
    await new Promise((resolve) => setTimeout(resolve, 50));

    console.log(
      '[authService] Development token initialized/refreshed with proper JWT format'
    );

    // Verify token was actually stored
    const storedToken = localStorage.getItem('auth_token');
    if (!storedToken) {
      console.error(
        '[authService] ERROR: Token was not actually stored in localStorage! Zustand may be interfering.'
      );
      // Try to check if it's in Zustand store instead
      try {
        const zustandData = localStorage.getItem(PERSIST_KEY);
        if (zustandData) {
          const parsed = JSON.parse(zustandData);
          console.log(
            '[authService] Zustand state exists:',
            Object.keys(parsed.state || {})
          );
        }
      } catch {
        console.log('[authService] Could not parse Zustand data');
      }
      throw new Error('Failed to store token in localStorage');
    }

    console.log('[authService] ✅ Token verified in localStorage');

    // Set up auto-refresh every 14 minutes (token expires in 15 minutes)
    // This prevents token expiry during long sessions
    setTimeout(
      () => {
        if (process.env.NODE_ENV === 'development') {
          console.log('[authService] Auto-refreshing development token...');
          initializeDevToken().catch((e) => {
            console.error('[authService] Failed to auto-refresh token:', e);
          });
        }
      },
      14 * 60 * 1000
    ); // 14 minutes in milliseconds

    return mockToken;
  } catch (error) {
    console.error('[authService] ERROR in initializeDevToken:', error);
    // If development token fails, return null - frontend should redirect to login
    return null;
  }
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
    console.error('Error fetching OAuth providers:', error);
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
    console.error(`Error getting ${provider} login URL:`, error);
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

      const mockToken = await createMockJWTToken(mockUser);

      // Store tokens
      localStorage.setItem('auth_token', mockToken);
      localStorage.setItem('user', JSON.stringify(mockUser));

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

    // Store tokens
    if (data.token) {
      localStorage.setItem('auth_token', data.token);
    }
    if (data.refresh_token) {
      localStorage.setItem('refresh_token', data.refresh_token);
    }
    if (data.user) {
      localStorage.setItem('user', JSON.stringify(data.user));
    }

    // Clear state
    sessionStorage.removeItem('oauth_state');

    return data;
  } catch (error) {
    console.error(`Error handling ${provider} callback:`, error);
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
      localStorage.setItem('user', JSON.stringify(data.user));
    }
    return data.user;
  } catch (error) {
    console.error('Error validating user:', error);
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
