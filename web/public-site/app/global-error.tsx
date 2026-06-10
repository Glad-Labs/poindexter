'use client';
import * as Sentry from '@sentry/nextjs';
import { useEffect } from 'react';

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Global error boundary — catches errors thrown in the root layout and any
 * nested segments not already caught by a closer error.tsx. Must include its
 * own <html>/<body> because it replaces the root layout entirely. (#1324)
 */
export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily: 'system-ui, sans-serif',
          background: '#0a0f14',
          color: '#e2e8f0',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          textAlign: 'center',
          padding: '2rem',
        }}
      >
        <div>
          <p
            style={{
              fontSize: '0.75rem',
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              color: '#64748b',
              marginBottom: '1rem',
            }}
          >
            GLAD LABS · CRITICAL ERROR
          </p>
          <h1 style={{ fontSize: '2rem', fontWeight: 700, marginBottom: '1rem' }}>
            Something went wrong.
          </h1>
          <p style={{ color: '#94a3b8', marginBottom: '2rem', maxWidth: '32rem' }}>
            A critical error occurred. Our team has been notified.
            {error?.digest ? ` (ref: ${error.digest})` : ''}
          </p>
          <button
            onClick={reset}
            style={{
              padding: '0.6rem 1.5rem',
              background: '#0e7490',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '0.875rem',
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
