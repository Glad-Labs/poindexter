'use strict';

// Verifies http() enforces a request timeout via AbortController. Same vm
// harness as api.token.test.js, plus AbortController in the sandbox and a
// setTimeout override that collapses the 8s timeout to ~0.
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

test('http() aborts and rejects when a request exceeds the timeout', async () => {
  // fetch resolves the token immediately, but a data GET never settles on its
  // own — it only rejects when its AbortSignal fires.
  const fetchStub = (url, opts) => {
    if (String(url).endsWith('/token')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ access_token: 'jwt', expires_in: 3600 }),
      });
    }
    return new Promise((_resolve, reject) => {
      opts.signal.addEventListener('abort', () =>
        reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
      );
    });
  };

  const sandbox = {
    console,
    // Collapse any setTimeout(…, 8000) to fire on the next tick.
    setTimeout: (fn) => setTimeout(fn, 0),
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    fetch: fetchStub,
    PX_API_LIVE: true,
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage();
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);

  const api = sandbox.PX.api;
  api.setClient('cid', 'secret');
  api.setLive(true);

  await assert.rejects(api.posts(), /timed out/);
});
