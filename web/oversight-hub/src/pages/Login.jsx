import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { generateGitHubAuthURL } from '../services/authService';
import useAuth from '../hooks/useAuth';
import './Login.css';

const Login = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const clientId = process.env.REACT_APP_GH_OAUTH_CLIENT_ID;
  // Mock auth ONLY allowed in development
  const isDevelopment = process.env.NODE_ENV === 'development';
  const useMockAuth =
    isDevelopment && process.env.REACT_APP_USE_MOCK_AUTH === 'true';
  const [debugInfo, setDebugInfo] = useState('');

  useEffect(() => {
    const info = {
      environment: process.env.NODE_ENV,
      clientId: clientId ? 'Set' : 'NOT SET',
      mockAuth:
        isDevelopment && useMockAuth ? 'ENABLED (DEV ONLY)' : 'DISABLED',
    };
    setDebugInfo(JSON.stringify(info, null, 2));

    // Warn if mock auth is enabled in production
    if (!isDevelopment && process.env.REACT_APP_USE_MOCK_AUTH === 'true') {
      console.error('❌ SECURITY: Mock auth enabled in non-development mode!');
    }
  }, [clientId, useMockAuth, isDevelopment]);

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
          'GitHub Client ID not configured. Check REACT_APP_GH_OAUTH_CLIENT_ID environment variable.'
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
