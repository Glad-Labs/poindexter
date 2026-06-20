# sentry-relay (Cloudflare Worker)

Public, hardened front door that lets a self-hosted, LAN-only error tracker
(GlitchTip / any Sentry-compatible ingest) receive errors from **public
visitors' browsers**. The browser Sentry SDK tunnels error envelopes to this
Worker; the Worker validates them and forwards the raw envelope to the
tracker's ingest endpoint, which is reachable from the Cloudflare edge via a
**path-scoped Tailscale Funnel** (URL kept as a Worker secret).

## Why a relay

The public site is served from Vercel; the error tracker is self-hosted on the
operator's LAN. **Neither Vercel functions nor public browsers can reach the
LAN.** The Sentry SDK's same-origin `tunnelRoute: '/monitoring'` is a Vercel
route, so it had nowhere to forward to — frontend errors silently went nowhere
even though the privacy policy advertised error monitoring as active. (Same
wall that retired the old same-origin `/api/page-views` route.)

This Worker closes the gap the same way the **page-views-beacon** does
(browser → CF edge → operator backend), with two differences: it _forwards_
the envelope (rather than writing to Analytics Engine), and the hop from the
edge to the LAN is a **Tailscale Funnel** instead of a CF-hosted sink.

```
visitor browser ──POST envelope──▶ CF Worker (this) ──▶ Tailscale Funnel ──▶ GlitchTip ingest
   (Sentry SDK tunnel)              origin + project          (public HTTPS,        (LAN-only)
                                    allowlist + rate limit     path-scoped)
```

## Security posture

- **Origin allowlist** (`ALLOWED_ORIGINS`) — only the public-site origins may POST.
- **Project allowlist** (`ALLOWED_PROJECT_IDS`) — the open-proxy guard. The
  Worker forwards an envelope **only** if its DSN project id is on the list;
  an empty list **fails closed** (forwards nothing). This stops the relay being
  abused to pump events at arbitrary projects on your backend.
- **Per-IP rate limit** — 120 req/min (errors are burstier than page views).
- **Funnel URL is a Worker secret** — the operator's tailnet hostname never
  appears in browser source or in this repo. The Funnel is **path-scoped** to
  the ingest endpoint, so the tracker's dashboard stays unexposed.
- The forwarded envelope still carries its own DSN key, so the tracker
  authenticates ingest exactly as it would for a direct send.

The Worker code here contains **no operator-specific identifiers** (per
`feedback_no_operator_info_to_public_repo`) — you fill those in at deploy time.

## Operator setup

### 1. Expose GlitchTip's ingest over a path-scoped Tailscale Funnel

GlitchTip listens on the LAN (e.g. `http://localhost:8080`). Funnel just the
ingest path so the dashboard stays private:

```bash
# Serve the local GlitchTip ingest publicly, scoped to the envelope path.
tailscale funnel --set-path /api https://localhost:8080/api
tailscale funnel status     # note the public https://<host>.<tailnet>.ts.net URL
```

> Scope the Funnel to `/api` (ingest) only — never the bare root, which would
> expose the GlitchTip dashboard. Confirm `https://<host>.<tailnet>.ts.net/`
> (no path) does **not** serve the dashboard.

### 2. Configure + deploy the Worker

```bash
cd infrastructure/cloudflare/sentry-relay
npm install

# Mint a deploy token (least privilege): CF dashboard → My Profile → API
# Tokens → custom token, scope: Account → Workers Scripts → Edit.
export CLOUDFLARE_API_TOKEN=<token>

# Set the Funnel base URL as a SECRET (keeps the tailnet hostname out of the
# repo and browser source). Value is the origin only, no trailing path:
echo "https://<host>.<tailnet>.ts.net" | npx wrangler secret put GLITCHTIP_INGEST_ORIGIN

npm run deploy   # prints the workers.dev URL it published to
```

Then set the non-secret vars (CF dashboard → the Worker → Settings → Variables,
or edit `wrangler.toml` and redeploy):

- `ALLOWED_ORIGINS` = your public-site origins, e.g.
  `https://gladlabs.io,https://www.gladlabs.io`
- `ALLOWED_PROJECT_IDS` = your GlitchTip project id(s), e.g. `1`
  (find it in the GlitchTip project's DSN: `https://<key>@.../<project_id>`)

### 3. Map a subdomain (recommended)

CF dashboard → your zone → Workers Routes → map `sentry-relay.<your-domain>/*`
→ `sentry-relay`. Avoids leaking the `workers.dev` origin in DevTools and gives
the browser a same-site tunnel target (better ad-blocker evasion).

### 4. Point the public site at the relay

In Vercel → Environment Variables, set:

- `NEXT_PUBLIC_SENTRY_DSN` =
  `https://<glitchtip_public_key>@sentry-relay.<your-domain>/<project_id>`
  — the **host is the relay's public domain** (safe to expose); the Worker
  reads only the project id and forwards to the real backend.
- `NEXT_PUBLIC_SENTRY_TUNNEL` = `https://sentry-relay.<your-domain>/relay`
  — the SDK posts envelopes here (see `web/public-site/sentry.client.config.ts`).

Redeploy the public site so the env bakes in. When `NEXT_PUBLIC_SENTRY_TUNNEL`
is set, the build skips the dead same-origin `/monitoring` route (see
`web/public-site/next.config.js`).

## Verification

```bash
# 1. Wrong-origin POST is refused (403) once ALLOWED_ORIGINS is set.
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Origin: https://evil.example' \
  https://sentry-relay.<your-domain>/relay            # → 403

# 2. An envelope for a non-allowlisted project is refused (403).
printf '%s\n' '{"dsn":"https://k@sentry-relay.example/999"}' '{"type":"event"}' \
  | curl -s -o /dev/null -w '%{http_code}\n' -X POST --data-binary @- \
    https://sentry-relay.<your-domain>/relay          # → 403

# 3. Trigger a real client error on the site, then watch GlitchTip:
#    your GlitchTip project → Issues shows the new event within ~1 min.
```

If events don't appear: check `wrangler tail sentry-relay` for the forward
status, and confirm the Funnel is up (`tailscale funnel status`) and GlitchTip
is reachable at the Funnel URL + `/api/<project_id>/envelope/`.

## Local development

```bash
npm install
npm test          # vitest — unit tests for the open-proxy guard helpers
npm run dev       # wrangler dev — serves on http://localhost:8787
```

`npm test` covers the envelope-header / DSN parsing and the project allowlist
(the fail-closed open-proxy guard). The fetch handler itself is edge-runtime
glue, verified with the curl smoke tests above.
