/**
 * Unit Tests for AuthClient
 * Tests token management, user profile, OAuth state, and event subscription
 */

import { describe, test, expect, beforeEach, vi } from 'vitest';
import { authClient } from '../authClient';

describe('AuthClient', () => {
  beforeEach(() => {
    // Clear auth client state before each test
    authClient.logout();
    // Clear all timer mocks
    vi.clearAllTimers();
  });

  describe('Token Management', () => {
    test('should store and retrieve token', () => {
      const testToken = 'test-jwt-token-12345';
      authClient.setToken(testToken);

      const retrievedToken = authClient.getToken();
      expect(retrievedToken).toBe(testToken);
    });

    test('should return null when no token exists', () => {
      authClient.clearToken();
      const token = authClient.getToken();
      expect(token).toBeNull();
    });

    test('should store token with expiry time', () => {
      const testToken = 'test-jwt-token-12345';
      const expiresIn = 7200; // 2 hours in seconds

      authClient.setToken(testToken, expiresIn);

      const retrievedToken = authClient.getToken();
      expect(retrievedToken).toBe(testToken);
    });

    test('should return null for expired token', () => {
      const testToken = 'test-jwt-token-12345';
      const expiresIn = -10; // Negative = already expired

      authClient.setToken(testToken, expiresIn);

      const retrievedToken = authClient.getToken();
      expect(retrievedToken).toBeNull();
    });

    test('should check token expiry with 5-minute buffer', () => {
      const testToken = 'test-jwt-token-12345';
      const expiresIn = 200; // 200 seconds = 3.3 minutes (within 5-min buffer)

      authClient.setToken(testToken, expiresIn);

      const retrievedToken = authClient.getToken();
      // Should return null because token expires within 5 minutes
      expect(retrievedToken).toBeNull();
    });

    test('should clear token', () => {
      authClient.setToken('test-token');
      authClient.clearToken();

      const token = authClient.getToken();
      expect(token).toBeNull();
    });
  });

  describe('User Profile Management', () => {
    test('should store and retrieve user profile', () => {
      const testUser = {
        id: 123,
        username: 'testuser',
        email: 'test@example.com',
        avatar: 'https://example.com/avatar.jpg',
      };

      authClient.setUser(testUser);

      const retrievedUser = authClient.getUser();
      expect(retrievedUser).toEqual(testUser);
    });

    test('should return null when no user exists', () => {
      authClient.clearUser();
      const user = authClient.getUser();
      expect(user).toBeNull();
    });

    test('should handle invalid JSON when retrieving user', () => {
      // Set invalid JSON directly to localStorage
      if (typeof window !== 'undefined') {
        localStorage.setItem('user', 'invalid-json{{{');
      }

      const user = authClient.getUser();
      expect(user).toBeNull();
    });

    test('should clear user profile', () => {
      authClient.setUser({ id: 123, username: 'testuser' });
      authClient.clearUser();

      const user = authClient.getUser();
      expect(user).toBeNull();
    });
  });

  describe('OAuth State Management', () => {
    test('should store and retrieve OAuth state', () => {
      const testState = 'abc123xyz';

      authClient.setOAuthState(testState);

      const retrievedState = authClient.getOAuthState();
      expect(retrievedState).toBe(testState);
    });

    test('should return null when no OAuth state exists', () => {
      authClient.clearOAuthState();
      const state = authClient.getOAuthState();
      expect(state).toBeNull();
    });

    test('should clear OAuth state', () => {
      authClient.setOAuthState('abc123');
      authClient.clearOAuthState();

      const state = authClient.getOAuthState();
      expect(state).toBeNull();
    });

    test('should validate and consume matching OAuth state', () => {
      authClient.setOAuthState('state-123', 'github');

      const result = authClient.validateAndConsumeOAuthState('state-123', {
        provider: 'github',
      });

      expect(result.valid).toBe(true);
      expect(result.reason).toBeNull();
      expect(authClient.getOAuthState()).toBeNull();
    });

    test('should reject mismatched OAuth provider', () => {
      authClient.setOAuthState('state-123', 'google');

      const result = authClient.validateAndConsumeOAuthState('state-123', {
        provider: 'github',
      });

      expect(result.valid).toBe(false);
      expect(result.reason).toBe('provider_mismatch');
      expect(authClient.getOAuthState()).toBeNull();
    });

    test('should reject expired OAuth state', () => {
      vi.useFakeTimers();
      const start = new Date('2026-01-01T00:00:00.000Z');
      vi.setSystemTime(start);

      authClient.setOAuthState('state-123', 'github');

      vi.setSystemTime(new Date(start.getTime() + 11 * 60 * 1000));

      const result = authClient.validateAndConsumeOAuthState('state-123', {
        provider: 'github',
      });

      expect(result.valid).toBe(false);
      expect(result.reason).toBe('state_expired');
      expect(authClient.getOAuthState()).toBeNull();

      vi.useRealTimers();
    });
  });

  describe('Authentication Headers', () => {
    test('should return headers with Authorization when token exists', () => {
      const testToken = 'test-jwt-token-12345';
      authClient.setToken(testToken);

      const headers = authClient.getAuthHeaders();
      expect(headers).toEqual({
        'Content-Type': 'application/json',
        Authorization: `Bearer ${testToken}`,
      });
    });

    test('should return headers without Authorization when no token exists', () => {
      authClient.clearToken();
      const headers = authClient.getAuthHeaders();
      expect(headers).toEqual({
        'Content-Type': 'application/json',
      });
    });

    test('should return headers without Authorization when token is expired', () => {
      authClient.setToken('expired-token', -10);

      const headers = authClient.getAuthHeaders();
      expect(headers).toEqual({
        'Content-Type': 'application/json',
      });
    });
  });

  describe('Authentication Status', () => {
    test('should return true when valid token exists', () => {
      authClient.setToken('valid-token', 7200); // 2 hours

      expect(authClient.isAuthenticated()).toBe(true);
    });

    test('should return false when no token exists', () => {
      authClient.clearToken();
      expect(authClient.isAuthenticated()).toBe(false);
    });

    test('should return false when token is expired', () => {
      authClient.setToken('expired-token', -10);

      expect(authClient.isAuthenticated()).toBe(false);
    });
  });

  describe('Logout', () => {
    test('should clear all auth data', () => {
      // Setup auth state
      authClient.setToken('test-token');
      authClient.setUser({ id: 123, username: 'testuser' });
      authClient.setOAuthState('abc123');

      // Logout
      authClient.logout();

      // Verify all cleared
      expect(authClient.getToken()).toBeNull();
      expect(authClient.getUser()).toBeNull();
      expect(authClient.getOAuthState()).toBeNull();
      expect(authClient.isAuthenticated()).toBe(false);
    });

    test('should notify subscribers on logout', () => {
      const callback = vi.fn();
      authClient.subscribe(callback);

      authClient.setToken('test-token');
      authClient.logout();

      // Verify key events were called
      expect(callback).toHaveBeenCalledWith('token_set');
      expect(callback).toHaveBeenCalledWith('logout');
      // Total calls should include: token_set, token_cleared, user_cleared, oauth_state_cleared, logout
      expect(callback.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe('Event Subscription', () => {
    test('should notify subscribers on token change', () => {
      const callback = vi.fn();
      authClient.subscribe(callback);

      authClient.setToken('test-token');

      expect(callback).toHaveBeenCalledWith('token_set');
    });

    test('should notify subscribers on logout', () => {
      const callback = vi.fn();
      authClient.subscribe(callback);

      authClient.setToken('test-token');
      authClient.logout();

      // Expect events: token_set, token_cleared, user_cleared, oauth_state_cleared, logout
      expect(callback).toHaveBeenCalled();
      expect(callback).toHaveBeenCalledWith('logout');
    });

    test('should support multiple subscribers', () => {
      const callback1 = vi.fn();
      const callback2 = vi.fn();

      authClient.subscribe(callback1);
      authClient.subscribe(callback2);

      authClient.setToken('test-token');

      expect(callback1).toHaveBeenCalledWith('token_set');
      expect(callback2).toHaveBeenCalledWith('token_set');
    });

    test('should allow unsubscribing', () => {
      const callback = vi.fn();
      const unsubscribe = authClient.subscribe(callback);

      authClient.setToken('test-token-1');
      expect(callback).toHaveBeenCalledTimes(1);

      unsubscribe();

      authClient.setToken('test-token-2');
      // Should still be 1 (not called again after unsubscribe)
      expect(callback).toHaveBeenCalledTimes(1);
    });
  });

  describe('SSR Safety', () => {
    test('should not throw when accessing auth methods', () => {
      expect(() => {
        authClient.getToken();
        authClient.getUser();
        authClient.isAuthenticated();
      }).not.toThrow();
    });
  });

  describe('Edge Cases', () => {
    test('should handle null token', () => {
      // In-memory storage: null is falsy, getToken returns null
      authClient.setToken(null);

      const token = authClient.getToken();
      expect(token).toBeNull();
    });

    test('should handle undefined token', () => {
      // In-memory storage: undefined is falsy, getToken returns null
      authClient.setToken(undefined);

      const token = authClient.getToken();
      expect(token).toBeNull();
    });

    test('should handle empty string token', () => {
      authClient.setToken('');

      const token = authClient.getToken();
      // Empty string is falsy, so getToken returns null
      expect(token).toBeNull();
    });

    test('should handle null user', () => {
      // localStorage.setItem converts null to string "null"
      authClient.setUser(null);

      const user = authClient.getUser();
      // getUser tries to parse "null" which is valid JSON
      expect(user).toBe(null);
    });

    test('should handle non-object user', () => {
      // localStorage.setItem converts value to string
      authClient.setUser('not-an-object');

      const user = authClient.getUser();
      // getUser tries to parse string, which succeeds (valid JSON string)
      expect(user).toBe('not-an-object');
    });

    test('should handle zero expiresIn as no expiry', () => {
      authClient.setToken('test-token', 0);

      // Token should be retrievable - 0 means no expiry tracking
      const token = authClient.getToken();
      expect(token).toBe('test-token');
    });

    test('should handle negative expiresIn as expired', () => {
      authClient.setToken('test-token', -100);

      const token = authClient.getToken();
      expect(token).toBeNull();
    });
  });
});
