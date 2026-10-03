/**
 * @jest-environment node
 *
 * vercel.json's ignoreCommand decides whether a production build runs.
 *
 * Vercel reads exit 0 as "skip" and exit 1 as "build"; any other code marks the
 * deploy ERROR. The command compares HEAD with VERCEL_GIT_PREVIOUS_SHA, the
 * commit of the last successful deployment, not with HEAD^. With HEAD^, a site
 * change whose own build never ran (cancelled behind a newer push, say) was
 * lost for good as soon as a docs-only commit landed on top of it.
 *
 * Each case runs the real command from vercel.json with `sh -c` inside a
 * scratch git repository laid out like the monorepo.
 */
const { execFileSync, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CONFIG = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'vercel.json'), 'utf8')
);
const COMMAND = CONFIG.ignoreCommand;

let repo;
const sha = {};

function git(...args) {
  return execFileSync('git', args, { cwd: repo, encoding: 'utf8' }).trim();
}

function commit(name, file) {
  const full = path.join(repo, file);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.appendFileSync(full, `${name}\n`);
  git('add', '-A');
  git('commit', '-q', '-m', name);
  sha[name] = git('rev-parse', 'HEAD');
}

/** Run the ignoreCommand at `head` with the given previous-deploy SHA. */
function decide(head, previous) {
  git('checkout', '-q', '--detach', sha[head]);
  const env = { ...process.env };
  delete env.VERCEL_GIT_PREVIOUS_SHA;
  if (previous !== undefined) env.VERCEL_GIT_PREVIOUS_SHA = previous;
  // Vercel runs the command from the project's root directory.
  const res = spawnSync('sh', ['-c', COMMAND], {
    cwd: path.join(repo, 'web/public-site'),
    env,
  });
  return res.status === 0
    ? 'skip'
    : res.status === 1
      ? 'build'
      : `error ${res.status}`;
}

beforeAll(() => {
  repo = fs.mkdtempSync(path.join(os.tmpdir(), 'vercel-ignore-'));
  git('init', '-q');
  git('config', 'user.email', 'test@example.com');
  git('config', 'user.name', 'test');
  commit('base', 'web/public-site/app/page.js');
  commit('site-change', 'web/public-site/app/page.js');
  commit('docs-only', 'docs/notes.md');
  commit('brand-change', 'packages/brand/tokens.css');
  commit('docs-again', 'docs/notes.md');
});

afterAll(() => {
  fs.rmSync(repo, { recursive: true, force: true });
});

test('stays under the 256-character limit Vercel puts on ignoreCommand', () => {
  expect(COMMAND.length).toBeLessThanOrEqual(256);
});

test('skips a docs-only commit when the last deploy already has every site change', () => {
  expect(decide('docs-only', sha['site-change'])).toBe('skip');
});

test('builds a docs-only commit when a site change since the last deploy never shipped', () => {
  // The bug the HEAD^ rule had: HEAD^..HEAD touches only docs, so it skipped
  // and the site change before it never reached production.
  expect(decide('docs-only', sha.base)).toBe('build');
});

test('builds when a shared dependency of the site changed', () => {
  expect(decide('docs-again', sha['docs-only'])).toBe('build');
});

test('builds a redeploy of the commit that is already live', () => {
  expect(decide('site-change', sha['site-change'])).toBe('build');
});

test('builds when Vercel gives no previous deploy', () => {
  expect(decide('docs-only', undefined)).toBe('build');
  expect(decide('docs-only', '')).toBe('build');
});

test('builds, rather than erroring, when the previous deploy is not in the clone', () => {
  // A shallow clone may not reach it; git errors must never escape as a
  // non-0/1 exit, which Vercel turns into a failed deploy.
  expect(decide('docs-only', 'f'.repeat(40))).toBe('build');
  expect(decide('docs-only', 'not-a-sha')).toBe('build');
});
