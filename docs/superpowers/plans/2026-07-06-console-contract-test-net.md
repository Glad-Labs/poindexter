# Console Live-Contract Test Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin every `PX.api` network surface with an offline, manifest-driven contract test net so a broken live branch fails a test before it ships, backed by a nightly self-hosted drift job that auto-merges benign backend changes and holds breaking ones for a human.

**Architecture:** A single `contracts.manifest.js` array of surface descriptors feeds a generic `node:test` runner. Each surface is loaded through the existing `api.prom.test.js` `node:vm` sandbox pattern, but with a **recorder `fetch`** that captures the outgoing request and replays a recorded fixture. Depth is tiered per row: request-contract for reads/writes, plus an adapter-output shape assertion and a dependency-free OpenAPI-schema check for response-transforming reads. Fixtures + a pruned OpenAPI snapshot are recorded from the real worker by `record-fixtures.mjs`; a nightly `console-contract-drift.yml` re-records, auto-merges a green refresh PR (Discord ping), and holds a red one (Telegram ping).

**Tech Stack:** Node 24 built-in test runner (`node --test`), `node:vm`, `node:assert/strict`, `node:fs` — **no runtime dependencies**. GitHub Actions (self-hosted runner) + `git` + `gh` CLI for the nightly job.

## Global Constraints

- **Zero runtime dependency in the console subtree.** `console-unit` stays a plain `node --test` with no `npm ci` for tests. The schema validator is hand-rolled (~60 lines). `ajv` may only ever drop in behind the `validateAgainstSchema` seam as a devDependency, and only if full JSON-Schema semantics are needed (out of scope here).
- **All new files live under `src/cofounder_agent/console/js/__tests__/contracts/`.** These are Node-only CommonJS test-support files (`require`/`module.exports`) — they are never loaded in the browser, so no browser dual-mode shim is needed (unlike `api.js`).
- **vm sandbox must include `AbortController`** plus `console`, `setTimeout`, `clearTimeout`, `URLSearchParams`, `performance`, `fetch`, and `window`=sandbox with an in-memory `localStorage`. `http()` wraps every fetch in an abort-timeout; a sandbox missing `AbortController` throws `AbortController is not defined` and breaks every test in the file.
- **Cross-realm assertion rule.** Values returned from `api.js` (the adapter output `out`) carry the vm realm's prototypes — assert on them with `typeof` / `Array.isArray` / key presence, **never** `assert.deepEqual`/`deepStrictEqual` (cross-realm prototype check fails on structurally-equal values). Request **captures** (`{method, url, body}`) are built in the main realm by the recorder, so `assert.deepEqual` on a captured `body` is safe.
- **No-dummy-data.** Fixtures contain only real recorded values or honest-empty payloads (`[]`, `{}`, `null`, zero counts) — never fabricated rows. Write surfaces are never recorded (a real `POST …/decide` would mutate prod), so they carry request-contract only.
- **Mirror safety.** The `console/` subtree is a Pro-tier overlay stripped from the public poindexter mirror, so `contracts/` (and its fixtures) is stripped too. Recorded fixtures still must not contain operator secrets — the settings endpoint pre-masks secrets as `'********'`, and the drift job's OAuth client is scoped `api:read` only.
- **Every change via PR; linear history (squash merge); CI green is the merge gate.** Run `npm run format` (Prettier) before every commit — the pre-commit hook runs `prettier --check` + `eslint --max-warnings=0` and will reject unformatted files.
- **The per-PR net is offline + deterministic.** It validates recorded fixtures against the _committed_ snapshot; it never touches a live worker. The only live-backend touch is the nightly `console-contract-drift.yml` on the self-hosted runner — never a PR gate.

---

## File Structure

| File                                           | Responsibility                                                                                                                                                                                                                                                                                                         |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `contract-runtime.js`                          | Shared Node helpers: `loadApiWithFetch(fetchImpl, seed)` (vm loader), `loadApiWithRecorder(responder, seed)` (recording + fixture-replay fetch), `assertRequest(call, expected, name)`, `requestMatches(call, expected)`, and the dep-free `validateAgainstSchema(value, snapshot, ref, name)`. `module.exports` only. |
| `contract-runtime.test.js`                     | Self-tests for the harness helpers (loader captures a request; body capture; `assertRequest` bites on mismatch; schema validator pass/fail).                                                                                                                                                                           |
| `contracts.manifest.js`                        | The one source of truth: an array of ~40 surface descriptors (`name`, `invoke`, `request`, optional `fixture`/`shape`/`openapi`). Doubles as the executable endpoint map. `module.exports` only.                                                                                                                       |
| `run-contracts.test.js`                        | Generic runner: iterates the manifest, one `node:test` per surface. Auto-discovered by the existing `npm run test:console` glob — zero PR-CI config change.                                                                                                                                                            |
| `serviceHealth.contract.test.js`               | Bespoke test for the two composite surfaces (`serviceHealth`, `gpu`) whose multi-fetch fan-out a manifest row would obscure.                                                                                                                                                                                           |
| `fixtures/*.json`                              | Recorded real read responses, one per read surface (`<name>.json`).                                                                                                                                                                                                                                                    |
| `openapi.snapshot.json`                        | Pruned committed snapshot of the worker's `/openapi.json` (only the console's paths + full `components/schemas`).                                                                                                                                                                                                      |
| `record-fixtures.mjs`                          | Refresh tool (needs a live worker + read-only OAuth client). Re-records read fixtures, re-dumps the pruned snapshot, prints the drift diff, exits **0 = no drift / 1 = drift**. Never gates a PR.                                                                                                                      |
| `.github/workflows/console-contract-drift.yml` | Nightly + `workflow_dispatch` drift job on the self-hosted runner: record → on drift open a refresh PR → auto-merge if green (Discord) / hold if red (Telegram).                                                                                                                                                       |
| `README.md`                                    | When/how to refresh; tiering rules; the empty-in-prod caveat; the nightly job + its secrets.                                                                                                                                                                                                                           |

---

## Task 1: Harness core — `contract-runtime.js` loader + request assertions

**Files:**

- Create: `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js`
- Test: `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js`

**Interfaces:**

- Consumes: the real `src/cofounder_agent/console/js/api.js` (read from disk, eval'd in a vm).
- Produces:
  - `loadApiWithFetch(fetchImpl, seed = {}) → api` — evals `api.js` in a vm sandbox (browser globals + `PX_API_LIVE=true` + a fake `localStorage` seeded from `seed`), returns `sandbox.PX.api`.
  - `loadApiWithRecorder(responder, seed = {}) → { api, calls }` — like above but `fetch` answers `/token` with a fake JWT, records every other call as `{ method, url, body }` onto `calls`, and replays `responder({method,url,body})` → `{ status, payload }` (default 200 `{}`).
  - `assertRequest(call, expected, name)` — throws `AssertionError` (message prefixed `[name]`) unless `call` matches `expected` (`method`, `path`, `query`, `body`; or Prometheus `host:'prometheus'` + `query`).
  - `requestMatches(call, expected) → boolean` — non-throwing predicate form of `assertRequest`.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js`:

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadApiWithRecorder,
  assertRequest,
  requestMatches,
} = require('./contract-runtime.js');

test('loadApiWithRecorder captures a read surface request', async () => {
  const { api, calls } = loadApiWithRecorder(() => ({
    status: 200,
    payload: {},
  }));
  await api.seo();
  const nonToken = calls.filter((c) => !c.url.endsWith('/token'));
  assert.equal(nonToken.length, 1);
  assert.equal(nonToken[0].method, 'GET');
  assert.ok(nonToken[0].url.endsWith('/api/seo'));
});

test('loadApiWithRecorder parses a write surface body into the capture', async () => {
  const { api, calls } = loadApiWithRecorder(() => ({
    status: 200,
    payload: {},
  }));
  await api.reject('task_1');
  const call = calls.find((c) => c.url.includes('/reject'));
  assert.deepEqual(call.body, {
    reason: 'operator_rejected',
    feedback: '',
    allow_revisions: true,
  });
});

test('assertRequest throws (prefixed) on a path mismatch', () => {
  const call = { method: 'GET', url: '/api/seo', body: undefined };
  assert.throws(
    () => assertRequest(call, { method: 'GET', path: '/api/seooo' }, 'seo'),
    /\[seo\] path/
  );
});

test('requestMatches is the non-throwing form of assertRequest', () => {
  const call = { method: 'GET', url: '/api/seo?x=1', body: undefined };
  assert.equal(requestMatches(call, { method: 'GET', path: '/api/seo' }), true);
  assert.equal(
    requestMatches(call, { method: 'POST', path: '/api/seo' }),
    false
  );
});

test('assertRequest checks a Prometheus PromQL query', () => {
  const call = {
    method: 'GET',
    url: 'http://localhost:9091/api/v1/query?query=up',
    body: undefined,
  };
  assertRequest(call, { host: 'prometheus', query: 'up' }, 'promVector'); // no throw
  assert.throws(
    () =>
      assertRequest(call, { host: 'prometheus', query: 'down' }, 'promVector'),
    /\[promVector\] promql/
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js`
Expected: FAIL — `Cannot find module './contract-runtime.js'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js`:

```js
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
function loadApiWithFetch(fetchImpl, seed = {}) {
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
  vm.runInContext(API_SRC, sandbox);
  return sandbox.PX.api;
}

// Load api.js with a recording fetch: answers /token with a fake JWT so
// getToken() proceeds, records every other call as {method,url,body}, and
// replays responder({method,url,body}) → {status,payload} (default 200 {}).
function loadApiWithRecorder(responder, seed = {}) {
  const calls = [];
  const fetchImpl = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    const u = String(url);
    if (u.endsWith('/token')) {
      return jsonResponse(200, { access_token: 'test-jwt', expires_in: 3600 });
    }
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ method, url: u, body });
    const r = (responder && responder({ method, url: u, body })) || {};
    return jsonResponse(
      r.status || 200,
      r.payload === undefined ? {} : r.payload
    );
  };
  const api = loadApiWithFetch(fetchImpl, {
    px_client_id: 'test-client',
    px_client_secret: 'test-secret',
    px_live: '1',
    ...seed,
  });
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

module.exports = {
  jsonResponse,
  makeLocalStorage,
  loadApiWithFetch,
  loadApiWithRecorder,
  assertRequest,
  requestMatches,
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js`
Expected: PASS — `# pass 5`, `# fail 0`.

- [ ] **Step 5: Commit**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js \
        src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js
git commit -m "test(console): contract-net harness — vm loader + request assertions"
```

---

## Task 2: Dependency-free schema validator in `contract-runtime.js`

**Files:**

- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js` (add `validateAgainstSchema` + `module.exports`)
- Test: `src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js` (append cases)

**Interfaces:**

- Produces: `validateAgainstSchema(value, snapshot, ref, name)` — locates `snapshot.paths[ref.path][ref.method].responses['200'].content['application/json'].schema`, resolves local `$ref`s into `snapshot.components.schemas`, and structurally checks `value`: `object` → every `required` key present + recurse present `properties`; `array` → recurse `items`; primitives → `typeof`; extra fields allowed; `null` tolerated; `anyOf`/`oneOf` tolerated (skip). No schema for the endpoint → no-op. Throws `AssertionError` (prefixed `[name]`) on mismatch.

- [ ] **Step 1: Write the failing test**

Append to `contract-runtime.test.js`:

```js
const { validateAgainstSchema } = require('./contract-runtime.js');

const SNAP = {
  paths: {
    '/api/media-approval/pending': {
      get: {
        responses: {
          200: {
            content: {
              'application/json': {
                schema: { $ref: '#/components/schemas/MediaPendingResponse' },
              },
            },
          },
        },
      },
    },
    '/api/nomodel': { get: { responses: { 200: { description: 'ok' } } } },
  },
  components: {
    schemas: {
      MediaPendingResponse: {
        type: 'object',
        required: ['items', 'total'],
        properties: {
          total: { type: 'integer' },
          items: {
            type: 'array',
            items: { $ref: '#/components/schemas/MediaItem' },
          },
        },
      },
      MediaItem: {
        type: 'object',
        required: ['post_id', 'medium'],
        properties: { post_id: { type: 'string' }, medium: { type: 'string' } },
      },
    },
  },
};

test('validateAgainstSchema passes a conforming value (extra fields allowed)', () => {
  const value = {
    items: [{ post_id: 'p1', medium: 'video', extra: 1 }],
    total: 1,
    added_by_backend: true, // additive change must not break the console
  };
  validateAgainstSchema(
    value,
    SNAP,
    { path: '/api/media-approval/pending', method: 'get' },
    'mediaQueue'
  ); // no throw
});

test('validateAgainstSchema fails on a missing required key', () => {
  const value = { items: [{ medium: 'video' }], total: 1 }; // item missing post_id
  assert.throws(
    () =>
      validateAgainstSchema(
        value,
        SNAP,
        { path: '/api/media-approval/pending', method: 'get' },
        'mediaQueue'
      ),
    /\[mediaQueue\].*post_id/
  );
});

test('validateAgainstSchema fails on a wrong primitive type', () => {
  const value = { items: [], total: 'nope' }; // total should be a number
  assert.throws(
    () =>
      validateAgainstSchema(
        value,
        SNAP,
        { path: '/api/media-approval/pending', method: 'get' },
        'mediaQueue'
      ),
    /\[mediaQueue\].*total/
  );
});

test('validateAgainstSchema is a no-op when the endpoint has no response schema', () => {
  validateAgainstSchema(
    { anything: true },
    SNAP,
    { path: '/api/nomodel', method: 'get' },
    'nomodel'
  ); // no throw
});

test('validateAgainstSchema tolerates null values and empty arrays', () => {
  const value = { items: [], total: 0 };
  validateAgainstSchema(
    value,
    SNAP,
    { path: '/api/media-approval/pending', method: 'get' },
    'mediaQueue'
  ); // no throw
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js`
Expected: FAIL — `validateAgainstSchema is not a function` (not yet exported).

- [ ] **Step 3: Write minimal implementation**

In `contract-runtime.js`, add these functions above `module.exports`:

```js
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
```

Add `validateAgainstSchema`, `resolveRef`, and `checkNode` to `module.exports`:

```js
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js`
Expected: PASS — `# pass 10`, `# fail 0`.

- [ ] **Step 5: Commit**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.js \
        src/cofounder_agent/console/js/__tests__/contracts/contract-runtime.test.js
git commit -m "test(console): dep-free OpenAPI structural schema validator"
```

---

## Task 3: Manifest (tier-1 reads) + generic runner

**Files:**

- Create: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js`
- Create: `src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`

**Interfaces:**

- Consumes: `loadApiWithRecorder`, `assertRequest`, `requestMatches`, `validateAgainstSchema` (Task 1–2).
- Produces:
  - `contracts.manifest.js` → `module.exports = [ row, … ]`. Row: `{ name, invoke, request, fixture?, shape?, openapi? }`. `request` is one expected call **or an array** for composite surfaces.
  - `run-contracts.test.js` — the full generic runner (handles request single/array, shape, openapi). It does not change after this task; tier-2/tier-3 rows are appended to the manifest in later tasks and are exercised by this same runner.

**Row-field contract (used by all manifest tasks):**

- `name` (string) — subtest label + `PX.api` method name.
- `invoke(api)` (fn) — calls the method with encoded args; may be async.
- `request` — `{ method, path, query?, body? }` for worker calls; `{ host:'prometheus', method?, query }` for Prometheus; or an **array** of those for composites.
- `fixture?` (string) — fixture filename; defaults to `<name>.json`. The runner replays it (or `{}` if the file is absent) as the endpoint response.
- `shape?(out)` (fn) — asserts the adapter output shape using `typeof`/`Array.isArray` only (cross-realm rule).
- `openapi?` — `{ path, method }` OpenAPI template key; the fixture is validated against the snapshot there.

- [ ] **Step 1: Write the failing test (the runner)**

Create `src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`:

```js
'use strict';
// Generic contract runner. One node:test per manifest surface. Auto-discovered
// by `npm run test:console` (the __tests__/**/*.test.js glob) — no CI change.
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {
  loadApiWithRecorder,
  assertRequest,
  requestMatches,
  validateAgainstSchema,
} = require('./contract-runtime.js');
const manifest = require('./contracts.manifest.js');

const FIX_DIR = path.join(__dirname, 'fixtures');
const SNAPSHOT = (() => {
  const p = path.join(__dirname, 'openapi.snapshot.json');
  return fs.existsSync(p)
    ? JSON.parse(fs.readFileSync(p, 'utf8'))
    : { paths: {}, components: { schemas: {} } };
})();

function loadFixture(file) {
  const p = path.join(FIX_DIR, file);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
}

for (const row of manifest) {
  test(`contract: ${row.name}`, async () => {
    const fixtureFile = row.fixture || `${row.name}.json`;
    const fixture = loadFixture(fixtureFile);
    // Replay the recorded fixture (or {} when none) as the 200 response for
    // every non-/token call this surface makes.
    const { api, calls } = loadApiWithRecorder(() => ({
      status: 200,
      payload: fixture == null ? {} : fixture,
    }));

    const out = await row.invoke(api);

    // ── request contract (single expected call, or an array for composites) ──
    const expectedReqs = Array.isArray(row.request)
      ? row.request
      : [row.request];
    if (Array.isArray(row.request)) {
      // order-independent: each expected must match some captured call
      expectedReqs.forEach((exp) => {
        const hit = calls.some((c) => requestMatches(c, exp));
        assert.ok(
          hit,
          `[${row.name}] no captured call matched ${JSON.stringify(exp)}; saw ${JSON.stringify(calls)}`
        );
      });
    } else {
      const nonToken = calls.filter((c) => !c.url.endsWith('/token'));
      assert.equal(
        nonToken.length,
        1,
        `[${row.name}] expected exactly one request, saw ${JSON.stringify(nonToken)}`
      );
      assertRequest(nonToken[0], row.request, row.name);
    }

    // ── adapter-output shape (transform surfaces) ──
    if (row.shape) {
      assert.ok(
        fixture != null,
        `[${row.name}] shape assertion needs fixtures/${fixtureFile} — run record-fixtures.mjs`
      );
      row.shape(out);
    }

    // ── schema conformance (where a response_model exists) ──
    if (row.openapi && fixture != null) {
      validateAgainstSchema(fixture, SNAPSHOT, row.openapi, row.name);
    }
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: FAIL — `Cannot find module './contracts.manifest.js'`.

- [ ] **Step 3: Write minimal implementation (tier-1 rows)**

Create `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js`:

```js
'use strict';
// The one source of truth for the console live-contract net. Each row pins a
// PX.api network surface: its outgoing request always; its adapter output shape
// and OpenAPI schema where it transforms a response. Doubles as the executable
// endpoint map. Local config setters (setLive/setClient/setBase/setPrometheus/
// setSim/getSim/setScope/grafanaBase/setGrafanaEmbed/isLive/config) touch no
// network and are intentionally excluded.
const assert = require('node:assert/strict');

module.exports = [
  // ══ TIER 1 — read pass-through (request contract; +openapi where a
  //    response_model exists and the branch does not reshape) ══
  {
    name: 'health',
    invoke: (api) => api.health(),
    request: { method: 'GET', path: '/api/health' },
  },
  {
    name: 'listApprovals',
    invoke: (api) => api.listApprovals(),
    request: {
      method: 'GET',
      path: '/api/tasks/pending-approval',
      query: { limit: 50 },
    },
  },
  {
    name: 'listTasks',
    invoke: (api) => api.listTasks(),
    request: { method: 'GET', path: '/api/tasks' },
  },
  {
    name: 'getTask',
    invoke: (api) => api.getTask('task_1'),
    request: { method: 'GET', path: '/api/tasks/task_1' },
  },
  {
    name: 'listTopicProposals',
    invoke: (api) => api.listTopicProposals(),
    request: { method: 'GET', path: '/api/topics/proposals' },
  },
  {
    name: 'findings',
    invoke: (api) => api.findings(),
    request: { method: 'GET', path: '/api/findings' },
  },
  {
    name: 'logs',
    invoke: (api) => api.logs(),
    request: { method: 'GET', path: '/api/logs' },
  },
  {
    name: 'traces',
    invoke: (api) => api.traces(),
    request: { method: 'GET', path: '/api/traces' },
  },
  {
    name: 'seo',
    invoke: (api) => api.seo(),
    request: { method: 'GET', path: '/api/seo' },
  },
  {
    name: 'socialDrafts',
    invoke: (api) => api.socialDrafts(),
    request: { method: 'GET', path: '/api/social/drafts' },
  },
  {
    name: 'memorySearch',
    invoke: (api) => api.memorySearch('hello'),
    request: {
      method: 'GET',
      path: '/api/memory/search',
      query: { q: 'hello' },
    },
    openapi: { path: '/api/memory/search', method: 'get' },
  },
  {
    name: 'posts',
    invoke: (api) => api.posts(),
    request: { method: 'GET', path: '/api/posts' },
    openapi: { path: '/api/posts', method: 'get' },
  },
  {
    name: 'analyticsViews',
    invoke: (api) => api.analyticsViews(),
    request: { method: 'GET', path: '/api/analytics/views' },
    openapi: { path: '/api/analytics/views', method: 'get' },
  },
  {
    name: 'budget',
    invoke: (api) => api.budget(),
    request: { method: 'GET', path: '/api/metrics/costs/budget' },
    openapi: { path: '/api/metrics/costs/budget', method: 'get' },
  },
  {
    name: 'newsletter',
    invoke: (api) => api.newsletter(),
    request: { method: 'GET', path: '/api/newsletter/stats' },
    openapi: { path: '/api/newsletter/stats', method: 'get' },
  },
  {
    name: 'brainActivity',
    invoke: (api) => api.brainActivity(),
    request: { method: 'GET', path: '/api/brain/stats' },
    openapi: { path: '/api/brain/stats', method: 'get' },
  },
];
```

> **Note for the executor:** the `query`/`path`/`body`/`openapi` values above were transcribed from the real `api.js` live branches. If any request assertion fails, re-read that surface's `live:` branch in `src/cofounder_agent/console/js/api.js` and correct the row — the live branch is the source of truth, and a mismatch here is exactly the drift the net exists to catch. Tier assignment follows the spec rule: a branch that only returns `http(...)` is tier 1; one that reshapes is tier 3; a POST/PUT/PATCH/DELETE is tier 2.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: PASS — 16 `contract: …` subtests, `# fail 0`. (The `openapi` rows no-op because `openapi.snapshot.json` does not exist yet — the runner's `fixture != null` guard skips them; they light up in Task 6.)

- [ ] **Step 5: Verify the whole console suite still passes**

Run: `npm run test:console`
Expected: PASS — the existing 38 tests **plus** the 16 new `contract: …` subtests, `# fail 0`. Confirms zero-config auto-discovery via the `__tests__/**/*.test.js` glob.

- [ ] **Step 6: Commit**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js \
        src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js
git commit -m "test(console): contract manifest (tier-1 reads) + generic runner"
```

---

## Task 4: Manifest tier-2 (write / mutation) rows + body-regression proof

**Files:**

- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (append write rows)

**Interfaces:**

- Consumes: the runner + row-field contract from Task 3. Write rows carry `request` with `body` (deep-equal asserted) and no `fixture`/`shape`/`openapi` — recording a POST would mutate prod (no-dummy-data / non-goal).

- [ ] **Step 1: Append the tier-2 rows**

Insert before the closing `];` of `contracts.manifest.js`:

```js
  // ══ TIER 2 — write / mutation (request contract incl. body; never recorded) ══
  {
    name: 'updateSetting',
    invoke: (api) => api.updateSetting('site_title', 'Glad Labs'),
    request: { method: 'PUT', path: '/api/settings/site_title', body: { value: 'Glad Labs' } },
  },
  {
    name: 'approve',
    invoke: (api) => api.approve('task_1'),
    request: { method: 'POST', path: '/api/tasks/task_1/approve', body: { approved: true, auto_publish: false } },
  },
  {
    name: 'reject',
    invoke: (api) => api.reject('task_1', 'needs sources'),
    // Regression anchor: reject must send RejectionRequest {reason, feedback,
    // allow_revisions}, NOT the approve route's {human_feedback} (audit bug #3).
    request: {
      method: 'POST',
      path: '/api/tasks/task_1/reject',
      body: { reason: 'operator_rejected', feedback: 'needs sources', allow_revisions: true },
    },
  },
  {
    name: 'publishTask',
    invoke: (api) => api.publishTask('task_1'),
    request: { method: 'POST', path: '/api/tasks/task_1/publish' },
  },
  {
    name: 'retryTask',
    invoke: (api) => api.retryTask('task_1'),
    request: { method: 'PUT', path: '/api/tasks/task_1/status', body: { status: 'pending' } },
  },
  {
    name: 'killTask',
    invoke: (api) => api.killTask('task_1'),
    request: { method: 'DELETE', path: '/api/tasks/task_1' },
  },
  {
    name: 'rankTopicBatch',
    invoke: (api) => api.rankTopicBatch('batch_1', ['cand_a', 'cand_b']),
    request: { method: 'POST', path: '/api/topics/batch_1/rank', body: { ordered_candidate_ids: ['cand_a', 'cand_b'] } },
  },
  {
    name: 'resolveTopicBatch',
    invoke: (api) => api.resolveTopicBatch('batch_1'),
    request: { method: 'POST', path: '/api/topics/batch_1/resolve' },
  },
  {
    name: 'rejectTopicBatch',
    invoke: (api) => api.rejectTopicBatch('batch_1', 'off_topic'),
    request: { method: 'POST', path: '/api/topics/batch_1/reject', body: { reason: 'off_topic' } },
  },
  {
    name: 'mediaDecide',
    invoke: (api) => api.mediaDecide('post_1', 'video', false),
    request: {
      method: 'POST',
      path: '/api/media-approval/post_1/video/decide',
      body: { approved: false, notes: null },
    },
  },
  {
    name: 'scheduleShift',
    invoke: (api) => api.scheduleShift('1 hour', ['post_1']),
    request: { method: 'PATCH', path: '/api/scheduling/shift', body: { by_delta: '1 hour', post_ids: ['post_1'] } },
  },
  {
    name: 'socialDraftAction',
    invoke: (api) => api.socialDraftAction('draft_1', 'approve'),
    request: { method: 'POST', path: '/api/social/drafts/draft_1/approve' },
  },
  {
    name: 'restartService',
    invoke: (api) => api.restartService('poindexter-worker'),
    request: { method: 'POST', path: '/api/admin/restart', body: { service: 'poindexter-worker' } },
  },
  {
    name: 'rebuildExport',
    invoke: (api) => api.rebuildExport(),
    request: { method: 'POST', path: '/api/export/rebuild' },
  },
```

- [ ] **Step 2: Run to verify the new rows pass**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: PASS — now 30 `contract: …` subtests, `# fail 0`.

- [ ] **Step 3: Prove the body assertion bites (AC3 — the reject-field regression)**

Temporarily edit the `reject` row's body to the WRONG (pre-fix) field — change `feedback: 'needs sources'` to `human_feedback: 'needs sources'` — then run:

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: FAIL — `[reject] body` AssertionError (the net catches the exact audit-bug-#3 class).

Then **restore** the row to the correct body and re-run:

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: PASS — `# fail 0`.

- [ ] **Step 4: Commit**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js
git commit -m "test(console): contract manifest tier-2 write rows (body contracts)"
```

---

## Task 5: `record-fixtures.mjs` + read-only OAuth client → record real fixtures + snapshot

**Files:**

- Create: `src/cofounder_agent/console/js/__tests__/contracts/record-fixtures.mjs`
- Create (recorded, committed): `src/cofounder_agent/console/js/__tests__/contracts/fixtures/*.json`
- Create (recorded, committed): `src/cofounder_agent/console/js/__tests__/contracts/openapi.snapshot.json`

**Interfaces:**

- Consumes: `loadApiWithFetch` (Task 1) via `createRequire`, and the manifest (Task 3–4) — records every read row (GET rows carrying `fixture`/`shape`/`openapi`, plus Prometheus rows).
- Produces: `fixtures/<name>.json` (real recorded read responses) + `openapi.snapshot.json` (pruned). Exit **0 = no drift / 1 = drift** vs the committed artifacts.

**Prerequisite (operator action — requires the live worker at `localhost:8002`):** provision a least-privilege read-only OAuth client. Run:

```bash
poindexter auth register-client --name console-contract-drift --scopes "api:read" --grant-type client_credentials
```

It prints a `client_id` + `client_secret` once. Export them for the recording run (and later store them as GitHub Actions secrets in Task 8):

```bash
export CONSOLE_CONTRACT_CLIENT_ID=<printed client_id>
export CONSOLE_CONTRACT_CLIENT_SECRET=<printed client_secret>
```

- [ ] **Step 1: Write the recorder**

Create `src/cofounder_agent/console/js/__tests__/contracts/record-fixtures.mjs`:

```js
#!/usr/bin/env node
// Refresh contract fixtures + the pruned OpenAPI snapshot from a live worker.
// Reads (GET) only — write surfaces are never invoked (a real POST would mutate
// prod). Exit 0 = artifacts match the committed ones, 1 = they drifted.
//
//   node record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091
//   (creds via --client-id/--client-secret or CONSOLE_CONTRACT_CLIENT_ID/_SECRET env)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { loadApiWithFetch } = require('./contract-runtime.js');
const manifest = require('./contracts.manifest.js');

const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i >= 0 ? process.argv[i + 1] : d;
};
const BASE = arg('--base', 'http://localhost:8002');
const PROM = arg('--prometheus', 'http://localhost:9091');
const CLIENT_ID = arg('--client-id', process.env.CONSOLE_CONTRACT_CLIENT_ID);
const CLIENT_SECRET = arg(
  '--client-secret',
  process.env.CONSOLE_CONTRACT_CLIENT_SECRET
);
const FIX_DIR = path.join(__dirname, 'fixtures');

if (!CLIENT_ID || !CLIENT_SECRET) {
  console.error(
    'Missing OAuth client creds (--client-id/--client-secret or env).'
  );
  process.exit(2);
}
fs.mkdirSync(FIX_DIR, { recursive: true });

// A read row is one that declares a fixture/shape/openapi and whose (single or
// first) expected request is a GET — i.e. a surface whose response we replay.
function firstReq(row) {
  return Array.isArray(row.request) ? row.request[0] : row.request;
}
function isReadRow(row) {
  const r = firstReq(row);
  return (
    (row.fixture || row.shape || row.openapi) &&
    r &&
    (r.method || 'GET') === 'GET'
  );
}

// Record one row: load api.js with a forwarding fetch that hits the real worker
// / Prometheus (via cfg.base / cfg.prometheus) and captures the FIRST non-/token
// raw JSON response, then run the row's invoke.
async function recordRow(row) {
  let raw = null;
  const forwarding = async (url, opts) => {
    const res = await fetch(url, opts); // absolute URLs — api.js built them from cfg
    if (!String(url).endsWith('/token') && raw === null) {
      try {
        raw = await res.clone().json();
      } catch {
        raw = null;
      }
    }
    return res;
  };
  const api = loadApiWithFetch(forwarding, {
    px_base: BASE,
    px_prom: PROM,
    px_client_id: CLIENT_ID,
    px_client_secret: CLIENT_SECRET,
    px_live: '1',
  });
  await row.invoke(api);
  return raw;
}

async function mintToken() {
  const form = new URLSearchParams({
    grant_type: 'client_credentials',
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET,
  });
  const r = await fetch(BASE + '/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  });
  if (!r.ok) throw new Error(`/token → ${r.status}`);
  return (await r.json()).access_token;
}

// Dump /openapi.json and prune .paths to the manifest's openapi paths (keep full
// components — small, and the validator resolves $refs into it).
async function snapshot(tok) {
  const full = await (
    await fetch(BASE + '/openapi.json', {
      headers: { Authorization: 'Bearer ' + tok },
    })
  ).json();
  const wantPaths = new Set();
  manifest.forEach((row) => {
    if (row.openapi) wantPaths.add(row.openapi.path);
  });
  const paths = {};
  for (const p of wantPaths)
    if (full.paths && full.paths[p]) paths[p] = full.paths[p];
  return {
    openapi: full.openapi,
    info: { title: full.info?.title, version: full.info?.version },
    paths,
    components: full.components || { schemas: {} },
  };
}

const readRows = manifest.filter(isReadRow);
console.log(`Recording ${readRows.length} read fixtures from ${BASE} …`);
for (const row of readRows) {
  const raw = await recordRow(row);
  const file = path.join(FIX_DIR, row.fixture || `${row.name}.json`);
  fs.writeFileSync(file, JSON.stringify(raw, null, 2) + '\n');
  console.log(`  ✓ ${path.basename(file)}`);
}
const snap = await snapshot(await mintToken());
fs.writeFileSync(
  path.join(__dirname, 'openapi.snapshot.json'),
  JSON.stringify(snap, null, 2) + '\n'
);
console.log('  ✓ openapi.snapshot.json');

// Drift verdict: does anything under contracts/ differ from the committed tree?
const rel = 'src/cofounder_agent/console/js/__tests__/contracts';
const status = execFileSync('git', ['status', '--porcelain', '--', rel], {
  encoding: 'utf8',
});
if (status.trim()) {
  console.log('\n── DRIFT: contract artifacts changed ──');
  console.log(
    execFileSync('git', ['diff', '--stat', '--', rel], { encoding: 'utf8' })
  );
  process.exit(1);
}
console.log('\nNo drift — artifacts match the committed snapshot.');
process.exit(0);
```

- [ ] **Step 2: Record the real fixtures + snapshot (requires the worker up)**

Run: `node src/cofounder_agent/console/js/__tests__/contracts/record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091`
Expected: prints `✓ <name>.json` for each read fixture + `✓ openapi.snapshot.json`, then (on this first run, since nothing is committed yet) `── DRIFT … ──` and exit 1. The new files now exist under `fixtures/` and `openapi.snapshot.json`.

- [ ] **Step 3: Sanity-check the recorded fixtures are real (no fabrication)**

Run: `node -e "const f=require('fs'); const d='src/cofounder_agent/console/js/__tests__/contracts/fixtures'; for(const n of f.readdirSync(d)){const j=JSON.parse(f.readFileSync(d+'/'+n)); console.log(n, Array.isArray(j)?('array['+j.length+']'):typeof j);}"`
Expected: each fixture is a real object/array recorded from the worker (empty-but-honest where prod has no rows — e.g. `media-approval` pending may be `{items:[],total:0}`). Confirm no fixture contains a real secret value (settings secrets arrive pre-masked as `'********'`).

- [ ] **Step 4: Commit the recorder + recorded artifacts**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/record-fixtures.mjs \
        src/cofounder_agent/console/js/__tests__/contracts/fixtures \
        src/cofounder_agent/console/js/__tests__/contracts/openapi.snapshot.json
git commit -m "test(console): fixture recorder + recorded fixtures & OpenAPI snapshot"
```

- [ ] **Step 5: Verify the recorder now reports no drift**

Run: `node src/cofounder_agent/console/js/__tests__/contracts/record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091`
Expected: re-records identical artifacts, `No drift — artifacts match the committed snapshot.`, exit 0. (If it exits 1 with a diff, the backend genuinely changed between runs — commit the refreshed artifacts.)

---

## Task 6: Manifest tier-3 (read-transform + Prometheus) rows

**Files:**

- Modify: `src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js` (append transform + Prometheus rows)

**Interfaces:**

- Consumes: the runner (Task 3), the recorded `fixtures/*.json` + `openapi.snapshot.json` (Task 5). These rows carry `shape` (asserted on the adapter output with `typeof`/`Array.isArray`) and, where a worker `response_model` exists, `openapi`. Prometheus rows carry `shape` but no `openapi` (they hit Prometheus, not the worker).

- [ ] **Step 1: Append the tier-3 rows**

Insert before the closing `];` of `contracts.manifest.js`:

```js
  // ══ TIER 3 — read transform (request + adapter-shape + schema where routed) ══
  {
    name: 'listSettings',
    invoke: (api) => api.listSettings(),
    request: { method: 'GET', path: '/api/settings', query: { limit: 100, offset: 0 } },
    fixture: 'listSettings.json',
    shape: (out) => {
      assert.ok(Array.isArray(out.settings), 'settings is an array');
      assert.ok(Array.isArray(out.categories), 'categories is an array');
      assert.equal(typeof out.total, 'number', 'total is a number');
    },
    openapi: { path: '/api/settings', method: 'get' },
  },
  {
    name: 'mediaQueue',
    invoke: (api) => api.mediaQueue(),
    request: { method: 'GET', path: '/api/media-approval/pending' },
    fixture: 'mediaQueue.json',
    shape: (out) => {
      assert.ok(Array.isArray(out.queue), 'queue is an array');
      assert.equal(typeof out.gate2Pending, 'number', 'gate2Pending is a number');
      out.queue.forEach((r) => assert.equal(typeof r.post_id, 'string'));
    },
    openapi: { path: '/api/media-approval/pending', method: 'get' },
  },
  {
    name: 'schedule',
    invoke: (api) => api.schedule(),
    request: { method: 'GET', path: '/api/scheduling' },
    fixture: 'schedule.json',
    shape: (out) => {
      assert.ok(Array.isArray(out.rows), 'rows is an array');
      assert.equal(typeof out.count, 'number', 'count is a number');
    },
    openapi: { path: '/api/scheduling', method: 'get' },
  },
  {
    name: 'memoryStats',
    invoke: (api) => api.memoryStats(),
    request: { method: 'GET', path: '/api/memory/stats' },
    fixture: 'memoryStats.json',
    shape: (out) => {
      assert.equal(typeof out.totalEmbeddings, 'number', 'totalEmbeddings is a number');
      assert.ok(Array.isArray(out.bySource), 'bySource is an array');
      assert.ok(Array.isArray(out.byWriter), 'byWriter is an array');
    },
    openapi: { path: '/api/memory/stats', method: 'get' },
  },
  {
    name: 'pipelineEvents',
    invoke: (api) => api.pipelineEvents(),
    request: { method: 'GET', path: '/api/pipeline/events', query: { limit: 50, since_minutes: 120 } },
    fixture: 'pipelineEvents.json',
    shape: (out) => {
      assert.ok(Array.isArray(out), 'pipelineEvents returns an array of feed lines');
      out.forEach((line) => {
        assert.ok(Array.isArray(line.tag), 'each feed line has a [tone,label] tag');
        assert.equal(typeof line.html, 'string', 'each feed line has html');
      });
    },
    openapi: { path: '/api/pipeline/events', method: 'get' },
  },
  {
    name: 'voiceJoinUrl',
    invoke: (api) => api.voiceJoinUrl(),
    request: {
      method: 'GET',
      path: '/api/settings',
      query: { search: 'voice_agent_public_join_url', limit: 10 },
    },
    fixture: 'voiceJoinUrl.json',
    shape: (out) => assert.equal(typeof out, 'string', 'voiceJoinUrl returns a string'),
    openapi: { path: '/api/settings', method: 'get' },
  },

  // ══ TIER 3 — Prometheus (request PromQL + vector/scalar shape; no OpenAPI) ══
  {
    name: 'promScalar',
    invoke: (api) => api.promScalar('up'),
    request: { host: 'prometheus', query: 'up' },
    fixture: 'promScalar.json',
    shape: (out) => assert.ok(out === null || typeof out === 'number', 'scalar is number|null'),
  },
  {
    name: 'promVector',
    invoke: (api) => api.promVector('up'),
    request: { host: 'prometheus', query: 'up' },
    fixture: 'promVector.json',
    shape: (out) => {
      assert.ok(Array.isArray(out), 'vector is an array');
      out.forEach((s) => {
        assert.ok('labels' in s, 'each series has labels');
        assert.ok('value' in s, 'each series has a value');
      });
    },
  },
```

> **Executor note:** `listSettings`, `mediaQueue`, `schedule`, `memoryStats`, `pipelineEvents`, `voiceJoinUrl`, `promScalar`, `promVector` were confirmed as **reshaping** branches by reading `api.js` (they build a new object/array, not a bare `http(...)`), hence tier 3. `socialDrafts`, `seo`, `budget`, `brainActivity`, `posts`, `analyticsViews` were confirmed **pass-through** (bare `http(...)`), hence they stayed tier 1 in Task 3. If a shape assertion fails, the fixture recorded honest-empty (no rows) — the envelope assertions (`Array.isArray`, `typeof … number`) still hold; per-row assertions only run when the fixture has rows.

- [ ] **Step 2: Re-record so the tier-3 fixtures exist under their declared names**

The tier-3 rows now declare explicit `fixture:` filenames; re-run the recorder so those files are present (idempotent — reads only):

Run: `node src/cofounder_agent/console/js/__tests__/contracts/record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091`
Expected: writes `listSettings.json`, `mediaQueue.json`, `schedule.json`, `memoryStats.json`, `pipelineEvents.json`, `voiceJoinUrl.json`, `promScalar.json`, `promVector.json`; exits 1 (new files = drift) on this run.

- [ ] **Step 3: Run the contract net against the recorded fixtures**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: PASS — now 38 `contract: …` subtests (16 tier-1 + 14 tier-2 + 8 tier-3), `# fail 0`. The tier-3 rows now exercise the runner's `shape` + `openapi` branches (the schema layer validates each recorded fixture against `openapi.snapshot.json`).

- [ ] **Step 4: Prove the shape assertion bites**

Temporarily corrupt the `mediaQueue` shape — change `assert.equal(typeof out.gate2Pending, 'number', …)` to `assert.equal(typeof out.gate2Pending, 'string', …)` — then run:

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/run-contracts.test.js`
Expected: FAIL — `[mediaQueue]` / `gate2Pending is a number` AssertionError. **Restore** the row and re-run → PASS.

- [ ] **Step 5: Commit**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/contracts.manifest.js \
        src/cofounder_agent/console/js/__tests__/contracts/fixtures
git commit -m "test(console): contract manifest tier-3 transform + Prometheus rows"
```

---

## Task 7: Bespoke composite tests — `serviceHealth` + `gpu`

**Files:**

- Create: `src/cofounder_agent/console/js/__tests__/contracts/serviceHealth.contract.test.js`

**Interfaces:**

- Consumes: `loadApiWithRecorder` + `requestMatches` (Task 1). These two surfaces fan out to many calls (`serviceHealth`: 4 cAdvisor Prometheus queries + `GET /api/health`; `gpu`: 8 `nvidia_gpu_*` Prometheus scalars) and merge over `mock().services` / `mock().gpu` display scaffolding — a manifest row would obscure more than it documents (the spec's explicit "keep bespoke" case). The bespoke test pins the exact outgoing PromQL/paths and asserts the merge does not throw on honest-empty Prometheus.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/contracts/serviceHealth.contract.test.js`:

```js
'use strict';
// Bespoke contract tests for the two composite PX.api surfaces. serviceHealth
// and gpu fan out to many cAdvisor / nvidia_gpu Prometheus queries and merge
// over display scaffolding, so their contract is the SET of outgoing queries
// (a typo'd metric name silently breaks the live panel) rather than a single
// request row. Prometheus is replayed honest-empty ([] data), which is a real
// prod state (exporter down) and must not throw.
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadApiWithRecorder,
  requestMatches,
} = require('./contract-runtime.js');

// Replay every Prometheus instant-query as an empty result set, /api/health OK.
const emptyProm = () => ({ status: 200, payload: { data: { result: [] } } });

function promQueries(calls) {
  return calls
    .filter((c) => c.url.includes('/api/v1/query'))
    .map((c) => new URL(c.url).searchParams.get('query'));
}

test('serviceHealth issues the 4 cAdvisor queries + /api/health, no throw on empty', async () => {
  const { api, calls } = loadApiWithRecorder(emptyProm);
  const out = await api.serviceHealth();
  const q = promQueries(calls);
  const sel = '{name=~"poindexter.+"}';
  assert.ok(
    q.includes('time() - container_last_seen' + sel),
    'container_last_seen age query'
  );
  assert.ok(
    q.includes('rate(container_cpu_usage_seconds_total' + sel + '[1m]) * 100'),
    'cpu rate query'
  );
  assert.ok(q.includes('container_memory_usage_bytes' + sel), 'memory query');
  assert.ok(
    q.includes('time() - container_start_time_seconds' + sel),
    'uptime query'
  );
  assert.ok(
    calls.some((c) =>
      requestMatches(c, { method: 'GET', path: '/api/health' })
    ),
    'worker /api/health overlay'
  );
  // Merge over mock().services must not throw; host rows render neutral.
  assert.ok(Array.isArray(out), 'serviceHealth returns an array of rows');
});

test('gpu issues the 8 nvidia_gpu_* scalar queries, no throw on empty', async () => {
  const { api, calls } = loadApiWithRecorder(emptyProm);
  const out = await api.gpu();
  const q = promQueries(calls);
  [
    'nvidia_gpu_utilization_percent',
    'nvidia_gpu_temperature_celsius',
    'nvidia_gpu_power_draw_watts',
    'nvidia_gpu_power_limit_watts',
    'nvidia_gpu_memory_used_mib',
    'nvidia_gpu_memory_total_mib',
    'nvidia_gpu_fan_speed_percent',
    'nvidia_gpu_clock_graphics_mhz',
  ].forEach((metric) => assert.ok(q.includes(metric), `gpu queries ${metric}`));
  assert.equal(
    typeof out.util,
    'number',
    'gpu util falls back to a number on empty'
  );
  assert.ok(
    Array.isArray(out.procs),
    'gpu procs is an array (empty, not fabricated)'
  );
});
```

- [ ] **Step 2: Run test to verify it passes**

Run: `node --test src/cofounder_agent/console/js/__tests__/contracts/serviceHealth.contract.test.js`
Expected: PASS — `# pass 2`, `# fail 0`. (These assert real behavior of the current `api.js`, so they pass immediately; the value is regression-locking the exact query set — a later metric rename fails here.)

- [ ] **Step 3: Prove a query typo bites**

Temporarily change one asserted metric (e.g. `nvidia_gpu_temperature_celsius` → `nvidia_gpu_temp_celsius`) and run:
Expected: FAIL — `gpu queries nvidia_gpu_temp_celsius`. **Restore** and re-run → PASS.

- [ ] **Step 4: Commit**

```bash
npm run format
git add src/cofounder_agent/console/js/__tests__/contracts/serviceHealth.contract.test.js
git commit -m "test(console): bespoke contract tests for serviceHealth + gpu composites"
```

---

## Task 8: Nightly drift workflow + README

**Files:**

- Create: `.github/workflows/console-contract-drift.yml`
- Create: `src/cofounder_agent/console/js/__tests__/contracts/README.md`

**Interfaces:**

- Consumes: `record-fixtures.mjs` (Task 5) + the contract net (`npm run test:console`). Runs on the self-hosted runner (reaches `localhost:8002`), authenticates with the read-only `console-contract-drift` OAuth client via GitHub Actions secrets.

**Prerequisite (operator action — repo secrets on `Glad-Labs/glad-labs-stack`):** add `CONSOLE_CONTRACT_CLIENT_ID`, `CONSOLE_CONTRACT_CLIENT_SECRET` (the client from Task 5), `DISCORD_OPS_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. These can't be set from code; document them in the README and list them here.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/console-contract-drift.yml`:

```yaml
name: console-contract-drift

# Nightly (+ manual) live-backend drift check for the console contract net.
# Plain Node + git + gh — NO LLM, no Anthropic-API cost. Never a PR gate.
on:
  schedule:
    - cron: '0 8 * * *' # 08:00 UTC nightly (~00:00–03:00 US)
  workflow_dispatch:
    inputs:
      base:
        description: Worker base URL (self-hosted → localhost; container → host.docker.internal)
        default: http://localhost:8002
      prometheus:
        description: Prometheus base URL
        default: http://localhost:9091

concurrency:
  group: console-contract-drift
  cancel-in-progress: false

jobs:
  drift:
    runs-on: [self-hosted]
    permissions:
      contents: write
      pull-requests: write
    env:
      BASE: ${{ github.event.inputs.base || 'http://localhost:8002' }}
      PROM: ${{ github.event.inputs.prometheus || 'http://localhost:9091' }}
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      CONTRACTS: src/cofounder_agent/console/js/__tests__/contracts
    steps:
      # Pin the action to a commit SHA (repo lints third-party actions for SHA pins).
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          fetch-depth: 0

      - name: Record fixtures + snapshot (drift = exit 1)
        id: record
        continue-on-error: true
        env:
          CONSOLE_CONTRACT_CLIENT_ID: ${{ secrets.CONSOLE_CONTRACT_CLIENT_ID }}
          CONSOLE_CONTRACT_CLIENT_SECRET: ${{ secrets.CONSOLE_CONTRACT_CLIENT_SECRET }}
        run: node "$CONTRACTS/record-fixtures.mjs" --base "$BASE" --prometheus "$PROM"

      - name: No drift — done
        if: steps.record.outcome == 'success'
        run: echo "No console contract drift."

      # ── drift path (record exited 1) ──
      - name: Run contract tests against fresh fixtures
        if: steps.record.outcome == 'failure'
        id: verify
        continue-on-error: true
        run: npm run test:console

      - name: Open / update refresh PR
        if: steps.record.outcome == 'failure'
        id: pr
        run: |
          set -e
          BR="chore/console-contract-refresh"
          git config user.name "glad-labs-bot"
          git config user.email "bot@gladlabs.io"
          git checkout -B "$BR"
          git add "$CONTRACTS"
          git commit -m "chore(console): refresh contract fixtures + OpenAPI snapshot"
          git push -f -u origin "$BR"
          gh pr create --base main --head "$BR" \
            --title "chore(console): refresh contract fixtures" \
            --body "Automated nightly refresh of console contract fixtures + OpenAPI snapshot. Contract tests: ${{ steps.verify.outcome }}." \
            2>/dev/null || echo "PR already open"
          echo "branch=$BR" >> "$GITHUB_OUTPUT"

      - name: Benign drift → auto-merge + Discord
        if: steps.record.outcome == 'failure' && steps.verify.outcome == 'success'
        run: |
          gh pr merge --auto --squash "${{ steps.pr.outputs.branch }}"
          curl -fsS -X POST "${{ secrets.DISCORD_OPS_WEBHOOK_URL }}" \
            -H 'Content-Type: application/json' \
            -d '{"content":"🟢 Console contract fixtures refreshed (benign backend drift) — PR auto-merging once checks pass."}'

      - name: Breaking drift → hold + Telegram
        if: steps.record.outcome == 'failure' && steps.verify.outcome == 'failure'
        run: |
          curl -fsS -X POST \
            "https://api.telegram.org/bot${{ secrets.TELEGRAM_BOT_TOKEN }}/sendMessage" \
            -d "chat_id=${{ secrets.TELEGRAM_CHAT_ID }}" \
            --data-urlencode "text=🔴 Console contract BROKEN: backend changed a shape the console still assumes. Refresh PR chore/console-contract-refresh is held (red). An adapter in api.js needs updating."
```

- [ ] **Step 2: Lint the workflow YAML**

Run: `node -e "const y=require('fs').readFileSync('.github/workflows/console-contract-drift.yml','utf8'); const bad=y.split('\n').findIndex(l=>l.includes('\t')); if(bad>=0){console.error('tab at line '+(bad+1));process.exit(1)} console.log('no tabs; '+y.split('\n').length+' lines')"`
Expected: `no tabs; … lines` (YAML forbids tabs; catches the most common hand-edit break without a yaml dep). If `actionlint` is available on the runner, also run `actionlint .github/workflows/console-contract-drift.yml`.

- [ ] **Step 3: Write the README**

Create `src/cofounder_agent/console/js/__tests__/contracts/README.md`:

````markdown
# Console live-contract test net

Pins every `PX.api` network surface (`console/js/api.js`) so a broken live
branch fails a test before it ships. Closes the **mock-first live-branch drift**
class that caused 6 of the 8 bugs the 2026-07-04 console audit found.

## What runs when

- **Per PR (offline, deterministic):** `run-contracts.test.js` +
  `serviceHealth.contract.test.js` are auto-discovered by `npm run test:console`
  (the `__tests__/**/*.test.js` glob = the `console-unit` CI job). They replay
  the committed `fixtures/*.json` — **no worker, no network**.
- **Nightly (live backend):** `.github/workflows/console-contract-drift.yml` on
  the self-hosted runner re-records against the live worker. No drift → silent.
  Drift → opens `chore(console): refresh contract fixtures`; **green auto-merges**
  (Discord ping), **red holds** for a human (Telegram ping). No LLM / API cost.

## The manifest

`contracts.manifest.js` is the one source of truth — an array of surface rows,
also the executable endpoint map. Tiers fall out of which keys a row carries:

| Tier                | Keys                                         | Asserts                                                              |
| ------------------- | -------------------------------------------- | -------------------------------------------------------------------- |
| 1 read pass-through | `request` (+`openapi`)                       | method + path + query                                                |
| 2 write             | `request.body`                               | method + path + **body** (never recorded — a POST would mutate prod) |
| 3 read transform    | `request` + `fixture` + `shape` (+`openapi`) | + adapter output shape + schema                                      |

Set a surface's tier by reading its `live:` branch: bare `http(...)` → tier 1;
reshapes the response → tier 3; POST/PUT/PATCH/DELETE → tier 2. Composite
surfaces that fan out to many calls (`serviceHealth`, `gpu`) live in
`serviceHealth.contract.test.js`, not the manifest.

## Refreshing fixtures

```bash
node record-fixtures.mjs --base http://localhost:8002 --prometheus http://localhost:9091
# creds: --client-id/--client-secret or CONSOLE_CONTRACT_CLIENT_ID/_SECRET
```
````

Records read fixtures + prunes `/openapi.json` → `openapi.snapshot.json`, prints
the drift diff, exits **0 = no drift / 1 = drift**. Review the git diff, fix any
adapter a real backend change broke, commit. Provision the read-only client with:

```bash
poindexter auth register-client --name console-contract-drift --scopes "api:read" --grant-type client_credentials
```

## Nightly job secrets (on `Glad-Labs/glad-labs-stack`)

`CONSOLE_CONTRACT_CLIENT_ID`, `CONSOLE_CONTRACT_CLIENT_SECRET` (read-only OAuth
client), `DISCORD_OPS_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Honest limits

- **Empty-in-prod** endpoints record an honest-empty fixture; row-level shape
  checks only fire when data exists. Rows are never fabricated (no-dummy-data).
- **PR CI is offline** — it validates fixtures against the _committed_ snapshot.
  Live-backend drift is caught by the nightly job, which keeps the snapshot honest.
- **Zero runtime dependency** — the schema validator is hand-rolled; `console-unit`
  stays a plain `node --test`. `ajv` may drop in behind `validateAgainstSchema`
  only if full JSON-Schema semantics are ever needed.

````

- [ ] **Step 4: Final full-suite verification**

Run: `npm run test:console`
Expected: PASS — the existing 38 tests + 40 `contract: …` subtests + 2 composite bespoke tests, `# fail 0`.

- [ ] **Step 5: Commit**

```bash
npm run format
git add .github/workflows/console-contract-drift.yml \
        src/cofounder_agent/console/js/__tests__/contracts/README.md
git commit -m "ci(console): nightly contract-drift workflow + contract-net README"
````

---

## Self-Review (run before execution)

**Spec coverage** — every spec section maps to a task:

- Layered purpose (offline guard + OpenAPI drift) → Tasks 3–4 (offline request net) + Task 2/6 (schema check) + Task 8 (nightly drift). ✅
- All 36 surfaces, tiered → Tasks 3 (16 tier-1) + 4 (14 tier-2) + 6 (8 tier-3) + 7 (2 composites) = **40 network surfaces** (superset of the spec's ~36; `health` + `gpu` additionally covered per AC1 "every network surface"). ✅
- Manifest-driven → Task 3 `contracts.manifest.js` feeds runner (3), recorder (5), validator (6). ✅
- Recorded fixtures, never fabricated → Task 5 recorder + Step 3 sanity check + honest-empty handling in Task 6 note. ✅
- Components table (7 files + workflow) → all created: `contracts.manifest.js` (T3), `contract-runtime.js` (T1–2), `run-contracts.test.js` (T3), `fixtures/*.json` (T5), `openapi.snapshot.json` (T5), `record-fixtures.mjs` (T5), workflow (T8), `README.md` (T8). ✅
- Manifest row model (4 kinds) → tier-1 (T3), tier-2 write with body (T4), tier-3 transform (T6), Prometheus (T6); composite `request` array handled by the runner + used by the bespoke test (T7). ✅
- Offline data flow (6 steps) → encoded in `run-contracts.test.js` (T3): recorder fetch answers `/token`, records non-token calls, replays fixture; request/shape/schema assertions; `[name]`-prefixed messages. ✅
- Recording ritual → Task 5 `record-fixtures.mjs` (reads only, prunes snapshot, drift diff, exit 0/1). ✅
- Dep-free schema validator (~50–60 lines, object/array/primitive/null, extra-fields-allowed, no-op when no schema) → Task 2. ✅
- CI wiring (per-PR none; one new scheduled workflow) → confirmed glob auto-discovery (T3 Step 5); workflow is schedule/dispatch only (T8). ✅
- Known limits (empty-in-prod, offline PR CI) → README (T8) + Task 6 executor note. ✅
- Automated drift detection (record → no-drift silent / drift → PR → green auto-merge+Discord / red hold+Telegram) → Task 8 workflow steps. ✅
- All 9 acceptance criteria → AC1 (T3–7), AC2 (T3 Step 5), AC3 (T4 Step 3), AC4 (T6), AC5 (T5), AC6 (Global Constraints + Task-2 hand-rolled validator), AC7 (T5 Step 3), AC8 (T5 Steps 2/5), AC9 (T8). ✅

**Placeholder scan:** No TBD/TODO/"similar to"/"add error handling" — every code step carries complete code; every manifest row carries a real method/path/body transcribed from `api.js`. The two executor notes point at the source-of-truth branch for tier verification (a real process, not a placeholder), and are backed by the TDD run steps that would catch any transcription error.

**Type consistency:** `loadApiWithFetch`/`loadApiWithRecorder`/`assertRequest`/`requestMatches`/`validateAgainstSchema`/`jsonResponse` are defined in Task 1–2 and consumed with identical signatures in Tasks 3/5/7. Row fields (`name`/`invoke`/`request`/`fixture`/`shape`/`openapi`) are consistent across Tasks 3/4/6 and the runner. `request` single-vs-array handling in the runner (T3) matches the composite usage. Fixture-filename convention (`row.fixture || <name>.json`) is identical in the runner (T3) and recorder (T5).

---

## Notes carried from the parent sub-project (harness gotchas)

- `preview_*` browser tools were unreliable in the sub-project-A session; this plan needs **no browser** (pure Node), so that risk does not apply here.
- If a future step ever needs browser verification of the console, use a `ThreadingHTTPServer` no-cache static server + Playwright (single-threaded `http.server` wedges on a held browser connection). Not needed for B.
