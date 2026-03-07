import logger from '@/lib/logger';
/**
 * AuthContext - Global authentication state
 * Syncs with Zustand store to keep auth state consistent across entire app
 */

import React, { createContext, useState, useEffect, useCallback } from 'react';
import {
  logout as authLogout,
  getStoredUser,
  handleOAuthCallbackNew,
  validateAndGetCurrentUser,
} from '../services/authService';
import useStore from '../store/useStore';

export const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Get Zustand store functions
  const setStoreUser = useStore((state) => state.setUser);
  const setStoreIsAuthenticated = useStore((state) => state.setIsAuthenticated);
  const storeLogout = useStore((state) => state.logout);

  // Initialize auth state ONCE on mount
  useEffect(() => {
    const initializeAuth = async () => {
      try {
        logger.log(
          '🔐 [AuthContext] Starting authentication initialization...'
        );
        const startTime = Date.now();

        // Validate active cookie-based session first.
        const currentUser = await validateAndGetCurrentUser();
        if (currentUser) {
          setStoreUser(currentUser);
          setStoreIsAuthenticated(true);
          setUser(currentUser);
          setError(null);
          setLoading(false);
          const elapsed = Date.now() - startTime;
          logger.log(`✅ [AuthContext] Session restored (${elapsed}ms)`);
          return;
        }

        // Fall back to cached user profile for UI continuity when session lookup fails.
        const storedUser = getStoredUser();
        if (storedUser) {
          setStoreUser(storedUser);
          setStoreIsAuthenticated(false);
          setUser(storedUser);
        } else {
          setStoreIsAuthenticated(false);
          setUser(null);
        }
        setError(null);
        setLoading(false);
        const elapsed = Date.now() - startTime;
        logger.log(`✅ [AuthContext] Initialization complete (${elapsed}ms)`);
      } catch (err) {
        logger.error('❌ [AuthContext] Initialization error:', err);
        setError(err.message);
        setStoreIsAuthenticated(false);
        setUser(null);
        setLoading(false);
      }
    };

    initializeAuth();
  }, [setStoreUser, setStoreIsAuthenticated]);

  // Logout handler - sync with both AuthContext and Zustand
  const logout = useCallback(async () => {
    try {
      logger.log('🚪 [AuthContext] Logging out...');
      await authLogout();
      setUser(null);
      storeLogout(); // Clear Zustand store
      logger.log('✅ [AuthContext] Logout complete');
    } catch (err) {
      logger.error('❌ [AuthContext] Logout error:', err);
      setError(err.message);
    }
  }, [storeLogout]);

  // Set user after login - sync with both context and Zustand
  const setAuthUser = useCallback(
    (userData) => {
      logger.log('👤 [AuthContext] Setting user:', userData?.login);
      setUser(userData);
      setStoreUser(userData);
      setStoreIsAuthenticated(!!userData);
    },
    [setStoreUser, setStoreIsAuthenticated]
  );

  // OAuth callback handler
  const handleOAuthCallback = useCallback(
    async (provider, code, state) => {
      try {
        logger.log(
          `🔐 [AuthContext] Processing ${provider} OAuth callback...`
        );
        setLoading(true);
        const result = await handleOAuthCallbackNew(provider, code, state);

        if (result.user) {
          logger.log(
            `✅ [AuthContext] OAuth login successful for ${provider}`
          );
          setAuthUser(result.user);
          setError(null);
          return result.user;
        } else {
          throw new Error(`No user data returned from ${provider} OAuth`);
        }
      } catch (err) {
        logger.error('❌ [AuthContext] OAuth callback error:', err);
        setError(err.message);
        setLoading(false);
        throw err;
      }
    },
    [setAuthUser]
  );

  // Validate current user token
  const validateCurrentUser = useCallback(async () => {
    try {
      logger.log('🔐 [AuthContext] Validating current user...');
      const user = await validateAndGetCurrentUser();
      if (user) {
        setAuthUser(user);
        setError(null);
        return user;
      } else {
        setUser(null);
        storeLogout();
        return null;
      }
    } catch (err) {
      logger.error('❌ [AuthContext] Validation error:', err);
      setError(err.message);
      return null;
    }
  }, [setAuthUser, storeLogout]);

  const value = {
    user,
    loading,
    error,
    isAuthenticated: !!user,
    logout,
    setAuthUser,
    handleOAuthCallback,
    validateCurrentUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export default AuthContext;
