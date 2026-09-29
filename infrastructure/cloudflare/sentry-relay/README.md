# sentry-relay (Cloudflare Worker)

Public front door that lets a self-hosted, LAN-only error tracker (GlitchTip,
or any Sentry-compatible ingest) receive errors from a site's **visitors'
browsers and serverless functions**, without giving the tracker a public
address.

The Sentry SDK tunnels each error envelope to this Worker. The Worker checks
it and **queues** it in D1. Poindexter's `DrainSentryRelayJob` pulls the queue
outbound every 2 minutes and posts each envelope to GlitchTip over the LAN.
It is the same split as `unsubscribe-relay` and `ls-webhook-relay`: public
edge, CF-hosted queue, outbound poll.

```
browser ──────────┐
                  ├─POST /relay──▶ this Worker ──▶ D1 `envelopes`
serverless fn ────┘  (SDK tunnel)   checks + queues        │
                                                           │
DrainSentryRelayJob ◀── GET /pending (bearer) ─────────────┘
(worker, outbound)  ──▶ POST <glitchtip>/api/<id>/envelope/?sentry_key=<key>
                    ──▶ POST /ack (bearer), once GlitchTip answered
```

## Why a queue and not a forward

The tracker is LAN-only, and Vercel functions and public browsers cannot reach
the LAN. Forwarding from the edge would mean publishing the tracker's ingest
on a public tunnel. That was this Worker's first design; it was never
deployed, and it would have failed anyway for two reasons:

- **GlitchTip does not read the key from the envelope.** It authenticates
  ingest from `?sentry_key=` or `X-Sentry-Auth` only
  (`apps/event_ingest/authentication.py::auth_from_request`). A tunnelled
  envelope carries its DSN only in the envelope header, so a raw forward is
  answered `403 Denied`. The Worker stores the key beside each envelope and
  the drain puts it back on as a query parameter.
- **The browser could not reach it.** The site's CSP `connect-src` has to
  allow the relay's origin. The site now derives that entry from
  `NEXT_PUBLIC_SENTRY_TUNNEL`, the same variable the SDK sends to.

Pulling instead of pushing keeps the tracker private and the Worker simple:
it never needs to know where GlitchTip lives.

## What is queued

Only envelopes with an error-type item (`event`, `feedback`, `user_report`).
Sessions, client reports, transactions and replays are answered `200` and
dropped. The site's SDK config already sends errors only; the edge check
means an SDK default that drifts back on cannot fill the queue with page-view
noise.

## Security posture

- **Origin allowlist** (`ALLOWED_ORIGINS`): browser POSTs from any other
  origin get `403`. Server-side sends carry no `Origin` and rely on the next
  three checks.
- **Project allowlist** (`ALLOWED_PROJECT_IDS`), the open-proxy guard: only
  envelopes whose DSN names a listed project are queued. Unset **fails
  closed** (queues nothing).
- **Read side is bearer-authenticated** (`/pending`, `/ack`, constant-time
  compare). `SENTRY_RELAY_SECRET` unset → `503` on every path: an open
  `/pending` would hand out visitors' error reports.
- **Per-IP rate limit** of 120 requests/min, a **size cap**
  (`MAX_ENVELOPE_BYTES`, after gzip decompression) and a **queue cap**
  (`MAX_QUEUE_ROWS`, answered `503`, never evicts).
- **Retention** (`RETENTION_DAYS`): `/pending` prunes older rows and reports
  how many as `expired`, so a drain that was down for days shows up as lost
  envelopes (a `sentry_relay_envelopes_expired` finding), not a quiet queue.
- **No IP is stored.** The Worker reads `CF-Connecting-IP` for the rate limit
  only.

The code carries no operator-specific identifiers; those are Worker secrets
and Poindexter settings.

## Operator setup

### 1. Create a GlitchTip project for the site

```bash
docker exec -i poindexter-glitchtip-web python manage.py shell <<'EOF'
from apps.organizations_ext.models import Organization
from apps.projects.models import Project, ProjectKey
org = Organization.objects.get(slug="<your org slug>")
project = Project.objects.filter(organization=org, slug="public-site").first()
if project is None:
    project = Project.objects.create(organization=org, name="public-site",
                                     platform="javascript-nextjs", scrub_ip_addresses=True)
key = ProjectKey.objects.filter(project=project, is_active=True).order_by("id").first()
print(project.id, key.public_key.hex)
EOF
```

Note the project id and the **dashless** key (`.hex`). The JavaScript SDK's
DSN parser accepts only word characters in the key, so a DSN built from the
dashed UUID is rejected at runtime with a console-only `Invalid Sentry Dsn`
and the SDK never starts. The site's `next.config.js` refuses such a DSN at
build time.

### 2. Deploy the Worker

```bash
cd infrastructure/cloudflare/sentry-relay
npm install
npx wrangler login     # once per machine
npx wrangler deploy    # creates the `sentry-relay` D1 database on the first deploy
```

`wrangler.toml` names the database but carries no id: wrangler 4 finds the
database by name, or creates it. The table is created by the Worker on first
use.

### 3. Set the secrets

```bash
echo "https://example.com,https://www.example.com" | npx wrangler secret put ALLOWED_ORIGINS
echo "<project id>" | npx wrangler secret put ALLOWED_PROJECT_IDS
npx wrangler secret put SENTRY_RELAY_SECRET          # any long random string
```

Secrets survive deploys; `[vars]` do not, which is why none of these are in
`wrangler.toml`.

### 4. Point Poindexter at it

```bash
poindexter settings set sentry_relay_url https://sentry-relay.<you>.workers.dev --category observability
poindexter settings set sentry_relay_secret '<same value>' --secret --category observability
# Page on the site project's first event (the default is 10+ repeats):
poindexter settings set glitchtip_triage_alert_threshold_overrides '{"public-site": 1}'
```

`DrainSentryRelayJob` is a no-op until `sentry_relay_url` is set. It posts to
`glitchtip_base_url` (default `http://glitchtip-web:8000`, the compose
hostname).

### 5. Point the site at it

Set both, for the environments that should report (production):

- `NEXT_PUBLIC_SENTRY_DSN` = `https://<dashless key>@sentry-relay.<you>.workers.dev/<project id>`
- `NEXT_PUBLIC_SENTRY_TUNNEL` = `https://sentry-relay.<you>.workers.dev/relay`

Both are baked in at build time, so redeploy after setting them. A production
build fails if only one is set or the DSN is malformed: a half-configured
site would say "we've been notified" on its error pages and report nothing.

## Verification

```bash
R=https://sentry-relay.<you>.workers.dev

# Foreign origin and unauthenticated reads are refused:
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://evil.example' --data-binary x $R/relay   # 403
curl -s -o /dev/null -w '%{http_code}\n' $R/pending                                                          # 401

# Queue contents (a normal, drained queue is empty):
curl -s -H "Authorization: Bearer <secret>" "$R/pending?limit=5"
```

End to end: trigger an error on the site, then within about 2 minutes the
`drain_sentry_relay` job's `job_run` row reports `forwarded >= 1` and the
issue appears in the GlitchTip project. The brain's GlitchTip triage probe
pages it to Discord on its next cycle.

If nothing arrives: check the job's findings (`sentry_relay_drain_failed`,
`sentry_relay_envelope_rejected`, `sentry_relay_envelopes_expired`), then
`npx wrangler tail sentry-relay` for the Worker's answers, then the browser's
network tab for a `connect-src` CSP block on the tunnel URL.

## Tests

```bash
npm install
npm test     # vitest; the queue runs on real SQLite (node:sqlite, Node >= 22.13)
```

The handler tests run the Worker's SQL on real SQLite through a thin D1
double (`src/test-d1.ts`; D1 is SQLite), with the tunables read from this
`wrangler.toml`. Only the rate limiter and the secrets are substituted.
wrangler's `getPlatformProxy` would give a real local D1, but it runs the
`workerd` binary, which needs glibc 2.35, and the self-hosted CI runner's
glibc is older: the job died before a single test ran.
