/**
 * The site's own source, as the static guards in __tests__/ scan it.
 *
 * That is every module under app/, components/ and lib/, plus the root-level
 * modules that run in the build or at request time (next.config.js, proxy.ts,
 * the Sentry configs). Tests and the jest config are left out. The guards on
 * what the site's code reads (no-backend-url, env-example) share this one
 * definition, so a new source directory reaches both at once.
 * static-url-single-source.test.js walks a wider set on purpose (tests, e2e
 * specs, scripts), because a stray copy of the bucket URL matters there too.
 *
 * jest.config.cjs keeps __tests__/helpers/ out of the test run.
 */

const fs = require('node:fs');
const path = require('node:path');

const SITE_ROOT = path.join(__dirname, '..', '..');
const SOURCE_DIRS = ['app', 'components', 'lib'];
const SOURCE_EXT = /\.(js|jsx|ts|tsx|mjs|cjs)$/;

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

/** Site source paths, relative to the site root, sorted. */
function siteSourceFiles() {
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

// A comment may name a variable to explain it; only code uses one.
function isCommentLine(line) {
  return /^(\/\/|\/\*|\*)/.test(line.trim());
}

/**
 * Every line of the given files that is code rather than comment, as
 * `{ where: 'path:line', text }` with `text` trimmed.
 */
function siteCodeLines(files) {
  const lines = [];
  for (const rel of files) {
    const source = fs.readFileSync(path.join(SITE_ROOT, rel), 'utf8');
    source.split('\n').forEach((line, i) => {
      if (!isCommentLine(line)) {
        lines.push({ where: `${rel}:${i + 1}`, text: line.trim() });
      }
    });
  }
  return lines;
}

module.exports = { SITE_ROOT, siteSourceFiles, siteCodeLines };
