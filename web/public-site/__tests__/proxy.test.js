/**
 * @jest-environment node
 */
/**
 * Edge proxy tests — the 410-Gone behaviour for removed posts.
 *
 * A rejected/deleted post's per-slug JSON is pruned from R2, but the ISR route
 * renders not-found.tsx with a 200 (App-Router soft-404), which Google files as
 * "Crawled - currently not indexed". The proxy converts a *definitive* R2 404
 * into a 410 Gone so the URL deindexes cleanly — WITHOUT ever 410ing a live post
 * during a transient R2 outage (which would tell Google to deindex it).
 * (SEO indexing audit, 2026-07-03.)
 */
import { NextRequest } from 'next/server';
import { proxy, postIsGone } from '../proxy';

const R2 = 'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

function req(path) {
  return new NextRequest(`https://www.gladlabs.io${path}`);
}

beforeEach(() => {
  global.fetch = jest.fn();
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('postIsGone', () => {
  it('returns true only on a definitive 404', async () => {
    global.fetch.mockResolvedValue({ status: 404 });
    await expect(postIsGone('dead-slug')).resolves.toBe(true);
  });

  it('returns false for a live post (200)', async () => {
    global.fetch.mockResolvedValue({ status: 200 });
    await expect(postIsGone('live-slug')).resolves.toBe(false);
  });

  it('returns false on a 5xx outage — never deindex a maybe-live post', async () => {
    global.fetch.mockResolvedValue({ status: 503 });
    await expect(postIsGone('live-slug')).resolves.toBe(false);
  });

  it('returns false on a network error / timeout', async () => {
    global.fetch.mockRejectedValue(new Error('timeout'));
    await expect(postIsGone('live-slug')).resolves.toBe(false);
  });

  it('probes the per-slug JSON with a cheap HEAD request', async () => {
    global.fetch.mockResolvedValue({ status: 404 });
    await postIsGone('some-slug');
    expect(global.fetch).toHaveBeenCalledWith(
      `${R2}/posts/some-slug.json`,
      expect.objectContaining({ method: 'HEAD' })
    );
  });
});

describe('proxy() — removed posts return 410 Gone', () => {
  it('returns 410 with a noindex header for a removed post', async () => {
    global.fetch.mockResolvedValue({ status: 404 });
    const res = await proxy(req('/posts/removed-slug'));
    expect(res.status).toBe(410);
    expect(res.headers.get('x-robots-tag')).toBe('noindex');
  });

  it('passes a live post through (200, not 410)', async () => {
    global.fetch.mockResolvedValue({ status: 200 });
    const res = await proxy(req('/posts/live-slug'));
    expect(res.status).toBe(200);
  });

  it('passes through when R2 is down — a blip must never deindex a live post', async () => {
    global.fetch.mockRejectedValue(new Error('R2 unreachable'));
    const res = await proxy(req('/posts/live-slug'));
    expect(res.status).not.toBe(410);
  });

  it('does not probe R2 for non-post paths', async () => {
    const res = await proxy(req('/about'));
    expect(res.status).not.toBe(410);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('leaves the /posts/<slug>.md alternate to its own handler', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ title: 'X', content: 'body' }),
    });
    const res = await proxy(req('/posts/live-slug.md'));
    expect(res.status).not.toBe(410);
  });
});
