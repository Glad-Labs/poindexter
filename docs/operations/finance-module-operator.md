# FinanceModule — operator setup (PRIVATE OVERLAY)

> **Visibility: private** — this doc lives in `glad-labs-stack` only. The sync
> filter strips it from the public Poindexter mirror because it documents
> Matt's Mercury banking integration. See `scripts/sync-to-github.sh`.

**Last Updated:** 2026-05-13
**Module:** `src/cofounder_agent/modules/finance/`
**Spec:** [Module v1](../architecture/module-v1.md), umbrella [Glad-Labs/poindexter#490](https://github.com/Glad-Labs/poindexter/issues/490)
**API:** Mercury Banking — Read-Only scope only

---

## What FinanceModule does

Pulls account balances + transactions from Mercury's REST API every hour,
upserts into Postgres so the system has real-time financial state to
reference from Telegram chats / Discord ops / Grafana dashboards.

**Read-only by design.** The Mercury API token is provisioned with
Read-Only scope; the daemon never has the credentials to move money.
This aligns with [`feedback_oauth_scope_hygiene`](../../README.md#)
— least privilege for any external integration.

## What ships in F1 + F2 (2026-05-13)

| Layer                 | Where                                                                                                                                               |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FinanceModule` class | `modules/finance/finance_module.py`                                                                                                                 |
| Mercury HTTP client   | `modules/finance/mercury_client.py` — async httpx, typed dataclasses                                                                                |
| Hourly polling job    | `modules/finance/jobs/poll_mercury.py` — `PollMercuryJob` registered in `_SAMPLES`                                                                  |
| DB schema             | `modules/finance/migrations/20260513_195607_finance_accounts_and_transactions.py` — `finance_accounts`, `finance_transactions`, `finance_poll_runs` |
| Setting seeds         | `modules/finance/migrations/20260513_190000_seed_mercury_settings.py` — `mercury_api_token` (secret) + `mercury_enabled`                            |
| Operator CLI          | `poindexter finance balance` + `poindexter finance transactions <id>`                                                                               |

## Operator setup

### 1 — Mint a Mercury API token

Mercury dashboard → **Settings → API → Generate token** → scope: **Read-Only**.
Note: the token is shown once at creation. Copy it before closing the modal.

### 2 — IP allowlist

Mercury locks tokens to a list of source IPs. On Matt's residential connection:

- **IPv4:** `72.87.123.52` (Comcast lease — may rotate every few months)
- **IPv6:** `2600:4040:3002:6000::/64` prefix (Mercury accepts IPv6; the
  /64 is stable, the host portion rotates)

If httpx prefers IPv6 outbound (default on dual-stack), Mercury's IPv6
allowlist must include your IPv6 — IPv4-only allowlist will silently fail
auth on a dual-stack box. Get your current addresses with:

```bash
curl -s https://api.ipify.org    # IPv4
curl -s https://api64.ipify.org  # whichever your stack prefers
```

### 3 — Drop the token into `app_settings`

```bash
poindexter settings set mercury_api_token <YOUR_TOKEN> --secret
```

The CLI encrypts at rest via pgcrypto (`enc:v1:` ciphertext stored in
`app_settings.value`; decryption is automatic on read via
`plugins.secrets.get_secret`).

**Why `mercury_api_token` and not `finance_mercury_api_token`?** API keys are
shared integration resources — any future module that touches Mercury reads
the same setting. Matches the unprefixed convention used by `sentry_dsn`,
`telegram_bot_token`, `discord_ops_webhook_url`.

### 4 — Enable polling

```bash
poindexter settings set mercury_enabled true
```

The `PollMercuryJob` is gated by `mercury_enabled` — no-op when it's `false`
(default). This is the safe default so partial deployments don't 401 Mercury
hourly while the operator is still finishing setup.

### 5 — Smoke test

```bash
poindexter finance balance
```

Should print every account + balance + total. If you see
`MercuryAuthError`, check (a) the token is correct, (b) the IP allowlist
includes your IPv4 AND IPv6, (c) the token's scope is Read-Only (write-scope
tokens still work for read endpoints, but worth confirming).

```bash
poindexter finance transactions <account_id> --days 30
```

Lists income/expense for the last 30 days plus a net summary.

## What lives in Postgres after F2

- `finance_accounts` — one row per Mercury account, upserted on every poll.
  `current_balance` / `available_balance` are always the latest known
  values (no history — F2 deliberately doesn't snapshot history; if you
  want runway charting, the `finance_poll_runs` audit table gives you
  hourly checkpoints).
- `finance_transactions` — append-only ledger. PK = Mercury's transaction
  ID; `status` can flip pending → posted on a subsequent poll via
  `ON CONFLICT DO UPDATE`.
- `finance_poll_runs` — every poll attempt: `status` (`ok` / `auth_failed`
  / `api_error` / `exception`), `accounts_seen`, `transactions_new`,
  `transactions_updated`, `error_message`. This is the substrate for the
  upcoming Grafana finance dashboard.

Useful queries:

```sql
-- current total balance across all accounts
SELECT SUM(current_balance) AS total FROM finance_accounts;

-- last 24h cashflow
SELECT
  SUM(amount) FILTER (WHERE amount > 0) AS income,
  -SUM(amount) FILTER (WHERE amount < 0) AS expense,
  SUM(amount) AS net
FROM finance_transactions
WHERE posted_at >= NOW() - INTERVAL '24 hours';

-- polling health (last 24h)
SELECT status, COUNT(*)
FROM finance_poll_runs
WHERE started_at >= NOW() - INTERVAL '24 hours'
GROUP BY status;
```

## Deferred for future phases (F2c, F2d, …)

- **Brain knowledge entries** — nightly job that snapshots
  `current_balance` per account + 30-day net cashflow into
  `brain_knowledge`, so when an operator chat asks "what's our runway?"
  the brain has real numbers to reference.
- **Daily digest** — morning brief includes income/expense for the past
  24h + anomaly alerts (unusual large transactions, low-balance
  threshold breaches).
- **Grafana finance dashboard** — balance trend, daily cashflow chart,
  polling health panel. Reads from `finance_accounts`,
  `finance_transactions`, `finance_poll_runs`.

## Troubleshooting

| Symptom                                                               | Likely cause                                          | Fix                                                                                        |
| --------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `MercuryAuthError: 401`                                               | Token revoked / wrong / not yet propagated            | Check token in app_settings; mint a new one if needed                                      |
| `MercuryAuthError: 403`                                               | IP not whitelisted (often IPv6 missing)               | Add current IPv4 + IPv6 to Mercury's token allowlist                                       |
| `httpx.LocalProtocolError: Illegal header value b'Bearer enc:v1:...'` | Reading the raw ciphertext instead of decrypted token | Call `plugins.secrets.get_secret(conn, key)` not `SELECT value FROM app_settings` directly |
| `finance_poll_runs` rows with `status='auth_failed'` for hours        | Same as the two above                                 | Same fixes; the daemon will catch up on the next tick after correction                     |
| CLI errors with `mercury_api_token is empty`                          | Token not seeded into app_settings                    | `poindexter settings set mercury_api_token <token> --secret`                               |
| Polling never runs despite `mercury_enabled=true`                     | Worker hasn't restarted since enabling                | Restart worker: `docker restart poindexter-worker`                                         |
