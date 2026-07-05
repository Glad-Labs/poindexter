'use strict';

// Prometheus helpers must degrade to honest-empty (null / []) when Prometheus
// is unreachable, never throw — otherwise a Prom blip takes down the panel, and
// any future caller that forgets a call-site .catch() breaks. Tested directly
// (not through a caller) because every current caller already .catch()es, which
// would mask the helper's own behavior.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'api.js'), 'utf8');

function makeLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  };
}

function loadApi() {
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    // Every fetch rejects — simulate Prometheus down.
    fetch: () => Promise.reject(new Error('ECONNREFUSED')),
    PX_API_LIVE: true,
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage();
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return sandbox.PX.api;
}

test('promScalar returns null (does not throw) when the fetch rejects', async () => {
  const api = loadApi();
  assert.equal(await api.promScalar('up'), null);
});

test('promVector returns [] (does not throw) when the fetch rejects', async () => {
  const api = loadApi();
  // Array.isArray + length, not deepEqual: the array is minted inside the vm
  // realm, so deepStrictEqual's cross-realm prototype check would reject [] vs [].
  const out = await api.promVector('up');
  assert.ok(Array.isArray(out));
  assert.equal(out.length, 0);
});
