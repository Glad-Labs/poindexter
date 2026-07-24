/* Live-by-default when served from the worker (feedback_no_dummy_data).
   The 2026-07-23 Pop!_OS migration surfaced the trap this pins: a fresh
   browser profile has an empty localStorage, and the console silently
   rendered the ticking mock simulation as if it were the real stack. A
   /console/-served page must now default to LIVE; mock stays the default
   only off-worker (file://, static hosts, bare test windows) or by
   explicit opt-out (px_live='0' / window.PX_API_LIVE=false). */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'api.js'), 'utf8');

function makeLocalStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

function boot({ location, ls = {}, pxApiLive } = {}) {
  const sandbox = {
    console: { info() {}, warn() {}, error() {} },
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  };
  if (pxApiLive !== undefined) sandbox.PX_API_LIVE = pxApiLive;
  if (location) sandbox.location = location;
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage(ls);
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return sandbox.PX.api;
}

const workerLoc = {
  protocol: 'http:',
  pathname: '/console/',
  hostname: 'localhost',
};

test('served from the worker (/console/ over http) defaults to LIVE', () => {
  assert.equal(boot({ location: workerLoc }).isLive(), true);
});

test('explicit px_live="0" opts a worker-served page back into mock', () => {
  assert.equal(
    boot({ location: workerLoc, ls: { px_live: '0' } }).isLive(),
    false
  );
});

test('window.PX_API_LIVE=false overrides the worker-served default', () => {
  assert.equal(boot({ location: workerLoc, pxApiLive: false }).isLive(), false);
});

test('file:// (OSS demo case) still defaults to mock', () => {
  const loc = { protocol: 'file:', pathname: '/home/u/console/index.html' };
  assert.equal(boot({ location: loc }).isLive(), false);
});

test('http page NOT under /console (static host) still defaults to mock', () => {
  const loc = { protocol: 'http:', pathname: '/dashboard/' };
  assert.equal(boot({ location: loc }).isLive(), false);
});

test('bare window with no location (test harnesses) stays mock', () => {
  assert.equal(boot({}).isLive(), false);
});

test('px_live="1" still forces live anywhere', () => {
  assert.equal(boot({ ls: { px_live: '1' } }).isLive(), true);
});
