// Next.js instrumentation hook. Sentry v8+ initializes on the server only from
// here: without this file sentry.server.config.ts and sentry.edge.config.ts
// are never loaded, and every server-side Sentry.captureException is a no-op.
// Until 2026-09-28 the site had no instrumentation file, so no server error
// was ever reported.

import * as Sentry from '@sentry/nextjs';

export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('./sentry.server.config');
  }
  if (process.env.NEXT_RUNTIME === 'edge') {
    await import('./sentry.edge.config');
  }
}

// Errors thrown from server components, route handlers and server actions
// that nothing caught. Caught errors report through Sentry.captureException.
export const onRequestError = Sentry.captureRequestError;
