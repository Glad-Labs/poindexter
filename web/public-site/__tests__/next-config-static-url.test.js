/**
 * @jest-environment node
 *
 * next.config.js follows the bucket that lib/static-url.js names.
 *
 * The config allows the bucket's origin in the CSP `connect-src` and its host
 * in `images.remotePatterns`. Its own copy of the bucket URL is what once let
 * the CSP and the pages' fetch URL drift apart, and /search broke without an
 * error (Gitea #262). It reads the module now, so a bucket move needs no edit
 * here, and these tests hold it to that.
 *
 * The config is evaluated by plain Node, the way `next build` reads it, not by
 * Jest: a transform would resolve an import that Node's ESM loader cannot.
 */
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const { DEFAULT_STATIC_URL } = require('../lib/static-url');

const SITE_ROOT = path.join(__dirname, '..');
const DEFAULT_ORIGIN = new URL(DEFAULT_STATIC_URL).origin;
const DEFAULT_HOST = new URL(DEFAULT_STATIC_URL).hostname;

const PROBE = `
  const { default: config } = await import('./next.config.js');
  const headers = await config.headers();
  const csp = headers
    .flatMap((h) => h.headers)
    .find((h) => h.key === 'Content-Security-Policy').value;
  const connectSrc = csp
    .split(';')
    .map((d) => d.trim())
    .find((d) => d.startsWith('connect-src '))
    .split(' ')
    .slice(1);
  console.log(
    JSON.stringify({ connectSrc, remotePatterns: config.images.remotePatterns })
  );
`;

// The config as it evaluates under one NEXT_PUBLIC_STATIC_URL (undefined =
// unset).
function evaluateConfig(staticUrl) {
  const env = { ...process.env };
  // A Sentry DSN wraps the config, and a beacon or image CDN host adds entries
  // that these assertions are not about.
  for (const name of [
    'SENTRY_DSN',
    'NEXT_PUBLIC_SENTRY_DSN',
    'NEXT_PUBLIC_SENTRY_TUNNEL',
    'NEXT_PUBLIC_BEACON_URL',
    'NEXT_PUBLIC_IMAGE_CDN_HOST',
  ]) {
    delete env[name];
  }
  if (staticUrl === undefined) {
    delete env.NEXT_PUBLIC_STATIC_URL;
  } else {
    env.NEXT_PUBLIC_STATIC_URL = staticUrl;
  }
  const out = execFileSync(
    process.execPath,
    ['--input-type=module', '-e', PROBE],
    { cwd: SITE_ROOT, env, encoding: 'utf8' }
  );
  return JSON.parse(out);
}

describe('next.config.js and the static export bucket', () => {
  test('allows the default bucket for fetches and for image optimisation', () => {
    const { connectSrc, remotePatterns } = evaluateConfig(undefined);
    expect(connectSrc).toContain(DEFAULT_ORIGIN);
    expect(remotePatterns).toContainEqual({
      protocol: 'https',
      hostname: DEFAULT_HOST,
      pathname: '/**',
    });
  });

  test('follows NEXT_PUBLIC_STATIC_URL to another bucket without an edit', () => {
    const { connectSrc, remotePatterns } = evaluateConfig(
      'https://static.example.com/static'
    );
    expect(connectSrc).toContain('https://static.example.com');
    expect(connectSrc).not.toContain(DEFAULT_ORIGIN);
    expect(remotePatterns).toContainEqual({
      protocol: 'https',
      hostname: 'static.example.com',
      pathname: '/**',
    });
    // The old host is not kept by name. The *.r2.dev wildcard still covers any
    // R2 bucket, but a custom domain is covered only by this derived entry.
    expect(remotePatterns.map((p) => p.hostname)).not.toContain(DEFAULT_HOST);
  });

  test('allows the default bucket when the variable is not a URL', () => {
    // The pages' fetches fail on such a value, and the CSP must not gain the
    // string 'null' (what new URL('file:///x').origin is) or lose the bucket.
    const { connectSrc } = evaluateConfig('file:///tmp/static');
    expect(connectSrc).toContain(DEFAULT_ORIGIN);
    expect(connectSrc).not.toContain('null');
  });
});
