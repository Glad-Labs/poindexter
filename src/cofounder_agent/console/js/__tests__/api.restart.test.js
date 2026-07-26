'use strict';

// Contract tests for restartService() (poindexter#909) — the operator
// console's Service Health "Restart" button. Same vm harness as
// api.approvals.test.js / api.token.test.js: the real api.js IIFE evaluated
// against a browser-shaped global with a recording fetch stub.
//
// WHY this exists: restartService() previously POSTed to a placeholder
// /api/admin/restart that had no backend route at all (a 404 in live mode) —
// AND the app.jsx click handler never even called it, faking success purely
// client-side regardless of connection state. Nothing pinned the real wire
// shape, so a future refactor could silently reintroduce either bug. These
// tests pin: the queue POST has no body and hits the real route, and the
// live branch polls GET .../restart/{id} until a terminal status.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const API_JS = path.join(__dirname, '..', 'api.js');
const SOURCE = fs.readFileSync(API_JS, 'utf8');

function res(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'ERR',
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

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

// `handlers` maps a URL substring -> a function(opts) -> response body, so
// each test controls what the queue POST and the status-poll GET return
// without a shared fetch stub across files.
function makeAdapter(handlers) {
  const requests = [];
  const fetchStub = (url, opts) => {
    const u = String(url);
    if (u.endsWith('/token')) {
      return Promise.resolve(res({ access_token: 'jwt-1', expires_in: 3600 }));
    }
    requests.push({
      url: u,
      method: (opts && opts.method) || 'GET',
      body: opts && opts.body ? JSON.parse(opts.body) : null,
    });
    for (const [needle, fn] of handlers) {
      if (u.includes(needle)) return Promise.resolve(res(fn(opts)));
    }
    return Promise.resolve(res({ ok: true }));
  };

  const sandbox = {
    console,
    setTimeout,
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
  api.setClient('console-cid', 'console-secret');
  api.setLive(true);
  return { api, requests };
}

test('restartService() queues via POST /api/services/{container}/restart with no body', async () => {
  const { api, requests } = makeAdapter([
    [
      '/restart',
      (opts) =>
        opts.method === 'POST'
          ? {
              id: 'req-1',
              container: 'poindexter-pyroscope',
              status: 'pending',
            }
          : {
              id: 'req-1',
              container: 'poindexter-pyroscope',
              status: 'done',
              detail: 'restarted',
            },
    ],
  ]);

  const row = await api.restartService('poindexter-pyroscope');

  const post = requests.find((r) => r.method === 'POST');
  assert.ok(post.url.endsWith('/api/services/poindexter-pyroscope/restart'));
  // Container is the URL path, not a body field — bare POST.
  assert.equal(post.body, null);
  assert.equal(row.status, 'done');
});

test('restartService() URL-encodes the container name', async () => {
  const { api, requests } = makeAdapter([
    ['/restart', () => ({ id: 'req-2', status: 'done' })],
  ]);
  await api.restartService('poindexter-weird name');
  const post = requests.find((r) => r.method === 'POST');
  assert.ok(post.url.includes('poindexter-weird%20name'));
});

test('restartService() polls GET /api/services/restart/{id} and returns the terminal row', async () => {
  const { api, requests } = makeAdapter([
    [
      '/restart',
      (opts) =>
        opts.method === 'POST'
          ? { id: 'req-3', container: 'poindexter-worker', status: 'pending' }
          : {
              id: 'req-3',
              container: 'poindexter-worker',
              status: 'failed',
              detail: 'container not found (likely mid-recreate)',
            },
    ],
  ]);

  const row = await api.restartService('poindexter-worker');

  const poll = requests.find(
    (r) => r.method === 'GET' && r.url.includes('/restart/')
  );
  assert.ok(poll.url.endsWith('/api/services/restart/req-3'));
  assert.equal(row.status, 'failed');
  assert.match(row.detail, /not found/);
});

test('restartService() mock mode never touches the network', async () => {
  const { api, requests } = makeAdapter([]);
  api.setLive(false);
  api.setSim('normal');

  const row = await api.restartService('poindexter-pyroscope');

  assert.equal(requests.length, 0);
  assert.equal(row.status, 'done');
  assert.equal(row.container, 'poindexter-pyroscope');
});
