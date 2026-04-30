/**
 * Sitemap Generation Tests
 *
 * Tests the Next.js sitemap.ts route handler.
 * Verifies: Static pages, dynamic post/category/tag pages, error fallback
 *
 * sitemap.ts now reads from static JSON on R2/CDN (posts/index.json,
 * categories.json, sitemap.json) instead of FastAPI.
 */

// Mock the logger module
jest.mock('@/lib/logger', () => ({
  __esModule: true,
  default: {
    log: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  },
}));

// Mock fetch globally
global.fetch = jest.fn();

// Reset modules before each test to re-evaluate env-dependent code
beforeEach(() => {
  jest.resetModules();
  global.fetch.mockReset();
  process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
});

afterEach(() => {
  delete process.env.NEXT_PUBLIC_SITE_URL;
});

async function loadSitemap() {
  const mod = await import('../../app/sitemap');
  return mod.default;
}

/**
 * Helper: mock all 3 static JSON fetches (posts/index.json, categories.json, sitemap.json)
 */
function mockStaticJsonFetches({
  posts = [],
  categories = [],
  sitemapUrls = [],
} = {}) {
  global.fetch.mockImplementation((url) => {
    if (url.includes('/posts/index.json')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          posts,
          total: posts.length,
          exported_at: '2024-01-15T00:00:00Z',
        }),
      });
    }
    if (url.includes('/categories.json')) {
      return Promise.resolve({
        ok: true,
        json: async () => categories,
      });
    }
    if (url.includes('/sitemap.json')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ urls: sitemapUrls }),
      });
    }
    return Promise.resolve({ ok: false });
  });
}

describe('sitemap()', () => {
  it('should always include static and legal pages', async () => {
    mockStaticJsonFetches();

    const sitemap = await loadSitemap();
    const result = await sitemap();

    const urls = result.map((entry) => entry.url);
    expect(urls).toContain('https://example.com');
    expect(urls).toContain('https://example.com/about');
    expect(urls).toContain('https://example.com/posts');
    expect(urls).toContain('https://example.com/archive/1');
    expect(urls).toContain('https://example.com/legal/privacy');
    expect(urls).toContain('https://example.com/legal/terms');
    expect(urls).toContain('https://example.com/legal/cookie-policy');
    expect(urls).toContain('https://example.com/legal/data-requests');
  });

  it('should include post URLs from static JSON response', async () => {
    mockStaticJsonFetches({
      posts: [
        { slug: 'first-post', updated_at: '2024-01-01' },
        { slug: 'second-post', published_at: '2024-02-01' },
      ],
    });

    const sitemap = await loadSitemap();
    const result = await sitemap();
    const urls = result.map((e) => e.url);

    expect(urls).toContain('https://example.com/posts/first-post');
    expect(urls).toContain('https://example.com/posts/second-post');
  });

  it('should return only static pages when fetch fails', async () => {
    global.fetch.mockRejectedValue(new Error('Network error'));

    const sitemap = await loadSitemap();
    const result = await sitemap();

    // Should have exactly 8 static + legal pages
    expect(result.length).toBe(8);
  });

  it('should set priority=1 for the homepage', async () => {
    mockStaticJsonFetches();

    const sitemap = await loadSitemap();
    const result = await sitemap();
    const home = result.find((e) => e.url === 'https://example.com');

    expect(home.priority).toBe(1);
  });
});
