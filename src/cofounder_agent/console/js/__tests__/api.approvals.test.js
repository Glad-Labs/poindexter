'use strict';

// Contract tests for the approval-gate mutations in the operator-console API
// adapter (src/cofounder_agent/console/js/api.js): the REQUEST BODIES that
// approve() / reject() put on the wire. Same vm harness as api.token.test.js —
// the real api.js IIFE evaluated against a browser-shaped global with a
// counting fetch stub — because the adapter ships as a no-build in-browser
// asset and cannot be require()d.
//
// WHY these exist (2026-07-03): reject() shipped sending {human_feedback} —
// a field from the APPROVE route's schema — while the reject route's
// RejectionRequest (routes/approval_routes.py) REQUIRES {reason, feedback}.
// Every console reject 400'd (VALIDATION_ERROR) from day one. Nothing pinned
// the body shape, so the drift was invisible until an operator hit the
// button. These tests are that pin; if RejectionRequest changes, update both
// api.js and this file in lockstep.

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

// Fresh adapter realm whose fetch stub RECORDS every non-token request
// (method, url, parsed JSON body) so a test can assert the wire shape.
function makeRecordingAdapter() {
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
    return Promise.resolve(res({ ok: true }));
  };

  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController, // http() now wraps each fetch in an abortable timeout
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

test('reject() sends the RejectionRequest contract: reason + feedback + allow_revisions', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.reject('task-123', 'intro is padded, cut the throat-clearing');

  assert.equal(requests.length, 1);
  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/tasks/task-123/reject'));
  // The two REQUIRED RejectionRequest fields — omitting either is the
  // 400 VALIDATION_ERROR this file exists to prevent.
  assert.equal(typeof r.body.reason, 'string');
  assert.ok(r.body.reason.length > 0, 'reason must be non-empty');
  assert.equal(r.body.feedback, 'intro is padded, cut the throat-clearing');
  // Console reject means "send back to edit" (drawer copy) → rejected_retry.
  assert.equal(r.body.allow_revisions, true);
  // The old broken field must NOT resurface.
  assert.ok(!('human_feedback' in r.body));
});

test('reject() without feedback (row-level quick reject) still satisfies the contract', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.reject('task-456');

  const r = requests[0];
  assert.equal(typeof r.body.reason, 'string');
  assert.ok(r.body.reason.length > 0);
  assert.equal(r.body.feedback, ''); // honest-empty, not a fabricated note
  assert.equal(r.body.allow_revisions, true);
});

// The terminal half of the gate (console parity with `poindexter tasks reject
// --final`). A bad topic — or a dev diary for a quiet day — must be closable
// for good; before this the console could only ever emit rejected_retry, so
// every reject bounced the same draft straight back into the inbox.
test('reject({final:true}) sends allow_revisions:false → rejected_final', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.reject('task-789', 'topic is a dud, do not regenerate', {
    final: true,
  });

  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/tasks/task-789/reject'));
  // The bit that actually stops the retry loop.
  assert.equal(r.body.allow_revisions, false);
  // Both required fields still present — a terminal reject is still a
  // RejectionRequest, and omitting either still 400s.
  assert.equal(r.body.feedback, 'topic is a dud, do not regenerate');
  assert.equal(typeof r.body.reason, 'string');
  assert.ok(r.body.reason.length > 0);
  // Reason is self-describing in the audit trail, so a `rejected_final` row
  // is legible without cross-referencing allow_revisions.
  assert.equal(r.body.reason, 'operator_rejected_final');
});

// Fail-safe direction: anything that forgets the opts arg must land on the
// RECOVERABLE branch. panels.jsx calls `onReject(it)` with one arg, and a
// default that flipped to terminal would silently kill tasks on a stray click.
test('reject() defaults to retry when opts is omitted or empty', async () => {
  for (const opts of [undefined, {}, { final: false }]) {
    const { api, requests } = makeRecordingAdapter();
    await api.reject('task-000', 'note', opts);
    assert.equal(
      requests[0].body.allow_revisions,
      true,
      `opts=${JSON.stringify(opts)} must stay retry`
    );
  }
});

test('approve() stages only — approved:true with auto_publish:false on the wire', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.approve('task-789');

  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/tasks/task-789/approve'));
  assert.equal(r.body.approved, true);
  // feedback_human_approval: approve = stage, never go-live from this button.
  assert.equal(r.body.auto_publish, false);
});
