'use strict';
// Shared harness for the console live-contract test net. Loads the real api.js
// inside a node:vm sandbox (the api.prom.test.js pattern) with a fetch that
// records outgoing requests and replays recorded fixtures, so each PX.api live
// branch can be exercised offline and its request/response contract asserted.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// __dirname = …/console/js/__tests__/contracts ; api.js is two levels up.
const API_SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'api.js'),
  'utf8'
);

function makeLocalStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  };
}

// A minimal fetch Response stand-in. clone() returns self (single-read is fine
// for our callers: api.js reads .json() once; the recorder reads a clone once).
function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    json: async () => payload,
    text: async () =>
      typeof payload === 'string' ? payload : JSON.stringify(payload),
    clone() {
      return this;
    },
  };
}

// Eval api.js in a browser-shaped vm sandbox. `seed` pre-populates localStorage
// (px_base / px_prom / px_client_id / px_client_secret / px_live / …).
// `opts.preload` names sibling console modules (e.g. 'data.js') to run BEFORE
// api.js — needed by composite surfaces (serviceHealth/gpu) whose live branches
// merge Prometheus data over the window.PX mock scaffolding data.js defines.
function loadApiWithFetch(fetchImpl, seed = {}, opts = {}) {
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    AbortController, // http() aborts on timeout — REQUIRED or every call throws
    performance,
    fetch: fetchImpl,
    PX_API_LIVE: true,
  };
  sandbox.window = sandbox;
  sandbox.localStorage = makeLocalStorage(seed);
  vm.createContext(sandbox);
  for (const f of opts.preload || []) {
    vm.runInContext(
      fs.readFileSync(path.join(__dirname, '..', '..', f), 'utf8'),
      sandbox
    );
  }
  vm.runInContext(API_SRC, sandbox);
  return sandbox.PX.api;
}

// Load api.js with a recording fetch: answers /token with a fake JWT so
// getToken() proceeds, records every other call as {method,url,body}, and
// replays responder({method,url,body}) → {status,payload} (default 200 {}).
function loadApiWithRecorder(responder, seed = {}, opts = {}) {
  const calls = [];
  const fetchImpl = async (url, reqOpts = {}) => {
    const method = reqOpts.method || 'GET';
    const u = String(url);
    if (u.endsWith('/token')) {
      return jsonResponse(200, { access_token: 'test-jwt', expires_in: 3600 });
    }
    const body = reqOpts.body ? JSON.parse(reqOpts.body) : undefined;
    calls.push({ method, url: u, body });
    const r = (responder && responder({ method, url: u, body })) || {};
    return jsonResponse(
      r.status || 200,
      r.payload === undefined ? {} : r.payload
    );
  };
  const api = loadApiWithFetch(
    fetchImpl,
    {
      px_client_id: 'test-client',
      px_client_secret: 'test-secret',
      px_live: '1',
      ...seed,
    },
    opts
  );
  return { api, calls };
}

// Assert a captured call matches an expected contract. Worker rows key on
// method/path/query(object)/body; Prometheus rows on host:'prometheus'+query
// (the decoded PromQL). Messages are prefixed with `name` for legibility.
function assertRequest(call, expected, name) {
  assert.ok(call, `[${name}] expected a request to be issued, got none`);
  const url = new URL(call.url, 'http://local'); // base lets relative paths parse
  if (expected.method) {
    assert.equal(call.method, expected.method, `[${name}] method`);
  }
  if (expected.host === 'prometheus') {
    assert.equal(url.pathname, '/api/v1/query', `[${name}] prometheus path`);
    assert.equal(
      url.searchParams.get('query'),
      expected.query,
      `[${name}] promql`
    );
  } else {
    if (expected.path) {
      assert.equal(url.pathname, expected.path, `[${name}] path`);
    }
    if (expected.query) {
      for (const [k, v] of Object.entries(expected.query)) {
        assert.equal(
          url.searchParams.get(k),
          String(v),
          `[${name}] query param ${k}`
        );
      }
    }
  }
  if (expected.body !== undefined) {
    assert.deepEqual(call.body, expected.body, `[${name}] body`);
  }
}

function requestMatches(call, expected) {
  try {
    assertRequest(call, expected, 'x');
    return true;
  } catch {
    return false;
  }
}

// Resolve a (possibly nested) local $ref into snapshot.components.schemas.
function resolveRef(schema, snapshot) {
  let s = schema;
  let guard = 0;
  while (s && s.$ref && guard++ < 20) {
    const parts = s.$ref.replace(/^#\//, '').split('/'); // e.g. components/schemas/X
    s = parts.reduce((o, k) => (o == null ? o : o[k]), snapshot);
  }
  return s || {};
}

// Structural conformance check. Extra fields are allowed (additive backend
// changes must not break the console); null and anyOf/oneOf unions are tolerated.
function checkNode(value, schema, snapshot, label) {
  schema = resolveRef(schema, snapshot);
  if (Array.isArray(schema.allOf)) {
    schema.allOf.forEach((s) => checkNode(value, s, snapshot, label));
  }
  if (value === null || value === undefined) return; // presence enforced by parent `required`
  if (schema.anyOf || schema.oneOf) return; // union: too rich for structural check — tolerate
  const type = schema.type;
  if (type === 'object' || schema.properties || schema.required) {
    assert.equal(typeof value, 'object', `${label}: expected object`);
    assert.ok(!Array.isArray(value), `${label}: expected object, got array`);
    (schema.required || []).forEach((k) =>
      assert.ok(
        Object.prototype.hasOwnProperty.call(value, k),
        `${label}: missing required key '${k}'`
      )
    );
    Object.entries(schema.properties || {}).forEach(([k, sub]) => {
      if (Object.prototype.hasOwnProperty.call(value, k)) {
        checkNode(value[k], sub, snapshot, `${label}.${k}`);
      }
    });
  } else if (type === 'array') {
    assert.ok(Array.isArray(value), `${label}: expected array`);
    if (schema.items) {
      value.forEach((el, i) =>
        checkNode(el, schema.items, snapshot, `${label}[${i}]`)
      );
    }
  } else if (type === 'integer' || type === 'number') {
    assert.equal(typeof value, 'number', `${label}: expected number`);
  } else if (type === 'string') {
    assert.equal(typeof value, 'string', `${label}: expected string`);
  } else if (type === 'boolean') {
    assert.equal(typeof value, 'boolean', `${label}: expected boolean`);
  }
  // absent/unknown type (free-form object) → no further check
}

// Validate a recorded fixture against the committed OpenAPI snapshot. No-op when
// the endpoint declares no JSON 200 schema (endpoints without a response_model).
function validateAgainstSchema(value, snapshot, ref, name) {
  const op =
    snapshot &&
    snapshot.paths &&
    snapshot.paths[ref.path] &&
    snapshot.paths[ref.path][ref.method];
  const schema =
    op &&
    op.responses &&
    op.responses['200'] &&
    op.responses['200'].content &&
    op.responses['200'].content['application/json'] &&
    op.responses['200'].content['application/json'].schema;
  if (!schema) return; // no response_model → nothing to check
  checkNode(value, schema, snapshot, `[${name}] $`);
}

module.exports = {
  jsonResponse,
  makeLocalStorage,
  loadApiWithFetch,
  loadApiWithRecorder,
  assertRequest,
  requestMatches,
  validateAgainstSchema,
  resolveRef,
  checkNode,
};
