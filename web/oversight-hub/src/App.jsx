import logger from '@/lib/logger';
import React, { useEffect } from 'react';
import { BrowserRouter as Router, useLocation } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { WebSocketProvider } from './context/WebSocketContext';
import ErrorBoundary from './components/ErrorBoundary';
import AppRoutes from './routes/AppRoutes';
import NotificationCenter from './components/notifications/NotificationCenter';
import useStore from './store/useStore';
import useAuth from './hooks/useAuth';
import './OversightHub.css';

const AppContent = () => {
  const theme = useStore((state) => state.theme);
  const { loading, isAuthenticated } = useAuth();
  const location = useLocation();

  // Check if user is on a public route (login, auth/callback)
  const isPublicRoute =
    location.pathname === '/login' || location.pathname.startsWith('/auth/');

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  // Show loading while checking authentication
  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
          fontSize: '18px',
          color: '#666',
        }}
      >
        <div>Initializing...</div>
      </div>
    );
  }

  // Public routes (login, auth callback) don't need sidebar/command pane
  if (isPublicRoute) {
    return <AppRoutes />;
  }

  // Protected routes require authentication and show new dashboard layout
  if (!isAuthenticated) {
    return <AppRoutes />;
  }

  // Render new dashboard layout (OversightHub with built-in layout)
  return <AppRoutes />;
};

const App = () => {
  useEffect(() => {
    // Handle unhandled promise rejections
    const handleUnhandledRejection = (event) => {
      logger.error('Unhandled promise rejection:', event.reason);
      // You can optionally send to error tracking service here (e.g., Sentry)
    };

    window.addEventListener('unhandledrejection', handleUnhandledRejection);

    return () => {
      window.removeEventListener(
        'unhandledrejection',
        handleUnhandledRejection
      );
    };
  }, []);

  return (
    <ErrorBoundary>
      <AuthProvider>
        <WebSocketProvider>
          <Router
            future={{
              v7_startTransition: true,
              v7_relativeSplatPath: true,
            }}
          >
            <AppContent />
            <NotificationCenter />
          </Router>
        </WebSocketProvider>
      </AuthProvider>
    </ErrorBoundary>
  );
};

export default App;
