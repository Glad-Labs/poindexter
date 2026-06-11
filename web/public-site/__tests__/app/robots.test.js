/**
 * Robots.txt Generation Tests
 *
 * Tests the Next.js robots.ts route handler.
 * Verifies: User-agent rules, disallowed paths, sitemap URL
 */

const originalEnv = process.env;

beforeEach(() => {
  jest.resetModules();
  process.env = { ...originalEnv };
});

afterAll(() => {
  process.env = originalEnv;
});

describe('robots()', () => {
  it('should return rules for the default user-agent allowing /', async () => {
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    const defaultRule = result.rules.find((r) => r.userAgent === '*');
    expect(defaultRule).toBeDefined();
    expect(defaultRule.allow).toBe('/');
  });

  it('should disallow /api/, /admin/, /private/ for default UA', async () => {
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    const defaultRule = result.rules.find((r) => r.userAgent === '*');
    expect(defaultRule.disallow).toContain('/api/');
    expect(defaultRule.disallow).toContain('/admin/');
    expect(defaultRule.disallow).toContain('/private/');
  });

  it('should NOT disallow /_next/ — Google needs the CSS/JS there to render', async () => {
    // Blocking /_next/ walls Google off from the CSS/JS it uses to render
    // pages, and those assets were the bulk of the "Blocked by robots.txt"
    // entries in Search Console (SEO indexing audit, 2026-06-04).
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    const defaultRule = result.rules.find((r) => r.userAgent === '*');
    expect(defaultRule.disallow).not.toContain('/_next/');
  });

  it('should NOT disallow /.well-known/ — agent discovery bots need access to api-catalog, mcp, and agent-skills', async () => {
    // Blocking /.well-known/ prevents agent-discovery crawlers from reaching
    // /.well-known/api-catalog, /.well-known/mcp/server-card.json, and
    // /.well-known/agent-skills/index.json, breaking the RFC 9727 / SEP-1649
    // agent-readiness surface.
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    const defaultRule = result.rules.find((r) => r.userAgent === '*');
    expect(defaultRule.disallow).not.toContain('/.well-known/');
  });

  it('should block DotBot and allow AhrefsBot/SemrushBot for SEO analysis', async () => {
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    // DotBot is blocked (aggressive scraper)
    const dotbot = result.rules.find((r) => r.userAgent === 'DotBot');
    expect(dotbot).toBeDefined();
    expect(dotbot.disallow).toBe('/');

    // AhrefsBot and SemrushBot are intentionally NOT blocked (needed for SEO backlink analysis)
    expect(
      result.rules.find((r) => r.userAgent === 'AhrefsBot')
    ).toBeUndefined();
    expect(
      result.rules.find((r) => r.userAgent === 'SemrushBot')
    ).toBeUndefined();
  });

  it('should include sitemap URL', async () => {
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    expect(result.sitemap).toBe('https://example.com/sitemap.xml');
  });

  it('should strip trailing slash from SITE_URL', async () => {
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com/';
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    expect(result.sitemap).toBe('https://example.com/sitemap.xml');
  });

  it('should fall back to www.gladlabs.io when SITE_URL is unset', async () => {
    delete process.env.NEXT_PUBLIC_SITE_URL;
    const { default: robots } = await import('../../app/robots');
    const result = robots();

    expect(result.sitemap).toBe('https://www.gladlabs.io/sitemap.xml');
  });
});
