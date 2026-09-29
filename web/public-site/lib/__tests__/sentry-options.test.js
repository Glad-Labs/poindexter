/**
 * @jest-environment node
 */
/**
 * lib/sentry-options.ts — what every Sentry runtime on the site sends.
 *
 * Errors only, and only when the relay is fully configured: a DSN without
 * the tunnel would post to an endpoint the relay does not serve and drop
 * every report while looking configured.
 */
import {
  baseSentryOptions,
  sentryRelayEnabled,
  withoutSessionTracking,
} from '../sentry-options';

// Fake key; built rather than written out so secret scanners don't read it
// as a credential.
const DSN = `https://${'c'.repeat(32)}@relay.example.com/2`;
const TUNNEL = 'https://relay.example.com/relay';

describe('sentryRelayEnabled', () => {
  test('needs both the DSN and the tunnel', () => {
    expect(sentryRelayEnabled({ dsn: DSN, tunnel: TUNNEL })).toBe(true);
    expect(sentryRelayEnabled({ dsn: DSN })).toBe(false);
    expect(sentryRelayEnabled({ tunnel: TUNNEL })).toBe(false);
    expect(sentryRelayEnabled({ dsn: '', tunnel: '' })).toBe(false);
  });
});

describe('baseSentryOptions', () => {
  const options = baseSentryOptions({ dsn: DSN, tunnel: TUNNEL });

  test('routes every envelope through the relay tunnel', () => {
    expect(options.dsn).toBe(DSN);
    expect(options.tunnel).toBe(TUNNEL);
  });

  test('sends no PII and no client reports', () => {
    expect(options.sendDefaultPii).toBe(false);
    expect(options.sendClientReports).toBe(false);
  });

  test('leaves tracing and replay off', () => {
    // Any of these would put a non-error envelope on the relay's queue.
    expect(options).not.toHaveProperty('tracesSampleRate');
    expect(options).not.toHaveProperty('tracesSampler');
    expect(options).not.toHaveProperty('replaysSessionSampleRate');
    expect(options).not.toHaveProperty('replaysOnErrorSampleRate');
  });
});

describe('withoutSessionTracking', () => {
  test('drops only the BrowserSession integration', () => {
    const defaults = [
      { name: 'InboundFilters' },
      { name: 'BrowserSession' },
      { name: 'GlobalHandlers' },
      { name: 'Dedupe' },
    ];
    expect(withoutSessionTracking(defaults).map((i) => i.name)).toEqual([
      'InboundFilters',
      'GlobalHandlers',
      'Dedupe',
    ]);
  });
});
