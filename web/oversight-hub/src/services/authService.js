import logger from '@/lib/logger';
/**
 * GitHub OAuth Authentication Service
 * Handles OAuth flow and cookie-based session verification.
 */

import { getApiUrl, getEnv } from '../config/apiConfig';
import { authClient } from '../lib/authClient';

const API_BASE_URL = getApiUrl();

const isMockAuthEnabled = () => getEnv('REACT_APP_USE_MOCK_AUTH') === 'true';

const isMockCode = (code) =>
  typeof code === 'string' && code.startsWith('mock_auth_code_');

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
  authClient.setOAuthState(state, 'github');

  return `https://github.com/login/oauth/authorize?client_id=${clientId}&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${scope}&state=${state}`;
};

/**
 * Exchange GitHub authorization code for access token
 * @param {string} code - Authorization code from GitHub
 * @returns {Promise<object>} - User data and token
 */
export const exchangeCodeForToken = async (
  code,
  callbackState = null,
  provider = 'github'
) => {
  try {
    if (isMockCode(code)) {
      if (!isMockAuthEnabled()) {
        throw new Error('Mock auth code received but mock auth is disabled');
      }

      const mockAuth = await import('./mockAuthService');
      return await mockAuth.exchangeCodeForToken(code);
    }

    // Real GitHub OAuth
    const effectiveState = callbackState || authClient.getOAuthState();
    const stateValidation = authClient.validateAndConsumeOAuthState(
      effectiveState,
      {
        provider,
      }
    );

    if (!stateValidation.valid) {
      throw new Error(
        `CSRF validation failed: ${stateValidation.reason || 'unknown_error'}`
      );
    }

    const response = await fetch(`${API_BASE_URL}/api/auth/github/callback`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ code, state: effectiveState }),
      credentials: 'include', // Include cookies for session
    });

    if (!response.ok) {
      throw new Error(`Authentication failed: ${response.statusText}`);
    }

    const data = await response.json();

    // Store user profile using centralized auth client
    if (data.user) {
      authClient.setUser(data.user);
    }

    // Store JWT token if provided by backend
    if (data.token) {
      authClient.setToken(data.token, data.expires_in);
    }

    return data;
  } catch (error) {
    logger.error('Error exchanging code for token:', error);
    throw error;
  }
};

/**
 * Verify current session is valid
 * @returns {Promise<object|null>} - User data if valid, null otherwise
 */
export const verifySession = async () => {
  try {
    return await validateAndGetCurrentUser();
  } catch (error) {
    logger.error('Error verifying session:', error);
    return null;
  }
};

/**
 * Logout user - clear tokens and user data
 * @returns {Promise<void>}
 */
export const logout = async () => {
  try {
    await fetch(`${API_BASE_URL}/api/auth/logout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
    });

    // Clear client-side auth data using centralized client
    authClient.logout();
  } catch (error) {
    logger.error('Error during logout:', error);
    // Still clear local cache even if API call fails
    authClient.logout();
  }
};

/**
 * Get stored user data
 * @returns {object|null} - Parsed user object or null
 */
export const getStoredUser = () => {
  const user = authClient.getUser();
  logger.log(
    '[authService.getStoredUser] Looking for user...',
    user ? 'FOUND' : 'NOT FOUND'
  );
  return user;
};

// Legacy function body preserved for reference:
/*
  try {
    const parsed = userStr ? JSON.parse(userStr) : null;
    if (parsed) {
      logger.log('[authService.getStoredUser] Parsed user:', parsed.login);
    }
    return parsed;
  } catch (e) {
    logger.error('[authService.getStoredUser] Failed to parse user:', e);
    return null;
  }
};

/**
 * Token accessor kept for compatibility with existing imports.
 * Cookie-based auth means no token is available in JavaScript.
 */
export const isTokenExpired = () => true;

/**
 * Get stored auth token (with expiry check)
 * @returns {string|null} - Auth token or null if expired
 */
export const getAuthToken = () => {
  return null;
};

/**
 * Initialize development token if not present or expired
 * Only used in development/local environment
 * Automatically refreshes token every 14 minutes to prevent expiry (tokens last 15 min)
 * @returns {Promise<string>} - Mock development token
 */
export const initializeDevToken = async () => {
  try {
    // For development with backend DEVELOPMENT_MODE, only cache non-sensitive user profile.
    const mockUser = {
      id: 'dev_user_local',
      email: 'dev@localhost',
      username: 'dev-user',
      login: 'dev-user',
      name: 'Development User',
      avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
      auth_provider: 'mock',
    };

    // Use dev-token format that backend recognizes (bypasses JWT validation)
    // Backend auth_unified.py accepts tokens starting with "dev-" or equal to "dev-token"
    const mockToken = 'dev-token';

    authClient.setUser(mockUser);
    authClient.setToken(mockToken);
    logger.log('[authService] Development profile initialized');
    return null;
  } catch (error) {
    logger.error('[authService] ERROR in initializeDevToken:', error);
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
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
    credentials: 'include',
  });

  if (response.status === 401) {
    // Token expired or invalid
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
    logger.error('Error fetching OAuth providers:', error);
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
    logger.error(`Error getting ${provider} login URL:`, error);
    throw error;
  }
}

/**
 * Handle OAuth callback - NEW FastAPI endpoint
 * @param {string} provider - OAuth provider
 * @param {string} code - Authorization code
 * @param {string} state - State parameter for CSRF verification
 * @returns {Promise<object>} User/session payload (user profile + optional compatibility fields)
 */
export async function handleOAuthCallbackNew(provider, code, state) {
  try {
    if (isMockCode(code)) {
      if (!isMockAuthEnabled()) {
        throw new Error('Mock auth code received but mock auth is disabled');
      }

      const mockAuth = await import('./mockAuthService');
      const mockData = await mockAuth.exchangeCodeForToken(code);
      authClient.clearOAuthState();
      return mockData;
    }

    const effectiveState = state || authClient.getOAuthState();
    const stateValidation = authClient.validateAndConsumeOAuthState(
      effectiveState,
      {
        provider,
      }
    );

    if (!stateValidation.valid) {
      throw new Error(
        `CSRF validation failed: ${stateValidation.reason || 'unknown_error'}`
      );
    }

    const response = await fetch(
      `${API_BASE_URL}/api/auth/${provider}/callback`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, state: effectiveState }),
        credentials: 'include',
      }
    );

    if (!response.ok) {
      throw new Error(`OAuth callback failed: ${response.statusText}`);
    }

    const data = await response.json();

    // Store user profile
    if (data.user) {
      authClient.setUser(data.user);
    }

    // Store JWT token if provided by backend
    if (data.token) {
      authClient.setToken(data.token, data.expires_in);
    }

    return data;
  } catch (error) {
    logger.error(`Error handling ${provider} callback:`, error);
    throw error;
  }
}

/**
 * Validate token is still valid and get current user
 * @returns {Promise<object>} Current user data
 */
export async function validateAndGetCurrentUser() {
  try {
    // For dev-token, skip API validation and return cached user immediately
    const storedToken = authClient.getToken();
    const storedUser = authClient.getUser();

    if (storedToken === 'dev-token' && storedUser) {
      logger.log('[authService] Dev-token detected, using cached user');
      return storedUser;
    }

    // Only attempt API validation if there is some session indicator:
    // either a non-dev in-memory token or a stored user profile (which suggests
    // a prior successful session that may still have a valid HttpOnly cookie).
    // Skipping this check avoids a guaranteed 401 + cascading noisy errors on
    // cold page loads where neither token nor user are present.
    if (!storedToken && !storedUser) {
      logger.log(
        '[authService] No session indicators, skipping /api/auth/me check'
      );
      return null;
    }

    const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
    });

    if (!response.ok) {
      if (response.status === 401) {
        // Session has expired — clear local state only.
        // Do NOT call the logout API: if the session is already invalid on the
        // server there is nothing to invalidate, and the POST to /api/auth/logout
        // would itself return 401, triggering another round of cascading errors.
        authClient.logout();
        return null;
      }
      throw new Error(`Failed to validate user: ${response.statusText}`);
    }

    const data = await response.json();

    // Store user profile
    if (data.user) {
      authClient.setUser(data.user);
    }

    // Store JWT token if provided
    if (data.token) {
      authClient.setToken(data.token, data.expires_in);
    }

    return data.user;
  } catch (error) {
    logger.error('Error validating user:', error);
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
  return !!getStoredUser();
}
