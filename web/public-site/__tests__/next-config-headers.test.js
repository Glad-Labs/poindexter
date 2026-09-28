/**
 * @jest-environment node
 *
 * `next dev` must not be told that /_next/static is `immutable`.
 *
 * Dev serves its chunks under stable URLs (chunks/app/layout.js, no content
 * hash). A browser told `immutable` keeps the first bundle it saw across
 * reloads, so edits look like they did nothing (found 2026-09-28 while
 * working stack#4195; `curl -sD-` on the chunk showed the header). Next warns
 * about the same mistake at every dev start: load-custom-routes flags any
 * header whose source starts with `/_next/` and sets Cache-Control, which is
 * the condition `nextInternalCacheControlSources` repeats below.
 *
 * Production chunk URLs are hashed, so the 30-day header stays there.
 */

// next.config.js is an ES module that imports @sentry/nextjs. It only calls
// withSentryConfig when a DSN is set, and the shared setup mock does not
// provide it, so stub it: a SENTRY_DSN in the developer's shell must not
// break this file.
jest.mock('@sentry/nextjs', () => ({
  withSentryConfig: (config) => config,
}));

const ORIGINAL_NODE_ENV = process.env.NODE_ENV;

afterEach(() => {
  process.env.NODE_ENV = ORIGINAL_NODE_ENV;
});

// headers() reads NODE_ENV when it is called, so one import serves every mode.
async function headersFor(nodeEnv) {
  process.env.NODE_ENV = nodeEnv;
  const { default: config } = await import('../next.config.js');
  return config.headers();
}

const hasCacheControl = (rule) =>
  rule.headers.some((header) => header.key.toLowerCase() === 'cache-control');

function nextInternalCacheControlSources(rules) {
  return rules
    .filter(
      (rule) => rule.source.startsWith('/_next/') && hasCacheControl(rule)
    )
    .map((rule) => rule.source);
}

describe('next.config.js headers()', () => {
  it.each(['development', 'test'])(
    'sets no Cache-Control on /_next/ routes when NODE_ENV is %s',
    async (nodeEnv) => {
      const rules = await headersFor(nodeEnv);
      expect(nextInternalCacheControlSources(rules)).toEqual([]);
    }
  );

  it('keeps the 30-day immutable header on /_next/static in production', async () => {
    const rules = await headersFor('production');
    const rule = rules.find((r) => r.source === '/_next/static/:path*');
    expect(rule).toBeDefined();
    expect(rule.headers).toEqual([
      { key: 'Cache-Control', value: 'public, max-age=2592000, immutable' },
    ]);
  });

  it('keeps the security headers in every mode', async () => {
    // The dev gate must drop one entry, not the array around it.
    for (const nodeEnv of ['development', 'test', 'production']) {
      const rules = await headersFor(nodeEnv);
      const security = rules.find(
        (r) =>
          r.source === '/:path*' &&
          r.headers.some((h) => h.key === 'Strict-Transport-Security')
      );
      expect(security).toBeDefined();
    }
  });
});
