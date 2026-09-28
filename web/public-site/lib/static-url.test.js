/**
 * @jest-environment node
 *
 * lib/static-url: the one place the site names its static export bucket.
 *
 * Every page, route and the edge proxy reads the R2 export through
 * STATIC_URL, and next.config.js and the podcast and video feeds take the
 * bucket's origin from STATIC_ORIGIN. These tests pin how both resolve, and
 * that plain Node can load the file, which is how next.config.js gets it.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { DEFAULT_STATIC_URL } from './static-url';

const SITE_ROOT = path.join(__dirname, '..');

/**
 * The module as it evaluates under one NEXT_PUBLIC_STATIC_URL (undefined =
 * unset). It reads the variable once, at import, so each case needs a fresh
 * evaluation.
 */
function load(staticUrl) {
  const savedEnv = process.env;
  try {
    process.env = { ...savedEnv };
    if (staticUrl === undefined) {
      delete process.env.NEXT_PUBLIC_STATIC_URL;
    } else {
      process.env.NEXT_PUBLIC_STATIC_URL = staticUrl;
    }
    let fresh;
    jest.isolateModules(() => {
      fresh = require('./static-url');
    });
    return fresh;
  } finally {
    process.env = savedEnv;
  }
}

const DEFAULT_ORIGIN = new URL(DEFAULT_STATIC_URL).origin;

describe('the default bucket', () => {
  it('is an https URL for the static/ prefix, and no more than that', () => {
    const url = new URL(DEFAULT_STATIC_URL);
    expect(url.protocol).toBe('https:');
    expect(url.pathname).toBe('/static');
    expect(url.search + url.hash).toBe('');
  });

  it('is what STATIC_URL and STATIC_ORIGIN resolve to when nothing is set', () => {
    const { STATIC_URL, STATIC_ORIGIN } = load(undefined);
    expect(STATIC_URL).toBe(DEFAULT_STATIC_URL);
    expect(STATIC_ORIGIN).toBe(DEFAULT_ORIGIN);
  });

  it('is used when NEXT_PUBLIC_STATIC_URL is set to nothing', () => {
    // A blank variable in the deploy environment must not become a base URL
    // of '' that turns every fetch into a relative one.
    const { STATIC_URL, STATIC_ORIGIN } = load('');
    expect(STATIC_URL).toBe(DEFAULT_STATIC_URL);
    expect(STATIC_ORIGIN).toBe(DEFAULT_ORIGIN);
  });
});

describe('a bucket set in NEXT_PUBLIC_STATIC_URL', () => {
  it('replaces the default, and its origin follows', () => {
    const { STATIC_URL, STATIC_ORIGIN } = load(
      'https://static.example.com/static'
    );
    expect(STATIC_URL).toBe('https://static.example.com/static');
    expect(STATIC_ORIGIN).toBe('https://static.example.com');
  });

  it('keeps its port and drops its path and query from the origin', () => {
    const { STATIC_URL, STATIC_ORIGIN } = load(
      'https://cdn.example.com:8443/a/b/static?x=1'
    );
    expect(STATIC_URL).toBe('https://cdn.example.com:8443/a/b/static?x=1');
    expect(STATIC_ORIGIN).toBe('https://cdn.example.com:8443');
  });

  it('may be http, for a local stand-in bucket', () => {
    const { STATIC_ORIGIN } = load('http://127.0.0.1:9/static');
    expect(STATIC_ORIGIN).toBe('http://127.0.0.1:9');
  });
});

describe('a value that is not an absolute http(s) URL', () => {
  // STATIC_URL stays what was set, so a bad value fails where it is fetched
  // rather than being papered over. STATIC_ORIGIN feeds the CSP and the image
  // patterns, which need a real origin, so it falls back to the default
  // bucket's. `new URL('file:///x').origin` is the string 'null'.
  it.each([
    ['text', 'not a url'],
    ['a path', '/static'],
    ['a file: URL', 'file:///tmp/static'],
    ['a data: URL', 'data:text/plain,static'],
  ])('%s keeps STATIC_URL as set and never empties STATIC_ORIGIN', (_, bad) => {
    const { STATIC_URL, STATIC_ORIGIN } = load(bad);
    expect(STATIC_URL).toBe(bad);
    expect(STATIC_ORIGIN).toBe(DEFAULT_ORIGIN);
  });
});

describe('loaded by plain Node', () => {
  // next.config.js imports this file while Node's own ESM loader starts the
  // build, with no transform between. Jest transforms everything, so it would
  // not notice an extensionless import or a syntax only a bundler reads.
  it('is native ESM with no imports of its own, and reads the env', () => {
    const out = execFileSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        "import * as m from './lib/static-url.js'; console.log(JSON.stringify(m));",
      ],
      {
        cwd: SITE_ROOT,
        env: {
          ...process.env,
          NEXT_PUBLIC_STATIC_URL: 'https://static.example.com/static',
        },
        encoding: 'utf8',
      }
    );
    expect(JSON.parse(out)).toEqual({
      DEFAULT_STATIC_URL,
      STATIC_URL: 'https://static.example.com/static',
      STATIC_ORIGIN: 'https://static.example.com',
    });
  });
});
