/**
 * @jest-environment node
 *
 * proxy.ts — Cloudflare-safe markdown content negotiation.
 *
 * Regression for the 2026-06-29 edge-cache incident: Cloudflare's cache key
 * ignores `Vary: Accept` (only `Accept-Encoding` is honored), so a markdown
 * variant served at the SAME URL as the HTML page poisons the shared cache —
 * once an AI agent fills it with `Accept: text/markdown`, human browsers get
 * raw markdown too. The fix: serve cacheable markdown on a distinct URL
 * (`/posts/<slug>.md`) and make the same-URL negotiation non-cacheable so it
 * can never poison the HTML cache key.
 */
import { NextRequest } from 'next/server';

import { proxy } from './proxy';

const POST = {
  title: 'PII leaks and the three-stage shakedown of X distribution',
  published_at: '2026-06-29T18:55:36Z',
  author: 'Glad Labs',
  excerpt: 'A routine audit surfaced our own email in the public mirror.',
  content: '<p>We found our own personal email live in the public mirror.</p>',
};

function mockFetchOk(post: Record<string, unknown> = POST) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => post,
    text: async () => '',
  }) as unknown as typeof fetch;
}

function req(url: string, accept = '*/*') {
  return new NextRequest(url, { headers: { accept } });
}

describe('proxy markdown content negotiation (Cloudflare-safe)', () => {
  afterEach(() => jest.restoreAllMocks());

  test('/posts/<slug>.md serves cacheable markdown on a distinct URL', async () => {
    mockFetchOk();
    const res = await proxy(req('https://www.gladlabs.io/posts/pii-leaks.md'));

    expect(res.headers.get('content-type')).toMatch(/text\/markdown/);
    // A distinct URL is safe to cache at the edge (own cache key).
    expect(res.headers.get('cache-control')).toMatch(/max-age=\d+/);
    expect(res.headers.get('cache-control') ?? '').not.toMatch(/no-store/);

    const body = await res.text();
    expect(body).toContain(
      '# PII leaks and the three-stage shakedown of X distribution',
    );
  });

  test('Accept: text/markdown on the HTML URL returns NON-cacheable markdown', async () => {
    mockFetchOk();
    const res = await proxy(
      req('https://www.gladlabs.io/posts/pii-leaks', 'text/markdown'),
    );

    expect(res.headers.get('content-type')).toMatch(/text\/markdown/);
    // Must NOT be edge-cacheable: CF ignores Vary:Accept, so a cached markdown
    // copy under the HTML key would be served to browsers too.
    expect(res.headers.get('cache-control') ?? '').toMatch(/no-store/);
    // Advertises the cacheable, poison-safe alternate.
    expect(res.headers.get('link') ?? '').toContain('/posts/pii-leaks.md');
  });

  test('browser (Accept: text/html) passes through to HTML, not markdown', async () => {
    mockFetchOk();
    const res = await proxy(
      req('https://www.gladlabs.io/posts/pii-leaks', 'text/html'),
    );

    expect(res.headers.get('content-type') ?? '').not.toMatch(/text\/markdown/);
    expect(res.headers.get('x-markdown-tokens')).toBeNull();
    expect(res.headers.get('vary') ?? '').toMatch(/accept/i);
  });

  test('missing post on the .md URL falls through (no markdown response)', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({}),
      text: async () => '',
    }) as unknown as typeof fetch;

    const res = await proxy(req('https://www.gladlabs.io/posts/missing.md'));
    // Falls through to normal handling — never a 200 markdown body.
    expect(res.headers.get('x-markdown-tokens')).toBeNull();
  });
});

describe('proxy /robots.txt Content-Signal append', () => {
  afterEach(() => jest.restoreAllMocks());

  test('appends the agents-welcome Content-Signal directive', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => 'User-Agent: *\nAllow: /',
      json: async () => ({}),
    }) as unknown as typeof fetch;

    const res = await proxy(req('https://www.gladlabs.io/robots.txt'));
    const body = await res.text();

    expect(res.headers.get('content-type')).toMatch(/text\/plain/);
    // Metadata-route output is preserved…
    expect(body).toContain('User-Agent: *');
    // …with the explicit all-welcome declaration appended. gladlabs.io is the
    // funnel for Poindexter: models training on it and answer engines grounding
    // on it are distribution, so the site declares yes on all three signals.
    expect(body).toContain(
      'Content-Signal: ai-train=yes, search=yes, ai-input=yes',
    );

    // The self-fetch carries the recursion-guard bypass header.
    const fetchInit = (global.fetch as jest.Mock).mock.calls[0][1];
    expect(fetchInit.headers['x-proxy-bypass']).toBe('1');
  });

  test('bypassed request (proxy self-fetch) passes through untouched', async () => {
    global.fetch = jest.fn() as unknown as typeof fetch;

    const res = await proxy(
      new NextRequest('https://www.gladlabs.io/robots.txt', {
        headers: { accept: '*/*', 'x-proxy-bypass': '1' },
      }),
    );

    // No re-fetch, no appended body — the metadata route serves it directly.
    expect(global.fetch).not.toHaveBeenCalled();
    expect(res.headers.get('x-markdown-tokens')).toBeNull();
  });

  test('upstream failure falls through to the unmodified metadata route', async () => {
    global.fetch = jest
      .fn()
      .mockRejectedValue(new Error('edge fetch failed')) as unknown as typeof fetch;

    const res = await proxy(req('https://www.gladlabs.io/robots.txt'));

    // Never a 5xx from the proxy itself — degraded mode serves robots.txt
    // without the directive rather than breaking crawler access entirely.
    expect(res.status).toBe(200);
  });
});
