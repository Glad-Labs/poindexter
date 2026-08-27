# ls-webhook-relay (Cloudflare Worker)

Public front door that catches **Lemon Squeezy webhooks** so a local-first
Poindexter install (no public ingress) can still receive checkout
`custom_data` — the buyer's GitHub username that drives fully-automatic
Pro-repo delivery.

## Why a relay

The storefront's Pro CTA appends `checkout[custom][github_username]` to the
Lemon Squeezy buy URL. That value **survives checkout** and comes back in
webhook payloads as `meta.custom_data` — but the LS **REST API structurally
omits it**: order and subscription objects have no custom-data field at all
(verified live 2026-08-26). So the delivery poll
(`services/pro_delivery.py`, which by design cannot receive webhooks — the
worker has no public ingress) had no way to learn the username, and delivery
degraded to a manual `poindexter pro link` per buyer.

This Worker closes that gap the same way the sibling `page-views-beacon`
does (public edge → CF-hosted sink → operator backend polls):

```
Lemon Squeezy ──POST webhook──▶ CF Worker (this) ──▶ Workers KV
                (HMAC-SHA256      verify signature,     sub:<id>   → {custom_data,…}
                 X-Signature)     extract custom_data   order:<id> → {custom_data,…}
                                       ▲
SyncProSubscriptionsJob ──GET /lookup/<key>──┘
(worker, outbound poll,    (bearer = the same
 every 5 min)               webhook secret)
```

The Worker serves the read side itself (`GET /lookup/<key>`), authenticated
with the **same shared secret that signs the webhooks** — so the whole relay
runs on one credential both sides already hold, and the operator's poll
needs **no Cloudflare API token**.

The poll stays the source of truth for subscription **state** (it reconciles
after downtime, where a missed webhook is lost); the relay only carries the
one field the REST API withholds. A webhook missed while Cloudflare is down
(LS retries a few times, then gives up) degrades to exactly the previous
behavior: a `pro_delivery_action_needed` finding naming the one-command
manual link.

**Deliberately NOT a revenue pipe.** The Worker stores custom_data mappings
only — it never writes `revenue_events`, so the webhook-vs-poll dedup
reconciliation warned about in `services/pro_delivery.py` never arises.

## Security posture

- **HMAC verification** — Lemon Squeezy signs every delivery
  (`X-Signature: hex(HMAC-SHA256(secret, body))`); the Worker verifies via
  `crypto.subtle.verify` (native constant-time compare) and rejects
  everything else with 401. No signing secret configured → **503, fail
  closed** (an unverified store would let anyone plant GitHub usernames).
- **Read auth** — `GET /lookup/<key>` requires `Authorization: Bearer
<that same secret>` (constant-time compare); the path only accepts the
  exact write-side key shapes (`sub:<id>` / `order:<id>`), so the endpoint
  can't probe arbitrary KV keys, and there is no list endpoint.
- **Per-IP rate limit** — 60 req/min; LS sends a handful of events per
  purchase and the poll reads a couple of keys per tick, so this only
  exists to cap brute-force grinding against the HMAC or the bearer.
- **Data minimization** — stores `{event_name, order_id, subscription_id,
custom_data, stored_at}` and nothing else (no emails, no card metadata —
  the poll gets everything else from the REST API it already talks to).
  Keys expire after `RETENTION_DAYS` (default 90); renewal webhooks refresh
  the clock, so only lapsed buyers' rows age out.
- The Worker code contains **no operator-specific identifiers** — ids and
  secrets are filled at deploy time.

## Operator setup

### 1. Create the KV namespace and deploy

```bash
cd infrastructure/cloudflare/ls-webhook-relay
npm install
npx wrangler login                          # once per machine (browser approval)
npx wrangler kv namespace create RELAY_KV   # note the returned id
# uncomment the [[kv_namespaces]] block in wrangler.toml and paste the id
# (the block ships commented out — wrangler refuses to parse a placeholder id)
npx wrangler deploy                          # note the workers.dev URL
npx wrangler secret put LS_WEBHOOK_SECRET    # same value as app_settings.lemon_squeezy_webhook_secret
```

### 2. Point Lemon Squeezy at the relay

```bash
poindexter pro relay register https://ls-webhook-relay.<you>.workers.dev
```

This creates (or updates) the LS webhook via the API with the right events
(`order_created`, `subscription_created`, `subscription_updated`) and your
signing secret, records `pro_delivery_relay_url` — **which is the relay's
only setting and its enable switch**: the sync reads mappings back through
the Worker's `/lookup` endpoint using the same secret, so there is nothing
else to configure — and lists any other webhooks on the store so stale ones
are visible (`poindexter pro relay remove <id>` deletes one). `poindexter
pro relay status` shows both sides of the config any time.

### 3. Smoke test

```bash
# Unsigned write and unauthenticated read must both bounce:
curl -i -X POST https://ls-webhook-relay.<you>.workers.dev              # → 401
curl -i https://ls-webhook-relay.<you>.workers.dev/lookup/sub:1         # → 401

# Real payload: Lemon Squeezy dashboard → Settings → Webhooks → the relay
# webhook → any delivery → Resend. Then confirm the mapping landed:
npx wrangler kv key list --binding RELAY_KV --remote
```

From then on a purchase flows end-to-end with zero operator action: LS
webhook → KV within a second, and the next 5-minute sync tick picks the
username up, upserts it (operator-set usernames always win), and sends the
GitHub invite.

## Testing

```bash
npm test   # vitest over the pure helpers (signature check, payload extraction)
```
