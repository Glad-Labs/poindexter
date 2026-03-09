import { Component, ErrorInfo, ReactNode } from 'react';
import Link from 'next/link';
import { logError, getErrorType, getErrorMessage } from '../lib/error-handling';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: { component?: string };
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  onReset?: () => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  errorType: string | null;
}

/**
 * Error Boundary Component
 * Catches React errors and displays a fallback UI
 * Usage: Wrap pages or sections with <ErrorBoundary>Content</ErrorBoundary>
 */
export default class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      errorType: null,
    };
  }

  static getDerivedStateFromError(_error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    const errorType = getErrorType(error);

    this.setState({
      error,
      errorInfo,
      errorType,
    });

    // Log error to monitoring service
    logError(error, {
      component: this.props.fallback?.component || 'Unknown',
      componentStack: errorInfo.componentStack,
    });

    // Call optional error callback
    this.props.onError?.(error, errorInfo);
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      errorType: null,
    });

    // Call optional reset callback
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      return (
        <ErrorFallback
          error={this.state.error}
          errorType={this.state.errorType}
          onReset={this.handleReset}
          isDevelopment={process.env.NODE_ENV === 'development'}
        />
      );
    }

    return this.props.children;
  }
}

interface ErrorFallbackProps {
  error: Error | null;
  errorType: string | null;
  onReset: () => void;
  isDevelopment: boolean;
}

/**
 * Error Fallback Component
 * Displays user-friendly error message with full WCAG 2.1 AA compliance
 */
function ErrorFallback({
  error,
  errorType,
  onReset,
  isDevelopment,
}: ErrorFallbackProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const errorMessage = getErrorMessage(error, errorType as any);

  return (
    <div
      className="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-black flex flex-col"
      role="alert"
      aria-live="assertive"
      aria-atomic="true"
    >
      {/* Skip to main error content */}
      <a
        href="#error-main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:bg-red-500 focus:text-white focus:px-4 focus:py-2 focus:rounded"
      >
        Skip to error details
      </a>

      {/* Main Content */}
      <main
        id="error-main"
        className="flex-1 container mx-auto px-4 md:px-6 py-12 md:py-24 flex flex-col justify-center"
        role="main"
      >
        <div className="max-w-2xl mx-auto">
          {/* Error Icon */}
          <div className="text-center mb-8">
            <div className="text-6xl mb-4" aria-hidden="true">
              ⚠️
            </div>
            <h1
              className="text-4xl font-bold text-red-400 mb-2"
              id="error-heading"
            >
              Something went wrong
            </h1>
            <p className="text-gray-400 text-lg" id="error-description">
              {errorMessage}
            </p>
          </div>

          {/* Development Error Details */}
          {isDevelopment && error && (
            <details
              className="bg-gray-800/50 rounded-lg p-6 mb-8 border border-red-700/50"
              aria-label="Error details for developers"
            >
              <summary className="cursor-pointer text-red-300 font-semibold mb-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 rounded px-2">
                📋 Error Details (Development)
              </summary>
              <div className="mt-4">
                <div className="bg-gray-900 rounded p-4 mb-4 overflow-auto max-h-48">
                  <code
                    className="text-red-400 font-mono text-sm whitespace-pre-wrap break-words"
                    role="log"
                  >
                    {error.message}
                  </code>
                </div>
                {error.stack && (
                  <div className="bg-gray-900 rounded p-4 overflow-auto max-h-32">
                    <code
                      className="text-gray-400 font-mono text-xs whitespace-pre-wrap break-words"
                      role="log"
                    >
                      {error.stack}
                    </code>
                  </div>
                )}
              </div>
            </details>
          )}

          {/* Action Buttons */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-8">
            <button
              onClick={onReset}
              className="px-8 py-3 bg-cyan-500 hover:bg-cyan-600 text-white font-semibold rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 transition-all duration-200 cursor-pointer"
              aria-label="Try to reload the page and recover from the error"
            >
              🔄 Try Again
            </button>
            <Link
              href="/"
              className="px-8 py-3 bg-gray-700 hover:bg-gray-600 text-white font-semibold rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 transition-all duration-200 text-center"
              aria-label="Return to home page"
            >
              ← Go Home
            </Link>
          </div>

          {/* Recovery Tips */}
          <section
            className="bg-blue-900/20 border border-blue-700/50 rounded-lg p-6"
            aria-labelledby="recovery-heading"
          >
            <h2
              id="recovery-heading"
              className="text-blue-300 font-semibold mb-3"
            >
              💡 Recovery Tips:
            </h2>
            <ul className="text-blue-200/80 space-y-2 text-sm">
              <li>✓ Try refreshing the page</li>
              <li>✓ Clear your browser cache and cookies</li>
              <li>✓ Check your internet connection</li>
              <li>✓ Try a different browser</li>
              <li>✓ Contact support if the problem persists</li>
            </ul>
          </section>
        </div>
      </main>

      {/* Footer */}
      <footer
        className="border-t border-gray-700 bg-gray-900/50 py-6"
        role="contentinfo"
      >
        <div className="container mx-auto px-4 text-center text-gray-500 text-sm">
          <p>
            Need help?{' '}
            <a
              href="mailto:hello@glad-labs.com"
              className="text-cyan-400 hover:text-cyan-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 rounded"
              aria-label="Contact support via email"
            >
              Contact support
            </a>
          </p>
        </div>
      </footer>
    </div>
  );
}
