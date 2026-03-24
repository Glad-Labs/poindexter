import logger from '@/lib/logger';
import { authClient } from '../lib/authClient';
/**
 * Mock GitHub OAuth Authentication Service
 * ⚠️ DEVELOPMENT ONLY - For local testing without GitHub credentials
 *
 * ⚠️ WARNING: This service must ONLY be used in development mode (NODE_ENV === 'development')
 * The mock tokens generated here are NOT valid and should NEVER be used in production.
 *
 * To use: Set REACT_APP_USE_MOCK_AUTH=true in .env.local
 * To disable: Remove the environment variable or set to 'false'
 *
 * In production:
 * - This file should be excluded from the build
 * - REACT_APP_USE_MOCK_AUTH should never be set
 * - authService.js handles real GitHub OAuth flow
 */

// Safety check - warn if accidentally enabled in non-dev
if (process.env.NODE_ENV !== 'development') {
  logger.error(
    '❌ SECURITY WARNING: Mock auth service is being used in non-development mode! ' +
      'This is a security risk. Ensure REACT_APP_USE_MOCK_AUTH is not set in production.'
  );
}

/**
 * Mock GitHub OAuth - simulates authorization redirect
 * In production, this would redirect to github.com
 * In development, we skip GitHub and go straight to callback
 *
 * ⚠️ These mock tokens are NOT valid and NOT secure
 */
export const generateMockGitHubAuthURL = (_clientId) => {
  if (process.env.NODE_ENV !== 'development') {
    throw new Error('Mock auth is disabled in non-development environments');
  }

  // Simulate what GitHub would do - generate a mock authorization code
  const mockCode = 'mock_auth_code_' + Math.random().toString(36).substring(7);

  // Store the code for the callback to retrieve
  sessionStorage.setItem('mock_auth_code', mockCode);

  // Store the mock state so the CSRF check in handleOAuthCallbackNew passes.
  // Without this, a stale oauth_state from a previous real GitHub attempt
  // causes a "CSRF state mismatch" error on the callback page.
  sessionStorage.setItem('oauth_state', 'mock_state');

  // In real flow, this would be:
  // https://github.com/login/oauth/authorize?client_id=...
  // But in mock mode, we redirect directly to callback with the code
  return `${window.location.origin}/auth/callback?code=${mockCode}&state=mock_state`;
};

/**
 * Mock token exchange - simulates GitHub's token endpoint
 * Returns a fake user and token
 *
 * ⚠️ DEVELOPMENT ONLY - These tokens are NOT valid for production
 */
export const exchangeCodeForToken = async (code) => {
  try {
    if (process.env.NODE_ENV !== 'development') {
      throw new Error('Mock auth is disabled in non-development environments');
    }

    // Simulate network delay
    await new Promise((resolve) => setTimeout(resolve, 800));

    // Check if this is a mock code
    if (!code.startsWith('mock_auth_code_')) {
      throw new Error('Invalid mock auth code');
    }

    // Fetch a real backend-signed JWT from the dev-token endpoint
    // This ensures all API calls use a valid token that passes JWT verification
    const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
    let mockToken;
    let mockUser;

    try {
      const resp = await fetch(`${apiUrl}/api/auth/dev-token`, {
        method: 'POST',
      });
      if (resp.ok) {
        const data = await resp.json();
        mockToken = data.token || data.access_token;
        mockUser = data.user || {
          id: data.user_id || 'dev_user_local',
          login: data.login || 'dev-user',
          email: data.email || 'dev@example.com',
          name: data.name || 'Development User',
          avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
        };
      }
    } catch (e) {
      logger.warn('Failed to fetch dev-token from backend, using fallback:', e);
    }

    // Fallback if backend is unreachable
    if (!mockToken) {
      mockToken = 'dev-token';
      mockUser = {
        id: 'mock_user_12345',
        login: 'dev-user',
        email: 'dev@example.com',
        name: 'Development User',
        avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
      };
    }

    // Store both user profile and auth token via centralized auth client
    authClient.setUser(mockUser);
    authClient.setToken(mockToken);
    // Persist token to localStorage so getAuthToken() finds it across page navigations.
    // sessionStorage is cleared on navigation; Zustand persist gets wiped by AuthContext init.
    // localStorage.auth_token is the reliable fallback that survives both.
    localStorage.setItem('auth_token', mockToken);
    sessionStorage.setItem('auth_token', mockToken);

    return {
      token: mockToken,
      user: mockUser,
    };
  } catch (error) {
    logger.error('Error in mock token exchange:', error);
    throw error;
  }
};

/**
 * Mock session verification
 */
export const verifySession = async () => {
  try {
    // In mock mode we only rely on a cached local profile.
    return authClient.getUser();
  } catch (error) {
    logger.error('Error verifying mock session:', error);
    return null;
  }
};

/**
 * Logout - removes stored credentials
 */
export const logout = async () => {
  authClient.logout();
  sessionStorage.removeItem('mock_auth_code');
};
