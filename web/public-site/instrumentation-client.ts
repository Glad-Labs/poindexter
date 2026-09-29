// Sentry browser SDK. Next.js runs this file before the app hydrates.
//
// Initializes only when BOTH NEXT_PUBLIC_SENTRY_DSN and
// NEXT_PUBLIC_SENTRY_TUNNEL are set; see lib/sentry-options.ts for why the
// tunnel is required and why only errors are sent.

import * as Sentry from '@sentry/nextjs';

import {
  baseSentryOptions,
  sentryRelayEnabled,
  withoutSessionTracking,
} from './lib/sentry-options';

// Read verbatim so Next.js inlines the values into the browser bundle.
const relay = {
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tunnel: process.env.NEXT_PUBLIC_SENTRY_TUNNEL,
};

if (sentryRelayEnabled(relay)) {
  Sentry.init({
    ...baseSentryOptions(relay),
    integrations: withoutSessionTracking,
  });
}
