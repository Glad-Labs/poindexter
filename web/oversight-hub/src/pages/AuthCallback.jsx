import logger from '@/lib/logger';
/**
 * OAuth Callback Handler
 * Processes GitHub/Google OAuth callback and exchanges code for token
 * Supports both old (exchangeCodeForToken) and new (handleOAuthCallbackNew) auth functions
 */

import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { CircularProgress, Alert, Box } from '@mui/material';
import {
  exchangeCodeForToken,
  handleOAuthCallbackNew,
} from '../services/authService';
import useAuth from '../hooks/useAuth';

const AuthCallback = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { setUser, setAccessToken, setIsAuthenticated } = useAuth();
  const [error, setError] = useState(null);

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const provider = searchParams.get('provider') || 'github'; // Default to github
        const error = searchParams.get('error');

        // Check for OAuth provider errors
        if (error) {
          logger.error('OAuth error:', error);
          setError(`OAuth error: ${error}`);
          setTimeout(() => navigate('/login'), 3000);
          return;
        }

        if (!code) {
          logger.error('No authorization code received');
          setError('No authorization code received');
          setTimeout(() => navigate('/login'), 3000);
          return;
        }

        // Try new OAuth callback handler first (preferred)
        let userData;
        try {
          userData = await handleOAuthCallbackNew(provider, code, state);
        } catch (err) {
          logger.warn(
            'New OAuth handler failed, trying legacy handler:',
            err.message
          );
          // Fallback to legacy handler
          const data = await exchangeCodeForToken(code, state, provider);
          userData = data.user || data;
        }

        // Update auth context with user data
        const userToSet = userData.user || userData;
        setUser(userToSet);

        // Store the token
        if (userData.token) {
          setAccessToken(userData.token);
        }

        // Mark user as authenticated
        setIsAuthenticated(true);

        // Redirect to dashboard
        navigate('/', { replace: true });
      } catch (err) {
        logger.error('Error handling OAuth callback:', err);
        setError(err.message || 'Failed to authenticate. Please try again.');
        setError((prev) => `${prev}`); // Keep error visible
      }
    };

    handleCallback();
  }, [searchParams, navigate, setUser, setAccessToken, setIsAuthenticated]);

  // Render loading state
  if (!error) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          flexDirection: 'column',
          gap: 2,
        }}
      >
        <CircularProgress sx={{ color: 'white' }} />
        <div style={{ textAlign: 'center', color: 'white' }}>
          <h2>Authenticating...</h2>
          <p>Please wait while we verify your credentials.</p>
        </div>
      </Box>
    );
  }

  // Render error state
  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        p: 2,
      }}
    >
      <Box sx={{ maxWidth: 400 }}>
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
        <button
          onClick={() => navigate('/login')}
          style={{
            width: '100%',
            padding: '12px',
            backgroundColor: '#667eea',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '16px',
            fontWeight: 'bold',
          }}
        >
          Back to Login
        </button>
      </Box>
    </Box>
  );
};

export default AuthCallback;
