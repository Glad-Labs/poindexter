// Unit tests for the affiliate-redirect Worker.
//
// Two layers. The pure helpers (`codeFromPath`, `resolveTarget`) cover the
// routing math: which path segment becomes the code, and that an unknown code
// resolves to null. The handler suite covers what the helpers can't see — that
// the four outcomes stay distinguishable on the wire.
//
// That second suite exists because they once weren't. Every unhappy path
// (unset config, unreachable map, malformed LINKS_URL, unknown code) returned
// the same 302 to HOME_URL, so a Worker whose LINKS_URL had been clobbered by
// a deploy was externally identical to a healthy one — affiliate attribution
// could die for weeks with nothing anywhere reporting it. These tests are the
// regression guard for that conflation, not just coverage.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import worker, { codeFromPath, resolveTarget } from './index';

describe('codeFromPath', () => {
  it('extracts the code from /go/<code>', () => {
    expect(codeFromPath('/go/mercury')).toBe('mercury');
  });
  it('extracts the code from a bare /<code> (subdomain routing)', () => {
    expect(codeFromPath('/mercury')).toBe('mercury');
  });
  it('returns empty string for /go/ and /', () => {
    expect(codeFromPath('/go/')).toBe('');
    expect(codeFromPath('/')).toBe('');
  });
  it('ignores trailing slashes and nested segments', () => {
    expect(codeFromPath('/go/mercury/')).toBe('mercury');
  });
});

describe('resolveTarget', () => {
  const map = { mercury: { url: 'https://mercury.com/r/x' } };

  it('resolves a known code to its url', () => {
    expect(resolveTarget(map, 'mercury')).toBe('https://mercury.com/r/x');
  });
  it('returns null for an unknown code', () => {
    expect(resolveTarget(map, 'nope')).toBeNull();
  });
  it('returns null against an empty map', () => {
    expect(resolveTarget({}, 'mercury')).toBeNull();
  });
});

describe('fetch handler', () => {
  const LINKS_URL = 'https://r2.example.test/static/affiliate-links.json';
  const HOME_URL = 'https://www.example.com';
  const MAP = { mercury: { url: 'https://mercury.example/r/x' } };

  // The literal placeholder this repo used to commit in wrangler.toml [vars].
  // A deploy from a clean checkout would have written exactly this string over
  // the live binding.
  const PLACEHOLDER = 'https://<r2-public-host>/static/affiliate-links.json';

  let writeDataPoint: ReturnType<typeof vi.fn>;

  function env(over: Record<string, unknown> = {}) {
    return {
      ANALYTICS_ENGINE: { writeDataPoint },
      LINKS_URL,
      HOME_URL,
      ...over,
    } as unknown as Parameters<typeof worker.fetch>[1];
  }

  const call = (e: Parameters<typeof worker.fetch>[1], path = '/go/mercury') =>
    worker.fetch(new Request(`https://site.example.test${path}`), e);

  beforeEach(() => {
    writeDataPoint = vi.fn();
    // Always a cache miss, so every test exercises the fetch path.
    vi.stubGlobal('caches', {
      default: { match: async () => undefined, put: async () => undefined },
    });
    vi.stubGlobal('fetch', async () => Response.json(MAP));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('redirects a known code to its merchant URL and logs one click', async () => {
    const res = await call(env());
    expect(res.status).toBe(302);
    expect(res.headers.get('location')).toBe(MAP.mercury.url);
    expect(writeDataPoint).toHaveBeenCalledTimes(1);
  });

  it('redirects an unknown code home without logging a click', async () => {
    const res = await call(env(), '/go/no-such-code');
    expect(res.status).toBe(302);
    expect(res.headers.get('location')).toBe(`${HOME_URL}/`);
    expect(writeDataPoint).not.toHaveBeenCalled();
  });

  it('redirects a blank code home', async () => {
    const res = await call(env(), '/go/');
    expect(res.status).toBe(302);
    expect(res.headers.get('location')).toBe(`${HOME_URL}/`);
  });

  it('fails loud with 503 when LINKS_URL is unset', async () => {
    const res = await call(env({ LINKS_URL: undefined }));
    expect(res.status).toBe(503);
  });

  it('fails loud with 503 when HOME_URL is unset', async () => {
    const res = await call(env({ HOME_URL: undefined }));
    expect(res.status).toBe(503);
  });

  // The deploy-clobber scenario, end to end: [vars] overwrites the real host
  // with the committed placeholder. It must not masquerade as an unknown code.
  it('returns 502 — not a home redirect — when LINKS_URL is the committed placeholder', async () => {
    const res = await call(env({ LINKS_URL: PLACEHOLDER }));
    expect(res.status).toBe(502);
    expect(res.headers.get('location')).toBeNull();
  });

  it('returns 502 when the map URL 404s', async () => {
    vi.stubGlobal(
      'fetch',
      async () => new Response('not found', { status: 404 })
    );
    const res = await call(env());
    expect(res.status).toBe(502);
  });

  it('returns 502 when the map body is not JSON', async () => {
    vi.stubGlobal('fetch', async () => new Response('<html>nope</html>'));
    const res = await call(env());
    expect(res.status).toBe(502);
  });

  it('returns 502 when the map body is JSON of the wrong shape', async () => {
    vi.stubGlobal('fetch', async () => Response.json([1, 2, 3]));
    const res = await call(env());
    expect(res.status).toBe(502);
  });

  it('returns 502 when the map fetch throws (R2 unreachable)', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new Error('network');
    });
    const res = await call(env());
    expect(res.status).toBe(502);
  });
});
