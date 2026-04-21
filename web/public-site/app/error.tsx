'use client';
import * as Sentry from '@sentry/nextjs';

import Link from 'next/link';
import { useEffect } from 'react';
import { SUPPORT_EMAIL } from '@/lib/site.config';

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Error Boundary Component
 * Catches errors and displays recovery options
 * Shows as the error.tsx page in Next.js
 */
export default function Error({ error, reset }: ErrorProps) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  const isNetworkError =
    error?.message?.includes('fetch') || error?.message?.includes('network');
  const isNotFoundError = error?.message?.includes('404');

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-black flex flex-col">
      {/* Main Content */}
      <div className="flex-1 container mx-auto px-4 sm:px-6 lg:px-8 py-12 md:py-24 flex flex-col justify-center">
        <div className="max-w-2xl mx-auto text-center">
          {/* Error Icon */}
          <div className="mb-8">
            <div className="text-7xl mb-4">
              {isNetworkError ? '🔌' : isNotFoundError ? '🔍' : '⚠️'}
            </div>
            <h1 className="text-5xl md:text-6xl font-bold text-red-400 mb-2">
              Something went wrong
            </h1>
            <p className="text-gray-400 text-lg">
              {isNetworkError
                ? 'Network connection error'
                : isNotFoundError
                  ? 'Page not found'
                  : 'An unexpected error occurred'}
            </p>
          </div>

          {/* Error Details */}
          <div className="bg-gray-800/50 rounded-lg p-6 mb-8 text-left border border-gray-700">
            <h2 className="text-sm text-gray-400 mb-2 font-semibold">
              Error Details
            </h2>
            <p className="text-sm text-red-300/80 font-mono break-words">
              {isNetworkError
                ? 'Unable to connect to the server'
                : 'An unexpected error occurred'}
            </p>
          </div>

          {/* Description */}
          <div className="mb-8 md:mb-12">
            {isNetworkError ? (
              <div>
                <p className="text-gray-300 text-lg mb-3">
                  We&apos;re unable to load content right now. This could be due
                  to:
                </p>
                <ul className="text-gray-400 text-left max-w-md mx-auto space-y-2">
                  <li>• Your internet connection is unstable</li>
                  <li>• The server is temporarily unavailable</li>
                  <li>• A proxy or firewall is blocking the request</li>
                </ul>
              </div>
            ) : isNotFoundError ? (
              <p className="text-gray-300 text-lg">
                The resource you&apos;re looking for couldn&apos;t be found.
              </p>
            ) : (
              <div>
                <p className="text-gray-300 text-lg mb-3">
                  We apologize for the inconvenience. Our team has been notified
                  and is working to fix this issue.
                </p>
                <p className="text-gray-400 text-sm">
                  Error ID:{' '}
                  {Math.random().toString(36).substr(2, 9).toUpperCase()}
                </p>
              </div>
            )}
          </div>

          {/* Action Buttons */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-8">
            <button
              onClick={() => reset()}
              className="inline-block px-8 py-3 bg-cyan-500 hover:bg-cyan-600 text-white font-semibold rounded-lg transition-colors duration-200"
            >
              🔄 Try Again
            </button>
            <Link
              href="/"
              className="inline-block px-8 py-3 bg-gray-700 hover:bg-gray-600 text-white font-semibold rounded-lg transition-colors duration-200"
            >
              ← Go Home
            </Link>
          </div>

          {/* Recovery Tips */}
          <div className="bg-blue-900/20 border border-blue-700/50 rounded-lg p-6 mb-8 text-left">
            <h2 className="text-blue-300 font-semibold mb-3">Recovery Tips</h2>
            <ul className="text-blue-200/80 space-y-2 text-sm">
              <li>✓ Check your internet connection</li>
              <li>✓ Clear your browser cache and cookies</li>
              <li>✓ Try a different browser or incognito mode</li>
              <li>✓ Refresh the page or try again in a few minutes</li>
            </ul>
          </div>

          {/* Helpful Links */}
          <div>
            <h2 className="text-gray-400 mb-4">
              Need help? Here are useful links:
            </h2>
            <div className="flex flex-wrap justify-center gap-4">
              <Link
                href="/"
                className="text-cyan-400 hover:text-cyan-300 underline"
              >
                Homepage
              </Link>
              <span className="text-gray-600">•</span>
              <Link
                href="/archive/1"
                className="text-cyan-400 hover:text-cyan-300 underline"
              >
                Blog Archive
              </Link>
              <span className="text-gray-600">•</span>
              <a
                href={`mailto:${SUPPORT_EMAIL}`}
                className="text-cyan-400 hover:text-cyan-300 underline"
              >
                Contact Support
              </a>
            </div>
          </div>
        </div>
      </div>

      {/* Footer Info */}
      <div className="border-t border-gray-700 bg-gray-900/50 py-6">
        <div className="container mx-auto px-4 text-center text-gray-500 text-sm">
          <p>
            If this problem persists, please{' '}
            <a
              href={`mailto:${SUPPORT_EMAIL}?subject=Website%20Error`}
              className="text-cyan-400 hover:text-cyan-300"
            >
              report the issue
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
}
