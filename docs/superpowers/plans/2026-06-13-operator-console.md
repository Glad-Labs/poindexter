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

**Endpoints the console assumes that DO NOT exist** (and how the plan resolves them): `GET /api/approvals*` (→ use `/api/tasks/*` above), `POST /api/tasks/{id}/retry` (→ `PUT …/status`), `POST /api/tasks/{id}/cancel` (→ `DELETE`), `GET /probes` for service health (→ Prometheus `up{}` + `/api/health`, Phase 5), `POST /api/admin/restart` / `run-probes` (→ new guarded route, Phase 5), topic-triage actions (MCP-only today → new HTTP routes, Phase 4).

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

## Phase 4 — Topics triage (NEW surface; needs thin HTTP routes)

> Outcome: the operator can review, rank, resolve, and reject proposed topics from the console. **Reality:** the triage logic exists as MCP tools (`topics_rank_batch / reject_batch / resolve_batch / edit_winner / show_batch`) but **not** as HTTP routes — only `POST /api/topics/from-url(s)` exist (`topics_routes.py`). This phase adds the HTTP surface over the same service.

### Task 4.1: Expose topic-triage over HTTP (backend, TDD)

**Files:**

- Modify: `src/cofounder_agent/routes/topics_routes.py`
- Test: `src/cofounder_agent/tests/unit/routes/test_topics_triage_routes.py`

- [ ] **Step 1: Write failing tests** for `GET /api/topics/proposals` (list pending batch) and `POST /api/topics/{id}/resolve` / `/reject` / `/rank`, asserting they call the existing topic-proposal service (the same one the MCP tools use — find it via the MCP tool implementation `topics_resolve_batch`).
- [ ] **Step 2: Run** → FAIL (routes absent).
- [ ] **Step 3: Implement** the routes delegating to the shared service; auth via `verify_api_token`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit.**

### Task 4.2: Topics panel in the console

**Files:** Modify `js/api.js` (add `topics*` methods), `js/panels2.jsx` (new `TopicsPanel`), `js/app.jsx` (rail entry + section), `js/data.js` (mock shape)

- [ ] **Step 1:** Add `RAIL` entry `{ id:'topics', icon:'overview', label:'Topics' }` and a `TopicsPanel` rendering the pending batch with rank/resolve/reject actions.
- [ ] **Step 2:** Wire to the Phase-4.1 routes; optimistic update + error toast.
- [ ] **Step 3: Verify** live against a real proposal batch.
- [ ] **Step 4: Commit.**

---

## Phase 5 — Health & Services: a real truth source (drop the fictional probes)

> Outcome: service health is sourced from Prometheus `up{}` (`:9091`) + `/api/health`; the GPU HUD uses real `nvidia_gpu_*` series; the service list reflects the real container topology; and the operator gets a **guarded restart** action.

### Task 5.1: Service health from Prometheus + `/api/health`

**Files:** Modify `js/api.js` (`probes()` → real health), `js/data.js` (`services`), `js/panels.jsx` (`ServiceGrid`)

- [ ] **Step 1:** Replace `probes()`'s `GET /probes` live branch with: (a) `GET /api/health` for the worker, and (b) Prometheus `up{}` instant queries per service for the rest. Delete the "28 probes / Run all 28" UI and the `runAllProbes`/`/api/admin/run-probes` calls (the brain's probes have no HTTP surface; alerts arrive via Telegram/AlertManager).
- [ ] **Step 2:** Update the **real container list** in `data.js`: `poindexter-worker` (API, :8002), **`poindexter-prefect-worker`** (pipeline — the one that actually runs content), `poindexter-brain-daemon`, `postgres`, `ollama`, `sdxl-server`, `prometheus` (:9091), `grafana`, `loki`, `tempo`, `alertmanager`, `langfuse`, `glitchtip`, `pgadmin`, `pyroscope`, `uptime-kuma`, `livekit`, speaches sidecar(s). Mark which are tailnet-only.
- [ ] **Step 3: Verify** the grid against `docker ps` + `up{}` in Prometheus.
- [ ] **Step 4: Commit.**

### Task 5.2: GPU HUD on real metrics

**Files:** Modify `js/api.js` (`gpu()` ~`:290-303`)

- [ ] **Step 1:** Point the Prometheus base at `:9091` and the queries at the real exporter series `nvidia_gpu_*` (single-sourced per #653) — utilisation, temp, power, VRAM used/total, clock. Confirm exact metric names with `mcp__grafana__list_prometheus_metric_names` or the Hardware & Power dashboard's panel queries.
- [ ] **Step 2: Verify** the HUD against the Hardware & Power Grafana board.
- [ ] **Step 3: Commit.**

### Task 5.3: Guarded restart endpoint (backend, TDD)

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

### Task 6.1: Replace cloud-spend mock with energy + API spend

**Files:** Modify `js/data.js` (`cost`), `js/panels.jsx` (`CostPanel`)

- [ ] **Step 1:** Drop the R2/CF/B2 `byProvider` mock. Model: energy (watt-hours/kWh from `cost_guard` + EIA rate), LLM spend by model/tier from `cost_logs`, and the daily/monthly caps (`daily_spend_limit_usd` / `monthly_spend_limit_usd` — the keys fixed in #598). Render `$0` infra honestly.
- [ ] **Step 2:** Source live values from `GET /api/analytics/views` siblings / a thin cost read (the Cost & Analytics Grafana board's queries are the reference; if no HTTP route exists, render the energy/caps from settings + note the spend read as a backend follow-up — empty, not mocked).
- [ ] **Step 3: Commit.**

### Task 6.2: Anthropic API spend tracker

**Files:** Modify `js/panels.jsx` (`CostPanel` sub-section)

- [ ] **Step 1:** Add a small "Agent API spend" readout (the scheduled-agent fleet billing that goes full-rate 2026-06-15). Wire to `cost_logs` filtered to the API provider once the read exists; until then, render an explicit "not wired" state.
- [ ] **Step 2: Commit.**

---

## Phase 7 — Findings triage (NEW)

> Outcome: probe-findings routing is visible and actionable, mirroring the Findings dashboard (#461) and the `findings_list` MCP tool.

### Task 7.1: Findings panel

**Files:** Modify `js/panels2.jsx` (new `FindingsPanel`), `js/app.jsx` (section), `js/api.js`

- [ ] **Step 1:** Source from `audit_log` where `event_type='finding'` (the Findings board's source). If no HTTP read exists, add a thin `GET /api/findings` route (backend, TDD, same pattern as Task 4.1) delegating to the findings service behind `findings_list`.
- [ ] **Step 2:** Render emitted vs pending-delivery counts, by-kind/severity, and the `kind → delivery` policy; actions = ack/route.
- [ ] **Step 3: Commit.**

---

## Phase 8 — Brain: real embeddings + memory/decision recall (NEW)

> Outcome: the Brain panel shows the real embedding corpus and becomes a _recall_ surface (`search_memory`, `recall_decision`), not just a counter.

### Task 8.1: Real embedding stats

**Files:** Modify `js/api.js` (`memoryStats` already → `/api/memory/stats`), `js/data.js` (`brain`), `js/panels.jsx` (`BrainPanel`)

- [ ] **Step 1:** Map the real `/api/memory/stats` shape into the panel (corpus is ~16,932 across posts/issues/audit/memory/brain/claude_sessions — confirm the live shape at `memory_dashboard_routes.py:116`). Remove the 957 / issues-heavy mock.
- [ ] **Step 2: Commit.**

### Task 8.2: Memory + decision search

**Files:** Modify `js/panels.jsx` (`BrainPanel`), `js/api.js` (add `memorySearch(q)`)

- [ ] **Step 1:** Add a search box → `GET /api/memory/search?q=` rendering hits; add a "recall decision" lookup if an HTTP route exists (else note as follow-up).
- [ ] **Step 2: Commit.**

---

## Phase 9 — Media Gate-2 (mostly real; wire it)

> Outcome: the Media panel lists rendered podcast/video awaiting Gate-2 and approves to the publish queue (`project_video_pipeline_workstream`; media generates on **publish**, not approve — `reference_media_gen_triggers_on_publish`).

### Task 9.1: Wire media queue + Gate-2 approve

**Files:** Modify `js/api.js` (add media methods → `routes/video_routes.py` / `podcast_routes.py`), `js/panels.jsx` (`MediaPanel`), `js/app.jsx` (`A.mediaApprove`)

- [ ] **Step 1:** Confirm the media list + Gate-2 approve endpoints in `video_routes.py` / `podcast_routes.py`; wire the panel + the inbox `media` kind to them.
- [ ] **Step 2: Verify** against a real rendered item on a dev niche.
- [ ] **Step 3: Commit.**

---

## Phase 10 — Scheduled-publish queue (NEW)

> Outcome: the operator sees queue depth, next slot, past-due, and the upcoming-24h table (the System Health board's scheduled-publish panel set), and can nudge the schedule.

### Task 10.1: Scheduled-publish panel

**Files:** Modify `js/panels2.jsx` (new `SchedulePanel`), `js/app.jsx`, `js/api.js`

- [ ] **Step 1:** Source from `pipeline_tasks` (`scheduled_publisher`) — list `approved`/scheduled rows with publish times. Add a thin `GET /api/schedule` read if none exists (backend, TDD). Actions: publish-now (`/publish`), reschedule (`PUT …/status` or a schedule field).
- [ ] **Step 2: Commit.**

---

## Phase 11 — SEO refresh loop (NEW)

> Outcome: the live SEO-refresh loop (`seo.refresh.enabled=true`, #1466, `project_seo_harvest_phase2`) is visible — what's queued for refresh, what shipped.

### Task 11.1: SEO panel

**Files:** Modify `js/panels2.jsx` (new `SeoPanel`), `js/app.jsx`, `js/api.js`

- [ ] **Step 1:** Render the seo_refresh queue + recent refreshes (source from the SEO-harvest tables / settings). Backend read added TDD if absent.
- [ ] **Step 2: Commit.**

---

## Phase 12 — Live feed, voice, and ship-it actions

> Outcome: the audit feed shows **real** events (stop fabricating lines), the voice button connects the real bridge, and the operator can trigger a static-export rebuild.

### Task 12.1: Real audit feed

**Files:** Modify `js/app.jsx` (the `liveTemplates` simulator), `js/api.js` (`pipelineEvents` already → `/api/pipeline/events`)

- [ ] **Step 1:** On live, replace the random `liveTemplates` generator with a poll of `GET /api/pipeline/events` (newest-first, dedup by id). Keep the simulator for mock mode only. Consider SSE later (note, not now).
- [ ] **Step 2: Commit.**

### Task 12.2: Voice + rebuild actions

**Files:** Modify `js/app.jsx` (`A.voice`, add `A.rebuild`), `js/api.js`

- [ ] **Step 1:** `A.voice` → the real voice bridge (the `voice-bridge` MCP `voice_join_room` is session-scoped; for the console, deep-link to the tap-to-join URL `https://nightrider.taild4f626.ts.net/voice/join` rather than fake a connection). `A.rebuild` → a static-export rebuild trigger (the `rebuild_static_export` capability; wire to its route or note as follow-up).
- [ ] **Step 2: Commit.**

---

## Phase 13 — Revenue (honest empty state) + polish

> Outcome: revenue renders an explicit pre-revenue empty state (no fabricated $); mobile-first layout is verified; the README is rewritten to the truth; optional build-step is documented.

### Task 13.1: Revenue empty-until-live

**Files:** Modify `js/data.js` (`revenue`), `js/panels.jsx` (`RevenuePanel`)

- [ ] **Step 1:** Replace the fabricated $1,284/mo mock with `$0` / "billing not live yet" until the revenue engine reports real orders. Operator-specific billing wiring stays in the private overlay; the OSS panel reads the generic revenue engine.
- [ ] **Step 2: Commit.**

### Task 13.2: Mobile-first pass

**Files:** Modify `css/console.css`, `css/modes.css`

- [ ] **Step 1:** Add breakpoints so the rail collapses to a bottom tab bar, the masonry becomes a single column, and the Action Inbox + Approvals are reachable in ≤2 taps at 390px. Verify in a phone viewport (Playwright `page.setViewportSize({width:390,height:844})`).
- [ ] **Step 2: Commit.**

### Task 13.3: README + docs truth pass

**Files:** Modify `src/cofounder_agent/console/README.md`

- [ ] **Step 1:** Rewrite the endpoint map to the verified table; replace the "paste API_TOKEN" auth section with the OAuth client-credentials runbook; remove the `index.html`-as-existing claim (now true) and the false "all confirmed" line. Note the Grafana-replacement posture + which deep-dives stay external.
- [ ] **Step 2: Commit.**

### Task 13.4 (optional/future): build step

- [ ] Document (do not implement now) a future esbuild/vite precompile so the console can ship production-fast if it ever leaves local-operator use.

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
