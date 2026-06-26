# Poindexter Operator Console — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `src/cofounder_agent/console/` prototype into the primary, action-first operator surface for Poindexter — replacing Grafana as the day-to-day cockpit — wired to the _real_ API contract, the _real_ pipeline model, and the surfaces the operator actually drives from a phone.

**Architecture:** A no-build, in-browser React 18 SPA served same-origin from the FastAPI worker at `/console/`. One adapter file (`js/api.js`) is the sole seam to the stack; every panel reads through it. Auth is OAuth2 client-credentials (mint + cache + refresh a short-lived JWT). The console is the cockpit; Grafana / Langfuse / Tempo / Pyroscope remain deep-dive tools reached from the Launcher (no re-implementing flame graphs or trace waterfalls in a hand-rolled SPA).

**Tech Stack:** React 18 (UMD) + Babel-standalone (in-browser JSX), the `@glad-labs/brand` E3 token system, FastAPI worker (`src/cofounder_agent/`), Prometheus (`:9091`), Playwright (console smoke), pytest (backend additions).

---

## Operating assumptions (correct me before executing if any are wrong)

1. **Mobile-first is a first-class requirement.** The operator drives this from the Claude app / a phone browser on the tailnet. Every panel must be usable at ~390px. The existing desktop grid is retained behind breakpoints.
2. **Keep the no-build setup** (in-browser Babel). A precompile/build step is listed once as optional-future (Phase 13), not required.
3. **Console = primary surface; the LGTM+P stack stays as deep-dive tools.** We absorb Grafana's _operator-glance + action_ role, not its trace/profile/log-exploration role.
4. **No fabricated data, ever.** Panels with no live source yet render an explicit empty/`$0`/"not wired" state — never mock numbers (`feedback_no_dummy_data`). Revenue/billing is pre-revenue and renders empty until the billing provider is live.
5. **Every change ships behind a PR with tests + doc updates** (`feedback_all_changes_via_pr`, `feedback_docs_and_tests_default`). Backend additions get pytest; console behaviour gets a Playwright smoke + manual/visual verification via the built-in dev-sim and live toggle.

## Testing posture (read once)

This is an in-browser SPA with **no existing JS unit harness** in `console/`. We do not fabricate one per-panel. Verification is layered:

- **Backend additions** (new/changed routes) → pytest, following `tests/unit/` patterns. TDD: failing test first.
- **`api.js` contract** → a Playwright smoke (`web/public-site` already uses Playwright; we add one console spec) that loads `/console/`, flips to live against a running worker, and asserts the foundational surfaces (health, settings, pending-approval) return and render.
- **Panel UI** → manual/visual verification via the Connection panel's **Dev simulation** (normal/slow/error/empty) plus a live pass. Each task names the exact thing to look at (`feedback_visual_verification`).

## Verified endpoint contract (the source of truth for `api.js`)

| Surface                        | Method & path                                                                                                                                                  | Notes (verified in routes/)                                                                                                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Token                          | `POST /token`                                                                                                                                                  | form-urlencoded `grant_type=client_credentials&client_id&client_secret[&scope]` → `{access_token, token_type:"Bearer", expires_in, scope}`. `oauth_routes.py:315`, `:357`        |
| Health                         | `GET /api/health`                                                                                                                                              | `main.py:1038`                                                                                                                                                                   |
| List settings                  | `GET /api/settings?category=&search=&offset=&limit=`                                                                                                           | `SettingListResponse`; secrets masked `********`. `settings_routes.py:52`                                                                                                        |
| Update setting                 | `PUT /api/settings/{id}` body `{value:"…"}`                                                                                                                    | `settings_routes.py:269`                                                                                                                                                         |
| Pending approvals              | `GET /api/tasks/pending-approval?limit=&offset=&task_type=&sort_by=&sort_order=`                                                                               | → `{total,limit,offset,count,tasks:[{task_id,task_name,topic,task_type,status,created_at,quality_score,content_preview,featured_image_url,metadata}]}`. `approval_routes.py:240` |
| Approve (stage)                | `POST /api/tasks/{task_id}/approve` body `{approved:true, human_feedback?, reviewer_id?, featured_image_url?, image_source?, auto_publish:false, publish_at?}` | **`auto_publish` defaults false — staging only.** `task_publishing_routes.py:166`                                                                                                |
| Reject                         | `POST /api/tasks/{task_id}/reject` body `{human_feedback}`                                                                                                     | `approval_routes.py:68`                                                                                                                                                          |
| Publish (ship)                 | `POST /api/tasks/{task_id}/publish`                                                                                                                            | separate gate. `task_publishing_routes.py:629`                                                                                                                                   |
| Go-live (existing post)        | `POST /api/tasks/{post_id}/go-live`                                                                                                                            | `task_publishing_routes.py:834`                                                                                                                                                  |
| List tasks                     | `GET /api/tasks?offset=&limit=&status=&category=&search=`                                                                                                      | `TaskListResponse`; row `id` is stringified `task_id`. `task_routes.py:724`                                                                                                      |
| Get task                       | `GET /api/tasks/{task_id}`                                                                                                                                     | `UnifiedTaskResponse`. `task_routes.py:807`                                                                                                                                      |
| Retry                          | `PUT /api/tasks/{task_id}/status` body `{status:"pending", …}`                                                                                                 | `task_status_routes.py:42` (verb is PUT)                                                                                                                                         |
| Cancel                         | `DELETE /api/tasks/{task_id}`                                                                                                                                  | `task_routes.py:835`                                                                                                                                                             |
| Pipeline events                | `GET /api/pipeline/events` (+ `/task/{task_id}`)                                                                                                               | `pipeline_events_routes.py:84`                                                                                                                                                   |
| Memory stats                   | `GET /api/memory/stats`; search `GET /api/memory/search`                                                                                                       | `memory_dashboard_routes.py:116`                                                                                                                                                 |
| Posts                          | `GET /api/posts`                                                                                                                                               | `cms_routes.py:63`                                                                                                                                                               |
| Analytics views                | `GET /api/analytics/views`                                                                                                                                     | `cms_routes.py:631`                                                                                                                                                              |
| Module probes (discovery only) | `GET /api/modules/probes`                                                                                                                                      | returns `{count:0,probes:[]}` today — **not** service health. `module_probes_routes.py:29`                                                                                       |

**Endpoints the console assumes that DO NOT exist** (and how the plan resolves them): `GET /api/approvals*` (→ use `/api/tasks/*` above), `POST /api/tasks/{id}/retry` (→ `PUT …/status`), `POST /api/tasks/{id}/cancel` (→ `DELETE`), `GET /probes` for service health (→ cAdvisor `container_last_seen` + `/api/health`, Phase 5.1 ✅), `run-probes` (removed — brain probes have no HTTP surface), `POST /api/admin/restart` (→ brain-routed guarded action, Phase 5.3 deferred), topic-triage actions (MCP-only today → new HTTP routes, Phase 4).

---

## Phase 0 — Foundation: make it load, make auth real

> Outcome: `/console/` opens, authenticates with OAuth2 client-credentials, and a live "Test connection" succeeds. Nothing else is live yet. This is the gate to every later phase.

### Task 0.1: Add the missing `index.html` entry document

**Files:**

- Create: `src/cofounder_agent/console/index.html`

The worker already mounts the folder (`main.py:1028`, `html=True`) but there is no `index.html`, so `/console/` 404s today. This file is the entry the README has always referenced.

- [ ] **Step 1: Create `index.html`** (loads CSS cascade, React 18 UMD, Babel, then JS in dependency order — plain `.js` globals first, `.jsx` components, `app.jsx` last)

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1, viewport-fit=cover"
    />
    <meta name="color-scheme" content="dark" />
    <title>Poindexter · Operator Console</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@400;500;600;700&display=swap"
      rel="stylesheet"
    />
    <!-- Brand + console styles (cascade order matters: tokens → type → brand → components → effects → modes → console) -->
    <link rel="stylesheet" href="css/colors.css" />
    <link rel="stylesheet" href="css/typography.css" />
    <link rel="stylesheet" href="css/brand.css" />
    <link rel="stylesheet" href="css/components.css" />
    <link rel="stylesheet" href="css/effects.css" />
    <link rel="stylesheet" href="css/modes.css" />
    <link rel="stylesheet" href="css/console.css" />
  </head>
  <body>
    <div id="root"></div>

    <!-- React 18 + in-browser JSX, vendored locally (same-origin, offline-safe, no CDN-compromise vector). See Step 2. -->
    <script src="js/vendor/react.production.min.js"></script>
    <script src="js/vendor/react-dom.production.min.js"></script>
    <script src="js/vendor/babel.min.js"></script>

    <!-- Plain-JS globals first: window.PX (data), window.PX_SETTINGS, window.PX.api -->
    <script src="js/data.js"></script>
    <script src="js/settings-data.js"></script>
    <script src="js/api.js"></script>

    <!-- JSX components (Babel-compiled). app.jsx is LAST: it calls ReactDOM.createRoot. -->
    <script
      type="text/babel"
      data-presets="react"
      src="js/primitives.jsx"
    ></script>
    <script type="text/babel" data-presets="react" src="js/panels.jsx"></script>
    <script
      type="text/babel"
      data-presets="react"
      src="js/panels2.jsx"
    ></script>
    <script type="text/babel" data-presets="react" src="js/drawer.jsx"></script>
    <script type="text/babel" data-presets="react" src="js/modes.jsx"></script>
    <script
      type="text/babel"
      data-presets="react"
      src="js/palette.jsx"
    ></script>
    <script
      type="text/babel"
      data-presets="react"
      src="js/settings.jsx"
    ></script>
    <script type="text/babel" data-presets="react" src="js/app.jsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Vendor the runtime locally** (resolves the CDN-compromise risk; aligns with local-first / offline operation)

The console must work on the tailnet box without reaching the public internet, and loading scripts from a floating CDN tag (`react@18`, `@babel/standalone@7`) is both an availability risk and a supply-chain risk — and Subresource Integrity is impossible on a floating tag (the hash changes every upstream patch). Vendor pinned, checksum-recorded copies into `console/js/vendor/`:

```bash
mkdir -p src/cofounder_agent/console/js/vendor
cd src/cofounder_agent/console/js/vendor
curl -fsSLo react.production.min.js     https://unpkg.com/react@18.3.1/umd/react.production.min.js
curl -fsSLo react-dom.production.min.js https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js
curl -fsSLo babel.min.js                https://unpkg.com/@babel/standalone@7.26.4/babel.min.js
# Record checksums so future re-vendors are verifiable (commit this file). Bump a version here if any URL 404s.
sha256sum *.js > VENDOR_CHECKSUMS.txt
```

If you keep a CDN variant instead (not recommended for this local tool), you MUST pin exact versions and add `integrity="sha384-…" crossorigin="anonymous"` to each tag — generate each hash with `curl -fsSL <pinned-url> | openssl dgst -sha384 -binary | openssl base64 -A`.

- [ ] **Step 3: Verify it loads**

Start the worker (`python -m uvicorn main:app --host 0.0.0.0 --port 8002` from `src/cofounder_agent`), open `http://localhost:8002/console/`.
Expected: the console renders in **mock** mode (KPI strip, Action Inbox, etc.). Browser console shows the `[PX.api] MOCK mode.` hint. No 404, and no network calls to unpkg.

> Note on load order: each `text/babel` script compiles and runs in order; top-level `function Foo(){}` declarations become globals, and `App()` only _references_ them at render time, so as long as `app.jsx` is last, all components resolve. If a `data-presets` value of `react` errors on a given Babel build, use `data-presets="react,env"`.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/index.html src/cofounder_agent/console/js/vendor
git commit -m "feat(console): add index.html + vendored React/Babel so /console/ loads offline"
```

### Task 0.2: Replace static-Bearer with OAuth2 client-credentials in the adapter

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (config block ~`:36-47`, `headers()`/`http()` ~`:49-65`, `setToken` ~`:114-117`, `health()` ~`:136-160`)

Static Bearer is gone (`middleware/api_token_auth.py:54`). The adapter must mint a JWT from `POST /token` and refresh it before expiry.

- [ ] **Step 1: Replace the token config + add an OAuth token manager**

Replace the `token:` line in `cfg` and add client creds + an in-memory token cache:

```js
const cfg = {
  base: LS.getItem('px_base') ?? '',
  prometheus: LS.getItem('px_prom') ?? 'http://localhost:9091', // NOTE: 9091, not 9090
  clientId: LS.getItem('px_client_id') ?? '',
  clientSecret: LS.getItem('px_client_secret') ?? '',
  scope: LS.getItem('px_scope') ?? '',
  live: (window.PX_API_LIVE ?? false) || LS.getItem('px_live') === '1',
  sim: LS.getItem('px_sim') ?? 'normal',
};

// In-memory OAuth token cache (never persisted — short-lived JWT).
let _tok = { value: '', exp: 0 };

async function getToken() {
  const now = Date.now();
  if (_tok.value && now < _tok.exp - 60_000) return _tok.value; // 60s skew
  if (!cfg.clientId || !cfg.clientSecret)
    throw new Error(
      'No OAuth client configured. Set client_id + client_secret in App Settings → Connection.'
    );
  const form = new URLSearchParams({
    grant_type: 'client_credentials',
    client_id: cfg.clientId,
    client_secret: cfg.clientSecret,
  });
  if (cfg.scope) form.set('scope', cfg.scope);
  const res = await fetch((cfg.base || '') + '/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(
      `/token → ${res.status} ${res.statusText} ${detail}`.trim()
    );
  }
  const j = await res.json();
  _tok = {
    value: j.access_token,
    exp: Date.now() + (Number(j.expires_in) || 3600) * 1000,
  };
  return _tok.value;
}
```

- [ ] **Step 2: Make `http()` async-auth + retry-once on 401**

```js
async function http(method, path, body, root) {
  const url = (root ?? cfg.base) + path;
  const doFetch = async () => {
    const tok = await getToken();
    return fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + tok,
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  };
  let res = await doFetch();
  if (res.status === 401) {
    _tok = { value: '', exp: 0 }; // force re-mint and retry once
    res = await doFetch();
  }
  if (!res.ok)
    throw new Error(`${method} ${path} → ${res.status} ${res.statusText}`);
  return res.status === 204 ? null : res.json();
}
```

- [ ] **Step 3: Replace `setToken` with credential setters**

```js
setClient(id, secret) {
  cfg.clientId = id || ''; cfg.clientSecret = secret || '';
  LS.setItem('px_client_id', cfg.clientId);
  LS.setItem('px_client_secret', cfg.clientSecret); // local operator tool, same-origin; document the trade-off
  _tok = { value: '', exp: 0 };
},
setScope(s) { cfg.scope = s || ''; LS.setItem('px_scope', cfg.scope); },
```

- [ ] **Step 4: Update `health()` live branch** to mint a token first (it already falls back to `/api/settings?limit=1`; both now ride the JWT via `http()`). Update the default `prometheus` references everywhere from `9090` → `9091`.

- [ ] **Step 5: Verify** — in mock mode nothing changed. (Live verification happens in Task 0.4.)

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/api.js
git commit -m "feat(console): OAuth2 client-credentials token flow (static Bearer is gone, #249)"
```

### Task 0.3: Connection panel — client_id / client_secret instead of paste-a-token

**Files:**

- Modify: `src/cofounder_agent/console/js/settings.jsx` (the Connection panel) and, if present, `js/settings-data.js`

- [ ] **Step 1:** Replace the single "Bearer token" input with **Client ID** + **Client Secret** (+ optional Scope) fields, wired to `PX.api.setClient(id, secret)` / `PX.api.setScope(s)`. Keep Worker base URL (blank = same-origin) and Prometheus URL (default `http://localhost:9091`).
- [ ] **Step 2:** Add a one-line helper under the fields: _"Mint a dedicated client: `poindexter auth register-client --name poindexter-console --scopes "api:read api:write" --grant-type client_credentials` — the secret is shown once; paste client_id/secret here."_ Do NOT reuse `migrate-cli`/`migrate-scripts` clients: those persist an encrypted-only copy in `app_settings` (unusable from a browser) and conflate the console with a headless-consumer identity. A dedicated client bounds the blast radius (revoke independently if the browser leaks).
- [ ] **Step 3:** Keep the **Test connection** button → `PX.api.health()`; on success show the latency + `(same-origin)`; on failure show the thrown message verbatim (it will name `/token` failures clearly).
- [ ] **Step 4: Verify** visually in mock + the Dev-sim error mode (Test connection shows the simulated failure).
- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/console/js/settings.jsx
git commit -m "feat(console): Connection panel collects OAuth client creds + 9091 prometheus default"
```

### Task 0.4: First genuinely-live surface end-to-end (health + settings read)

**Files:** none new — this is the live validation of Tasks 0.1–0.3.

- [ ] **Step 1:** Provision a **dedicated** console OAuth client: `poindexter auth register-client --name poindexter-console --scopes "api:read api:write" --grant-type client_credentials`, capture the `client_id` + `client_secret` it prints once. (register-client prints plaintext and persists nothing extra — the right shape for a browser. The `migrate-*` helpers store an encrypted-only app_settings copy + bootstrap.toml plaintext for headless consumers; don't reuse those identities in the browser.)
- [ ] **Step 2:** Open `/console/` → App Settings → Connection. Enter creds, base blank, Test connection.
      Expected: `live` health returns `{ok:true, mode:'live', ms:…}`.
- [ ] **Step 3:** Toggle **Live** on. Confirm the Settings panel lists real `app_settings` (secrets shown as `********`).
- [ ] **Step 4:** Edit one non-secret setting (e.g. a content knob) → `PUT /api/settings/{id}` → toast "Applied". Re-read confirms persistence.
- [ ] **Step 5: Commit** (docs only — record the go-live runbook)

```bash
git add docs/superpowers/plans/2026-06-13-operator-console.md
git commit -m "docs(console): record Phase 0 go-live runbook"
```

### Task 0.5: Console smoke test (Playwright)

**Files:**

- Create: `web/public-site/e2e/console-smoke.spec.ts` (or the repo's existing Playwright dir — confirm with `npm run test:e2e` config)

- [ ] **Step 1: Write the failing smoke** — navigate to `${WORKER}/console/`, assert `#root` renders the topbar `OPERATOR CONSOLE`, assert mock-mode boot hint, then (guarded by an env var with creds) flip live and assert the Settings panel shows ≥1 row.

```ts
import { test, expect } from '@playwright/test';
const BASE = process.env.CONSOLE_BASE ?? 'http://localhost:8002';
test('console loads and renders the operator shell', async ({ page }) => {
  await page.goto(`${BASE}/console/`);
  await expect(page.getByText('OPERATOR')).toBeVisible();
  await expect(page.locator('#root .rail')).toBeVisible();
});
```

- [ ] **Step 2: Run** `npm run test:e2e -- console-smoke` → expect PASS against a running worker.
- [ ] **Step 3: Commit**

```bash
git add web/public-site/e2e/console-smoke.spec.ts
git commit -m "test(console): Playwright smoke for the operator shell"
```

---

## Phase 1 — Approvals (the #1 operator job), wired live with the two-gate model

> Outcome: the Action Inbox lists real `awaiting_approval` tasks, and the operator can **Approve (stage)**, **Reject**, and **Publish (ship)** as three distinct actions — matching the backend's enforced `approve != publish`.

### Task 1.1: Fix the approvals endpoints in `api.js`

**Files:** Modify `src/cofounder_agent/console/js/api.js` (`listApprovals`/`approve`/`reject` ~`:187-204`; `retryTask`/`killTask` ~`:219-230`)

- [ ] **Step 1:** Rewrite the approval + task-mutation methods to the verified contract and add `publishTask`:

```js
listApprovals() {
  return pick(
    () => http('GET', '/api/tasks/pending-approval?limit=50'),
    () => ({ tasks: mock().inbox.filter((i) => i.kind === 'approve') })
  );
},
approve(id, opts = {}) {
  // Stage only. auto_publish stays false — publish is a separate gate.
  return pick(
    () => http('POST', `/api/tasks/${id}/approve`, { approved: true, auto_publish: false, ...opts }),
    () => ({ ok: true })
  );
},
reject(id, human_feedback = '') {
  return pick(
    () => http('POST', `/api/tasks/${id}/reject`, { human_feedback }),
    () => ({ ok: true })
  );
},
publishTask(id) {
  return pick(
    () => http('POST', `/api/tasks/${id}/publish`),
    () => ({ ok: true })
  );
},
retryTask(id) {
  return pick(
    () => http('PUT', `/api/tasks/${id}/status`, { status: 'pending' }),
    () => ({ ok: true })
  );
},
killTask(id) {
  return pick(
    () => http('DELETE', `/api/tasks/${id}`),
    () => ({ ok: true })
  );
},
```

- [ ] **Step 2:** Update the endpoint-map comment block at the top of `api.js` to the verified table (delete the false "all confirmed" claims).
- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/console/js/api.js
git commit -m "fix(console): wire approvals/retry/cancel to real /api/tasks endpoints + add publish"
```

### Task 1.2: Map the `pending-approval` response into the inbox shape

**Files:** Modify `src/cofounder_agent/console/js/app.jsx` (the `inbox` state init + a new live-load effect)

The console's inbox item shape is `{id, kind, title, sub[], age, tags[], detail{}}`. The API returns `{task_id, task_name, topic, quality_score, content_preview, created_at, featured_image_url, metadata}`. Add a transform and load it on live.

- [ ] **Step 1:** Add a pure mapper near `trunc()` in `app.jsx`:

```js
function approvalToInbox(t) {
  return {
    id: t.task_id,
    kind: 'approve',
    priority: 1,
    title: t.task_name || t.topic || `Task ${t.task_id}`,
    sub: [
      [
        'QUALITY',
        t.quality_score != null ? String(Math.round(t.quality_score)) : '—',
      ],
      ['TYPE', t.task_type || 'blog_post'],
      ['TOPIC', t.topic || '—'],
    ],
    age: t.created_at ? PX.ago(minsSince(t.created_at)) : '',
    tags: [['cyan', 'READY']],
    detail: {
      excerpt: t.content_preview || '',
      quality: t.quality_score,
      pipeline: t.status,
      topic: t.topic,
      featured_image_url: t.featured_image_url,
      task: t.task_id,
    },
  };
}
function minsSince(iso) {
  return Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
}
```

- [ ] **Step 2:** Add an effect that, when `PX.api.isLive()`, loads `listApprovals()` and merges mapped approvals into `inbox` (replacing the mock approve-kind items, preserving fail/alert/drift/media kinds until their phases). Poll on the existing 5-min SYNC cadence.
- [ ] **Step 3: Verify** live: the inbox "Awaiting Approval" count matches `GET /api/tasks/pending-approval` `total`; titles/quality are real.
- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/js/app.jsx
git commit -m "feat(console): load real pending approvals into the Action Inbox"
```

### Task 1.3: Wire Approve / Reject / Publish actions with optimistic update + honest errors

**Files:** Modify `src/cofounder_agent/console/js/app.jsx` (`A.approve`, `A.reject`, add `A.publish`)

- [ ] **Step 1:** Make `A.approve`/`A.reject` call the API and only remove the inbox row on success; on failure, restore + red toast with the thrown message. Change the approve toast copy from "queued to publish" to **"Approved — staged (not published)"**.

```js
approve: async (e) => {
  const prev = inbox;
  removeInbox(e.id); closeDrawer();
  try {
    await PX.api.approve(e.id);
    pushToast(`Approved — “${trunc(e.title)}” staged (not published)`, 'mint', '✓');
    pushFeed(['mint', 'APPROVE'], `operator approved <b>${trunc(e.title)}</b> → staged`);
  } catch (err) {
    setInbox(prev); pushToast(`Approve failed — ${err.message}`, 'red', '✕');
  }
},
publish: async (e) => {
  closeDrawer();
  try {
    await PX.api.publishTask(e.id);
    pushToast(`Published — “${trunc(e.title)}” is live`, 'mint', '✓');
    pushFeed(['mint', 'PUBLISH'], `operator published <b>${trunc(e.title)}</b>`);
  } catch (err) { pushToast(`Publish failed — ${err.message}`, 'red', '✕'); }
},
```

- [ ] **Step 2:** Add a **Publish** button in the approve-kind drawer/inbox row (distinct from Approve). The natural flow: Approve → row moves to an "Approved — awaiting publish" group → Publish ships it. (A lightweight approach for v1: after approve, refetch and show approved tasks via `GET /api/tasks?status=approved`; render a small "Ready to publish" list with a Publish button.)
- [ ] **Step 3: Verify** the three actions against a real `awaiting_approval` task on a dev niche. Confirm in DB / `/api/tasks` that approve sets `approved` (not `published`) and publish sets `published`.
- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/console/js/app.jsx
git commit -m "feat(console): live approve/reject/publish with two-gate model + optimistic UX"
```

### Task 1.4: Approval drawer shows real content

**Files:** Modify `src/cofounder_agent/console/js/drawer.jsx`

- [ ] **Step 1:** For `kind==='approve'`, render `detail.excerpt` (content_preview), `quality`, `featured_image_url` (thumbnail), topic, and the three buttons (Approve / Reject-with-feedback / Publish). Reject opens a small feedback textarea → `A.reject(e, feedback)`.
- [ ] **Step 2: Verify** visually; reject sends `human_feedback`.
- [ ] **Step 3: Commit.**

---

## Phase 2 — Settings, fully live (mostly already real)

> Outcome: the Settings mode is the trusted read/write surface for ~685 keys, with category grouping, search, and secret masking.

### Task 2.1: Confirm + harden the live settings wiring

**Files:** Modify `src/cofounder_agent/console/js/settings.jsx`, `js/settings-data.js`

- [ ] **Step 1:** Load real settings via `PX.api.listSettings(category)`; group by `category`; render `is_secret` rows as masked with an "unset" indicator for empty (`''` is the unset sentinel — `feedback_app_settings_value_not_null`).
- [ ] **Step 2:** Wire inline edit → `PX.api.updateSetting(id, value)`; show the from→to in the audit feed (already coded in `app.jsx`).
- [ ] **Step 3:** Add a category filter + search box (server-side `?category=&search=`).
- [ ] **Step 4: Verify** live across 2–3 categories; confirm secrets never round-trip a real value.
- [ ] **Step 5: Commit.**

---

## Phase 3 — Pipeline panel: model the REAL graph_def, not the deleted 6-stage flow

> Outcome: the Pipeline panel reflects the live 36-node `canonical_blog` graph_def (writer / image / QA-rail / SEO / finalize blocks) and per-task progress, replacing the `research→draft→edit→illustrate→review→publish` model that was deleted 2026-05-16.

### Task 3.1: Replace the stage model in mock + panel

**Files:** Modify `src/cofounder_agent/console/js/data.js` (`pipeline.stages`), `js/panels.jsx` (`PipelinePanel`)

- [ ] **Step 1:** Replace the 6 stages with the real **node blocks** (a readable grouping of the 36 nodes — see `services/canonical_blog_spec.py::CANONICAL_BLOG_GRAPH_DEF`):

```js
stages: [
  { name: 'writer',   nodes: ['generate_draft','generate_title','normalize_draft','self_review'], count: 0, state: '' },
  { name: 'image',    nodes: ['plan_image_markers','generate_images','inject_images','caption_images'], count: 0, state: '' },
  { name: 'qa',       nodes: ['programmatic','critic','deepeval','ragas','vision','citations','consistency','aggregate'], count: 0, state: '' },
  { name: 'seo',      nodes: ['generate_all_metadata'], count: 0, state: '' },
  { name: 'finalize', nodes: ['compile_meta','persist_task','record_version','evaluate_auto_publish'], count: 0, state: '' },
],
```

- [ ] **Step 2:** Update `PipelinePanel` labels/tooltips to these blocks; the per-node detail is available in the task drawer (Task 3.3).
- [ ] **Step 3: Commit.**

### Task 3.2: Wire pipeline counts + task list to live

**Files:** Modify `js/api.js` (`listTasks` already correct), `js/app.jsx` (live load), `js/panels.jsx`

- [ ] **Step 1:** On live, load `GET /api/tasks?limit=50` and derive per-block counts from each task's current stage/status; render the real `tasks[]` (id, topic, status, quality, model, age) in the panel's task table.
- [ ] **Step 2:** Map task `status` → the block it's in (running/awaiting_approval/approved/published/failed). Where the API exposes the current node, prefer it; otherwise bucket by status.
- [ ] **Step 3: Verify** counts match `GET /api/tasks` filtered by status.
- [ ] **Step 4: Commit.**

### Task 3.3: Task drawer — real nodes, retry, cancel

**Files:** Modify `js/drawer.jsx`, `js/app.jsx` (`A.retry`/`A.kill` already call the fixed API)

- [ ] **Step 1:** Task drawer shows: status, current block, quality, model, age, and (live) the `GET /api/pipeline/events/task/{task_id}` timeline. Buttons: **Retry** (`PUT …/status {status:'pending'}`) and **Cancel** (`DELETE`). Retry copy notes it also clears a poisoned LangGraph checkpoint (`reference_langgraph_checkpoint_poisoning`).
- [ ] **Step 2: Verify** retry moves a failed task back to `pending`; cancel removes it.
- [ ] **Step 3: Commit.**

### Task 3.4: Per-task QA rail outcomes

**Files:** Modify `js/panels.jsx` (`QAPanel`), `js/data.js` (`qa`)

- [ ] **Step 1:** Re-label the QA panel to the real rails: hard gates `qa.programmatic` + `qa.critic`; advisory `qa.deepeval`(×3) / `qa.ragas` / `qa.vision` / `qa.citations` / `qa.consistency` / `qa.self_consistency` / `qa.web_factcheck` (`project_qa_rails_state_2026_06`). Replace invented validator rules with the real `content_validator` rule families (programmatic anti-hallucination). Source per-pass data from `audit_log` where `event_type='qa_pass_completed'` (the QA Rails dashboard's source) — expose via a thin read if no HTTP route exists yet (note as a backend follow-up; render empty until wired, no mock).
- [ ] **Step 2: Commit.**

---

## Phase 4 — Topics triage (NEW surface; needs thin HTTP routes) — ✅ SHIPPED

> Outcome: the operator can review, rank, resolve, and reject proposed topics from the console. **Reality:** the triage logic exists as MCP tools (`topics_rank_batch / reject_batch / resolve_batch / edit_winner / show_batch`) but **not** as HTTP routes — only `POST /api/topics/from-url(s)` exist (`topics_routes.py`). This phase adds the HTTP surface over the same service.

> **As built (correction to the assumption above):** the MCP tools delegate to **`services.topic_batch_service.TopicBatchService`** — a **batch** model, **not** `topic_proposal_service` (which is for hand-injecting a single topic into the gate, a different job). A discovery sweep produces one _open batch_ per niche holding ~N ranked candidates. So the surface is batch-oriented and the plan's `POST /api/topics/{id}/…` cleanly reinterprets `{id}` as a **batch_id**: resolving advances the operator-ranked **rank-1 candidate** into the pipeline; rejecting discards the whole batch (→ `expired`) and frees the niche's one-open-batch slot. A stuck _open_ batch is the recurring "content goes dark" failure class, so this is the drain valve. Endpoints shipped: `GET /api/topics/proposals`, `POST /api/topics/{batch_id}/{rank,resolve,reject}`. Backing the GET is a new thin `TopicBatchService.list_open_batches()` (open batches across all niches, each a merged candidate view + resolved niche slug/name), unit-tested by a real-DB roundtrip. `edit_winner` (the 5th MCP tool) was intentionally **not** exposed in v1 — scope held to the plan's named four. Live console→worker→DB E2E is the post-merge operator step (needs this code deployed + a live open batch).

### Task 4.1: Expose topic-triage over HTTP (backend, TDD)

**Files:**

- Modify: `src/cofounder_agent/routes/topics_routes.py`
- Test: `src/cofounder_agent/tests/unit/routes/test_topics_triage_routes.py`

- [x] **Step 1: Write failing tests** — `tests/unit/routes/test_topics_triage_routes.py` (13 tests, service mocked) pins the HTTP contract for `GET /api/topics/proposals` + `POST /api/topics/{batch_id}/{rank,resolve,reject}`; plus a real-DB roundtrip for the new `TopicBatchService.list_open_batches()` in `tests/unit/services/test_topic_batch_service.py`. (Service is `TopicBatchService`, the batch model — not `topic_proposal_service`.)
- [x] **Step 2: Run** → FAIL (13 route tests failed: `TopicBatchService` not imported / 404).
- [x] **Step 3: Implement** the routes delegating to `TopicBatchService` (`db_service.pool` → `TopicBatchService(pool, site_config=…)`); auth via `verify_api_token`; `{batch_id}` parsed to UUID (400 on malformed); unranked-resolve `ValueError` → 400. Added `list_open_batches()` + `OpenBatch` dataclass to the service so the route stays a thin single-collaborator serializer.
- [x] **Step 4: Run** → PASS (13 route + 21 service tests green; ruff clean). Commit `76ed17055`.
- [x] **Step 5: Commit.** `76ed17055`.

### Task 4.2: Topics panel in the console

**Files:** Modify `js/api.js` (add `topics*` methods), `js/panels2.jsx` (new `TopicsPanel`), `js/app.jsx` (rail entry + section), `js/data.js` (mock shape)

- [x] **Step 1:** Added `RAIL` entry `{ id:'topics', icon:'overview', label:'Topics' }` (with an open-batch **count badge**) + `sec-topics` section + `TopicsPanel` (`panels2.jsx`) rendering each open batch → ranked candidates with **Pick** (rank #1) / **Resolve** / **Reject**. Resolve is disabled until a winner is picked (mirrors the backend's unranked-resolve 400).
- [x] **Step 2:** Wired to the Phase-4.1 routes via `api.js` (`listTopicProposals` / `rankTopicBatch` / `resolveTopicBatch` / `rejectTopicBatch` on the `pick(live, mock)` seam) + a 5-min live-load effect; `A.topicPick/Resolve/Reject` do optimistic updates with honest red-toast rollback. `data.js` `PX.topics` mock matches the live shape.
- [x] **Step 3: Verify** — mock-mode browser pass (Playwright): panel renders, rail badge = open-batch count, Resolve disabled until Pick, Pick optimistically marks the winner + enables Resolve, no JS/Babel errors; eslint + prettier clean via the commit hook. _Live console→worker→DB pass deferred to the post-merge operator go-live (needs this code deployed + a live open batch)._
- [x] **Step 4: Commit.** `acb028f99`.

---

## Phase 5 — Health & Services: a real truth source (drop the fictional probes)

> Outcome: service health is sourced from cAdvisor `container_last_seen` (`:9091`) + `/api/health`; the GPU HUD uses real `nvidia_gpu_*` series; the service list reflects the real container topology; and the operator gets a **guarded restart** action.

**STATUS — 5.1 + 5.2 SHIPPED (2026-06-13, this PR).** The read-only health surfaces are live on the adapter seam (mock mode unchanged; live mode verified against the real stack — all 19 container-backed services resolve `ok`, 0 down; all 8 GPU scalars resolve). **Task 5.3 (guarded restart) is intentionally deferred to a follow-up PR:** the worker container has NO `docker.sock` mount (only `poindexter-brain-daemon` does, RW), so a restart route on the worker cannot shell out to docker-compose — it must route through the brain via the DB spinal cord. That's a backend-design task, not a console edit, so it ships on its own.

> Correction to the original plan: service health uses cAdvisor `container_last_seen`, **not** `up{}`. cAdvisor emits a series for **all ~39 running containers**, whereas `up{}` only has the ~12 Prometheus scrape targets — so `up{}` alone can't see most of the stack. The freshness expression `time() - container_last_seen` gives a per-container liveness age; the sibling cAdvisor series (`container_memory_usage_bytes`, `rate(container_cpu_usage_seconds_total[1m])`, `container_start_time_seconds`, the `image` label) make the detail drawer fully real too.

### Task 5.1: Service health from cAdvisor + `/api/health` — ✅ SHIPPED

**Files:** `js/api.js` (`probes()` → `serviceHealth()` + `promVector`), `js/data.js` (`services`), `js/panels.jsx` (`ServiceGrid`), `js/app.jsx` (30s live poll), `js/modes.jsx` (SystemMap node keys)

- [x] **Step 1:** Replaced `probes()` with `serviceHealth()`. Live branch derives status from `time() - container_last_seen{name=~"poindexter.+"}` (<60s `ok` · ≥60s `stale` · absent `down`), plus real `cpu`/`mem`/`uptime`/`image` from the sibling cAdvisor series and a worker `/api/health` overlay (container-up ≠ FastAPI-answering). Deleted the "Run all 28 probes" UI + `runAllProbes`/`/api/admin/run-probes` (the brain's probes have no HTTP surface; alerts arrive via Telegram/AlertManager). Added a `promVector` helper + a 30s live-poll effect in `app.jsx`.
- [x] **Step 2:** Rewrote `data.js` `services` to the **real curated topology** (20 operator-meaningful subsystems): `worker` (:8002), **`prefect-worker`** (the pipeline runner that actually generates content), `brain-daemon`, `postgres-local`, `ollama` (`host:true` — :11434, no cAdvisor series), `image-gen-server`, `prefect-server`, `prometheus` (:9091), `grafana`, `loki`, `tempo`, `pyroscope`, `alertmanager`, `langfuse-web`, `glitchtip-web`, `pgadmin`, `cadvisor`, `uptime-kuma`, `livekit` (tailnet), `speaches` (tailnet). Each carries `container` (the real cAdvisor `name`); `host`/`tailnet` flags mark the exceptions. Realigned the SystemMap (`modes.jsx`) node/edge keys to the renamed services.
- [x] **Step 3: Verified** — simulated the live mapping against real Prometheus (all 19 container services `ok`, 0 down, real cpu/mem/uptime/image), and a headless mock-mode render (20 rows, 0 runtime errors, "Run all probes" gone).
- [x] **Step 4: Committed.**

### Task 5.2: GPU HUD on real metrics — ✅ SHIPPED

**Files:** `js/api.js` (`gpu()`), `js/app.jsx` (GPU poll → real in live), `js/panels.jsx` (`GpuHud` driver guard)

- [x] **Step 1:** Pointed `gpu()` at the verified `nvidia_gpu_*` series at `:9091` (the ones the Hardware & Power board reads): `nvidia_gpu_utilization_percent`, `_temperature_celsius`, `_power_draw_watts`, `_power_limit_watts`, `_memory_used_mib`/`_memory_total_mib` (÷1024 → GB), `_fan_speed_percent`, `_clock_graphics_mhz`. `driver`/`procs` aren't in the exporter → left empty in live (no fabricated data); `utilHist`/`tempHist` seed flat-real and the live poll shifts real samples in. The `app.jsx` GPU timer polls real `gpu()` in live mode (mock keeps the local jitter).
- [x] **Step 2: Verified** — all 8 scalars resolve against real Prometheus (idle box: 0% util, 35°C, 65/600 W, 2.5/31.8 GB).
- [x] **Step 3: Committed.**

### Task 5.3: Guarded restart endpoint (backend, TDD) — ⏭ DEFERRED (follow-up PR)

**Files:**

- Create: `src/cofounder_agent/routes/admin_routes.py`
- Test: `src/cofounder_agent/tests/unit/routes/test_admin_restart.py`
- Modify: `src/cofounder_agent/utils/route_registration.py` (mount the router)

The console's "restart service" has no backend. Add a minimal, **allow-listed** restart that the operator can trigger (`feedback_self_heal_not_suppress` — operator action of last resort; brain self-heals first).

- [ ] **Step 1: Write failing tests:** `POST /api/admin/restart {service}` (a) 401 without JWT, (b) 400 for a service not on the allow-list, (c) 200 + dispatch for an allow-listed service. Mock the docker/compose call.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `admin_routes.py`: `verify_api_token` dep, an allow-list from `app_settings` (`console_restart_allowlist`, default the local stack services), and a dispatch to the restart mechanism (docker-compose restart of the named container) with fail-loud errors. No silent default service (`feedback_no_silent_defaults`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5:** Update `api.js` `restartService(name)` to `POST /api/admin/restart`. Remove the `TODO(live)` note in the README.
- [ ] **Step 6: Commit.**

---

## Phase 6 — Cost reframed: energy + LLM/API spend (not cloud egress)

> Outcome: the Cost panel tells the truth — infra is ~$0/mo self-hosted; the real levers are **energy (kWh via cost_guard)** and **LLM/API spend against cost_guard caps**, with the now-material **Anthropic API billing** surfaced (`project_anthropic_billing_split_2026_06`).

**STATUS — 6.1 + 6.2 SHIPPED (2026-06-14, this PR).** The Cost panel + drawer are reframed to the honest model, and the spend-vs-cap headline is live-wired to the one cost route that exists (`GET /api/metrics/costs/budget` — verified mounted + JWT-protected). The by-model / daily-series / energy reads have **no HTTP route**, so live mode renders them as an explicit "backend read pending" empty (never mocked) — a clean backend follow-up.

### Task 6.1: Replace cloud-spend mock with energy + API spend — ✅ SHIPPED

**Files:** `js/data.js` (`cost`), `js/panels.jsx` (`CostPanel`), `js/drawer.jsx` (`case 'cost'`), `js/api.js` (`budget()`), `js/app.jsx` (cost state + budget poll)

- [x] **Step 1:** Dropped the R2/CF/B2 `byProvider` cloud-egress mock entirely. The new model: **$0 infra** (self-hosted, honest one-liner), **LLM/API spend vs the monthly cap** (headline + meter), local **energy** (kWh × EIA rate), and **daily burn → projected**. `byModel`/`daily` keep illustrative mock in mock mode only.
- [x] **Step 2:** Live-wired the spend summary to **`GET /api/metrics/costs/budget`** (`CostAggregationService.get_budget_status` → `amount_spent`/`monthly_budget`/`percent_used`/`daily_burn_rate`/`projected_final_cost`/`alerts`/`status`) via a new `api.budget()` + a 5-min poll. The by-model / daily / energy reads are **not routed** (`get_breakdown_by_model` / `get_daily` are service-only), so live mode sets them empty and the panel + drawer render an explicit **"backend read pending"** — honest, never mocked. Verified the route is mounted (401, not 404) + registered.
- [x] **Step 3: Committed.**

### Task 6.2: Anthropic API spend tracker — ✅ SHIPPED

**Files:** `js/panels.jsx` (`CostPanel` Agent-API row), `js/data.js` (`agentApiMonth`/`agentApiNote`), `js/drawer.jsx`

- [x] **Step 1:** Added an **"Agent API"** readout to the Cost panel + drawer. There's no by-provider cost route, so Anthropic spend can't be separated from the total yet — the readout renders the honest state: `$0/mo` with the note _"scheduled agents paused 2026-06-09 · full Anthropic rate from 2026-06-15"_ (`project_anthropic_billing_split_2026_06`). When a by-provider `cost_logs` read lands, this row wires to it (backend follow-up).
- [x] **Step 2: Committed.**

---

## Phase 7 — Findings triage (NEW)

> Outcome: probe-findings routing is visible and actionable, mirroring the Findings dashboard (#461) and the `findings_list` MCP tool.

**STATUS — SHIPPED (2026-06-14, this PR).** First backend+frontend phase: a new `GET /api/findings` route (TDD) + the console Findings panel. The "actions = ack/route" idea was **dropped** — findings are delivered autonomously by the brain's `findings_alert_router` (watermark-based) and have no ack/route HTTP surface, so the panel is a **read-only triage view** (no fabricated mutation buttons, matching the self-heal-not-suppress model + the Phase 5 "run all probes" removal).

### Task 7.1: Findings route + panel — ✅ SHIPPED

**Files:** `routes/findings_routes.py` (new), `services/findings_read.py` (new), `utils/route_registration.py` (register), `tests/unit/routes/test_findings_routes.py` (new), `tests/unit/services/test_findings_read.py` (new), `js/api.js` (`findings()`), `js/data.js` (`findings` mock), `js/panels2.jsx` (`FindingsPanel`), `js/app.jsx` (state + poll + RAIL + section), `js/drawer.jsx` (`case 'findings'`)

- [x] **Step 1 (backend, TDD):** No findings HTTP read existed, so added `GET /api/findings` → `services.findings_read.read_findings`, which runs the same `audit_log` (`event_type='finding'`) query as the `findings_list` MCP tool but returns a **structured** summary: `{findings[], counts{emitted,pending}, by_kind[], by_severity[], delivery_by_kind{}, watermark, hours}`. Status mirrors the router job — `routed` (`id <= watermark`) / `PENDING` (routable, above watermark) / `log-only` (info severity). **5 route tests** (mocked contract: shape, query-param forwarding, defaults, 422 bounds, 401 auth) + **2 DB-roundtrip tests** (real SQL: rollups, status, delivery policy, kind filter, pending-only) — all 7 green; ruff + prettier clean.
- [x] **Step 2 (console):** `FindingsPanel` renders emitted/pending counts, by-severity chips, and the latest findings (kind · title · status · delivery), with the `kind → delivery` policy table + full list in the detail drawer. Read-only (no ack/route). New `Findings` rail entry + 5-min live poll; mock mode keeps `PX.findings`. Headless mock render verified (panel + drawer, 0 runtime errors).
- [x] **Step 3: Committed.**

---

## Phase 8 — Brain: real embeddings + memory/decision recall (NEW) — ✅ SHIPPED

> Outcome: the Brain panel shows the real embedding corpus and becomes a _recall_ surface (`search_memory`, `recall_decision`), not just a counter.

**Implementation note (correction):** Phase 8 carried **zero backend work** — both `GET /api/memory/stats` and `GET /api/memory/search` already existed (`memory_dashboard_routes.py`), so this was a pure console-wiring phase (no new TDD route, unlike Phase 7). The route boundary, not the mock, decides what renders: corpus (total / by-source / by-writer) + semantic search are **real**; queue depth, last-cycle, the decisions feed, growth sparkline, and the "Trigger embed cycle" button are brain-daemon internals (`brain_queue` / `brain_decisions`) with **no HTTP route** → live mode shows an honest `— · no HTTP route` state, never the mock's fabricated numbers (`feedback_no_dummy_data`). Verified: mock headless render 12/12 + 0 runtime errors; live-shape mapper sim 9/9 against the route's exact response shape.

### Task 8.1: Real embedding stats — ✅ SHIPPED

**Files:** `js/api.js` (`memoryStats` now maps the live shape → panel shape), `js/data.js` (`brain` reshaped to real source_tables + `byWriter`), `js/app.jsx` (live `brain` state + 60s `memoryStats` poll; `PX.brain` → live `brain` across panel/drawer/wall), `js/panels.jsx` (`BrainPanel` honest-empty queue/decisions), `js/modes.jsx` (wall Embed-Queue KPI honest-empty), `js/drawer.jsx` (real `byWriter` table w/ staleness; growth/recent honest-empty in live)

- [x] **Step 1:** Mapped `/api/memory/stats` (`total` → `totalEmbeddings`, `by_source_table` → `bySource`, `by_writer` → `byWriter` w/ staleness) onto the panel; dropped the 957 / issues-heavy mock for the real source_tables (posts/issues/audit/memory/brain/claude_sessions, ~16,932).
- [x] **Step 2: Committed.** (folded into the single Phase 8 commit)

### Task 8.2: Memory + decision search — ✅ SHIPPED

**Files:** `js/api.js` (add `memorySearch(q, opts)` → `/api/memory/search`), `js/panels2.jsx` (new `MemorySearch` widget), `js/drawer.jsx` (hosts `<MemorySearch sources={…}/>` in the Brain deep-dive)

- [x] **Step 1:** Added a recall search box → `GET /api/memory/search?q=&source_table=&limit=` rendering hits (source/writer/similarity/preview). The **"recall decision"** ask is satisfied by the same widget — a data-driven `source_table` scope select (from the live `by_source_table` keys) lets the operator scope to `memory`/`brain` for decision-log embeddings, so no dedicated `recall_decision` route was needed.
- [x] **Step 2: Committed.**

---

## Phase 9 — Media Gate-2 (mostly real; wire it) — ✅ SHIPPED

> Outcome: the Media panel lists rendered podcast/video awaiting Gate-2 and approves to the publish queue (`project_video_pipeline_workstream`; media generates on **publish**, not approve — `reference_media_gen_triggers_on_publish`).

**Implementation note (correction):** the Gate-2 surface is **`media_approval_routes.py`** (`GET /api/media-approval/pending`, `POST /api/media-approval/{post_id}/{medium}/decide`) — _not_ `video_routes.py` / `podcast_routes.py` (those are RSS/episode + generation routes). Both endpoints already existed (#1343), so this was again pure console-wiring — and the **first media mutation** surface. The decide endpoint approves/rejects (`approved=true` clears for dispatch, `false` regenerates); media still generates on publish downstream. Real: the pending queue + `gate2Pending` (= `total`). Honest-empty in live: the render-rate KPIs (`renderSuccess24h` / `dispatched` / `videosPersisted`) have no read on this route → `—`.

### Task 9.1: Wire media queue + Gate-2 approve — ✅ SHIPPED

**Files:** `js/api.js` (`mediaQueue()` → `/api/media-approval/pending`, `mediaDecide(post_id, medium, approved, notes)` → `…/decide`, + `relAge` helper), `js/panels2.jsx` (`MediaPanel` — null-safe KPIs, **reject** button, empty-state), `js/app.jsx` (`media` state + 60s poll; `A.mediaApprove`/`A.mediaReject` as optimistic mutations w/ rollback), `js/drawer.jsx` (media inbox foot → `mediaApprove`/`mediaReject`, fixing the reject that wrongly hit the post-approval endpoint)

- [x] **Step 1:** Confirmed the endpoints in **`media_approval_routes.py`** (corrected from the plan's `video_routes.py`/`podcast_routes.py`); wired the panel approve **+ reject** and the inbox `media` kind to `mediaDecide`. Optimistic queue removal + `gate2Pending` decrement, rolled back on failure.
- [x] **Step 2: Verified** — mock headless render + interaction **11/11** (panel KPIs/queue, drawer Gate-2 approve+reject, optimistic approve & reject row removal), **0 runtime errors**; live-shape mapper sim **10/10** against `list_pending`'s exact row shape (carries `post_id`+`medium` for `decide()`, null-safe quality/title, age from `created_at`). A live dev-niche render needs a real pending media row — deferred to operator (no rendered Gate-2 item queued right now).
- [x] **Step 3: Committed.**

---

## Phase 10 — Scheduled-publish queue (NEW) — ✅ SHIPPED

> Outcome: the operator sees queue depth, next slot, past-due, and the upcoming-24h table (the System Health board's scheduled-publish panel set), and can nudge the schedule.

**Implementation note (correction):** no new backend was needed — **`scheduling_routes.py`** (#1343) already exposes `GET /api/scheduling` (posts with `status='scheduled'` + a future `published_at`) plus assign / batch / **shift** / clear. So the planned "thin `GET /api/schedule` (TDD)" was unnecessary; this stayed pure console-wiring. The four stats (depth / next-slot / past-due / upcoming-24h) are **derived** from each row's `published_at` (calculated, not stored — `feedback_calculated_vs_generated`), not read from separate fields. Reschedule is a per-row **shift** (`PATCH /api/scheduling/shift`, optimistic) rather than the plan's `PUT …/status` guess. The panel is a self-contained masonry section after the approved-now `PublishQueue` (no RAIL entry — consistent with the other secondary panels).

### Task 10.1: Scheduled-publish panel — ✅ SHIPPED

**Files:** `js/panels2.jsx` (new `SchedulePanel` + `relWhen` helper, registered in the `Object.assign` export), `js/app.jsx` (`schedule` state + 60s poll + `A.scheduleShift` optimistic mutation + `sec-schedule` section), `js/api.js` (`schedule()` → `/api/scheduling`, `scheduleShift(byDelta, postIds)` → `…/shift`, + `relAge` already present), `js/data.js` (`schedule` mock — real row shape, one past-due slot)

- [x] **Step 1:** Sourced from `GET /api/scheduling` (corrected from the plan's `pipeline_tasks` + new-route guess); panel derives depth / next-slot / past-due / upcoming-24h from `published_at`; per-row **+1h / −1h** shift reschedules via `PATCH /api/scheduling/shift` (optimistic, rollback on failure). Publish-now was left to the existing approved-`PublishQueue` (the scheduled queue's job is the future slots).
- [x] **Step 2: Verified + committed.** Mock headless render + interaction **10/10, 0 runtime errors** (KPIs, derived past-due/overdue labelling, per-row shift; the optimistic shift flips a past-due row's count 1 → 0); live-derivation sim **7/7** against `scheduling_service.list_scheduled`'s exact row shape (`post_id`/`slug`/`title`/`published_at`/`status`).

---

## Phase 11 — SEO refresh loop (NEW) — ✅ SHIPPED

> Outcome: the live SEO-refresh loop (`seo.refresh.enabled=true`, #1466, `project_seo_harvest_phase2`) is visible — what's queued for refresh, what shipped.

**Implementation note:** the **only new-route TDD phase in the back half** — unlike media (#1343) / schedule (#1343) / findings, `seo_opportunities` had no console-shaped read. Added `services/seo_read.py::read_seo` + `routes/seo_routes.py` (`GET /api/seo`, OAuth-JWT) test-first. The read wraps the `seo_opportunities` lifecycle (`open → queued → refreshed → outcome-measured`) into a console summary: the actionable **queue** (open+queued, highest `gap_score` first), **recent refreshes** (rows the loop acted on, with a `baseline → outcome` SERP-position **delta** — positive = moved up, since a lower position number is better), and by-status / by-tier rollups. Read-only — the refresh loop runs autonomously, the console only observes. `Decimal`→`float` coercion at the read boundary (asyncpg returns `Decimal` for `NUMERIC`). The panel **derives** the delta arrow + colour from `baseline_position − outcome_position` (`feedback_calculated_vs_generated`), and `seo_opportunities` holds **live prod data** so the DB-roundtrip test seeds + cleans up by a scoped `test-seo-p11-` slug prefix (never a blanket `DELETE`).

### Task 11.1: SEO panel — ✅ SHIPPED

**Files:** `services/seo_read.py` (NEW — `read_seo(pool, *, limit)`), `routes/seo_routes.py` (NEW — `GET /api/seo`), `utils/route_registration.py` (+`seo_router` in `_WORKER_ROUTES`), `tests/unit/services/test_seo_read.py` + `tests/unit/routes/test_seo_routes.py` (NEW, TDD), `tests/unit/utils/test_route_registration.py` (count guard 25→26), `docs/reference/services.md` (regen → 383), `js/api.js` (`seo()` → `/api/seo`, pass-through — live shape == panel shape), `js/panels2.jsx` (new `SeoPanel` + `seoAgo` helper + `SEO_TIER_TAG`), `js/app.jsx` (`seo` state + 5-min poll + `sec-seo` section), `js/data.js` (`seo` mock — queue/refreshes/by_status/by_tier, real row shape)

- [x] **Step 1:** New-route TDD — `read_seo` over `seo_opportunities` (queue by `gap_score DESC`, refreshes by `COALESCE(outcome_measured_at, refreshed_at) DESC`, baseline→outcome delta), exposed at `GET /api/seo` (limit 1..100, default 30); panel renders the top-5 queue (tier tag + target_query + "pos X · N impr" + gap score + age) and recent refreshes (`baseline → outcome (▲/▼delta)`, coloured mint/amber/dim). Read-only — no mutation (the loop is autonomous).
- [x] **Step 2: Verified + committed.** Backend **35 tests pass** (5 route + DB-roundtrip incl. delta==6.0 from baseline12−outcome6, queue excludes refreshed+dismissed, gap DESC, `gap_score` is `float`), ruff clean, `services.md` regen → 383 with `seo_read` catalogued; mock headless render **8/8, 0 runtime errors**; live path validated end-to-end (read_seo DB-roundtrip + panel-consumes-mock-which-mirrors-live-shape + `api.seo()` pass-through).

---

## Phase 12 — Live feed, voice, and ship-it actions — ✅ SHIPPED

> Outcome: the audit feed shows **real** events (stop fabricating lines), the voice button opens the real join surface, and the operator can trigger a static-export rebuild.

**Implementation note:** pure console-wiring — both endpoints already exist (`GET /api/pipeline/events` from `pipeline_events_routes.py`, `POST /api/export/rebuild` from `cms_routes.py`), so no new backend. **Two corrections to the plan:** (1) the voice URL is **not** hardcoded to the tailnet host — that would leak operator infra into the public-mirror console AND trip the sync `LINE_REDACT_RE` (`nightrider|taild4f626`). Instead `A.voice` reads `app_settings.voice_agent_public_join_url` via `GET /api/settings?search=…` and opens it only when set (honest "voice not configured" toast otherwise) — `feedback_db_first_config` + `feedback_no_operator_info_to_public_repo`. (2) Since the feed renders each line's `html` via `dangerouslySetInnerHTML`, the event→feed-line mapper **escapes every interpolated value** (`escHtml`) — `audit_log.details` carries LLM/research-derived strings (reviewer feedback, topic titles, exception text) that are untrusted; only the wrapping `<b>`/`<span class="c-*">` markup is author-controlled.

### Task 12.1: Real audit feed — ✅ SHIPPED

**Files:** `js/api.js` (`pipelineEvents()` now maps `events[]` → feed-lines via new `eventToFeedLine` + `escHtml` helpers; endpoint-map comment), `js/app.jsx` (feed init empty on live; mock simulator guarded by `isLive()`; new live-poll effect)

- [x] **Step 1:** On live, `pipelineEvents()` fetches `GET /api/pipeline/events?limit=50&since_minutes=120` and maps each flattened `audit_log` event onto the feed-line shape (`{id, ts, tag:[tone,label], html}`) — `qa_decision`/`qa_aggregate` → mint/red by `approved`, rewrite → amber, task lifecycle → cyan/mint, fallback tone from `severity`. A new app.jsx effect polls every 5 s and prepends new lines deduped by `audit_log` event id; the feed starts **empty** on live (never the mock seed, `feedback_no_dummy_data`). The random `liveTemplates` simulator is now mock-only (guarded by `isLive()`). SSE noted as a future swap, not now.
- [x] **Step 2: Verified + committed.**

### Task 12.2: Voice + rebuild actions — ✅ SHIPPED

**Files:** `js/api.js` (`voiceJoinUrl()` settings-read, `rebuildExport()` → `POST /api/export/rebuild`), `js/app.jsx` (`A.voice` rewritten to open the configured URL, new `A.rebuild` optimistic action, new ⌘K "Rebuild static export" palette command under a new "Actions" group)

- [x] **Step 1:** `A.voice` opens `app_settings.voice_agent_public_join_url` in a new tab (config-driven, mirror-safe — see note) with an honest-empty toast when unset. `A.rebuild` POSTs `/api/export/rebuild` (optimistic toast + `PUBLISH` feed line, rollback toast on failure), reachable from the command palette.
- [x] **Step 2: Verified + committed.** Verification ran the **real** shipping code: mock-render regression (index.html mounts, **0 runtime errors**, seed feed renders, palette exposes "Rebuild static export") + an isolated harness exercising `PX.api.pipelineEvents()` against the exact `/api/pipeline/events` shape — **22/22**, including all 8 event-type→tone/label mappings, id/order preservation, and the **XSS escaping** (a `<img onerror>` reviewer name + `<script>` topic both escaped; no raw markup survives), plus `voiceJoinUrl` present/empty and `rebuildExport` POST.

---

## Phase 13 — Revenue (honest empty state) + polish — ✅ SHIPPED

> Outcome: revenue renders an explicit pre-revenue empty state (no fabricated $); mobile-first layout is verified; the README is rewritten to the truth; optional build-step is documented.

**Implementation note:** the final phase, all console-only. **Two plan corrections:** (1) revenue touched **three** files, not two — the fabricated mock also fed a `RevenuePanel` _and_ a drawer detail whose `r.byType[0][1]` would crash on an empty array, so `drawer.jsx` needed an empty-state branch + guard too. (2) Mobile was **not** a from-scratch build — a `@media (max-width:920px)` breakpoint already collapsed the rail→bottom-bar / masonry→1-col; the real defect (found only by measuring at 390px) was that the bottom bar's **11 nav buttons computed to a 708px-wide box** clipped by `.app{overflow:hidden}`, leaving the later tabs (Cost/Revenue) off-screen and untappable. Fixed with `grid-template-columns: minmax(0,1fr)` (let the column shrink below content min-content) + `.rail__btn{flex:1 1 0;min-width:0}` (buttons share the bar evenly), re-measured: rail box 390px, last button right-edge 384 ≤ 390.

### Task 13.1: Revenue empty-until-live — ✅ SHIPPED

**Files:** `js/data.js` (`revenue` mock zeroed + `live:false`), `js/panels2.jsx` (`RevenuePanel` empty-state — _panel lives in panels2, not panels.jsx_), `js/drawer.jsx` (revenue detail empty-state + `byType[0]` guard)

- [x] **Step 1:** Replaced the fabricated $1,284/mo + fake orders/top-posts with an honest pre-revenue shape (`live:false`, all scalars 0, arrays empty). Both surfaces branch on `live`: the panel shows `$0` / "billing not live yet · Lemon Squeezy store gated" (MoM delta + daily bars + top-posts dropped, div-by-zero on `prevMonth` guarded); the drawer shows a "billing isn't live yet…" status block instead of empty charts. No live `/api/revenue` route exists, so this is genuinely honest-empty in both modes (`feedback_no_dummy_data`), not a missing wire.
- [x] **Step 2: Verified + committed.**

### Task 13.2: Mobile-first pass — ✅ SHIPPED

**Files:** `css/console.css` (the `@media (max-width:920px)` block — `minmax(0,1fr)` app column + flex-shrink rail buttons + `overflow-x` safety net). `modes.css` already handled the mode surfaces; no change needed.

- [x] **Step 1:** Diagnosed at 390×844 with Playwright (not assumed). The structural collapse already existed; the fix was the clipped 11-button rail (see note). Re-verified: no document h-overflow, rail box 390px, every nav button on-screen, masonry single-column, 0 runtime errors.
- [x] **Step 2: Verified + committed.** 10/10 at 390px; screenshot confirmed the revenue drawer's pre-revenue state renders full-width on mobile.

### Task 13.3: README + docs truth pass — ✅ SHIPPED

**Files:** `src/cofounder_agent/console/README.md` (full rewrite)

- [x] **Step 1:** Replaced the "paste your `API_TOKEN`" auth section with the **OAuth 2.1 client-credentials** runbook (`poindexter auth register-client` → `setClient` → `/token`); rewrote the endpoint table to the **verified** 17-surface map (approvals/tasks corrected, GPU+service-health on Prometheus `:9091`, findings/media/schedule/seo/voice/rebuild added); dropped the stale "add a mount to `main.py`" (it's mounted at `/console/`) and the false "all confirmed" line; kept the one real `TODO(live)` (restart → brain-via-DB, Phase 5.3); noted the Grafana-replacement posture (Tempo/Pyroscope/Loki stay external) and the mobile bottom-tab-bar behaviour.
- [x] **Step 2: Verified + committed.**

### Task 13.4 (optional/future): build step — ✅ DOCUMENTED (not implemented)

- [x] The README "No build step" note now documents the future esbuild/vite precompile (bundle the `.jsx`, drop the Babel runtime) as an explicit follow-up — not done here, per the task.

---

## ✅ PLAN COMPLETE (2026-06-13)

All phases 0–13 shipped via squash-merged PRs against `main`. The operator console is live at `/console/`: real auth (OAuth2 client-credentials), real reads across approvals/tasks/pipeline-events/findings/brain/media/schedule/seo/cost/GPU/service-health, real mutations (approve/publish/retry/cancel/media-decide/reschedule/rebuild), honest empty-states everywhere a live source is absent (revenue, restart), and a verified 390px mobile layout. Remaining deferred item: the guarded restart route (Phase 5.3, brain-via-DB) — left as the single `TODO(live)`.

---

## Post-completion gap fix — overview KPI strip live-wire (2026-06-21)

Every panel was wired live, but the **headline overview KPI strip** was missed: `App` rendered `<KpiStrip kpis={PX.kpis} …/>` (the static mock) with **no live effect**, so in live mode it showed stale mock numbers that contradicted the live panels on the same screen — e.g. KPI "Cloud Spend $11.42 / $50" while the live Cost panel correctly read "$35.54 / $150", and KPI "Awaiting Approval 4" while the inbox read "Inbox zero".

**Fix:** a pure mapper `js/kpis.js` (`PX.kpisFromLive`, dual-mode browser + CommonJS) projects the real reads onto the strip in live mode; `app.jsx` adds a 5-min `kpiReads` effect (`GET /api/posts` + `/api/analytics/views`) and a `kpis` memo that composes them with the already-loaded `cost` and `inbox` state. Per-KPI source: **spend** ← the same `budget()`-loaded `cost` the Cost panel renders (cannot drift); **awaiting-approval** ← the live `inbox`; **published-30d** ← `/api/posts` (count + a real per-day histogram from the same rows); **page-views-24h** ← `/api/analytics/views?days=1`; **quality** & **failed** ← honest `—` (no `quality_score` on `/api/posts`, no 24h-failed route) — `feedback_no_dummy_data`, the brain `queueDepth → —` pattern.

**Adjacent panels evaluated (task ask):** `RevenuePanel` (raw `PX.revenue`) and `QAPanel` (raw `PX.qa`) are now documented **intentionally static** at their call sites — Revenue is pre-revenue/billing-gated with no `/api/revenue` read (renders honest `$0` via `live:false`); QA's rail list IS the real config and the panel already branches on `isLive()` for its meta (graduating a rail is a `qa_gates.<rail>.required_to_pass` change). `WallDisplay` stays on `PX.kpis` (ambient mode with hardcoded scaffolding + a numeric `.toFixed` on spend that honest-empty `—` would throw on) — documented; a separate change.

**Tests:** the honest-empty mapping is the one branch-heavy bit, so `js/kpis.js` is a pure (dual-mode) function with a `node:test` contract test at `js/__tests__/kpis.test.js` (9 cases) that joins the **console-unit harness** added in #1806 (`npm run test:console` → `node --test "js/__tests__/**/*.test.js"`; standalone: `node --test src/cofounder_agent/console/js/__tests__/kpis.test.js`). Unlike `api.token.test.js` (which vm-evaluates the `api.js` IIFE against a browser shim), `kpis.js` exports cleanly so the test requires it directly. Integration verified in real Chromium (mock no-regression + simulated-live: spend `$35.54`, approval `0`, published `3`, views `42`, quality/failed `—`, zero page errors).

---

## Self-review checklist (run before execution)

- **Spec coverage:** every gap from the cross-reference maps to a task — auth (0.2/0.3), approvals+publish (1.x), pipeline model (3.x), topics (4.x), health/probes/restart (5.x), cost reframe (6.x), findings (7.x), brain/memory (8.x), media (9.x), schedule (10.x), SEO (11.x), live feed/voice/rebuild (12.x), revenue/mobile/docs (13.x).
- **No fabricated data:** every panel without a live source renders an explicit empty/"not wired" state.
- **Contract accuracy:** all live endpoints trace to a verified route in the table above; the four non-existent ones are each resolved (rewire or new TDD route).
- **Sequencing:** Phase 0 gates everything (load + auth). Phases 1–2 deliver two genuinely-live, high-value surfaces fast. Later phases are independently shippable.

## Open decisions to confirm with the operator

1. **Restart scope (Task 5.3):** build the guarded `/api/admin/restart` now, or defer and keep services read-only + deep-link to Grafana/docker? (Plan assumes build-it, allow-listed.)
2. **Backend reads for Findings/Schedule/SEO/Cost-spend:** add thin `GET` routes as needed (plan's default), or gate those panels behind "render empty until a route exists"?
3. **Phase order after 0–3:** Topics (4) vs Health (5) first? (Plan does 4 → 5; both are high-value.)
