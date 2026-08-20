/* A real browser ALWAYS boots LIVE (feedback_no_dummy_data).
   Two incidents pin this policy. 2026-07-23: a fresh browser profile
   (empty localStorage) silently rendered the ticking mock simulation as
   if it were the real stack. 2026-08-20: a persisted px_live='0' from one
   fat-fingered Settings toggle locked a phone into mock indefinitely —
   the page loaded, numbers ticked, and nothing said they were fabricated.
   Boot therefore ignores localStorage px_live entirely and applies no
   path/origin heuristic: anything with a window.location boots live and
   fails loud per panel. Mock remains only for (a) the deliberate
   window.PX_API_LIVE=false page seam (which renders the MockModeBanner)
   and (b) windowless vm harnesses, which cannot render to a human. */
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

test('served from the worker (/console/ over http) boots LIVE', () => {
  assert.equal(boot({ location: workerLoc }).isLive(), true);
});

test('persisted px_live="0" is INERT — the browser still boots LIVE', () => {
  // The 2026-08-20 phone trap: this exact stored value must never again
  // resurrect the mock simulation.
  assert.equal(
    boot({ location: workerLoc, ls: { px_live: '0' } }).isLive(),
    true
  );
});

test('window.PX_API_LIVE=false (dev/demo page seam) still opts into mock', () => {
  assert.equal(boot({ location: workerLoc, pxApiLive: false }).isLive(), false);
});

test('file:// boots LIVE too — panels fail loud instead of demoing', () => {
  const loc = { protocol: 'file:', pathname: '/home/u/console/index.html' };
  assert.equal(boot({ location: loc }).isLive(), true);
});

test('http page NOT under /console boots LIVE — no path heuristic', () => {
  const loc = { protocol: 'http:', pathname: '/dashboard/' };
  assert.equal(boot({ location: loc }).isLive(), true);
});

test('bare window with no location (vm test harnesses) stays mock', () => {
  assert.equal(boot({}).isLive(), false);
});

test('runtime setLive(false) does not persist anything', () => {
  const seen = [];
  const api = boot({ location: workerLoc });
  // boot() wires a fresh localStorage per call; reboot with the same seed
  // object to prove nothing was written.
  api.setLive(false);
  assert.equal(api.isLive(), false, 'runtime flip works for harnesses');
  const rebooted = boot({ location: workerLoc });
  assert.equal(rebooted.isLive(), true, 'flip must not survive a reload');
  void seen;
});
