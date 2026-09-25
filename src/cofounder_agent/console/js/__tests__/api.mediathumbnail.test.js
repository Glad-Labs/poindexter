'use strict';

// Contract tests for PX.api.mediaThumbnailBlob (console/js/api.js): the fetch
// behind the media drawer's "YouTube thumbnail — uploads with this video"
// preview. Same harness as api.token.test.js: the REAL api.js IIFE evaluated in
// a Node `vm` context with a stubbed fetch.
//
// The contract that matters is the 404: a video rendered before custom
// thumbnails existed (or with them switched off) has none, and the drawer must
// then render the player exactly as before. So 404 → null, never an error.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'api.js'), 'utf8');

function res(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'ERR',
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    blob: async () => ({ kind: 'blob', body }),
  };
}

function makeAdapter(apiHandler, { live = true } = {}) {
  const calls = { token: 0, api: [] };
  const fetchStub = (url, opts) => {
    const u = String(url);
    if (u.endsWith('/token')) {
      calls.token += 1;
      return Promise.resolve(
        res({ access_token: `jwt-${calls.token}`, expires_in: 3600 })
      );
    }
    calls.api.push({
      url: u,
      auth: opts && opts.headers && opts.headers.Authorization,
    });
    return Promise.resolve(apiHandler(calls.api.length));
  };
  const store = new Map();
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController,
    performance,
    fetch: fetchStub,
    PX_API_LIVE: true,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
      clear: () => store.clear(),
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  const api = sandbox.PX.api;
  api.setClient('console-cid', 'console-secret');
  api.setLive(live);
  return { api, calls };
}

test('fetches the composed thumbnail with the bearer token', async () => {
  const { api, calls } = makeAdapter(() => res('jpeg-bytes'));
  const blob = await api.mediaThumbnailBlob('post 1');
  assert.deepEqual(blob, { kind: 'blob', body: 'jpeg-bytes' });
  assert.equal(calls.api.length, 1);
  assert.match(
    calls.api[0].url,
    /\/api\/media-approval\/post%201\/video\/thumbnail$/
  );
  assert.equal(calls.api[0].auth, 'Bearer jwt-1');
});

test('no thumbnail (404) is null, so the drawer renders the player as before', async () => {
  const { api } = makeAdapter(() => res({ detail: 'no thumbnail' }, 404));
  assert.equal(await api.mediaThumbnailBlob('p1'), null);
});

test('a 401 re-mints the token and retries once', async () => {
  const { api, calls } = makeAdapter((n) =>
    n === 1 ? res({}, 401) : res('jpeg')
  );
  const blob = await api.mediaThumbnailBlob('p1');
  assert.deepEqual(blob, { kind: 'blob', body: 'jpeg' });
  assert.equal(calls.api.length, 2);
  assert.equal(calls.token, 2);
});

test('a server error surfaces with its status', async () => {
  const { api } = makeAdapter(() => res('boom', 500));
  await assert.rejects(api.mediaThumbnailBlob('p1'), /thumbnail → 500/);
});

test('offline (not live) is null without a request', async () => {
  const { api, calls } = makeAdapter(() => res('jpeg'), { live: false });
  assert.equal(await api.mediaThumbnailBlob('p1'), null);
  assert.equal(calls.api.length, 0);
});
