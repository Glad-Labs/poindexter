/**
 * @jest-environment node
 *
 * RFC 9116 security.txt route tests — /.well-known/security.txt plus the
 * legacy top-level /security.txt redirect.
 */

const originalEnv = process.env;

beforeEach(() => {
  jest.resetModules();
  process.env = { ...originalEnv };
});

afterAll(() => {
  process.env = originalEnv;
});

describe('GET /.well-known/security.txt', () => {
  it('serves an RFC 9116 document with the required fields', async () => {
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com';
    const { GET } = await import('../../app/.well-known/security.txt/route');
    const res = await GET();

    expect(res.headers.get('content-type')).toMatch(/text\/plain/);
    const body = await res.text();
    expect(body).toContain('Contact: mailto:security@gladlabs.io');
    expect(body).toContain(
      'Canonical: https://example.com/.well-known/security.txt'
    );
    expect(body).toContain(
      'Policy: https://github.com/Glad-Labs/poindexter/blob/main/SECURITY.md'
    );
  });

  it('Expires is a valid future RFC 3339 date under a year out', async () => {
    const { GET } = await import('../../app/.well-known/security.txt/route');
    const res = await GET();
    const body = await res.text();

    const match = body.match(/^Expires: (.+)$/m);
    expect(match).not.toBeNull();
    const expires = new Date(match[1]);
    expect(Number.isNaN(expires.getTime())).toBe(false);
    expect(expires.getTime()).toBeGreaterThan(Date.now());
    expect(expires.getTime()).toBeLessThan(
      Date.now() + 366 * 24 * 60 * 60 * 1000
    );
  });

  it('is edge-cacheable for at most a day (rolling Expires stays fresh)', async () => {
    const { GET } = await import('../../app/.well-known/security.txt/route');
    const res = await GET();
    expect(res.headers.get('cache-control')).toMatch(/max-age=86400/);
  });

  it('falls back to www.gladlabs.io when SITE_URL is unset', async () => {
    delete process.env.NEXT_PUBLIC_SITE_URL;
    const { GET } = await import('../../app/.well-known/security.txt/route');
    const res = await GET();
    const body = await res.text();
    expect(body).toContain(
      'Canonical: https://www.gladlabs.io/.well-known/security.txt'
    );
  });
});

describe('GET /security.txt (legacy root location)', () => {
  it('308-redirects to the canonical /.well-known/ location', async () => {
    const { NextRequest } = await import('next/server');
    const { GET } = await import('../../app/security.txt/route');
    const res = GET(new NextRequest('https://example.com/security.txt'));

    expect(res.status).toBe(308);
    expect(res.headers.get('location')).toBe(
      'https://example.com/.well-known/security.txt'
    );
  });
});
