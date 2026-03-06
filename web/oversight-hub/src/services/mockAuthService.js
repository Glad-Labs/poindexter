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
  console.error(
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

    // Simulate a successful token response
    const mockUser = {
      id: 'mock_user_12345',
      login: 'dev-user',
      email: 'dev@example.com',
      name: 'Development User',
      avatar_url: 'https://avatars.githubusercontent.com/u/1?v=4',
    };

    // Store non-sensitive profile only. Session token is cookie-based.
    localStorage.setItem('user', JSON.stringify(mockUser));

    return {
      token: null,
      user: mockUser,
    };
  } catch (error) {
    console.error('Error in mock token exchange:', error);
    throw error;
  }
};

/**
 * Mock session verification
 */
export const verifySession = async () => {
  try {
    // In mock mode we only rely on a cached local profile.
    const user = localStorage.getItem('user');
    return user ? JSON.parse(user) : null;
  } catch (error) {
    console.error('Error verifying mock session:', error);
    return null;
  }
};

/**
 * Logout - removes stored credentials
 */
export const logout = async () => {
  localStorage.removeItem('user');
  sessionStorage.removeItem('mock_auth_code');
};
