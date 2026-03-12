import logger from '@/lib/logger';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { generateGitHubAuthURL } from '../services/authService';
import useAuth from '../hooks/useAuth';
import { getEnv } from '../config/apiConfig';
import './Login.css';

const Login = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const clientId = getEnv('REACT_APP_GH_OAUTH_CLIENT_ID');
  // Mock auth ONLY allowed in development
  const mode = getEnv('MODE', 'NODE_ENV') || 'development';
  const isDevelopment = mode === 'development';
  const useMockAuth =
    isDevelopment && getEnv('REACT_APP_USE_MOCK_AUTH') === 'true';
  const [debugInfo, setDebugInfo] = useState('');

  useEffect(() => {
    const info = {
      environment: mode,
      clientId: clientId ? 'Set' : 'NOT SET',
      mockAuth:
        isDevelopment && useMockAuth ? 'ENABLED (DEV ONLY)' : 'DISABLED',
    };
    setDebugInfo(JSON.stringify(info, null, 2));

    // Warn if mock auth is enabled in production
    if (!isDevelopment && getEnv('REACT_APP_USE_MOCK_AUTH') === 'true') {
      logger.error('❌ SECURITY: Mock auth enabled in non-development mode!');
    }
  }, [clientId, useMockAuth, isDevelopment, mode]);

  useEffect(() => {
    if (isAuthenticated) navigate('/');
  }, [isAuthenticated, navigate]);

  const handleGitHubLogin = async () => {
    if (useMockAuth && isDevelopment) {
      // Dynamically import mockAuthService only when needed in dev mode
      const { generateMockGitHubAuthURL } =
        await import('../services/mockAuthService');
      window.location.href = generateMockGitHubAuthURL(clientId || 'mock_id');
    } else {
      if (!clientId) {
        alert(
          'GitHub Client ID not configured. Set REACT_APP_GH_OAUTH_CLIENT_ID in web/oversight-hub/.env.local'
        );
        return;
      }
      window.location.href = generateGitHubAuthURL(clientId);
    }
  };

  return (
    <div className="login-container">
      <div className="login-card">
        <div className="login-header">
          <h1>Glad Labs</h1>
          <h2>Oversight Hub</h2>
        </div>
        <div className="login-body">
          <button
            className="github-login-btn"
            onClick={handleGitHubLogin}
            type="button"
          >
            {useMockAuth && isDevelopment
              ? 'Sign in (Mock - Dev Only)'
              : 'Sign in with GitHub'}
          </button>
          <div style={{ marginTop: '20px', fontSize: '11px', opacity: 0.5 }}>
            <pre>{debugInfo}</pre>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
