/**
 * Sentry SDK options shared by the browser, Node and edge runtimes.
 *
 * Errors only. Every envelope goes to the sentry-relay Cloudflare Worker
 * (the SDK's `tunnel`), which queues it for the operator's worker to pull
 * into a LAN-only GlitchTip (infrastructure/cloudflare/sentry-relay in the
 * main repo). The queue has a write budget, so nothing that isn't an error is
 * sent: no tracing, no replays, no session pings, no client reports. The
 * relay drops those at the edge anyway; not sending them saves the request.
 *
 * Callers pass the values in rather than this module reading process.env,
 * because Next.js inlines `process.env.NEXT_PUBLIC_X` into the browser bundle
 * only where the expression appears verbatim.
 */

export interface SentryRelayEnv {
  /** NEXT_PUBLIC_SENTRY_DSN — `https://<key>@<relay host>/<project id>`, key as dashless hex. */
  dsn?: string;
  /** NEXT_PUBLIC_SENTRY_TUNNEL — `https://<relay host>/relay`. */
  tunnel?: string;
}

/**
 * True only when BOTH are set. A DSN without the tunnel would send straight
 * to the DSN host's `/api/<id>/envelope/`, which the relay does not serve, so
 * every error would be lost while the SDK looked configured.
 */
export function sentryRelayEnabled({ dsn, tunnel }: SentryRelayEnv): boolean {
  return Boolean(dsn && tunnel);
}

/** Options every runtime shares. Tracing stays off: no tracesSampleRate. */
export function baseSentryOptions({ dsn, tunnel }: SentryRelayEnv) {
  return {
    dsn,
    tunnel,
    sendDefaultPii: false,
    sendClientReports: false,
  };
}

/**
 * Drop the browser SDK's BrowserSession integration, which sends a session
 * envelope on every page view. Used as the `integrations` callback.
 */
export function withoutSessionTracking<T extends { name: string }>(
  defaults: T[]
): T[] {
  return defaults.filter(
    (integration) => integration.name !== 'BrowserSession'
  );
}
