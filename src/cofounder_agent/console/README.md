# Poindexter Operator Console — deploy

Action-first operator UI for Poindexter. Replaces the read-only Grafana
dashboards with a console that also lets you _act_: approve content, retry/kill
tasks, ack alerts, fix URL drift, restart services, and edit `app_settings`
inline. Built on the `@glad-labs/brand` (E3) system.

## What's here

```
console/
├── index.html        ← entry (open this)
├── css/              ← brand tokens + console styles
└── js/               ← React app (in-browser Babel), data, API adapter
```

It ships with realistic **mock data** so it runs with zero backend. Flip it to
**live** from the in-app Connection panel (App Settings → top of page).

---

## 1. Serve it from the worker (recommended — same-origin, no CORS)

Add a static mount to `src/cofounder_agent/main.py`, **after** the routers are
included so it doesn't shadow `/api`:

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Operator console (static SPA). html=True serves index.html at /console/.
app.mount(
    "/console",
    StaticFiles(directory=Path(__file__).parent / "console", html=True),
    name="console",
)
```

Then with the worker running (`python -m uvicorn main:app --host 0.0.0.0 --port 8002`):

> Open **http://localhost:8002/console/**

The static mount is **not** behind `verify_api_token`, so the page loads
freely; the `/api/...` calls it makes carry your bearer token (below).

---

## 2. Go live (App Settings → Connection panel)

1. **Worker base URL** — leave **blank** (same-origin → relative `/api/...`).
2. **Bearer token** — paste your `API_TOKEN` value.
3. **Test connection** → then toggle **Live** on.

Settings now read/write your real `app_settings` table. Start here — it's the
safest read+write surface. Other panels still show mock data until their
endpoints are wired (see the adapter).

---

## 3. The API adapter — `js/api.js`

One file is the only seam between UI and your stack: `window.PX.api`. Every
method has a `live:` branch (real `fetch`) and a `mock:` branch. Flip surfaces
on **one at a time**. Endpoint map (all confirmed in
`src/cofounder_agent/routes/`):

| Surface           | Endpoint                                                            |
| ----------------- | ------------------------------------------------------------------- |
| settings          | `GET /api/settings` · `PUT /api/settings/{id}`                      |
| approvals         | `GET /api/approvals` · `POST /api/approvals/{id}/{approve\|reject}` |
| tasks             | `GET /api/tasks`, `/{id}` · `POST /api/tasks/{id}/{retry\|cancel}`  |
| events            | `GET /api/pipeline/events`                                          |
| brain / memory    | `GET /api/memory/stats`                                             |
| probes            | `GET /probes`                                                       |
| posts / analytics | `GET /api/posts` · `GET /api/analytics/views`                       |
| GPU               | Prometheus `GET /api/v1/query` (`:9090`)                            |

### Two `TODO(live)` spots that need your specifics

- **GPU** — set the Prometheus URL and match metric names to your exporter
  (DCGM vs `nvidia_gpu_exporter`). Search `gpu()` in `js/api.js`.
- **Restart service** — no worker route exists yet; it points at a placeholder
  `POST /api/admin/restart`. Wire it to your brain/docker mechanism.

### Dev tip

The Connection panel has a **Dev simulation** dropdown (mock only:
normal / slow / error / empty) so you can see loading/error/empty states
without a backend.

---

## Alternative: don't want to touch `main.py`?

Serve the folder with any static server on the **same origin** as the API, or
run a small sidecar (`serve.py` with CORS) that proxies to the worker. Ask and
it can be scaffolded.

---

## Notes

- **No build step.** React + Babel run in-browser via pinned CDN scripts. Fine
  for a local operator tool; precompile if you ever want it production-fast.
- **Brand.** Uses the E3 tokens (cyan/amber, JetBrains Mono + Space Grotesk,
  square corners, colorblind-safe glyphs).
- **Modes.** Console / Feed / Map / Wall + ⌘K command palette + App Settings.
