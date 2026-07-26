'use strict';

// Contract tests for the graph approval-gate methods in the operator-console
// API adapter (src/cofounder_agent/console/js/api.js): the URLs + REQUEST
// BODIES gatesPending() / gateApprove() / gateReject() put on the wire. Same
// vm harness as api.approvals.test.js — the real api.js IIFE evaluated
// against a browser-shaped global with a recording fetch stub — because the
// adapter ships as a no-build in-browser asset and cannot be require()d.
//
// WHY (2026-07-25): the NEEDS YOU gate lane wires POST
// /api/gates/pending/{task_id}/{approve|reject} (gates_routes.py). The
// server schemas are GateApproveRequest {feedback?} and GateRejectRequest
// {reason?} — pin those field names so console↔route drift can't ship
// silently (the api.approvals.test.js lesson: reject() 400'd from day one).

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
    return Promise.resolve(res({ ok: true, items: [] }));
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

test('gatesPending() reads the pending listing with a limit', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.gatesPending();

  assert.equal(requests.length, 1);
  const r = requests[0];
  assert.equal(r.method, 'GET');
  assert.ok(r.url.includes('/api/gates/pending'));
  assert.ok(r.url.includes('limit=50'));
});

test('gateApprove() posts GateApproveRequest {feedback} to /approve', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.gateApprove('t-123', 'title reads well');

  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/gates/pending/t-123/approve'));
  assert.equal(r.body.feedback, 'title reads well');
});

test('gateApprove() without a note sends honest-empty feedback', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.gateApprove('t-456');

  const r = requests[0];
  assert.ok(r.url.endsWith('/api/gates/pending/t-456/approve'));
  assert.equal(r.body.feedback, '');
});

test('gateReject() posts GateRejectRequest {reason} to /reject', async () => {
  const { api, requests } = makeRecordingAdapter();
  await api.gateReject('t-123', 'meta is worse than current');

  const r = requests[0];
  assert.equal(r.method, 'POST');
  assert.ok(r.url.endsWith('/api/gates/pending/t-123/reject'));
  assert.equal(r.body.reason, 'meta is worse than current');
  // The approve-schema field must never leak into reject.
  assert.ok(!('feedback' in r.body));
});
