/**
 * GitHub OAuth Authentication Service
 * Handles OAuth flow and cookie-based session verification.
 */

import { getApiUrl } from '../config/apiConfig';

const API_BASE_URL = getApiUrl();

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

    if (data.user) {
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
    return await validateAndGetCurrentUser();
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
    await fetch(`${API_BASE_URL}/api/auth/logout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
    });

    // Clear client-side profile cache regardless of API response
    localStorage.removeItem('user');
    sessionStorage.removeItem('oauth_state');
  } catch (error) {
    console.error('Error during logout:', error);
    // Still clear local cache even if API call fails
    localStorage.removeItem('user');
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

    localStorage.setItem('user', JSON.stringify(mockUser));
    console.log('[authService] Development profile initialized');
    return null;
  } catch (error) {
    console.error('[authService] ERROR in initializeDevToken:', error);
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
 * @returns {Promise<object>} User/session payload (user profile + optional compatibility fields)
 */
export async function handleOAuthCallbackNew(provider, code, state) {
  try {
    // Verify CSRF state
    const storedState = sessionStorage.getItem('oauth_state');
    if (storedState && storedState !== state) {
      throw new Error('CSRF state mismatch - potential security breach');
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
    const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
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
  return !!getStoredUser();
}
