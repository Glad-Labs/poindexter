# Console Reliability & Trust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operator console trustworthy as a primary UI — no hanging fetches, no silent failures, a visible global "backend unreachable" banner, and per-panel freshness — by centralizing polling into one tested hook.

**Architecture:** A new plain-JS module `js/reliability.js` holds pure logic (`computeStale`, `resourceReducer`, `connectionReducer`), a `window`-attached `ConnectionState` pub/sub store, and the `usePolledResource` React hook. `api.js`'s `http()` gains a timeout + `AbortController`; its two Prometheus helpers stop throwing. `app.jsx`'s ~17 hand-rolled poll effects migrate onto the hook; a `<ConnectionBanner>` and per-panel `<Freshness>` badge (both in `primitives.jsx`) render the connection/staleness state.

**Tech Stack:** React 18 (in-browser Babel, no build step), plain ES modules attached to `window`, Node's built-in `node:test` + `node:vm` for unit tests (no jsdom), Playwright for browser verification.

## Status (2026-07-05)

**Tasks 1–8 shipped and verified** on branch `docs/console-reliability-spec` (the "connection-level trust" foundation): `computeStale` / `resourceReducer` / `connectionReducer` / `ConnectionState` (10 Node tests), `http()` 8s timeout + `AbortController`, guarded + exposed Prometheus helpers, `usePolledResource`, `<ConnectionBanner>` + `<Freshness>` + `agoLabel`, and the `/api/health` heartbeat driving the banner + real topbar SYNC / Wall indicators. Full console suite: **36/36 green**; banner/heartbeat/freshness browser-verified end-to-end.

**Task 9 shipped (2026-07-06)** on branch `feat/console-per-panel-freshness` as the focused follow-up. Ten clean/merge/optimistic poll surfaces migrated onto `usePolledResource` with a per-panel `<Freshness>` badge — **serviceHealth, budget→cost, logs** (Chunk 1); **findings, seo, newsletter, traces** (Chunk 2a); **media, schedule, topics** (Chunk 2b). The hook gained a `mutate(updater)` optimistic-patch action (patches `data` without moving `lastUpdatedAt`, so the badge stays honest through an optimistic edit); the 14 optimistic drawer setters (services/media/schedule/topics) route through it. The six genuinely-bespoke effects (**feed, approvals, tasks, brain-pair, social, kpiExtras**) are left hand-rolled with inline "Task 9 exception" notes (Chunk 3) per the spec's "explicit, listed exceptions" clause. Per-poll error toasts on migrated panels were dropped for the persistent badge + global banner (feedback_no_popups / surface-once). Full console suite **38/38 green**; browser-verified via Playwright (10 badges, optimistic restart/discard hold the badge timestamp, no false-positive stale, 0 console errors). The REVISED taxonomy note on Task 9 below drove the chunking.

## Global Constraints

- **No build step.** Files are served as-is; JSX compiles in-browser via vendored Babel. A syntax error bricks the page — Prettier's parse is the only static gate, so every `.js`/`.jsx` edit must stay Prettier-clean.
- **`window`-global module style.** Non-JSX modules attach exports to `window` AND `module.exports` (dual-mode, mirroring `js/kpis.js:193-200`) so Node tests can `require()` the pure parts. JSX components attach to `window` via `Object.assign(window, {...})` (mirroring `js/primitives.jsx:328`).
- **Script load order is fixed** (`index.html`): plain-JS files load before the `type="text/babel"` JSX files. `reliability.js` loads in the plain-JS block, after `api.js`.
- **Fetch timeout: 8000 ms.** Stale threshold: `> 2 × intervalMs` or any error. Disconnected: `≥ 3` consecutive `/api/health` failures; cleared on first success. Poll cadences unchanged.
- **Colorblind-safe:** freshness/connection state is conveyed by text + icon + opacity, never color alone.
- **Tests:** `npm run test:console` (Node) must stay green; `npx prettier --check` clean. Commit after each task.

---

### Task 1: `computeStale` pure function + module scaffold

**Files:**

- Create: `src/cofounder_agent/console/js/reliability.js`
- Test: `src/cofounder_agent/console/js/__tests__/reliability.test.js`

**Interfaces:**

- Produces: `computeStale(lastUpdatedAt: number|null, intervalMs: number, error: any, now: number) → boolean`. Exported on `window.PXR` (browser) and `module.exports` (Node).

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/reliability.test.js`:

```js
'use strict';

// Unit tests for the console reliability core (js/reliability.js) — pure logic
// only (computeStale / resourceReducer / connectionReducer / ConnectionState).
// The React hook usePolledResource is browser-verified, not tested here.
const test = require('node:test');
const assert = require('node:assert/strict');
const { computeStale } = require('../reliability.js');

test('computeStale: fresh data within 2x interval is not stale', () => {
  const now = 100_000;
  assert.equal(computeStale(now - 5_000, 5_000, null, now), false);
});

test('computeStale: data older than 2x interval is stale', () => {
  const now = 100_000;
  assert.equal(computeStale(now - 11_000, 5_000, null, now), true);
});

test('computeStale: an error forces stale regardless of age', () => {
  const now = 100_000;
  assert.equal(computeStale(now, 5_000, new Error('boom'), now), true);
});

test('computeStale: never-loaded (null lastUpdatedAt) is stale', () => {
  assert.equal(computeStale(null, 5_000, null, 100_000), true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: FAIL — `Cannot find module '../reliability.js'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/cofounder_agent/console/js/reliability.js`:

```js
/* ──────────────────────────────────────────────────────────────
   Poindexter Operator Console — reliability core.
   Pure logic (computeStale / resourceReducer / connectionReducer),
   the ConnectionState store, and the usePolledResource hook.
   Dual-mode: window globals (browser) + module.exports (Node tests).
   ────────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  // A resource is stale when its last fetch errored, it has never loaded, or
  // its data is older than twice its own poll interval.
  function computeStale(lastUpdatedAt, intervalMs, error, now) {
    if (error != null) return true;
    if (lastUpdatedAt == null) return true;
    return now - lastUpdatedAt > 2 * intervalMs;
  }

  const api = { computeStale };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof window !== 'undefined') {
    window.PXR = Object.assign(window.PXR || {}, api);
  }
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/reliability.js src/cofounder_agent/console/js/__tests__/reliability.test.js
git commit -m "feat(console): reliability core — computeStale + module scaffold"
```

---

### Task 2: `resourceReducer` (retain-on-error state machine)

**Files:**

- Modify: `src/cofounder_agent/console/js/reliability.js`
- Test: `src/cofounder_agent/console/js/__tests__/reliability.test.js`

**Interfaces:**

- Consumes: nothing.
- Produces: `resourceReducer(state, action) → state` where `state = { data, lastUpdatedAt, error }` and `action` is one of `{type:'success', data, at}` | `{type:'error', error}`. Initial state: `{ data: null, lastUpdatedAt: null, error: null }` exported as `RESOURCE_INIT`. Both on `window.PXR` + `module.exports`.

- [ ] **Step 1: Write the failing test**

Append to `js/__tests__/reliability.test.js`:

```js
const { resourceReducer, RESOURCE_INIT } = require('../reliability.js');

test('resourceReducer: success sets data + timestamp, clears error', () => {
  const s = resourceReducer(RESOURCE_INIT, {
    type: 'success',
    data: [1, 2],
    at: 42,
  });
  assert.deepEqual(s, { data: [1, 2], lastUpdatedAt: 42, error: null });
});

test('resourceReducer: error retains previous data, records error', () => {
  const prev = { data: [1, 2], lastUpdatedAt: 42, error: null };
  const s = resourceReducer(prev, { type: 'error', error: new Error('down') });
  assert.deepEqual(s.data, [1, 2]); // retained
  assert.equal(s.lastUpdatedAt, 42); // unchanged — data is still from t=42
  assert.equal(s.error.message, 'down');
});

test('resourceReducer: success after error clears the error', () => {
  const errored = { data: [1], lastUpdatedAt: 42, error: new Error('x') };
  const s = resourceReducer(errored, { type: 'success', data: [9], at: 99 });
  assert.deepEqual(s, { data: [9], lastUpdatedAt: 99, error: null });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: FAIL — `resourceReducer` is undefined.

- [ ] **Step 3: Write minimal implementation**

In `reliability.js`, add before the `const api = {...}` line:

```js
const RESOURCE_INIT = { data: null, lastUpdatedAt: null, error: null };

// On success, swap in fresh data + timestamp and clear any error. On error,
// KEEP the last good data (panels hold their last values) and record the
// error so the UI can mark the panel stale.
function resourceReducer(state, action) {
  switch (action.type) {
    case 'success':
      return { data: action.data, lastUpdatedAt: action.at, error: null };
    case 'error':
      return { ...state, error: action.error };
    default:
      return state;
  }
}
```

Then extend the exports object:

```js
const api = { computeStale, resourceReducer, RESOURCE_INIT };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: PASS (7 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/reliability.js src/cofounder_agent/console/js/__tests__/reliability.test.js
git commit -m "feat(console): reliability — resourceReducer retain-on-error state machine"
```

---

### Task 3: `connectionReducer` + `ConnectionState` store

**Files:**

- Modify: `src/cofounder_agent/console/js/reliability.js`
- Test: `src/cofounder_agent/console/js/__tests__/reliability.test.js`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `connectionReducer(state, event) → state`, `state = { consecutiveHealthFailures, lastSeenAt }`, `event` = `{type:'health-ok', at}` | `{type:'health-fail'}`. `CONNECTION_INIT = { consecutiveHealthFailures: 0, lastSeenAt: null }`.
  - `isDisconnected(state) → boolean` (`consecutiveHealthFailures >= 3`).
  - `ConnectionState`: `{ reportHealth(ok: boolean, at?: number), getState() → state, subscribe(fn) → unsubscribe }`. On `window.PXR`.

- [ ] **Step 1: Write the failing test**

Append to `js/__tests__/reliability.test.js`:

```js
const {
  connectionReducer,
  CONNECTION_INIT,
  isDisconnected,
  ConnectionState,
} = require('../reliability.js');

test('connectionReducer: 3 consecutive failures → disconnected', () => {
  let s = CONNECTION_INIT;
  s = connectionReducer(s, { type: 'health-fail' });
  s = connectionReducer(s, { type: 'health-fail' });
  assert.equal(isDisconnected(s), false); // only 2
  s = connectionReducer(s, { type: 'health-fail' });
  assert.equal(isDisconnected(s), true); // 3
});

test('connectionReducer: a health-ok resets the failure counter and stamps lastSeen', () => {
  let s = { consecutiveHealthFailures: 5, lastSeenAt: 1 };
  s = connectionReducer(s, { type: 'health-ok', at: 777 });
  assert.equal(s.consecutiveHealthFailures, 0);
  assert.equal(s.lastSeenAt, 777);
  assert.equal(isDisconnected(s), false);
});

test('ConnectionState store: reportHealth drives getState + notifies subscribers', () => {
  const seen = [];
  const unsub = ConnectionState.subscribe((st) =>
    seen.push(isDisconnected(st))
  );
  ConnectionState.reportHealth(false);
  ConnectionState.reportHealth(false);
  ConnectionState.reportHealth(false);
  assert.equal(isDisconnected(ConnectionState.getState()), true);
  ConnectionState.reportHealth(true, 123);
  assert.equal(isDisconnected(ConnectionState.getState()), false);
  assert.equal(seen[seen.length - 1], false);
  unsub();
  ConnectionState.reportHealth(false); // no throw after unsubscribe
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: FAIL — `connectionReducer` / `ConnectionState` undefined.

- [ ] **Step 3: Write minimal implementation**

In `reliability.js`, add before the exports object:

```js
const CONNECTION_INIT = { consecutiveHealthFailures: 0, lastSeenAt: null };

function connectionReducer(state, event) {
  switch (event.type) {
    case 'health-ok':
      return { consecutiveHealthFailures: 0, lastSeenAt: event.at };
    case 'health-fail':
      return {
        ...state,
        consecutiveHealthFailures: state.consecutiveHealthFailures + 1,
      };
    default:
      return state;
  }
}

function isDisconnected(state) {
  return state.consecutiveHealthFailures >= 3;
}

// Module-level singleton: the health resource reports here; the banner + the
// topbar SYNC indicator subscribe. Keyed on a single connection signal.
const ConnectionState = (function () {
  let state = CONNECTION_INIT;
  const subs = new Set();
  return {
    reportHealth(ok, at) {
      state = connectionReducer(
        state,
        ok
          ? { type: 'health-ok', at: at ?? Date.now() }
          : { type: 'health-fail' }
      );
      subs.forEach((fn) => fn(state));
    },
    getState() {
      return state;
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
})();
```

Extend the exports object:

```js
const api = {
  computeStale,
  resourceReducer,
  RESOURCE_INIT,
  connectionReducer,
  CONNECTION_INIT,
  isDisconnected,
  ConnectionState,
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/reliability.js src/cofounder_agent/console/js/__tests__/reliability.test.js
git commit -m "feat(console): reliability — connection reducer + ConnectionState store"
```

---

### Task 4: `http()` timeout + AbortController

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js:142-163` (the `http` function)
- Test: `src/cofounder_agent/console/js/__tests__/api.http.test.js`

**Interfaces:**

- Consumes: nothing new.
- Produces: `http()` behavior — a request that exceeds 8000 ms is aborted and rejects with `Error` message containing `timed out`. Adds a module-level `const HTTP_TIMEOUT_MS = 8000;` inside the api.js IIFE.

- [ ] **Step 1: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/api.http.test.js`:

```js
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/api.http.test.js`
Expected: FAIL — the promise never rejects (times out the test runner) or rejects without "timed out", because `http()` doesn't abort yet.

- [ ] **Step 3: Write minimal implementation**

In `api.js`, add a constant near the top of the IIFE (just after `const cfg = {...};`, around line 77):

```js
// Client-side request ceiling. A hung backend rejects here instead of leaving
// a panel's poll pending forever (the poll cadence is the retry).
const HTTP_TIMEOUT_MS = 8000;
```

Replace the `doFetch` inner function in `http()` (lines 144-154) with:

```js
const doFetch = async () => {
  const tok = await getToken();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), HTTP_TIMEOUT_MS);
  try {
    return await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + tok,
      },
      signal: ctrl.signal,
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  } catch (e) {
    if (e && e.name === 'AbortError')
      throw new Error(
        `${method} ${path} → timed out after ${HTTP_TIMEOUT_MS}ms`
      );
    throw e;
  } finally {
    clearTimeout(timer);
  }
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/api.http.test.js`
Expected: PASS. Also run `node --test js/__tests__/api.token.test.js` — still PASS (no regression to the 401/429 paths).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.http.test.js
git commit -m "feat(console): http() request timeout via AbortController"
```

---

### Task 5: Guard + expose the Prometheus helpers

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js:166-185` (`promScalar`, `promVector`) + the `PX.api = {…}` object literal (line 398)
- Test: `src/cofounder_agent/console/js/__tests__/api.prom.test.js`

**Interfaces:**

- Consumes: nothing.
- Produces: `promScalar(promql)` returns `null` (never throws) when its fetch fails; `promVector(promql)` returns `[]`. Both are now exposed on `PX.api` (`PX.api.promScalar` / `PX.api.promVector`) — sub-projects C (time-series) and D (hardware/GPU/power) consume them for native Prometheus panels.

Why expose them: every _current_ caller already wraps these in a call-site `.catch()` (e.g. `gpu()` at api.js:1093-1100, the container-health method at api.js:951-963), so a test routed through a caller would pass **before** the fix — an invalid fail-first. Testing the helpers directly gives a true red→green, and exposing them removes the per-caller `.catch()` footgun for the C/D panels to come.

- [ ] **Step 1: Expose the helpers on `PX.api` (setup — no behavior change yet)**

In `api.js`, in the `PX.api = {` object literal (starts line 398), add two entries in the config group (just after `config: cfg,` at line 400):

```js
    // Prometheus instant-query helpers (reused by native hardware/DB panels).
    promScalar,
    promVector,
```

They're in IIFE scope from their definitions at api.js:166/177, so object-shorthand works. They still throw internally at this point — the guard lands in Step 4.

- [ ] **Step 2: Write the failing test**

Create `src/cofounder_agent/console/js/__tests__/api.prom.test.js`:

```js
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
  assert.deepEqual(await api.promVector('up'), []);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/api.prom.test.js`
Expected: FAIL — both tests reject with `ECONNREFUSED` because the helpers don't guard the fetch yet.

- [ ] **Step 4: Write minimal implementation**

In `api.js`, wrap `promScalar` (lines 166-172) body:

```js
async function promScalar(promql) {
  try {
    const u =
      cfg.prometheus + '/api/v1/query?query=' + encodeURIComponent(promql);
    const j = await (await fetch(u)).json();
    const v = j?.data?.result?.[0]?.value?.[1];
    return v != null ? Number(v) : null;
  } catch {
    return null; // Prometheus unreachable → honest-empty, never throw.
  }
}
```

And `promVector` (lines 177-185):

```js
async function promVector(promql) {
  try {
    const u =
      cfg.prometheus + '/api/v1/query?query=' + encodeURIComponent(promql);
    const j = await (await fetch(u)).json();
    return (j?.data?.result || []).map((r) => ({
      labels: r.metric || {},
      value: r.value ? Number(r.value[1]) : null,
    }));
  } catch {
    return []; // Prometheus unreachable → honest-empty, never throw.
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/api.prom.test.js`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/api.js src/cofounder_agent/console/js/__tests__/api.prom.test.js
git commit -m "feat(console): Prometheus helpers degrade to honest-empty + expose on PX.api"
```

---

### Task 6: `usePolledResource` hook

**Files:**

- Modify: `src/cofounder_agent/console/js/reliability.js`
- Modify: `src/cofounder_agent/console/index.html:39` (load `reliability.js` after `kpis.js`)
- Verify: browser (Playwright)

**Interfaces:**

- Consumes: `React` (global), `resourceReducer`, `RESOURCE_INIT`, `computeStale`, `ConnectionState`.
- Produces: `usePolledResource(fetchFn, { intervalMs, key }) → { data, lastUpdatedAt, error, stale }`. On `window.PXR`. `fetchFn` is any `() => Promise`. When `key === 'health'`, each outcome calls `ConnectionState.reportHealth(ok)`.

- [ ] **Step 1: Add the hook implementation**

In `reliability.js`, add before the exports object (the hook needs `React`, which `index.html` loads before `reliability.js`):

```js
// React polling hook. Owns the interval, per-cycle timeout/abort, retain-on-
// error state (via resourceReducer), and connection reporting. The fetchFn is
// provided by the caller (usually a PX.api method). Aborting on unmount / next
// cycle prevents a slow earlier response from clobbering a fresher one.
function usePolledResource(fetchFn, opts) {
  const intervalMs = opts.intervalMs;
  const key = opts.key;
  const React = window.React;
  const [state, dispatch] = React.useReducer(resourceReducer, RESOURCE_INIT);
  const fnRef = React.useRef(fetchFn);
  fnRef.current = fetchFn;

  React.useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const data = await fnRef.current();
        if (!alive) return;
        dispatch({ type: 'success', data, at: Date.now() });
        if (key === 'health') ConnectionState.reportHealth(true);
      } catch (e) {
        if (!alive) return;
        dispatch({ type: 'error', error: e });
        if (key === 'health') ConnectionState.reportHealth(false);
      }
    };
    run();
    const timer = setInterval(run, intervalMs);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [intervalMs, key]);

  const stale = computeStale(
    state.lastUpdatedAt,
    intervalMs,
    state.error,
    Date.now()
  );
  return {
    data: state.data,
    lastUpdatedAt: state.lastUpdatedAt,
    error: state.error,
    stale,
  };
}
```

Add `usePolledResource` to the exports object (browser only — it needs React, so guard the module-export path already excludes it by living only on `window.PXR`):

```js
const api = {
  computeStale,
  resourceReducer,
  RESOURCE_INIT,
  connectionReducer,
  CONNECTION_INIT,
  isDisconnected,
  ConnectionState,
};
// Hook is browser-only (needs React); attach directly, keep it out of
// module.exports so Node tests of the pure API don't pull React in.
if (typeof window !== 'undefined') {
  window.PXR = Object.assign(window.PXR || {}, api, { usePolledResource });
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
}
```

Remove the old dual-export block at the bottom of the file if it now duplicates this (there must be exactly one export block). The file ends with the single block above.

- [ ] **Step 2: Wire the script tag**

In `index.html`, add after line 39 (`<script src="js/kpis.js"></script>`):

```html
<!-- Reliability core (PXR.usePolledResource + ConnectionState) — before JSX. -->
<script src="js/reliability.js"></script>
```

- [ ] **Step 3: Run the pure-logic tests (no regression)**

Run: `cd src/cofounder_agent/console && node --test js/__tests__/reliability.test.js`
Expected: PASS (10 tests — the hook isn't tested here; the pure logic still is).

- [ ] **Step 4: Browser-verify the hook**

Serve and drive with Playwright (mirrors the Sparkline verification pattern):

```bash
cd src/cofounder_agent/console && python -m http.server 8899 --bind 127.0.0.1 &
```

Navigate to `http://127.0.0.1:8899/`, then evaluate:

```js
() =>
  new Promise((resolve) => {
    const R = window.React,
      RD = window.ReactDOM,
      H = window.PXR.usePolledResource;
    let calls = 0;
    function Probe() {
      const r = H(() => Promise.resolve(['ok', ++calls]), {
        intervalMs: 5000,
        key: 'probe',
      });
      return R.createElement(
        'span',
        { id: 'probe' },
        JSON.stringify({ data: r.data, stale: r.stale })
      );
    }
    const el = document.createElement('div');
    document.body.appendChild(el);
    RD.createRoot(el).render(R.createElement(Probe));
    setTimeout(
      () => resolve(document.getElementById('probe').textContent),
      200
    );
  });
```

Expected: `{"data":["ok",1],"stale":false}` — the hook ran the fetch once, populated data, and is not stale. Zero console errors. Stop the server.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/reliability.js src/cofounder_agent/console/index.html
git commit -m "feat(console): usePolledResource hook + wire reliability.js into index.html"
```

---

### Task 7: `<ConnectionBanner>` + `<Freshness>` components

**Files:**

- Modify: `src/cofounder_agent/console/js/primitives.jsx` (add both components + export them)
- Modify: `src/cofounder_agent/console/css/console.css` (banner + freshness styles)
- Verify: browser (Playwright)

**Interfaces:**

- Consumes: `window.PXR.ConnectionState`, `window.PXR.isDisconnected`, `Icon` (already in primitives).
- Produces (on `window`):
  - `ConnectionBanner()` — subscribes to `ConnectionState`; renders `null` when connected, else a fixed bar.
  - `Freshness({ lastUpdatedAt, stale })` — a small badge: `updated {ago}` / `stale {ago}`.
  - `agoLabel(ms)` helper → `"just now"` | `"8s"` | `"3m"` | `"2h"`.

- [ ] **Step 1: Add the components**

In `primitives.jsx`, before the `Object.assign(window, {...})` block (line 328), add:

```jsx
/* ─── Relative-age label ────────────────────────────────────── */
function agoLabel(ms) {
  if (ms == null) return '—';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

/* ─── Per-panel freshness badge (colorblind-safe) ───────────── */
function Freshness({ lastUpdatedAt, stale }) {
  const [, tick] = useState(0);
  // Re-render every 5s so "8s" ages up even without new data.
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 5000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className={`freshness ${stale ? 'freshness--stale' : ''}`}>
      {stale && <Icon name="alert" size={10} />}
      {stale ? 'stale ' : 'updated '}
      {agoLabel(lastUpdatedAt)}
    </span>
  );
}

/* ─── Global connection banner ──────────────────────────────── */
function ConnectionBanner() {
  const [st, setSt] = useState(window.PXR.ConnectionState.getState());
  useEffect(() => window.PXR.ConnectionState.subscribe(setSt), []);
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 5000);
    return () => clearInterval(t);
  }, []);
  if (!window.PXR.isDisconnected(st)) return null;
  return (
    <div className="connbanner" role="status">
      <Icon name="alert" size={13} />
      <span>
        Backend unreachable — reconnecting… (last seen {agoLabel(st.lastSeenAt)}
        )
      </span>
    </div>
  );
}
```

Add all three to the exports:

```js
Object.assign(window, {
  Icon,
  Sparkline,
  // …existing…
  agoLabel,
  Freshness,
  ConnectionBanner,
```

- [ ] **Step 2: Add styles**

Append to `src/cofounder_agent/console/css/console.css`:

```css
/* Global connection banner — fixed, non-blocking, high z-index. Amber, not
   red-only: colorblind-safe via icon + text. */
.connbanner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 6px 12px;
  font-family: var(--gl-font-mono);
  font-size: 12px;
  background: var(--gl-amber);
  color: var(--gl-on-amber, #1a1400);
  border-bottom: 1px solid var(--gl-hairline-strong);
}
/* Per-panel freshness badge. Stale = dim + italic + icon, never color alone. */
.freshness {
  font-family: var(--gl-font-mono);
  font-size: 10px;
  color: var(--gl-text-dim);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.freshness--stale {
  color: var(--gl-amber);
  font-style: italic;
  opacity: 0.85;
}
```

- [ ] **Step 3: Browser-verify both components**

Serve (`python -m http.server 8899`), navigate, then evaluate:

```js
() => {
  const R = window.React,
    RD = window.ReactDOM;
  const el = document.createElement('div');
  document.body.appendChild(el);
  RD.createRoot(el).render(
    R.createElement(
      'div',
      null,
      R.createElement(window.ConnectionBanner),
      R.createElement(window.Freshness, {
        lastUpdatedAt: Date.now() - 45000,
        stale: true,
      })
    )
  );
  return new Promise((res) =>
    setTimeout(() => {
      // Force 3 health failures → banner should appear.
      window.PXR.ConnectionState.reportHealth(false);
      window.PXR.ConnectionState.reportHealth(false);
      window.PXR.ConnectionState.reportHealth(false);
      setTimeout(
        () =>
          res({
            bannerText:
              (document.querySelector('.connbanner') || {}).textContent || null,
            freshnessText:
              (document.querySelector('.freshness') || {}).textContent || null,
            freshnessStale: !!document.querySelector('.freshness--stale'),
          }),
        100
      );
    }, 100)
  );
};
```

Expected: `bannerText` contains "Backend unreachable"; `freshnessText` contains "stale 45s"; `freshnessStale` is `true`. Zero console errors. Reset with `ConnectionState.reportHealth(true)` and confirm the banner disappears. Stop the server.

- [ ] **Step 4: Prettier + commit**

Run: `cd ../../.. && npx prettier --check src/cofounder_agent/console/js/primitives.jsx src/cofounder_agent/console/css/console.css` (fix with `--write` if needed).

```bash
git add src/cofounder_agent/console/js/primitives.jsx src/cofounder_agent/console/css/console.css
git commit -m "feat(console): ConnectionBanner + per-panel Freshness badge"
```

---

### Task 8: Health heartbeat + mount banner + replace hardcoded SYNC/LIVE

**Files:**

- Modify: `src/cofounder_agent/console/js/app.jsx` (add health poll, mount `<ConnectionBanner>`, drive the topbar SYNC indicator)
- Modify: `src/cofounder_agent/console/js/modes.jsx:551` (Wall-mode "LIVE · 5m SYNC")
- Verify: browser (Playwright) against the live worker

**Interfaces:**

- Consumes: `window.PXR.usePolledResource`, `window.PXR.ConnectionState`, `window.PXR.isDisconnected`, `window.ConnectionBanner`, `PX.api.health`.
- Produces: a mounted global banner + a real-connection-driven topbar indicator.

- [ ] **Step 1: Add the health heartbeat inside the `App` component**

In `app.jsx`, inside `function App()` (near the other hooks, after the state declarations ~line 60), add:

```js
// Connection heartbeat — the ONLY resource that drives the global banner.
// 30s cadence: fast enough to notice an outage, cheap enough to poll always.
const health = PX.api.isLive()
  ? window.PXR.usePolledResource(() => PX.api.health(), {
      intervalMs: 30_000,
      key: 'health',
    })
  : null;
```

Note: `usePolledResource` must be called unconditionally to satisfy the Rules of Hooks. Instead of the ternary above, call it unconditionally and make the fetch a no-op in mock mode:

```js
const health = window.PXR.usePolledResource(
  () => (PX.api.isLive() ? PX.api.health() : Promise.resolve({ ok: true })),
  { intervalMs: 30_000, key: 'health' }
);
```

Use the second (unconditional) form.

- [ ] **Step 2: Mount the banner**

In `app.jsx`, immediately inside the top-level returned element (before `{/* Topbar */}` at line 1360), add:

```jsx
<ConnectionBanner />
```

- [ ] **Step 3: Replace the hardcoded topbar SYNC indicator**

Replace `app.jsx:1403-1409` (`<div className="topbar__meta">…SYNC…5m…</div>`) with:

```jsx
<div className="topbar__meta">
  <span>
    <span className="k">SYNC</span>{' '}
    <span
      className="live-dot"
      style={{
        verticalAlign: 'middle',
        opacity: window.PXR.isDisconnected(
          window.PXR.ConnectionState.getState()
        )
          ? 0.3
          : 1,
      }}
    />{' '}
    {health && health.lastUpdatedAt
      ? window.agoLabel(health.lastUpdatedAt)
      : '—'}
  </span>
  <span className="tnum">{clock}</span>
</div>
```

- [ ] **Step 4: Fix the Wall-mode hardcoded string**

In `modes.jsx`, the Wall view receives props from `app.jsx`. Replace the hardcoded `LIVE · 5m SYNC` (line 551) with a `connected`/`lastSeen` prop. Simplest: pass `disconnected` + `lastSeenAt` into the Wall component from `app.jsx` and render:

```jsx
            <span className="live-dot" style={{ marginRight: 8, opacity: disconnected ? 0.3 : 1 }} />
            {disconnected ? 'OFFLINE' : 'LIVE'} · {lastSeenLabel} SYNC
```

Where `app.jsx` passes `disconnected={window.PXR.isDisconnected(window.PXR.ConnectionState.getState())}` and `lastSeenLabel={window.agoLabel(health?.lastUpdatedAt)}` to the Wall renderer. Locate the Wall component's prop list and thread these two through.

- [ ] **Step 5: Browser-verify against the live worker**

With the worker running, serve the console (or use the deployed `/console/`), go Live (or set `PX_API_LIVE`), and confirm: banner hidden while healthy; SYNC shows a real age ("just now" / "30s"). Then stop the worker (`docker stop poindexter-worker`), wait ~90s (3 health cycles), and confirm the banner appears and SYNC dims. Restart the worker; confirm the banner clears within one cycle. Zero console errors throughout.

- [ ] **Step 6: Prettier + commit**

```bash
npx prettier --check src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/modes.jsx
git add src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/modes.jsx
git commit -m "feat(console): health heartbeat + connection banner + real SYNC indicator"
```

---

### Task 9: Migrate the poll effects to `usePolledResource` + add Freshness to panels

> **REVISED (2026-07-05) — deferred to a follow-up PR.** Reading the real
> effects (app.jsx:122–633) disproved the uniform `fetch → setState` assumption
> below. Do the follow-up with this taxonomy, not a blanket rewrite:
>
> **Clean single-state (migrate these — safe):** `serviceHealth` (30s),
> `findings` (5m), `mediaQueue` (60s), `schedule` (60s), `seo` (5m),
> `newsletter` (5m), `logs` (10s, closes over `logFilter`), `traces` (60s).
> Each is `setX(res)` (some with an envelope/array guard). Wire the migration
> pattern above + a `<Freshness>` badge into their panel headers.
>
> **Merge-into-base (migrate, but fetchFn returns the merged shape vs `PX.<x>`
> base, not `prev`):** `budget` → merges into `cost` (byModel/daily/energy
> cleared to []/null; static $0-infra/energy fields come from the `PX.cost`
> base), `topics` (envelope guard).
>
> **Explicit exceptions (leave as bespoke effects; the spec's acceptance
> criterion 1 sanctions "explicit, listed exceptions" — document them in a
> comment):** `pipelineEvents`/feed (dedup by seen-id + fresh-flag animation +
> `setClock`), `approvals` (writes `inbox` **and** `approved`, maps
> `approvalToInbox`), `tasks` (`taskToRow` + `withLiveCounts` merge), `social`
> (writes `social` **and** `inbox`, maps `draftToInbox`), `kpiExtras` (3-way
> `Promise.all` → `kpiReads`), and the `brain` pair (`memoryStats` +
> `brainActivity` both write one `brain` state — two resources can't own it;
> either combine into one resource or leave both bespoke).
>
> **Error-UX caveat:** ~11 effects already `pushToast('… load failed')` on
> error (they are NOT silent). Migrating them trades a loud toast for a quieter
> `<Freshness stale>` badge. Preserve the toast where it exists — have the
> `fetchFn` catch → `pushToast` → rethrow (rethrow still marks the resource
> stale), or keep the toast at the call site. Do not silently drop toasts.
>
> The uniform steps below are kept as the mechanical reference for the clean
> subset only.

**Files:**

- Modify: `src/cofounder_agent/console/js/app.jsx` (replace ~17 poll effects; thread `stale`/`lastUpdatedAt` to panels)
- Modify: `src/cofounder_agent/console/js/panels.jsx`, `panels2.jsx` (render `<Freshness>` in panel headers)
- Verify: browser (Playwright)

**Interfaces:**

- Consumes: `window.PXR.usePolledResource`, `window.Freshness`.
- Produces: each migrated panel gets `{data, stale, lastUpdatedAt}` from the hook; panel headers show a `<Freshness>` badge.

**Migration pattern.** Each existing effect has this shape (example — the logs poll, `app.jsx:582-606`):

```js
// BEFORE (app.jsx:582-606) — the query string is built INLINE inside the effect.
const [logs, setLogs] = useS(PX.logs);
useEffect(() => {
  if (!PX.api.isLive()) return;
  let alive = true;
  const load = async () => {
    try {
      const qs =
        `?since=1h&limit=300` +
        (logFilter.service
          ? `&service=${encodeURIComponent(logFilter.service)}`
          : '') +
        (logFilter.level
          ? `&level=${encodeURIComponent(logFilter.level)}`
          : '');
      const res = await PX.api.logs(qs);
      if (alive && res) setLogs(res);
    } catch (_e) {}
  };
  load();
  const id = setInterval(load, 10 * 1000);
  return () => {
    alive = false;
    clearInterval(id);
  };
}, [logFilter]);
```

becomes (rebuild the SAME query string inline inside the `fetchFn` — there is no
`buildLogsQs` helper; keep the construction verbatim from the original effect):

```js
// AFTER
const logsR = window.PXR.usePolledResource(
  () => {
    if (!PX.api.isLive()) return Promise.resolve(PX.logs);
    const qs =
      `?since=1h&limit=300` +
      (logFilter.service
        ? `&service=${encodeURIComponent(logFilter.service)}`
        : '') +
      (logFilter.level ? `&level=${encodeURIComponent(logFilter.level)}` : '');
    return PX.api.logs(qs);
  },
  { intervalMs: 10_000, key: 'logs' }
);
const logs = logsR.data || PX.logs;
```

Then pass `stale={logsR.stale}` + `lastUpdatedAt={logsR.lastUpdatedAt}` to `<LogsPanel>`, which renders `<Freshness>` in its header.

Notes:

- Resources whose fetch depends on a filter/param (logs on `logFilter`) close over that param inside the `fetchFn`; the hook re-subscribes only on `intervalMs`/`key`, so read the latest param via the `fnRef` the hook already keeps current (the closure captures the newest `logFilter` because `fnRef.current` is reassigned each render). This preserves the existing filter-refetch behavior.
- Keep mock-mode behavior: in the `fetchFn`, return the mock (`PX.<x>`) via `Promise.resolve(...)` when `!PX.api.isLive()`, so mock mode is unchanged.
- The KPI-extras effect (`app.jsx:559-571`) feeds three sources into `kpisFromLive`; migrate it as one resource whose `fetchFn` does the same `Promise.all`, returning the combined object.

**Resource table — migrate all of these** (key, `PX.api` call, interval):

| key              | fetchFn call                                                                            | interval |
| ---------------- | --------------------------------------------------------------------------------------- | -------- |
| `pipelineEvents` | `pipelineEvents()`                                                                      | 5s       |
| `approvals`      | `Promise.all([listApprovals(), listTasks('?status=approved&limit=50')])`                | 5m       |
| `tasks`          | `listTasks('?limit=50')`                                                                | 5m       |
| `topics`         | `listTopicProposals()`                                                                  | 5m       |
| `serviceHealth`  | `serviceHealth()`                                                                       | 30s      |
| `budget`         | `budget()`                                                                              | 5m       |
| `findings`       | `findings()`                                                                            | 5m       |
| `memory`         | `memoryStats()`                                                                         | 60s      |
| `brain`          | `brainActivity()`                                                                       | 5m       |
| `media`          | `mediaQueue()`                                                                          | 60s      |
| `schedule`       | `schedule()`                                                                            | 60s      |
| `seo`            | `seo()`                                                                                 | 5m       |
| `social`         | `socialDrafts('?limit=50')`                                                             | 60s      |
| `newsletter`     | `newsletter()`                                                                          | 5m       |
| `kpiExtras`      | `Promise.all([posts(...), analyticsViews(...), listTasks('?status=failed&limit=100')])` | 5m       |
| `logs`           | `logs(qs)`                                                                              | 10s      |
| `traces`         | `traces('?hours=24&limit=50')`                                                          | 60s      |

(The mock-only `feedTimer`/`gpuTimer` at `app.jsx:75,97` are NOT polls of the backend — leave them.)

- [ ] **Step 1: Migrate three representative resources first**

Migrate `serviceHealth` (30s), `budget` (5m), and `logs` (10s, has a param) using the pattern above. Add `stale`/`lastUpdatedAt` props to their panels.

- [ ] **Step 2: Add `<Freshness>` to a shared panel header**

For panels built with the shared `Panel` primitive, pass `<Freshness .../>` via the `meta` prop. For hand-built headers (e.g. `LogsPanel` in `panels2.jsx`, which has its own `.panel__head`), add `<Freshness lastUpdatedAt={props.lastUpdatedAt} stale={props.stale} />` into the `.panel__head` next to the existing `panel__meta` span.

- [ ] **Step 3: Browser-verify the three**

Serve + Live. Confirm the three panels render data and show "updated Ns"; stop the worker and confirm those three go "stale Ns" while mock-only elements are unaffected; the global banner appears. Zero console errors.

- [ ] **Step 4: Migrate the remaining 14 resources**

Apply the identical transform to every remaining row in the table. After each few, run `npx prettier --check` on `app.jsx`.

- [ ] **Step 5: Full verification**

Run `npm run test:console` (all Node tests green). Serve + Live; confirm every panel shows freshness and no console errors. Stop the worker; confirm all migrated panels go stale + banner shows; restart; confirm recovery.

- [ ] **Step 6: Prettier + commit**

```bash
npx prettier --check src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/panels.jsx src/cofounder_agent/console/js/panels2.jsx
git add src/cofounder_agent/console/js/app.jsx src/cofounder_agent/console/js/panels.jsx src/cofounder_agent/console/js/panels2.jsx
git commit -m "feat(console): migrate all poll effects to usePolledResource + per-panel freshness"
```

---

## Acceptance criteria (from spec)

1. Every polling surface in `app.jsx` reads through `usePolledResource` (Task 9 table; mock-only timers excepted). ✅ Task 9
2. Simulated worker outage: banner within ~3 health cycles, clears within one cycle of recovery. ✅ Task 8 Step 5
3. Single failing endpoint → that panel stale, others fresh; full outage → banner + all stale. ✅ Task 9 Step 5
4. No fetch can hang past the timeout. ✅ Task 4
5. Prometheus helpers never throw when Prometheus is unreachable. ✅ Task 5
6. New unit tests green; happy-path panels still render. ✅ Tasks 1–5 (Node) + browser verifications
7. Prettier clean + all console Node tests green. ✅ each task's commit step
   - **Gate reality:** the console dir is **not** ESLint-linted — root `eslint.config.js` only matches `files: ['scripts/**', '*.config.*']`, and `src/cofounder_agent` isn't an npm workspace, so `npm run lint` skips it. The static gate for console code is **Prettier only** (`npm run format:check` at repo root, or `npx prettier --check <file>` per task). The console test job is `npm run test:console` (`node --test "src/cofounder_agent/console/js/__tests__/**/*.test.js"`) — its glob auto-discovers the three new test files with no wiring. The spec's "eslint clean" criterion does not apply here; treat it as satisfied vacuously.

## Notes for the implementer

- **In-browser Babel:** a JSX syntax error shows only at runtime (blank page + console error). After every `.jsx` edit, load the page once in the browser to confirm it renders before committing.
- **Rules of Hooks:** `usePolledResource` (and thus the `health` call) must be called unconditionally at the top level of `App` — never inside a condition or loop. Mock-mode branching goes _inside_ the `fetchFn`, not around the hook call.
- **One export block** in `reliability.js` — Task 6 consolidates to a single trailing export; make sure Tasks 1–3's interim export edits don't leave a duplicate.
