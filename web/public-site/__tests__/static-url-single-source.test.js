/**
 * @jest-environment node
 *
 * The site names its static export bucket in one file, and this test keeps it
 * that way.
 *
 * Every reader of the R2 export used to carry its own copy of
 * `process.env.NEXT_PUBLIC_STATIC_URL || '<bucket url>'`: the pages, the route
 * handlers, the sitemap, the feeds, the edge proxy, next.config.js (twice:
 * the CSP allow-list and the image patterns) and an e2e spec. A bucket move
 * meant editing all of them, and a missed one pointed one surface at the old
 * bucket with no error. An earlier drift between the fetch URL and the CSP
 * allow-list broke /search silently (Gitea #262).
 *
 * lib/static-url.js is the one place now. To move the bucket, edit
 * DEFAULT_STATIC_URL there, or set NEXT_PUBLIC_STATIC_URL in the deploy
 * environment. A file that reads the variable itself, declares its own
 * STATIC_URL, or spells out the bucket's host is the first step back to
 * fourteen edits, so this fails on it.
 */

const fs = require('node:fs');
const path = require('node:path');
const { DEFAULT_STATIC_URL } = require('../lib/static-url');

const SITE_ROOT = path.join(__dirname, '..');
const SHARED = path.join('lib', 'static-url.js');
const THIS_FILE = path.relative(SITE_ROOT, __filename);
const SOURCE_DIRS = [
  'app',
  'components',
  'lib',
  'e2e',
  '__tests__',
  'scripts',
  'public',
];
const SKIP_DIRS = new Set(['node_modules', '.next', 'coverage', 'logs']);
const SOURCE_EXT = /\.(js|jsx|ts|tsx|mjs|cjs)$/;

// The default bucket's own host, so the check follows a move to a new bucket,
// plus any R2 public-bucket host (pub-<hash>.r2.dev), so a copy of the OLD
// host that outlives a move is caught too.
const BUCKET_HOST = new URL(DEFAULT_STATIC_URL).hostname;
const R2_PUBLIC_HOST = /pub-[0-9a-f]{32}\.r2\.dev/;

const READS_ENV = /NEXT_PUBLIC_STATIC_URL/;
const DECLARES_OWN = /\b(?:const|let|var)\s+STATIC_(?:URL|ORIGIN)\b/;

function isJestFile(rel) {
  return (
    rel.split(path.sep).includes('__tests__') || /\.test\.[jt]sx?$/.test(rel)
  );
}

function walk(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, out);
    } else if (SOURCE_EXT.test(entry.name)) {
      out.push(path.relative(SITE_ROOT, full));
    }
  }
  return out;
}

// Every code file the site is built or tested from, but the shared module (the
// one place allowed to name the bucket) and this file (which names the
// patterns).
function sourceFiles() {
  const files = [];
  for (const dir of SOURCE_DIRS) {
    walk(path.join(SITE_ROOT, dir), files);
  }
  for (const name of fs.readdirSync(SITE_ROOT)) {
    if (SOURCE_EXT.test(name)) files.push(name);
  }
  return files.filter((rel) => rel !== SHARED && rel !== THIS_FILE).sort();
}

// A comment may talk about the variable and the bucket; code may not.
function isCommentLine(line) {
  return /^(\/\/|\/\*|\*)/.test(line.trim());
}

function linesOf(rel) {
  return fs
    .readFileSync(path.join(SITE_ROOT, rel), 'utf8')
    .split('\n')
    .map((text, i) => ({ text, n: i + 1 }));
}

// `file:line: text` for every line of `files` that `match` accepts.
function offendersIn(files, match) {
  const found = [];
  for (const rel of files) {
    for (const { text, n } of linesOf(rel)) {
      if (match(text, rel)) found.push(`${rel}:${n}: ${text.trim()}`);
    }
  }
  return found;
}

describe('the static export bucket is named in one file', () => {
  const files = sourceFiles();

  test('the scan covers the site source', () => {
    // A guard that scanned nothing has not passed.
    expect(files.length).toBeGreaterThan(80);
    expect(files).toEqual(
      expect.arrayContaining([
        'next.config.js',
        'proxy.ts',
        path.join('app', 'page.js'),
        path.join('app', 'feed.xml', 'route.ts'),
        path.join('components', 'Footer.js'),
        path.join('lib', 'posts.ts'),
        path.join('e2e', 'tag.spec.ts'),
        path.join('__tests__', 'proxy.test.js'),
      ])
    );
  });

  test('no code reads NEXT_PUBLIC_STATIC_URL, and none declares its own STATIC_URL', () => {
    // Jest files are exempt: they set the variable to exercise the module.
    const code = files.filter((rel) => !isJestFile(rel));
    expect(
      offendersIn(
        code,
        (text) =>
          !isCommentLine(text) &&
          (READS_ENV.test(text) || DECLARES_OWN.test(text))
      )
    ).toEqual([]);
  });

  test('no file spells out the bucket host', () => {
    expect(
      offendersIn(
        files,
        (text) => text.includes(BUCKET_HOST) || R2_PUBLIC_HOST.test(text)
      )
    ).toEqual([]);
  });
});

describe('the shared module', () => {
  const code = linesOf(SHARED)
    .filter(({ text }) => !isCommentLine(text))
    .map(({ text }) => text)
    .join('\n');

  test('names the variable in full, which is how Next inlines it', () => {
    expect(code).toMatch(/process\.env\.NEXT_PUBLIC_STATIC_URL\b/);
  });

  test('imports nothing, so next.config.js and the edge can load it as it is', () => {
    expect(code).not.toMatch(/^\s*import\b/m);
    expect(code).not.toMatch(/\bimport\s*\(/);
    expect(code).not.toMatch(/\brequire\s*\(/);
  });

  test('uses no Node API: its only use of process is the variable', () => {
    expect(code).not.toMatch(/\bprocess\.(?!env\.NEXT_PUBLIC_STATIC_URL\b)/);
    expect(code).not.toMatch(/\b(?:Buffer|__dirname|__filename)\b/);
  });
});
