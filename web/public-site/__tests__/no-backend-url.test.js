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

const fs = require('node:fs');
const path = require('node:path');

const SITE_ROOT = path.join(__dirname, '..');
const SOURCE_DIRS = ['app', 'components', 'lib'];
const SOURCE_EXT = /\.(js|jsx|ts|tsx|mjs|cjs)$/;
const RETIRED = /NEXT_PUBLIC_(API_BASE|FASTAPI)_URL/;

function isTestFile(rel) {
  return (
    rel.split(path.sep).includes('__tests__') || /\.test\.[jt]sx?$/.test(rel)
  );
}

function walk(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, out);
    } else if (SOURCE_EXT.test(entry.name)) {
      out.push(path.relative(SITE_ROOT, full));
    }
  }
  return out;
}

function sourceFiles() {
  const files = [];
  for (const dir of SOURCE_DIRS) {
    walk(path.join(SITE_ROOT, dir), files);
  }
  // Root-level modules that run in the build or at request time.
  for (const name of fs.readdirSync(SITE_ROOT)) {
    if (SOURCE_EXT.test(name) && !name.startsWith('jest.')) {
      files.push(name);
    }
  }
  return files.filter((rel) => !isTestFile(rel)).sort();
}

// A comment may name the variables to explain their retirement; code may not.
function isCommentLine(line) {
  return /^(\/\/|\/\*|\*)/.test(line.trim());
}

describe('the public site reads no backend URL', () => {
  const files = sourceFiles();

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

  test('no source line uses NEXT_PUBLIC_API_BASE_URL or NEXT_PUBLIC_FASTAPI_URL', () => {
    const offenders = [];
    for (const rel of files) {
      const lines = fs.readFileSync(path.join(SITE_ROOT, rel), 'utf8');
      lines.split('\n').forEach((line, i) => {
        if (RETIRED.test(line) && !isCommentLine(line)) {
          offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
