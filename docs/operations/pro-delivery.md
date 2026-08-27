# Pro delivery — pay→deliver chain

How a Poindexter Pro purchase turns into private-repo access with zero
operator action, and how a lapsed subscription loses it again. Tracking
issue: glad-labs-stack#3216.

## Architecture

```
buyer                Lemon Squeezy              worker (local)             GitHub
  │  checkout ────────►  subscription                │                        │
  │  (buy URL carries    (custom_data:               │ SyncProSubscriptionsJob│
  │   github_username)    github_username)           │ polls /v1/subscriptions│
  │                          │  ◄─────────────────── ┤  every 5 min           │
  │                          │ webhook                │ upsert pro_subscriptions
  │                          ▼                        │ reconcile access ─────►│ PUT /collaborators
  │                   CF Worker relay ──► Workers KV ◄┤ KV lookup for rows     │
  │                   (verifies HMAC,     sub:<id>    │ missing a username     │
  │                    parks custom_data) order:<id>  │                        │
  │ ◄────────────────────────────────────────────────────────────────────────┤ invite email
```

**Poll, not webhooks — plus a custom_data relay.** The worker has no public
ingress (local-first; Vercel functions can't reach the LAN; the Tailscale
Funnel is retired), so the `POST /api/webhooks/lemon-squeezy` route — still
mounted — can never hear a live event. `SyncProSubscriptionsJob` polls the
LS API from inside the network instead: no ingress, and a poll reconciles
after downtime where a missed webhook is lost forever. Buyer-visible
latency is one tick (~5 min), which reads as "the GitHub invite arrived
right after the receipt" (GitHub emails the invite natively; LS emails the
receipt instantly).

The one thing the poll cannot see is checkout **custom_data** — the buyer's
GitHub username. **Verified live on the 2026-08-26 test purchase (order
9315803):** the storefront's `checkout[custom][github_username]` param
_survives_ checkout (through the `/buy/` → `/checkout/cart/` 302) and comes
back in every webhook payload as `meta.custom_data` — but the LS **REST API
structurally omits it** (order/subscription objects have no custom-data
field at all, and buy-URL checkouts don't create retrievable checkout
objects). Hence the relay: a ~200-line Cloudflare Worker
([`infrastructure/cloudflare/ls-webhook-relay`](../../infrastructure/cloudflare/ls-webhook-relay/README.md))
receives the webhooks at the edge, verifies the HMAC signature, and parks
`meta.custom_data` in Workers KV under `sub:<id>` / `order:<id>` keys; the
sync reads those back through the Worker's own `GET /lookup/<key>` endpoint
(outbound-only), authenticated with the **same webhook signing secret** —
so the relay adds zero new credentials. The relay carries the username
mapping ONLY — it never writes `revenue_events`, so the poll stays the
single revenue path and the webhook-vs-poll dedup question never arises.
Username resolution order: REST attributes (dead today, self-activates if
LS adds the field) → relay lookup → manual `pro link` finding.

## Access policy

| LS status            | Access    | Why                                                                                 |
| -------------------- | --------- | ----------------------------------------------------------------------------------- |
| `on_trial`, `active` | yes       | the happy path                                                                      |
| `past_due`           | yes       | dunning grace — don't punish a card hiccup                                          |
| `cancelled`          | yes       | LS holds this status until `ends_at` passes (paid through), then flips to `expired` |
| `expired`, `unpaid`  | no        | terminal / never paid                                                               |
| `paused`             | no        | payments paused — "keep what you downloaded" applies, future updates don't          |
| anything else        | untouched | unmodeled state → recorded + visible, no GitHub mutation                            |

The sync is **row-driven, never set-driven**: it only invites/removes
GitHub users recorded in `pro_subscriptions`, so it cannot touch the
operator's account or hand-added collaborators.

## Setup (operator, once)

1. **Fine-grained PAT** for invites: GitHub → Settings → Developer settings
   → Fine-grained tokens. Repository access: **only** the deliverable repo
   (e.g. `Glad-Labs/poindexter-pro`). Repository permissions →
   **Administration: Read and write** (collaborator management needs it).
   Store it: `poindexter settings set pro_delivery_github_token <token> --secret`
2. **Settings:**
   - `pro_delivery_github_repo` = `Glad-Labs/poindexter-pro`
   - `pro_delivery_ls_store_id` / `pro_delivery_ls_product_id` — optional
     filters; set the product id if the store ever sells more than Pro
   - `lemon_squeezy_api_key` — already provisioned if the webhook era set it
3. **Storefront**: the Pro CTA collects the buyer's GitHub username and
   appends `checkout[custom][github_username]` to the buy URL
   (`web/storefront/components/ProCTA.jsx`); nothing to configure.
4. Flip `pro_delivery_enabled=true`. Enabled-but-unconfigured fails loud
   every tick (`pro_delivery_error` finding → Telegram) — by design.
5. **Verify with a live test purchase** before flipping `CHECKOUT_LIVE`
   (spec §8 Track B gate): buy, watch `poindexter pro status`, confirm the
   invite lands with zero operator action, then cancel + refund in LS.

## Webhook relay (optional — makes delivery fully automatic)

Without the relay, every purchase needs one `poindexter pro link` (LS
withholds the username from its REST API — see Architecture). Fine at
founding scale; the relay closes it for good. Full deploy detail in the
[Worker README](../../infrastructure/cloudflare/ls-webhook-relay/README.md);
the short version:

```bash
cd infrastructure/cloudflare/ls-webhook-relay
npm install
npx wrangler login                             # once per machine
npx wrangler kv namespace create RELAY_KV      # uncomment block in wrangler.toml, paste id
npx wrangler deploy                            # note the workers.dev URL
npx wrangler secret put LS_WEBHOOK_SECRET      # = lemon_squeezy_webhook_secret

poindexter pro relay register https://ls-webhook-relay.<you>.workers.dev
poindexter pro relay status                    # both halves at a glance
```

- `relay register` creates-or-updates the LS webhook via the API
  (`order_created`, `subscription_created`, `subscription_updated` — all
  custom_data carriers; the last also refreshes KV TTL on renewals),
  records `pro_delivery_relay_url` — the relay's **only** setting and its
  enable switch (reads ride the Worker's `/lookup` endpoint on the shared
  webhook secret, so no CF API token or extra credential exists) — and
  lists any other webhooks so stale registrations are visible
  (`poindexter pro relay remove <id>` deletes one).
- Half-set config fails loud: `pro_delivery_relay_url` set while
  `lemon_squeezy_webhook_secret` is missing (deleted after register ran)
  is a `pro_delivery_error` every tick, because an operator who registered
  the relay believes full-auto delivery is live.
- Steady-state cost is zero: the sync consults the relay only for
  access-status subscriptions whose `github_username` column is NULL — once
  a row is linked, no lookups at all. Free-tier Workers/KV is orders of
  magnitude above this traffic.
- Relay down / unreachable? The lookup fails open and that buyer gets
  the standard `pro_delivery_action_needed` finding — exactly the
  pre-relay behavior, never a stuck sync.

## Operating it

```bash
poindexter pro status            # config presence + subscription inventory
poindexter pro sync              # run one reconcile pass now
poindexter pro link 101 octocat  # attach a GitHub account + deliver now
poindexter pro unlink 101        # revoke + detach
```

- **`pro_delivery_action_needed` finding (Telegram):** a paying subscriber
  has no GitHub username anywhere — not in the REST payload (expected: LS
  withholds custom_data there), not in the relay KV (relay not deployed,
  webhook missed while the relay was down, or the buyer skipped/typoed the
  field). The finding body names the exact `poindexter pro link` command.
  This is the designed degradation, not an error: delivery still happens,
  one command later.
- **`pro_delivery_error` finding:** config missing (severity error) or
  LS/GitHub API failures (severity warn, per-subscription isolation — one
  bad row never strands the rest).
- Revenue: the sync writes the initial order into `revenue_events`
  (idempotent, `ls_order_<id>`), giving the parked Revenue board a live
  data path. Renewal invoices are a tracked follow-up on #3216.

## Freshness (the other half of the promise)

The `pro-freshness` ops session (Sun 04:30, `scripts/ops_sessions/pro_freshness.py`)
rebuilds the deliverable repo weekly from the live system: the tuned seed is
exported from **prod `app_settings`** (non-secret, identity/operator values
dropped and counted), the prompt pack mirrors the live SKILL.md packs
(post-#825 source of truth), the premium Grafana boards are re-copied
from provisioning, and the **operator console SPA** is exported (dev tests
excluded, `INSTALL.md` generated) — the console is Pro-tier by design
(stripped from the OSS mirror; the engine's presence-based mount serves it
wherever the directory exists), and the weekly export is how it reaches
buyers. The book is scanned for deleted-code fossils and stale
prices but never auto-edited. Every generated file passes the PII/secret
scrub gate or nothing is pushed. Runbook row in
[scheduled-agents.md](scheduled-agents.md); manual run:
`bash scripts/linux/run-session.sh pro-freshness`.

## Buyer side: adopting the seed (`poindexter pro apply`)

The seed is a **reference tuning**, not a drop-in — many values are tuned to
the seller's hardware and model fleet — so the buyer-side command is a
diff-and-adopt, safe by default:

```bash
poindexter pro apply ~/poindexter-pro            # dry-run report (default)
poindexter pro apply ~/poindexter-pro --apply    # adopt stock-value keys only
```

Buckets: **adoptable** (buyer still on OSS defaults — the only bucket the
plain `--apply` writes), **held for review** (model-pin / GPU / VRAM keys;
`--include-models` to adopt), **conflicts kept** (buyer customized — theirs
wins unless `--overwrite-conflicts`), identical, and unknown-to-this-engine
(newer-seed keys an older engine can't read — skipped). Secrets are never
written. Applied values go live within ~1 minute via the settings reload
job, no restart. The pro repo's `config/README.md` (regenerated by the
freshness session, counts included) teaches the same flow to buyers.

## Edge cases

- **Buyer changes GitHub account**: `poindexter pro unlink` then `pro link`
  with the new username.
- **Operator-set usernames win**: the sync only fills `github_username`
  when the column is NULL — it never overwrites a `pro link`.
- **Invite expired unaccepted** (GitHub expires repo invitations after ~7
  days): `poindexter pro unlink` + `pro link` re-sends.
- **Relay stores a wrong username** (buyer typo'd at checkout): the value
  still has to pass `normalize_github_username`, and an operator `pro link`
  always wins over it — `unlink` + `link` fixes any bad state.
- **Turning a relay into a revenue pipe**: don't, without reading the
  dedup note in `services/pro_delivery.py` — the shipped relay stores
  custom_data mappings only, which is why the poll can stay the single
  `revenue_events` writer.
