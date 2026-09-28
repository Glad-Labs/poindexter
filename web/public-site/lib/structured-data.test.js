/**
 * lib/structured-data — the BlogPosting JSON-LD on every post page.
 *
 * Pins where the schema's image URLs point. schema.org wants absolute URLs.
 * The static export's cover images already are (R2, Pexels, Cloudinary), and
 * a relative path can only name a file this site serves. The FastAPI worker
 * is never a base: the public site cannot reach it, and the old resolver
 * (lib/url.js, getImageURL) prefixed its URL and threw in production when
 * NEXT_PUBLIC_API_BASE_URL was unset.
 *
 * The fallback image and the publisher logo used to be /og-image.png and
 * /logo.png, which this site does not serve (both 404 on www.gladlabs.io), so
 * the tests check the file on disk rather than trusting a hardcoded name.
 */
import fs from 'node:fs';
import path from 'node:path';
import { generateBlogPostingSchema } from './structured-data';

const SITE = 'https://site.example';

function schemaFor(coverImageUrl, generate = generateBlogPostingSchema) {
  return generate(
    {
      title: 'A post',
      excerpt: 'An excerpt',
      content: 'one two three',
      slug: 'a-post',
      date: '2026-09-28T12:00:00Z',
      coverImage: coverImageUrl ? { url: coverImageUrl } : undefined,
    },
    SITE
  );
}

/** The file under public/ that a site-relative URL is served from. */
function publicFileFor(url) {
  expect(url.startsWith(`${SITE}/`)).toBe(true);
  return path.join(__dirname, '..', 'public', url.slice(SITE.length));
}

describe('generateBlogPostingSchema image URLs', () => {
  it('passes an absolute cover image URL through untouched', () => {
    const r2 =
      'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/images/featured/a.png';
    expect(schemaFor(r2).image.url).toBe(r2);
    expect(schemaFor('http://cdn.example/b.jpg').image.url).toBe(
      'http://cdn.example/b.jpg'
    );
  });

  it('resolves a relative cover image against the site URL', () => {
    expect(schemaFor('/images/cover.png').image.url).toBe(
      `${SITE}/images/cover.png`
    );
    expect(schemaFor('images/cover.png').image.url).toBe(
      `${SITE}/images/cover.png`
    );
  });

  it('falls back to an image the site serves when the post has no cover', () => {
    const url = schemaFor(undefined).image.url;
    expect(url).toBe(`${SITE}/og-image.jpg`);
    expect(fs.existsSync(publicFileFor(url))).toBe(true);
  });

  it('points the publisher logo at an image the site serves', () => {
    const url = schemaFor(undefined).publisher.logo.url;
    expect(url).toBe(`${SITE}/og-image.jpg`);
    expect(fs.existsSync(publicFileFor(url))).toBe(true);
  });

  it('needs no backend URL in production, and ignores one that is set', () => {
    const savedEnv = process.env;
    try {
      // Re-import under a production env, so module-level env reads (the
      // old resolver computed IS_PROD at import) are evaluated there too.
      const loadFresh = () => {
        let fresh;
        jest.isolateModules(() => {
          fresh = require('./structured-data').generateBlogPostingSchema;
        });
        return fresh;
      };

      process.env = { ...savedEnv, NODE_ENV: 'production' };
      delete process.env.NEXT_PUBLIC_API_BASE_URL;
      delete process.env.NEXT_PUBLIC_FASTAPI_URL;
      expect(schemaFor('/images/cover.png', loadFresh()).image.url).toBe(
        `${SITE}/images/cover.png`
      );

      process.env.NEXT_PUBLIC_API_BASE_URL = 'https://backend.example';
      expect(schemaFor('/images/cover.png', loadFresh()).image.url).toBe(
        `${SITE}/images/cover.png`
      );
    } finally {
      process.env = savedEnv;
    }
  });

  it('returns null for no post', () => {
    expect(generateBlogPostingSchema(null, SITE)).toBeNull();
  });
});
