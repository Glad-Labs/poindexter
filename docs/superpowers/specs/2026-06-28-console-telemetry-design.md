# Console Telemetry — Logs, Traces, and Embedded Grafana

**Date:** 2026-06-28
**Status:** Design approved, pending spec review
**Branch:** `claude/happy-chaplygin-fddc7b`

## Goal

Make the operator console (`src/cofounder_agent/console/`, served at `/console/`)
the single surface the operator looks at, so Grafana never has to be opened
directly. Driver is **UX ("one pane of glass")**, not infrastructure footprint —
Grafana keeps running underneath; we just stop typing its URL.

This spec brings the three things that still force a context-switch to Grafana
into the console:

1. **Operational logs** (today: Loki, opened in Grafana Explore)
2. **LLM traces** (today: Langfuse UI)
3. **Rich time-series history + Postgres internals** (today: Grafana dashboards)

## Decisions locked during brainstorming

| Decision                   | Choice                                        | Why                                                                      |
| -------------------------- | --------------------------------------------- | ------------------------------------------------------------------------ |
| Driver                     | One pane of glass (UX)                        | Bar is functional completeness, not resource savings                     |
| "Completely replace" means | Out of my face — Grafana may keep running     | Cheapest path that satisfies the goal                                    |
| Scope                      | Logs + traces + embedded Grafana              | Full "never open Grafana" coverage                                       |
| Langfuse waterfall         | **Deeplink to a separate tab** (not embedded) | Waterfall UI too costly to rebuild or embed; list-in-pane is the 80/20   |
| Logs rendering             | **Native** (worker proxy → Loki)              | Loki JSON is simple; brand-matched; no iframe                            |
| Traces list rendering      | **Native** (worker proxy → Langfuse)          | Langfuse secret key must stay server-side                                |
| History + DB charts        | **Embedded** Grafana `/d-solo` iframes        | Native multi-series time-range charting is the rejected months-long path |

## Grounding facts (verified in tree)

- The console already queries Prometheus **directly** at `localhost:9091`
  (`js/api.js:63,166`) because Prometheus returns `Access-Control-Allow-Origin: *`.
  Configurable via the Connection panel (`px_prom` localStorage key).
- **Loki does not send CORS headers by default** and **Langfuse's read API needs a
  secret key** → both must go through worker proxy routes, not direct browser fetch.
- Worker already knows the backends: `data_fabric_loki_url = http://loki:3100`,
  `langfuse_host` (settings_defaults.py:848,859). No `langfuse_public_key` /
  `langfuse_secret_key` defaults exist yet — they must be added (secret).
- The console's `A.logs` handler (`js/app.jsx:803`) is a placeholder toast today.
- Auth: console mints OAuth client-credentials JWTs for `/api/...` calls
  (README §2). The new routes sit behind the same `verify_api_token` middleware.

## Architecture

A new `telemetry` rail item hosts three stacked panels — **Logs**, **Traces**,
**Grafana** — so the Grafana brand seam (its skin vs. the console's E3 cyan/amber)
is contained to one tab and never bleeds into the action cockpit.

```
console rail: overview · pipeline · topics · social · brain · gpu · services ·
              audit · findings · cost · revenue · [NEW] telemetry
```

### 1. Logs panel (native, Loki proxy)

**Worker route:** `GET /api/logs`

- Query params: `query` (LogQL, optional — defaults to all), `service` (label
  filter), `level` (label/line filter), `since` (default `1h`), `limit` (default
  500, hard-capped at 1000).
- Proxies Loki `GET /loki/api/v1/query_range` using `data_fabric_loki_url`.
- Returns a flattened, console-friendly shape:
  `{ "lines": [{ "ts": <iso>, "service": <str>, "level": <str>, "line": <str> }], "stats": {...} }`.
- Behind `verify_api_token`. No inline SQL (it's an HTTP proxy), so the
  adapter-purity lint is satisfied by construction.

**Console `LogsPanel`** (`panels2.jsx`):

- Service + level filter chips, a query box, live-tail by polling `/api/logs`
  on a short cadence (default 10s; pause toggle).
- Lines colored by level, reusing the audit-feed visual vocabulary.
- Honest-empty when no backend / no rows (`feedback_no_dummy_data`).

### 2. Traces panel (native list + deeplink waterfall, Langfuse proxy)

**Worker route:** `GET /api/traces`

- Query params: `since` (default `24h`), `limit` (default 50, capped 200),
  optional `task_id` to scope to one pipeline task.
- Proxies Langfuse public traces API (`{langfuse_host}/api/public/traces`) with
  Basic auth from `langfuse_public_key` + `langfuse_secret_key` read via
  `site_config.get_secret(...)` (async; never cached, never sent to the browser).
- Returns rows: `{ "traces": [{ "id", "name", "model", "latency_ms", "cost_usd",
"qa_score", "task_id", "timestamp" }], ... }`.
- Fail-loud 503 with remediation text when the Langfuse keys are unset (mirrors
  the FinanceModule pattern), never a silent empty.

**Console `TracesPanel`** (`panels2.jsx`):

- Recent traces as rows: model · latency · cost · QA score, tied to the task.
- Each row has an **"open waterfall"** action that deeplinks to
  `{langfuse_host}/trace/{id}` in a **new tab**
  (`window.open(..., '_blank', 'noopener')`). The full waterfall stays in
  Langfuse — by design.

### 3. Embedded Grafana panel (history + DB)

**Console `GrafanaEmbed`** (`panels2.jsx`): renders `<iframe>` elements pointing
at `{px_grafana}/d-solo/<uid>?panelId=<N>&theme=dark&kiosk` (base from the
`px_grafana` localStorage config) for:

- Rich time-series history charts (spend-over-time, throughput-24h, GPU history)
  — the multi-series time-range views the console's instant Prometheus queries
  can't render.
- The Postgres internals board (`/d/database`) — connections, dead tuples,
  cache-hit ratio, slow queries.

**Grafana config (compose):**

- `GF_SECURITY_ALLOW_EMBEDDING=true`
- `GF_AUTH_ANONYMOUS_ENABLED=true` + `GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer`
  (safe — Grafana is local/tailnet-only; Grafana Cloud was retired 2026-05-03).
- Embed base is the client-side `px_grafana` localStorage config (default
  `http://localhost:3000`), surfaced in the Connection panel alongside `px_prom`.

## Settings & config

**Worker-side (`app_settings`):**

| Key                   | Where it lives               | Notes                                                                                                                       |
| --------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `langfuse_public_key` | **secret** (not in DEFAULTS) | matches `*_key` → auto-classified secret, auto-encrypted, masked in console; operator provisions via `set_secret` / `setup` |
| `langfuse_secret_key` | **secret** (not in DEFAULTS) | same — read server-side only via `get_secret`                                                                               |

`langfuse_host` already exists in DEFAULTS (`''`); the traces proxy reads it via
`site_config.get('langfuse_host', '')` for both the API base and the per-trace
deeplink. **Important:** `settings_defaults.py` is committed source whose DEFAULTS
registry is for **non-secret** keys only — keys matching `*_key`/`*_secret`/`*_password`
are deliberately excluded (the auto-encrypt trigger from migration 0130 would otherwise
bury a bogus ciphertext). So the two Langfuse keys are **never** seeded; they stay unset
until the operator provisions them. No new non-secret `app_settings` key is needed.

**Client-side (console localStorage), mirroring the existing `px_prom`:**

- `px_grafana` — Grafana embed base URL, default `http://localhost:3000`, settable in
  the Connection panel via a new `PX.api.setGrafanaEmbed(url)` (exactly like
  `setPrometheus`). The console hits Grafana's iframe URLs directly (browser → Grafana),
  same pattern as it already hits Prometheus directly.

**Deeplinks built server-side:** the `/api/traces` response includes a per-trace
`web_url = "{langfuse_host}/trace/{id}"`, so the browser never needs `langfuse_host`
itself — it just opens `row.web_url` in a new tab.

## Files touched

**Worker**

- `routes/logs_routes.py` (new) — `GET /api/logs` Loki proxy
- `routes/traces_routes.py` (new) — `GET /api/traces` Langfuse proxy
- `services/logs_read.py` + `services/traces_read.py` (new) — thin proxy read-services (route stays a serializer, mirroring `findings_read.py`)
- `utils/route_registration.py` — register the two routers in `_WORKER_ROUTES`
- No `settings_defaults.py` change — Langfuse keys are secrets (not seeded); Grafana base is client-side `px_grafana`
- Reuse the shared `app.state.http_client` (`httpx.AsyncClient`) for both proxies

**Console**

- `js/api.js` — `logs()` + `traces()` adapter methods (live `fetch` + mock branch
  via `pick`), `setGrafanaEmbed(u)` like `setPrometheus`
- `js/app.jsx` — `telemetry` rail item, `sec-telemetry` section, state + polling
  effects for logs/traces (live-only; mock keeps static seed)
- `js/panels2.jsx` — `LogsPanel`, `TracesPanel`, `GrafanaEmbed`
- `js/data.js` — realistic mock logs + traces for zero-backend mode
- `console/README.md` — document the new surface, routes, and Grafana embed config

**Infra**

- Grafana service in the compose file(s) — embedding + anonymous-viewer env vars

## Testing & docs (ship in the same change)

Per `feedback_docs_and_tests_default`:

- Worker route tests under `tests/unit/routes/` — proxy shaping, param clamping
  (`limit`/`since` caps), 503-on-missing-Langfuse-keys, auth required.
- Console contract tests via `npm run test:console` — `logs()`/`traces()` mappers
  (mock + live-shape), gated by the existing `console-unit` CI workflow.
- README + CLAUDE.md monitoring section updated.

## Risks & mitigations

1. **Grafana anonymous embed** — the one config touch outside code. Scoped to
   `Viewer` org role on a local/tailnet-only Grafana; no public exposure.
2. **Langfuse secrets** — stored as `app_settings` secrets, read via async
   `get_secret`, never cached, never sent to the browser. The proxy is the only
   reader.
3. **Loki query volume** — `limit` capped at 1000, `since` defaults to `1h`, so a
   broad query can't hammer the worker or Loki.
4. **Brand seam** — embedded Grafana won't match the E3 skin. Accepted, contained
   to the Telemetry tab. A custom Grafana theme is a future polish, not in scope.

## Out of scope / follow-ons

- Removing Grafana / the observability tier (the "actually gone" path) — only
  relevant if footprint ever becomes the driver (e.g. the consumer-tier hardware
  push, #1924). Native PromQL/Loki charting in the console would be required then.
- Native Langfuse waterfall rendering — deliberately deeplinked instead.
- Custom Grafana theme to match E3 brand tokens.
- Embedding Tempo trace waterfalls / Pyroscope flame graphs natively — still
  reached via Grafana deeplink from the Telemetry tab.
