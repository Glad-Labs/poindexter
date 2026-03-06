import React from 'react';
import { logError } from '../services/errorLoggingService';
import { Box, Typography, Button, Container } from '@mui/material';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';

/**
 * Error Boundary Component
 * Catches errors in child components and displays fallback UI
 * Prevents entire app from crashing on component errors
 */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      errorCount: 0,
    };
  }

  static getDerivedStateFromError(_error) {
    // Update state so the next render will show the fallback UI
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // Log error to console for debugging
    console.error('🔴 Error Boundary caught an error:', error);
    console.error('📋 Error Info:', errorInfo);

    // Update state with error details
    this.setState((prevState) => ({
      error,
      errorInfo,
      errorCount: prevState.errorCount + 1,
    }));

    // Log to error tracking service in production
    if (process.env.NODE_ENV === 'production') {
      this.logErrorToService(error, errorInfo);
    }
  }

  logErrorToService = (error, errorInfo) => {
    // Use centralized error logging service
    // This handles Sentry, backend logging, and proper auth headers
    logError(error, {
      componentStack: errorInfo?.componentStack || '',
      severity: 'critical',
      customContext: {
        userAgent: navigator.userAgent,
        url: window.location.href,
        environment: process.env.NODE_ENV,
      },
    }).catch((err) => {
      // Silently fail - error logging should never break the app
      console.error('Error logging failed:', err);
    });
  };

  handleReset = () => {
    // Reset error state
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  handleReload = () => {
    // Reload the page
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <Container maxWidth="sm" sx={{ py: 4 }}>
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              minHeight: '60vh',
              textAlign: 'center',
              gap: 3,
            }}
          >
            {/* Error Icon */}
            <ErrorOutlineIcon
              sx={{
                fontSize: 80,
                color: 'error.main',
                opacity: 0.8,
              }}
            />

            {/* Error Title */}
            <Typography
              variant="h4"
              component="h1"
              sx={{
                fontWeight: 'bold',
                color: 'error.main',
              }}
            >
              Oops! Something Went Wrong
            </Typography>

            {/* Error Message */}
            <Typography
              variant="body1"
              color="textSecondary"
              sx={{
                maxWidth: 400,
                lineHeight: 1.6,
              }}
            >
              An unexpected error occurred. The issue has been logged and our
              team will investigate. Please try refreshing the page or go back
              to the dashboard.
            </Typography>

            {/* Error Details (Development Only) */}
            {process.env.NODE_ENV === 'development' && this.state.error && (
              <Box
                sx={{
                  backgroundColor: '#f5f5f5',
                  border: '1px solid #ddd',
                  borderRadius: 1,
                  p: 2,
                  maxWidth: '100%',
                  textAlign: 'left',
                  overflow: 'auto',
                  maxHeight: 200,
                }}
              >
                <Typography
                  variant="caption"
                  component="div"
                  sx={{
                    fontFamily: 'monospace',
                    fontSize: '0.8rem',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    color: '#d32f2f',
                  }}
                >
                  {this.state.error.toString()}
                </Typography>
              </Box>
            )}

            {/* Action Buttons */}
            <Box
              sx={{
                display: 'flex',
                gap: 2,
                justifyContent: 'center',
                width: '100%',
                flexWrap: 'wrap',
              }}
            >
              <Button
                variant="contained"
                color="primary"
                onClick={this.handleReset}
                sx={{
                  minWidth: 150,
                }}
              >
                Try Again
              </Button>

              <Button
                variant="outlined"
                color="primary"
                onClick={this.handleReload}
                sx={{
                  minWidth: 150,
                }}
              >
                Reload Page
              </Button>

              <Button
                variant="outlined"
                color="secondary"
                href="/"
                sx={{
                  minWidth: 150,
                }}
              >
                Go Home
              </Button>
            </Box>

            {/* Error Count Indicator */}
            {this.state.errorCount > 2 && (
              <Typography
                variant="caption"
                color="error"
                sx={{
                  marginTop: 2,
                  padding: 1,
                  backgroundColor: 'rgba(211, 47, 47, 0.1)',
                  borderRadius: 1,
                }}
              >
                ⚠️ Multiple errors detected. A reload or restart is recommended.
              </Typography>
            )}
          </Box>
        </Container>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
