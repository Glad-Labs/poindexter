/**
 * @jest-environment node
 *
 * The public site has no backend URL, and this test keeps it that way.
 *
 * The FastAPI worker is local-first with no public ingress, so neither
 * Vercel nor a visitor's browser can reach it. Pages read the R2 static
 * export (lib/posts.ts) and newsletter signups go to Resend. Even so,
 * NEXT_PUBLIC_API_BASE_URL (older name NEXT_PUBLIC_FASTAPI_URL) outlived
 * every route that used it: next.config.js still required it for production
 * builds and added its origin to the CSP, and the value on Vercel was a
 * retired node's tailnet name that no longer resolved.
 *
 * A page that needs worker data has to get it another way: a static export,
 * or the worker pushing or pulling outbound. Before deleting this guard to
 * call the worker from the site, prove the site can reach it.
 */

const path = require('node:path');
const { siteCodeLines, siteSourceFiles } = require('./helpers/site-source');

const RETIRED = /NEXT_PUBLIC_(API_BASE|FASTAPI)_URL/;

describe('the public site reads no backend URL', () => {
  const files = siteSourceFiles();

  test('the scan covers the site source', () => {
    // A guard that scanned nothing has not passed.
    expect(files.length).toBeGreaterThan(20);
    expect(files).toEqual(
      expect.arrayContaining([
        'next.config.js',
        path.join('lib', 'posts.ts'),
        path.join('app', 'layout.js'),
      ])
    );
  });

  // A comment may name the variables to explain their retirement; code may not.
  test('no source line uses NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_FASTAPI_URL', () => {
    const offenders = siteCodeLines(files)
      .filter(({ text }) => RETIRED.test(text))
      .map(({ where, text }) => `${where}: ${text}`);
    expect(offenders).toEqual([]);
  });
});
