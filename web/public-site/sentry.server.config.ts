// Sentry Node SDK, loaded by instrumentation.ts register() in the Node.js
// runtime (route handlers, server components, ISR renders).
//
// Initializes only when BOTH NEXT_PUBLIC_SENTRY_DSN and
// NEXT_PUBLIC_SENTRY_TUNNEL are set. Server-side events go through the same
// relay as the browser's: a Vercel function cannot reach the LAN-only
// GlitchTip either. See lib/sentry-options.ts.

import * as Sentry from '@sentry/nextjs';

import { baseSentryOptions, sentryRelayEnabled } from './lib/sentry-options';

const relay = {
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tunnel: process.env.NEXT_PUBLIC_SENTRY_TUNNEL,
};

if (sentryRelayEnabled(relay)) {
  Sentry.init({
    ...baseSentryOptions(relay),
    integrations: [
      // Replaces the SDK's default Http integration, which records incoming
      // requests as sessions and ships a session aggregate that is not an
      // error. Next.js instruments incoming requests itself.
      Sentry.httpIntegration({
        disableIncomingRequestSpans: true,
        sessions: false,
      }),
    ],
  });
}
