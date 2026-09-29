/**
 * @jest-environment node
 *
 * .env.example lists every environment variable the site reads, and only
 * those.
 *
 * Nothing held it to that before. By 2026-09-28 fourteen variables read in
 * site source had no entry, among them NEXT_PUBLIC_BEACON_URL, without which
 * every page view is lost with no error, while NEXT_PUBLIC_ENABLE_SEARCH had
 * an entry that nothing read. The Vercel env is set by hand in its dashboard,
 * so this file is the repo's only record of what a deploy, or a fork, has to
 * set.
 *
 * This fails when:
 *  - site source reads a variable that has no `NAME=` line in .env.example
 *    (commented out counts: `# NAME=`);
 *  - .env.example has a `NAME=` line that no site source reads;
 *  - site source reaches process.env in a way this scan can't name
 *    (`process.env[key]`, `const env = process.env`), because a read it can't
 *    see would pass unchecked.
 * Variables the toolchain or host sets (Next.js, Jest, the CI runner,
 * Vercel) are exempt, each with its reason, and an exemption that nothing
 * reads any more fails too.
 */

const fs = require('node:fs');
const path = require('node:path');
const {
  SITE_ROOT,
  siteCodeLines,
  siteSourceFiles,
} = require('./helpers/site-source');

// Set by the toolchain or host, never by a deploy, so .env.example leaves
// them out.
const SET_BY_PLATFORM = {
  NODE_ENV: 'Next.js sets development or production; Jest sets test',
  NEXT_RUNTIME:
    'Next.js sets nodejs or edge; instrumentation.ts branches on it',
  VERCEL_GIT_COMMIT_SHA:
    'Vercel sets it on every build; next.config.js names the Sentry release after it',
  CI: 'the CI runner sets it; only playwright.config.js reads it',
};

// `process.env.NAME` and `process.env?.NAME` are the shapes that name their
// variable; any other access is one this scan can't see into.
const NAMED_READ = /process\.env\??\.([A-Za-z_$][\w$]*)/g;
const ANY_ACCESS = /process\.env\b/g;
// An entry is a `NAME=` line, commented out or not.
const ENTRY = /^#?\s*([A-Z][A-Z0-9_]*)=/;

function envReads(files) {
  const reads = new Map(); // name -> ['path:line', ...]
  const unnamed = [];
  for (const { where, text } of siteCodeLines(files)) {
    const names = [...text.matchAll(NAMED_READ)].map((match) => match[1]);
    if ((text.match(ANY_ACCESS) || []).length > names.length) {
      unnamed.push(`${where}: ${text}`);
    }
    for (const name of names) {
      reads.set(name, [...(reads.get(name) || []), where]);
    }
  }
  return { reads, unnamed };
}

function documentedNames() {
  const text = fs.readFileSync(path.join(SITE_ROOT, '.env.example'), 'utf8');
  const names = new Set();
  for (const line of text.split('\n')) {
    const match = ENTRY.exec(line.trim());
    if (match) names.add(match[1]);
  }
  return names;
}

describe('.env.example documents the environment the site reads', () => {
  const files = siteSourceFiles();
  const { reads, unnamed } = envReads(files);
  const entries = documentedNames();

  test('the scan sees the site source and the example file', () => {
    // A check that scanned nothing has not passed.
    expect(files.length).toBeGreaterThan(20);
    expect([...reads.keys()]).toEqual(
      expect.arrayContaining([
        'NEXT_PUBLIC_SITE_URL',
        'NEXT_PUBLIC_STATIC_URL',
        'REVALIDATE_SECRET',
      ])
    );
    expect(entries.size).toBeGreaterThan(10);
  });

  test('every process.env access names its variable', () => {
    expect(unnamed).toEqual([]);
  });

  test('every variable the site reads has an entry', () => {
    const missing = [...reads]
      .filter(
        ([name]) => !entries.has(name) && !Object.hasOwn(SET_BY_PLATFORM, name)
      )
      .map(([name, where]) => `${name} (read at ${where.join(', ')})`);
    expect(missing).toEqual([]);
  });

  test('every entry names a variable the site reads', () => {
    const unread = [...entries].filter((name) => !reads.has(name));
    expect(unread).toEqual([]);
  });

  test('every platform-set exemption is still read', () => {
    const stale = Object.keys(SET_BY_PLATFORM).filter(
      (name) => !reads.has(name)
    );
    expect(stale).toEqual([]);
  });
});
