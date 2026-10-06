/**
 * @jest-environment node
 */
/**
 * next.config.js — the error relay's wiring into the build.
 *
 * Pins the two ways site error reporting went dark without anyone noticing:
 * the browser's send target missing from CSP connect-src (every tunnel POST
 * blocked, same shape as the 2026-06 page-views beacon outage), and a
 * half-configured DSN/tunnel pair that ships a site whose error pages say
 * "we've been notified" while every report is dropped.
 */

// Fake key, dashless (the form the SDK accepts); built rather than written
// out so secret scanners don't read it as a credential.
const KEY = 'c'.repeat(32);
const DSN = `https://${KEY}@relay.example.com/2`;
const TUNNEL = 'https://relay.example.com/relay';

const originalEnv = process.env;
let withSentryConfig;

function loadConfig(env) {
  process.env = { ...originalEnv, ...env };
  let config;
  jest.isolateModules(() => {
    withSentryConfig = jest.fn((cfg, opts) => ({
      ...cfg,
      __sentryOptions: opts,
    }));
    jest.doMock('@sentry/nextjs/config', () => ({ withSentryConfig }));
    config = require('../next.config.js').default;
  });
  return config;
}

async function connectSrc(config) {
  const blocks = await config.headers();
  const csp = blocks
    .flatMap((b) => b.headers)
    .find((h) => h.key === 'Content-Security-Policy').value;
  return csp
    .split(';')
    .map((d) => d.trim())
    .find((d) => d.startsWith('connect-src'));
}

afterEach(() => {
  process.env = originalEnv;
  jest.resetModules();
});

describe('connect-src', () => {
  test('allows the relay origin the SDK tunnels to', async () => {
    const config = loadConfig({
      NEXT_PUBLIC_SENTRY_DSN: DSN,
      NEXT_PUBLIC_SENTRY_TUNNEL: TUNNEL,
    });
    expect((await connectSrc(config)).split(' ')).toContain(
      'https://relay.example.com'
    );
  });

  test('adds nothing when the relay is not configured', async () => {
    const config = loadConfig({
      NEXT_PUBLIC_SENTRY_DSN: '',
      NEXT_PUBLIC_SENTRY_TUNNEL: '',
    });
    expect(await connectSrc(config)).not.toContain('relay.example.com');
  });
});

describe('withSentryConfig', () => {
  test('wraps only when both values are set, without calling sentry.io', () => {
    const config = loadConfig({
      NEXT_PUBLIC_SENTRY_DSN: DSN,
      NEXT_PUBLIC_SENTRY_TUNNEL: TUNNEL,
    });
    expect(withSentryConfig).toHaveBeenCalledTimes(1);
    const options = config.__sentryOptions;
    expect(options.telemetry).toBe(false);
    expect(options.sourcemaps).toEqual({ disable: true });
    expect(options.release.create).toBe(false);
    // The same-origin rewrite only works for sentry.io DSNs; the relay is
    // the tunnel.
    expect(options).not.toHaveProperty('tunnelRoute');
  });

  test("names the release after Vercel's commit so events say which deploy", () => {
    const config = loadConfig({
      NEXT_PUBLIC_SENTRY_DSN: DSN,
      NEXT_PUBLIC_SENTRY_TUNNEL: TUNNEL,
      VERCEL_GIT_COMMIT_SHA: 'abc1234',
    });
    expect(config.__sentryOptions.release.name).toBe('abc1234');
  });

  test('passes the config through untouched without the relay', () => {
    const config = loadConfig({
      NEXT_PUBLIC_SENTRY_DSN: '',
      NEXT_PUBLIC_SENTRY_TUNNEL: '',
    });
    expect(withSentryConfig).not.toHaveBeenCalled();
    expect(config).not.toHaveProperty('__sentryOptions');
  });
});

describe('production build guard', () => {
  const prod = { NODE_ENV: 'production' };

  test('refuses a DSN without a tunnel, and a tunnel without a DSN', () => {
    expect(() =>
      loadConfig({
        ...prod,
        NEXT_PUBLIC_SENTRY_DSN: DSN,
        NEXT_PUBLIC_SENTRY_TUNNEL: '',
      })
    ).toThrow(/must be set together/);
    expect(() =>
      loadConfig({
        ...prod,
        NEXT_PUBLIC_SENTRY_DSN: '',
        NEXT_PUBLIC_SENTRY_TUNNEL: TUNNEL,
      })
    ).toThrow(/must be set together/);
  });

  test('refuses a tunnel that is not a URL', () => {
    expect(() =>
      loadConfig({
        ...prod,
        NEXT_PUBLIC_SENTRY_DSN: DSN,
        NEXT_PUBLIC_SENTRY_TUNNEL: 'relay.example.com/relay',
      })
    ).toThrow(/is not a valid URL/);
  });

  test('refuses a DSN whose key is the dashed UUID form', () => {
    // GlitchTip shows keys dashed; @sentry/core's DSN regex rejects that at
    // runtime with only a console line, so the build must refuse it.
    expect(() =>
      loadConfig({
        ...prod,
        NEXT_PUBLIC_SENTRY_DSN:
          'https://cccccccc-cccc-cccc-cccc-cccccccccccc@relay.example.com/2',
        NEXT_PUBLIC_SENTRY_TUNNEL: TUNNEL,
      })
    ).toThrow(/not a DSN the Sentry SDK accepts/);
  });

  test('builds with both set, and with neither', () => {
    expect(() =>
      loadConfig({
        ...prod,
        NEXT_PUBLIC_SENTRY_DSN: DSN,
        NEXT_PUBLIC_SENTRY_TUNNEL: TUNNEL,
      })
    ).not.toThrow();
    expect(() =>
      loadConfig({
        ...prod,
        NEXT_PUBLIC_SENTRY_DSN: '',
        NEXT_PUBLIC_SENTRY_TUNNEL: '',
      })
    ).not.toThrow();
  });
});
