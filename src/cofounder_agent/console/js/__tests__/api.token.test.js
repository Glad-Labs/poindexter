'use strict';

// Contract tests for the operator-console API adapter's OAuth token minting
// (src/cofounder_agent/console/js/api.js). They run on Node's built-in test
// runner against the REAL api.js IIFE evaluated inside a Node `vm` context — no
// build step and no new dependencies. The console ships as no-build,
// in-browser-Babel static assets, so the IIFE expects a browser-shaped global;
// the context below provides the minimal slice api.js touches (`window` ===
// global, an in-memory `localStorage`, plus `fetch` / `URLSearchParams` /
// `performance` / `console` / `setTimeout`). We avoid jsdom on purpose: it is
// far more machinery than this needs and the repo's `undici` CVE-override
// breaks jsdom's resource dispatcher.
//
// Regression target (2026-06-21): flipping the console to LIVE mounts ~11 panel
// effects that each call http() -> getToken() against an initially-empty token
// cache. With no in-flight dedup, every concurrent first-caller mints its own
// `POST /token`, and the worker's rate limiter returns 429 for the losers.
// These tests pin the contract: concurrent callers coalesce onto ONE mint while
// caching, cache-clear re-mint, and the existing 401-retry-once path keep
// working. The adapter is exercised only through its public PX.api surface, so
// the contract holds regardless of how the dedup is implemented internally.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const API_JS = path.join(__dirname, '..', 'api.js');
const SOURCE = fs.readFileSync(API_JS, 'utf8');

// A minimal fetch Response stand-in — only the bits api.js actually touches.
function res(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'ERR',
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

// In-memory localStorage shim (getItem returns null when absent, like the DOM).
function makeLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => {
      m.set(k, String(v));
    },
    removeItem: (k) => {
      m.delete(k);
    },
    clear: () => {
      m.clear();
    },
  };
}

// Fresh adapter realm per test: a vm context with a counting fetch stub.
// tokenHandler(n) / apiHandler(n, url, opts) let a test shape responses; both
// default to a benign success. setTimeoutImpl swaps the timer the adapter uses
// (so a test can collapse the 429 backoff delay to ~0). Returns { api, calls }
// where calls.token counts POST /token mints.
function makeAdapter({ tokenHandler, apiHandler, setTimeoutImpl } = {}) {
  const calls = { token: 0, api: 0 };

  const fetchStub = (url, opts) => {
    const u = String(url);
    if (u.endsWith('/token')) {
      calls.token += 1;
      return Promise.resolve(
        tokenHandler
          ? tokenHandler(calls.token)
          : res({ access_token: `jwt-${calls.token}`, expires_in: 3600 })
      );
    }
    calls.api += 1;
    return Promise.resolve(
      apiHandler ? apiHandler(calls.api, u, opts) : res({})
    );
  };

  // Browser-shaped global: window === global (as in a real browser), an
  // in-memory localStorage, and the host globals api.js reads. PX_API_LIVE
  // starts true so the boot hint stays quiet and the live branches are taken.
  const sandbox = {
    console,
    setTimeout: setTimeoutImpl || setTimeout,
    clearTimeout,
    URLSearchParams,
    performance,
    fetch: fetchStub,
    PX_API_LIVE: true,
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage();
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);

  const api = sandbox.PX.api;
  api.setClient('console-cid', 'console-secret');
  api.setLive(true);
  return { api, calls };
}

// A representative slice of the panel reads that fire on a live mount, each of
// which routes through http() -> getToken().
const mountReads = (api) => [
  api.posts(),
  api.findings(),
  api.listTasks('?limit=50'),
  api.listApprovals(),
  api.listTopicProposals(),
  api.budget(),
  api.memoryStats(),
  api.mediaQueue(),
  api.schedule(),
  api.seo(),
  api.pipelineEvents(),
];

test('coalesces a burst of concurrent token mints into a single POST /token', async () => {
  const { api, calls } = makeAdapter();
  await Promise.all(mountReads(api));
  assert.equal(
    calls.token,
    1,
    `live mount should mint one token, not one per panel (got ${calls.token})`
  );
});

test('reuses the cached token across sequential calls', async () => {
  const { api, calls } = makeAdapter();
  await api.posts();
  await api.findings();
  await api.budget();
  assert.equal(calls.token, 1);
});

test('re-mints after the token cache is cleared (credential rotation)', async () => {
  const { api, calls } = makeAdapter();
  await api.posts();
  assert.equal(calls.token, 1);
  api.setClient('console-cid', 'rotated-secret'); // clears the in-memory token
  await api.posts();
  assert.equal(calls.token, 2);
});

test('a 401 on an API call clears the token and retries once with a fresh mint', async () => {
  const { api, calls } = makeAdapter({
    apiHandler: (n) =>
      n === 1 ? res({ detail: 'expired' }, 401) : res({ ok: true }),
  });
  const out = await api.posts();
  assert.deepEqual(out, { ok: true }); // the retry succeeded
  assert.equal(calls.api, 2); // initial 401 + one retry
  assert.equal(calls.token, 2); // original mint + re-mint after the 401 clear
});

test('retries the token mint once after a 429 before surfacing the error', async () => {
  const { api, calls } = makeAdapter({
    setTimeoutImpl: (fn) => setTimeout(fn, 0), // collapse the backoff delay
    tokenHandler: (n) =>
      n === 1
        ? res({ detail: 'rate limited' }, 429)
        : res({ access_token: 'jwt', expires_in: 3600 }),
  });
  const out = await api.posts();
  assert.deepEqual(out, {}); // the API read succeeded on the retried token
  assert.equal(calls.token, 2); // the 429 mint + one backoff retry
});

test('a sustained 429 on the token mint still surfaces after the single retry', async () => {
  const { api, calls } = makeAdapter({
    setTimeoutImpl: (fn) => setTimeout(fn, 0),
    tokenHandler: () => res({ detail: 'rate limited' }, 429),
  });
  await assert.rejects(api.posts(), /\/token → 429/);
  assert.equal(calls.token, 2); // one retry, then give up (no infinite loop)
});
