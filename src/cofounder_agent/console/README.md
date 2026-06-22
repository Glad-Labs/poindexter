# Poindexter Operator Console

Action-first operator UI for Poindexter. Where the Grafana dashboards are
read-only, this console also lets you _act_: approve/publish content, retry or
cancel tasks, triage findings, approve Gate-2 media, reschedule the publish
queue, trigger a static-export rebuild, and edit `app_settings` inline. Built on
the `@glad-labs/brand` (E3) system.

It is meant to be the day-to-day **cockpit**; the deep-dive telemetry stays in
Grafana — Tempo traces, Pyroscope flame graphs, and the Loki log explorer are
linked out from the console rather than reimplemented.

## What's here

```
console/
├── index.html        ← entry (already served at /console/ — see §1)
├── css/              ← brand tokens + console styles
└── js/               ← React app (in-browser Babel), data, API adapter
```

It ships with realistic **mock data** so it runs with zero backend. Flip it to
**live** from the in-app Connection panel (App Settings → Connection).

---

## 1. It's already served from the worker (same-origin, no CORS)

`main.py` mounts this folder at `/console/` (`StaticFiles(..., html=True)`,
mounted **after** the API routers so it never shadows `/api`). With the worker
running:

> Open **http://localhost:8002/console/**

The static mount is **not** behind `verify_api_token`, so the page itself loads
freely; the `/api/...` calls it makes carry a short-lived OAuth JWT (below).

---

## 2. Go live (App Settings → Connection panel)

Auth is **OAuth 2.1 client-credentials only** — the static `API_TOKEN` Bearer
was removed in #249. Provision a dedicated console client, then hand the adapter
its credentials:

```bash
poindexter auth register-client \
  --name poindexter-console \
  --scopes "api:read api:write" \
  --grant-type client_credentials
# prints client_id + client_secret ONCE
```

1. **Worker base URL** — leave **blank** (same-origin → relative `/api/...`).
2. **Client ID + secret** — paste the two values from above
   (`PX.api.setClient(id, secret)` under the hood). The adapter mints a JWT from
   `POST /token` and refreshes it automatically; the secret is kept in
   `localStorage`, never sent anywhere but `/token`.
3. **Test connection** → then toggle **Live** on (`PX.api.setLive(true)`,
   persisted).

Settings then read/write your real `app_settings` table. Most read surfaces are
already wired (below); anything without a live route renders an explicit
empty/"not wired" state rather than mock numbers.

---

## 3. The API adapter — `js/api.js`

One file is the only seam between UI and your stack: `window.PX.api`. Every
method has a `live:` branch (real `fetch`) and a `mock:` branch via
`pick(liveFn, mockFn)`. Endpoint map (verified against
`src/cofounder_agent/routes/`):

| Surface           | Endpoint(s)                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| token             | `POST /token` (grant_type=client_credentials → JWT)                                                          |
| health            | `GET /api/health`                                                                                            |
| settings          | `GET /api/settings` · `PUT /api/settings/{id}`                                                               |
| approvals         | `GET /api/tasks/pending-approval` · `POST /api/tasks/{id}/{approve\|reject\|publish}` (approve ≠ publish)    |
| tasks             | `GET /api/tasks`, `/{id}` · `PUT /api/tasks/{id}/status` (retry→pending) · `DELETE /api/tasks/{id}` (cancel) |
| events            | `GET /api/pipeline/events` (the live audit feed)                                                             |
| brain / memory    | `GET /api/memory/stats` · `GET /api/memory/search`                                                           |
| cost              | `GET /api/metrics/costs/budget` (spend vs cap)                                                               |
| findings          | `GET /api/findings` (probe-routing triage, #461)                                                             |
| media (Gate-2)    | `GET /api/media-approval/pending` · `POST /{post_id}/{medium}/decide`                                        |
| schedule          | `GET /api/scheduling` · `PATCH /api/scheduling/shift` (reschedule)                                           |
| seo               | `GET /api/seo` (SEO-refresh queue + outcomes, #1466)                                                         |
| voice             | `GET /api/settings` → `voice_agent_public_join_url` (operator config)                                        |
| rebuild           | `POST /api/export/rebuild` (full static re-export + ISR revalidate)                                          |
| posts / analytics | `GET /api/posts` · `GET /api/analytics/views`                                                                |
| service health    | Prometheus `GET /api/v1/query` — cAdvisor `container_last_seen` (`:9091`) + `/api/health`                    |
| GPU               | Prometheus `GET /api/v1/query` — `nvidia_gpu_*` (`:9091`)                                                    |

### Overview KPI strip (live)

The headline KPI strip is live-wired through a pure mapper (`js/kpis.js` →
`PX.kpisFromLive`, contract-tested by `js/__tests__/kpis.test.js` via
`npm run test:console`): **spend** reuses the
same `budget()` read the Cost panel renders (so the two can't disagree),
**awaiting-approval** comes from the live inbox, **published (30d)** + **page
views (24h)** from `GET /api/posts` + `GET /api/analytics/views`, and
**avg-quality** / **failed** render an honest `—` (no backing read —
`quality_score` isn't on `/api/posts` and there's no 24h-failed route). Mock
mode keeps the static `PX.kpis`. The Revenue and QA panels are intentionally
static (documented at their call sites in `app.jsx`): Revenue is
pre-revenue/billing-gated with no `/api/revenue` read, and QA's rail list is the
real config already (graduating a rail is a `qa_gates.<rail>.required_to_pass`
change via `poindexter qa-gates require <rail>` / `… advisory <rail>`, not a
console edit).

### One `TODO(live)` spot left

- **Restart service** — there is no worker route yet; `restartService()` points
  at a placeholder `POST /api/admin/restart`. The intended wiring (Phase 5.3) is
  through the **brain via the DB spinal cord** — the console writes a restart
  intent the brain-daemon claims — not a direct container kill from the API.
  Until then, the Services panel is read-only and deep-links to Grafana/docker.

### Note on Prometheus

The local stack runs Prometheus on **`:9091`** (not the upstream default
`:9090`). Set the Prometheus base in the Connection panel if yours differs.

### Dev tip

The Connection panel has a **Dev simulation** dropdown (mock only:
normal / slow / error / empty) so you can exercise loading/error/empty states
without a backend.

---

## Notes

- **No build step.** React + Babel run in-browser via pinned vendored scripts
  (`js/vendor/`). Fine for a local operator tool. If it ever needs to ship
  production-fast, the future move is an esbuild/vite precompile of the `.jsx`
  into one bundle and dropping the Babel runtime — documented as a follow-up,
  not done here.
- **One token mint per load.** Going live mounts ~11 panels that each hit the
  API; `getToken()` coalesces their concurrent OAuth mints behind a single
  in-flight `POST /token` (and backs off + retries once on a `429`), so a live
  page load mints exactly one JWT instead of a thundering herd that trips the
  worker's rate limiter. Contract-tested in `js/__tests__/api.token.test.js`
  (`npm run test:console`; also gated by the `console-unit` CI workflow).
- **Mobile.** At ≤920px the left rail collapses to a bottom tab bar and the
  masonry becomes a single column; verified down to a 390px phone viewport.
- **Brand.** E3 tokens (cyan/amber, JetBrains Mono + Space Grotesk, square
  corners, colorblind-safe glyphs).
- **Modes.** Console / Feed / Map / Wall + ⌘K command palette + App Settings.
