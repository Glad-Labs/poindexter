# unsubscribe-relay (Cloudflare Worker)

Public front door for **newsletter unsubscribes** so a local-first Poindexter
install (no public ingress) can honour an opt-out.

## Why a relay

The unsubscribe flow was complete on the backend — per-subscriber tokens,
mandatory since #252, rate limited, no token-validity oracle — and **404 on
every public exit**:

```
https://<site>/newsletter/unsubscribe?token=…   → 404   (the link in every email)
https://<site>/api/newsletter/unsubscribe       → 404   (where a page would POST)
```

`List-Unsubscribe` pointed at the same dead URL with
`List-Unsubscribe-Post: One-Click`, so Gmail and Apple Mail's inbox
unsubscribe button POSTed to a 404 too.

**This is the one piece the poll pattern cannot replace.** Elsewhere (Lemon
Squeezy invoices, Resend delivery state) the provider's API answers and the
worker polls outbound. Here a human in a mail client must reach something
live, so a public surface is genuinely required.

```
recipient ──GET ?token=──▶ this Worker ──▶ confirmation page (NOTHING stored)
recipient ──POST────────▶ this Worker ──▶ Workers KV  unsub:<token>
Gmail one-click ─POST───▶ this Worker ──▶ Workers KV  unsub:<token>
                                                │
ApplyUnsubscribeRequestsJob ──GET /pending──────┘   (bearer, every 5 min)
                            ──POST /ack─────────▶   after the DB write lands
```

## GET must not mutate

Corporate mail scanners, antivirus link-checkers and client prefetchers
follow every URL in an email. A GET that unsubscribed would silently drop
subscribers who never clicked, so **GET renders a confirm button and only
POST records anything**. RFC 8058 one-click is already a POST, so the inbox
button still works in a single step.

## Security posture

- **The token is the credential.** 43 base64url chars (~256 bits from
  `secrets.token_urlsafe(32)`). The write side is deliberately
  unauthenticated — the recipient holding the link is the authorisation.
- **The Worker cannot validate tokens** (no database). It enforces _shape_,
  rate limit and TTL so KV can't be used as free storage; the backend
  validates on apply, where an unknown token updates zero rows.
- **Read side is bearer-authenticated** (`/pending`, `/ack`) with a
  constant-time compare. Unset secret → **503 everywhere, fail closed**: an
  open `/pending` would leak which tokens are in flight.
- **Per-IP rate limit** — 30/min. A real recipient clicks once.
- **Data minimisation** — stores `{requested_at, via}` under the token. No
  email address ever reaches the edge.
- No operator-specific identifiers in the code; ids and secrets are filled
  at deploy time.

## Operator setup

### 1. Create the KV namespace and deploy

```bash
cd infrastructure/cloudflare/unsubscribe-relay
npm install
npx wrangler login                          # once per machine
npx wrangler kv namespace create RELAY_KV   # note the returned id
# uncomment [[kv_namespaces]] in wrangler.toml and paste the id
npx wrangler deploy                          # note the workers.dev URL
npx wrangler secret put UNSUBSCRIBE_RELAY_SECRET   # any long random string
```

### 2. Point Poindexter at it

```bash
poindexter settings set newsletter_unsubscribe_relay_url https://unsubscribe-relay.<you>.workers.dev --allow-new
poindexter settings set newsletter_unsubscribe_relay_secret '<same value>' --secret --allow-new
```

**Until `newsletter_unsubscribe_relay_url` is set the newsletter refuses to
send** and raises a `newsletter_unsubscribe_unconfigured` finding. That is
deliberate: mail with a dead opt-out is a compliance problem (CAN-SPAM,
GDPR, Gmail's bulk-sender rules), so stopping is the correct failure mode,
and it is loud rather than silent.

### 3. Smoke test

```bash
# Confirmation page renders, and stores nothing:
curl -s "https://unsubscribe-relay.<you>.workers.dev/?token=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" | head -5

# Read side refuses without the bearer:
curl -i https://unsubscribe-relay.<you>.workers.dev/pending        # → 401

# With it (expect an empty queue):
curl -s -H "Authorization: Bearer <secret>" \
     https://unsubscribe-relay.<you>.workers.dev/pending           # → {"tokens":[]}
```

Then send yourself a post and click the unsubscribe link end to end. Within
5 minutes `newsletter_subscribers.unsubscribed_at` should be set — verify
that, not the page, because the page confirms optimistically while the DB
write happens on the next poll tick.

### Note on already-sent mail

Emails sent **before** the relay was configured carry the old
`{site_url}/newsletter/unsubscribe` URL and stay broken — the relay cannot
retroactively fix a link already in someone's inbox. Only new sends get the
working URL.

## Testing

```bash
npm test   # vitest over the Worker (13 cases, no network)
```
