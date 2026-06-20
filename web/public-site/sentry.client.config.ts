// Sentry client-side configuration
// Only initializes if NEXT_PUBLIC_SENTRY_DSN is set in environment.

import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const tunnel = process.env.NEXT_PUBLIC_SENTRY_TUNNEL;

if (dsn) {
  Sentry.init({
    dsn,

    // Route error envelopes through the sentry-relay Cloudflare Worker when
    // configured (poindexter#711 item 2). The self-hosted tracker is LAN-only:
    // public browsers can't POST to it directly, and the same-origin Vercel
    // `tunnelRoute` can't reach it either (Vercel functions can't see the LAN).
    // The relay is the public hop that forwards envelopes to the tracker over a
    // Tailscale Funnel. See infrastructure/cloudflare/sentry-relay/. Unset →
    // the SDK sends straight to the DSN host (correct for a hosted DSN).
    ...(tunnel ? { tunnel } : {}),

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
