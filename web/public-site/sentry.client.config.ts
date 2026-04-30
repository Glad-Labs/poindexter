// Sentry client-side configuration
// Only initializes if NEXT_PUBLIC_SENTRY_DSN is set in environment.

import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,

    // Capture 10% of transactions for performance monitoring (adjust in production)
    tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,

    // Capture 100% of sessions with errors for replay
    replaysOnErrorSampleRate: 1.0,

    // Capture 1% of all sessions for replay (adjust in production)
    replaysSessionSampleRate: 0.01,

    // Do not send PII to Sentry
    sendDefaultPii: false,

    integrations: [
      Sentry.replayIntegration({
        // Mask all text and inputs to avoid capturing PII
        maskAllText: true,
        blockAllMedia: true,
      }),
    ],

    // Suppress Sentry logs in non-production environments
    debug: false,

    environment: process.env.NODE_ENV,
  });
}
