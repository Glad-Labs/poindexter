/**
 * @jest-environment node
 *
 * /podcast-feed.xml and /video-feed.xml proxy a feed that the backend
 * publishes next to `static/` in the R2 bucket, at <bucket origin>/podcast/
 * feed.xml and <bucket origin>/video/feed.xml. The origin comes from
 * lib/static-url.js, so a bucket move reaches these routes with everything
 * else. Each route used to hold the bucket's host a second time, as the
 * fallback for a NEXT_PUBLIC_STATIC_URL it could not parse, and nothing tested
 * either.
 */
const { DEFAULT_STATIC_URL, STATIC_ORIGIN } = require('../../lib/static-url');

const DEFAULT_ORIGIN = new URL(DEFAULT_STATIC_URL).origin;

const FEEDS = [
  ['podcast', '../../app/podcast-feed.xml/route', '/podcast/feed.xml'],
  ['video', '../../app/video-feed.xml/route', '/video/feed.xml'],
];

// The route as it evaluates under one NEXT_PUBLIC_STATIC_URL (undefined = as
// this test run has it). It builds its feed URL once, at import.
function loadRoute(routePath, staticUrl) {
  const savedEnv = process.env;
  try {
    if (staticUrl !== undefined) {
      process.env = { ...savedEnv, NEXT_PUBLIC_STATIC_URL: staticUrl };
    }
    let route;
    jest.isolateModules(() => {
      route = require(routePath);
    });
    return route;
  } finally {
    process.env = savedEnv;
  }
}

describe.each(FEEDS)('%s feed route', (_name, routePath, feedPath) => {
  const realFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    global.fetch = realFetch;
  });

  test('proxies the feed from the bucket origin', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      text: async () => '<rss version="2.0"></rss>',
    });
    const res = await loadRoute(routePath).GET();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch.mock.calls[0][0]).toBe(`${STATIC_ORIGIN}${feedPath}`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toMatch(/^application\/rss\+xml/);
    await expect(res.text()).resolves.toBe('<rss version="2.0"></rss>');
  });

  test('follows NEXT_PUBLIC_STATIC_URL to another bucket', async () => {
    global.fetch.mockResolvedValue({ ok: true, text: async () => '<rss/>' });
    await loadRoute(routePath, 'https://static.example.com/static').GET();
    expect(global.fetch.mock.calls[0][0]).toBe(
      `https://static.example.com${feedPath}`
    );
  });

  test('uses the default bucket when the variable is not a URL', async () => {
    global.fetch.mockResolvedValue({ ok: true, text: async () => '<rss/>' });
    await loadRoute(routePath, 'not a url').GET();
    expect(global.fetch.mock.calls[0][0]).toBe(`${DEFAULT_ORIGIN}${feedPath}`);
  });

  test('answers 502 when the bucket has no feed', async () => {
    global.fetch.mockResolvedValue({ ok: false, status: 404 });
    const res = await loadRoute(routePath).GET();
    expect(res.status).toBe(502);
  });

  test('answers 502 when the fetch itself fails', async () => {
    global.fetch.mockRejectedValue(new Error('bucket unreachable'));
    const res = await loadRoute(routePath).GET();
    expect(res.status).toBe(502);
  });
});
