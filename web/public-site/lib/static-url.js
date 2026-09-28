/**
 * Where the site reads its static export from: the one place it is named.
 *
 * The content pipeline publishes everything the public site renders (the post
 * index, per-post JSON, categories, the sitemap feed, the podcast and video
 * feeds) to an R2 bucket, and the site reads it from there. Every reader used
 * to carry its own copy of `process.env.NEXT_PUBLIC_STATIC_URL || '<bucket>'`,
 * and next.config.js kept a copy for the CSP allow-list. A bucket move meant
 * editing all of them, and a missed one silently pointed one surface at the
 * old bucket. When the CSP allow-list and the fetch URL drifted apart, /search
 * broke without an error (Gitea #262).
 *
 * Import from here instead. Moving the bucket is one edit, either
 * DEFAULT_STATIC_URL below or NEXT_PUBLIC_STATIC_URL in the deploy
 * environment, and nothing else. __tests__/static-url-single-source.test.js
 * fails if a second copy appears.
 *
 * What each consumer needs from this file:
 * - Plain ESM JavaScript with no imports. next.config.js loads it with Node's
 *   own ESM loader as the build starts, and that loader cannot read a .ts file
 *   on every Node this package supports. TypeScript callers get the types
 *   through `allowJs`, as they do for lib/site.config.js.
 * - No Node APIs. proxy.ts runs at the Vercel edge.
 * - The expression `process.env.NEXT_PUBLIC_STATIC_URL`, written out in full.
 *   Next inlines a NEXT_PUBLIC_* variable at build time only where it appears
 *   like that, never through a computed key.
 */

/** The R2 static export's public URL when NEXT_PUBLIC_STATIC_URL is unset. */
export const DEFAULT_STATIC_URL =
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

/**
 * Base URL of the static export. Append the file:
 * `${STATIC_URL}/posts/index.json`, `${STATIC_URL}/categories.json`, ...
 */
export const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL || DEFAULT_STATIC_URL;

// A URL's origin, or '' when it is not an absolute http(s) URL. Schemes with no
// origin (file:, data:) report the string 'null' from `new URL().origin`,
// which would otherwise reach the CSP.
function originOf(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:'
      ? parsed.origin
      : '';
  } catch {
    return '';
  }
}

/**
 * The bucket's origin: scheme and host, no path. It is what the CSP
 * `connect-src` allows and what `next/image` may optimise from, and it locates
 * what the bucket holds beside `static/`, such as `/podcast/feed.xml` and
 * `/video/feed.xml`. When NEXT_PUBLIC_STATIC_URL is not an absolute http(s)
 * URL this is the default bucket's origin, so it is never empty.
 */
export const STATIC_ORIGIN =
  originOf(STATIC_URL) || originOf(DEFAULT_STATIC_URL);
