// Sentry edge SDK, loaded by instrumentation.ts register() in the edge
// runtime.
//
// Initializes only when BOTH NEXT_PUBLIC_SENTRY_DSN and
// NEXT_PUBLIC_SENTRY_TUNNEL are set. See lib/sentry-options.ts.

import * as Sentry from '@sentry/nextjs';

import { baseSentryOptions, sentryRelayEnabled } from './lib/sentry-options';

const relay = {
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tunnel: process.env.NEXT_PUBLIC_SENTRY_TUNNEL,
};

if (sentryRelayEnabled(relay)) {
  Sentry.init(baseSentryOptions(relay));
}
