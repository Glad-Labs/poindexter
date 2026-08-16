'use strict';

// Contract tests for the draft-image mutations in the operator-console API
// adapter (src/cofounder_agent/console/js/api.js): the REQUEST BODIES that
// rebuildImages() / regenImage() put on the wire, plus the regen call's
// abort ceiling. Same vm harness as api.approvals.test.js — the real api.js
// IIFE evaluated against a browser-shaped global with a recording fetch
// stub — because the adapter ships as a no-build in-browser asset and
// cannot be require()d.
//
// Two contracts pinned:
//  1. Wire shapes — RebuildImagesRequest {allow_stock} and RegenImageRequest
//     {which, prompt} (routes/task_publishing_routes.py). A drifted body
//     400s only when an operator hits the button (the reject() lesson).
//  2. regenImage rides a LONG abort timer (300s, mirroring the CLI's
//     post_edit_regen_image_timeout_s), not the default 8s. The render
//     happens in-request on the GPU; an 8s client abort would report
//     "timed out" while the server kept rendering and swapped the image
//     anyway — the console lying about a mutation that actually landed.

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

// Fresh adapter realm whose fetch stub RECORDS every non-token request and
// whose setTimeout wrapper RECORDS timer delays — the abort timer's delay
// is the observable form of each call's timeout ceiling.
function makeRecordingAdapter(responseBody = { ok: true }) {
  const requests = [];
  const timerDelays = [];
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
    return Promise.resolve(res(responseBody));
  };

  const recordingSetTimeout = (fn, ms, ...rest) => {
    timerDelays.push(ms);
    return setTimeout(fn, ms, ...rest);
  };

  const sandbox = {
    console,
    setTimeout: recordingSetTimeout,
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
  return { api, requests, timerDelays };
}

// ── rebuildImages: queue the async all-image rebuild ────────────

test('rebuildImages() POSTs RebuildImagesRequest with allow_stock defaulting FALSE', async () => {
  const { api, requests } = makeRecordingAdapter({
    ok: true,
    task_id: 'rebuild-1',
  });
  await api.rebuildImages('task-123');

  assert.equal(requests.length, 1);
  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/tasks/task-123/rebuild-images'));
  // Fail-safe direction: stock fallback is opt-IN. A default that flipped
  // to true would let a failed generation silently ship a Pexels hero
  // instead of failing the rebuild loud (the gate atom's whole job).
  assert.equal(r.body.allow_stock, false);
});

test('rebuildImages(id, {allowStock:true}) sends allow_stock:true', async () => {
  const { api, requests } = makeRecordingAdapter({
    ok: true,
    task_id: 'rebuild-2',
  });
  await api.rebuildImages('task-456', { allowStock: true });

  assert.equal(requests[0].body.allow_stock, true);
});

test('rebuildImages() surfaces the queued job id from the response', async () => {
  const { api } = makeRecordingAdapter({
    ok: true,
    task_id: 'rebuild-uuid-1',
    target_task_id: 'task-123',
  });
  const out = await api.rebuildImages('task-123');
  assert.equal(out.task_id, 'rebuild-uuid-1');
});

// ── regenImage: one image, slow, long abort ceiling ─────────────

test('regenImage() POSTs RegenImageRequest {which, prompt}', async () => {
  const { api, requests } = makeRecordingAdapter({
    ok: true,
    new_url: 'https://cdn/x.png',
  });
  await api.regenImage('task-789', 'inline:2', 'a liquid cooling loop');

  assert.equal(requests.length, 1);
  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/tasks/task-789/regen-image'));
  // Both fields REQUIRED by RegenImageRequest — omitting either 400s.
  assert.equal(r.body.which, 'inline:2');
  assert.equal(r.body.prompt, 'a liquid cooling loop');
});

test('regenImage() abort timer is 300s; rebuildImages() stays on the 8s default', async () => {
  const { api, timerDelays } = makeRecordingAdapter({ ok: true });

  await api.rebuildImages('task-1');
  assert.ok(
    timerDelays.includes(8000),
    `rebuild (a fast enqueue) keeps the default ceiling — saw ${timerDelays}`
  );

  timerDelays.length = 0;
  await api.regenImage('task-1', 'featured', 'less busy, more sky');
  assert.ok(
    timerDelays.includes(300000),
    `regen must ride the long ceiling (render happens in-request) — saw ${timerDelays}`
  );
  assert.ok(
    !timerDelays.includes(8000),
    'regen must NOT also arm the 8s default timer'
  );
});
