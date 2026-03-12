import logger from '@/lib/logger';
/**
 * Centralized Authentication Client
 *
 * Single source of truth for token and session management.
 * Replaces scattered localStorage/sessionStorage access with a unified API.
 *
 * Features:
 * - Token storage/retrieval abstraction
 * - Token expiry checking
 * - Automatic refresh logic
 * - Logout cleanup
 * - Event-based auth state changes
 *
 * Usage:
 *   import { authClient } from './lib/authClient';
 *
 *   const token = authClient.getToken();
 *   authClient.setToken(newToken);
 *   authClient.logout();
 */

// Storage keys - centralized configuration
const STORAGE_KEYS = {
  AUTH_TOKEN: 'auth_token',
  USER: 'user',
  OAUTH_STATE: 'oauth_state',
  OAUTH_STATE_PROVIDER: 'oauth_state_provider',
  OAUTH_STATE_CREATED_AT: 'oauth_state_created_at',
  TOKEN_EXPIRY: 'token_expiry',
};

// Token expiry buffer (refresh 5 minutes before actual expiry)
const TOKEN_EXPIRY_BUFFER_MS = 5 * 60 * 1000;
const DEFAULT_OAUTH_STATE_MAX_AGE_MS = 10 * 60 * 1000;

class AuthClient {
  constructor() {
    this.listeners = [];
    // SECURITY: Token stored in memory only — not localStorage/sessionStorage.
    // This prevents XSS from stealing JWT tokens (CWE-312/CWE-313).
    // Tokens do not survive page refresh; cookie-based auth handles persistence.
    this._token = null;
    this._tokenExpiry = null;
    this._storage = typeof window !== 'undefined' ? window.localStorage : null;
    this._sessionStorage =
      typeof window !== 'undefined' ? window.sessionStorage : null;

    // Migrate: clear any legacy token from localStorage
    if (this._storage) {
      this._storage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
      this._storage.removeItem(STORAGE_KEYS.TOKEN_EXPIRY);
    }
  }

  /**
   * Get current authentication token
   * @returns {string|null} JWT token or null if not authenticated
   */
  getToken() {
    if (!this._token) {
      return null;
    }

    // Check if token is expired
    if (this.isTokenExpired()) {
      logger.warn('[AuthClient] Token expired, clearing...');
      this.clearToken();
      return null;
    }

    return this._token;
  }

  /**
   * Store authentication token (in memory only — never persisted to disk)
   * @param {string} token - JWT token
   * @param {number} expiresIn - Token lifetime in seconds (optional)
   */
  setToken(token, expiresIn = null) {
    this._token = token;

    // Calculate expiry if provided
    if (expiresIn) {
      this._tokenExpiry = Date.now() + expiresIn * 1000;
    } else {
      this._tokenExpiry = null;
    }

    this._notifyListeners('token_set');
  }

  /**
   * Clear authentication token
   */
  clearToken() {
    this._token = null;
    this._tokenExpiry = null;
    this._notifyListeners('token_cleared');
  }

  /**
   * Check if current token is expired
   * @returns {boolean} True if token is expired or will expire soon
   */
  isTokenExpired() {
    if (!this._tokenExpiry) {
      // No expiry set, assume token is valid (cookies handle expiry)
      return false;
    }

    const now = Date.now();

    // Consider expired if within buffer window
    return now >= this._tokenExpiry - TOKEN_EXPIRY_BUFFER_MS;
  }

  /**
   * Get stored user profile
   * @returns {object|null} User object or null
   */
  getUser() {
    if (!this._storage) return null;

    const userStr = this._storage.getItem(STORAGE_KEYS.USER);
    if (!userStr) {
      return null;
    }

    try {
      return JSON.parse(userStr);
    } catch (error) {
      logger.error('[AuthClient] Failed to parse user data:', error);
      return null;
    }
  }

  /**
   * Store user profile
   * @param {object} user - User profile object
   */
  setUser(user) {
    if (!this._storage) return;

    this._storage.setItem(STORAGE_KEYS.USER, JSON.stringify(user));
    this._notifyListeners('user_set');
  }

  /**
   * Clear user profile
   */
  clearUser() {
    if (!this._storage) return;

    this._storage.removeItem(STORAGE_KEYS.USER);
    this._notifyListeners('user_cleared');
  }

  /**
   * Get OAuth state value (CSRF protection)
   * @returns {string|null} OAuth state or null
   */
  getOAuthState() {
    if (!this._sessionStorage) return null;
    return this._sessionStorage.getItem(STORAGE_KEYS.OAUTH_STATE);
  }

  /**
   * Set OAuth state value
   * @param {string} state - Random state string
   */
  setOAuthState(state, provider = 'github') {
    if (!this._sessionStorage) return;
    this._sessionStorage.setItem(STORAGE_KEYS.OAUTH_STATE, state);
    this._sessionStorage.setItem(STORAGE_KEYS.OAUTH_STATE_PROVIDER, provider);
    this._sessionStorage.setItem(
      STORAGE_KEYS.OAUTH_STATE_CREATED_AT,
      Date.now().toString()
    );
    this._notifyListeners('oauth_state_set');
  }

  /**
   * Clear OAuth state
   */
  clearOAuthState() {
    if (!this._sessionStorage) return;
    this._sessionStorage.removeItem(STORAGE_KEYS.OAUTH_STATE);
    this._sessionStorage.removeItem(STORAGE_KEYS.OAUTH_STATE_PROVIDER);
    this._sessionStorage.removeItem(STORAGE_KEYS.OAUTH_STATE_CREATED_AT);
    this._notifyListeners('oauth_state_cleared');
  }

  /**
   * Validate OAuth state for CSRF protection without clearing it.
   * @param {string} receivedState - State from callback query params
   * @param {object} options - Validation options
   * @param {string|null} options.provider - Expected provider (optional)
   * @param {number} options.maxAgeMs - Max state age in milliseconds
   * @returns {{valid: boolean, reason: string|null}}
   */
  validateOAuthState(receivedState, options = {}) {
    const { provider = null, maxAgeMs = DEFAULT_OAUTH_STATE_MAX_AGE_MS } =
      options;

    if (!this._sessionStorage) {
      return { valid: false, reason: 'session_storage_unavailable' };
    }

    const storedState = this.getOAuthState();
    if (!storedState) {
      return { valid: false, reason: 'state_missing' };
    }

    if (!receivedState || receivedState !== storedState) {
      return { valid: false, reason: 'state_mismatch' };
    }

    const createdAtStr = this._sessionStorage.getItem(
      STORAGE_KEYS.OAUTH_STATE_CREATED_AT
    );
    if (createdAtStr) {
      const createdAt = parseInt(createdAtStr, 10);
      if (Number.isFinite(createdAt) && Date.now() - createdAt > maxAgeMs) {
        return { valid: false, reason: 'state_expired' };
      }
    }

    if (provider) {
      const storedProvider = this._sessionStorage.getItem(
        STORAGE_KEYS.OAUTH_STATE_PROVIDER
      );
      if (storedProvider && storedProvider !== provider) {
        return { valid: false, reason: 'provider_mismatch' };
      }
    }

    return { valid: true, reason: null };
  }

  /**
   * Validate and consume OAuth state in a single operation.
   * @param {string} receivedState - State from callback query params
   * @param {object} options - Validation options
   * @returns {{valid: boolean, reason: string|null}}
   */
  validateAndConsumeOAuthState(receivedState, options = {}) {
    const validation = this.validateOAuthState(receivedState, options);
    this.clearOAuthState();
    return validation;
  }

  /**
   * Check if user is authenticated
   * @returns {boolean} True if valid token exists
   */
  isAuthenticated() {
    return this.getToken() !== null;
  }

  /**
   * Complete logout - clear all auth data
   */
  logout() {
    this.clearToken();
    this.clearUser();
    this.clearOAuthState();
    this._notifyListeners('logout');
  }

  /**
   * Subscribe to auth state changes
   * @param {function} callback - Called when auth state changes
   * @returns {function} Unsubscribe function
   */
  subscribe(callback) {
    this.listeners.push(callback);
    return () => {
      this.listeners = this.listeners.filter((cb) => cb !== callback);
    };
  }

  /**
   * Notify all subscribers of auth state change
   * @private
   */
  _notifyListeners(event) {
    this.listeners.forEach((callback) => {
      try {
        callback(event);
      } catch (error) {
        logger.error('[AuthClient] Listener error:', error);
      }
    });
  }

  /**
   * Get authorization headers for API requests
   * @returns {object} Headers object with Authorization if token exists
   */
  getAuthHeaders() {
    const token = this.getToken();
    const headers = {
      'Content-Type': 'application/json',
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    return headers;
  }
}

// Singleton instance
export const authClient = new AuthClient();

// Named exports for convenience
export const getToken = () => authClient.getToken();
export const setToken = (token, expiresIn) =>
  authClient.setToken(token, expiresIn);
export const clearToken = () => authClient.clearToken();
export const getUser = () => authClient.getUser();
export const setUser = (user) => authClient.setUser(user);
export const clearUser = () => authClient.clearUser();
export const isAuthenticated = () => authClient.isAuthenticated();
export const logout = () => authClient.logout();
export const getAuthHeaders = () => authClient.getAuthHeaders();

export default authClient;
