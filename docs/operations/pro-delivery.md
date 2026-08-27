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
  │                          ◄──────────────────────┤  every 5 min           │
  │                                                  │ upsert pro_subscriptions
  │                                                  │ reconcile access ─────►│ PUT /collaborators
  │ ◄────────────────────────────────────────────────────────────────────────┤ invite email
```

**Poll, not webhooks.** The worker has no public ingress (local-first;
Vercel functions can't reach the LAN; the Tailscale Funnel is retired), so
the `POST /api/webhooks/lemon-squeezy` route — still mounted — can never
hear a live event. `SyncProSubscriptionsJob` polls the LS API from inside
the network instead: no ingress, and a poll reconciles after downtime where
a missed webhook is lost forever. Buyer-visible latency is one tick
(~5 min), which reads as "the GitHub invite arrived right after the
receipt" (GitHub emails the invite natively; LS emails the receipt
instantly).

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

## Operating it

```bash
poindexter pro status            # config presence + subscription inventory
poindexter pro sync              # run one reconcile pass now
poindexter pro link 101 octocat  # attach a GitHub account + deliver now
poindexter pro unlink 101        # revoke + detach
```

- **`pro_delivery_action_needed` finding (Telegram):** a paying subscriber
  has no GitHub username — LS didn't expose the checkout custom_data via
  the API, or the buyer skipped/typoed it. The finding body names the exact
  `poindexter pro link` command. This is the designed degradation, not an
  error: delivery still happens, one command later.
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
- **Webhook relay someday**: if a public relay is ever built, reconcile its
  `revenue_events` dedup (webhook_id-keyed) with the poll's order-id keys
  first — see the note in `services/pro_delivery.py`.
