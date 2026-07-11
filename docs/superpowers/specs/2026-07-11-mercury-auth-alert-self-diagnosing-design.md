# Mercury auth alert: self-diagnosing egress IP — design (2026-07-11)

## Problem

Mercury locks the read-only API token to a **source-IP allowlist**. The
`poindexter-worker` container egresses via Docker bridge NAT → the host's
residential Comcast IPv4, which **rotates via DHCP** (~monthly). Each rotation
401s the hourly `PollMercuryJob` until the operator manually adds the new IP at
Mercury → Settings → API → allowlist.

This has now happened three times (`72.87.123.52` → `.188` → `.86`). The latest
rotation cost **~4.7 days of stale balances** (last OK poll 2026-07-06 19:56 UTC,
first 401 at 2026-07-07 02:31 UTC, 114 consecutive `auth_failed` runs) before it
was noticed and fixed.

Two friction points compound the outage:

1. **Misattribution.** The `finance.poll_auth_lost` alert _and_ the
   `MercuryClient` 401 exception both say "token likely revoked or expired,"
   sending the operator to re-mint a token that is actually fine (len 89,
   unchanged across all three rotations). The real cause is always the IP.
2. **Discovery cost.** Even once you know it's the IP, you must go dig up the
   current egress IP (`docker exec poindexter-worker …`) before you can fix it.

## Decision

Make the alert **self-diagnosing**: when the poll 401s, the Telegram page states
the real cause (IP-allowlist miss) and includes the **current worker egress
IPv4**, so the fix is a one-tap copy-paste into Mercury's dashboard.

### Rejected alternative — stable-egress proxy

A durable "give the worker a fixed public IP" fix (small always-on node with a
static IP, route only Mercury's ~2 calls/hour through it via an httpx proxy on
`MercuryClient`) was considered and **rejected on 2026-07-11**. The tailnet has
no existing static-IP node (just the PC + a phone, both behind the rotating
Comcast IP), so it would mean standing up a new always-on external dependency
and cost for a **non-critical** hourly poll. The operator chose to keep the
manual ~monthly step but make it frictionless. Fully automating the allowlist
update is not possible — Mercury's allowlist is **not API-writable**.

## Scope

Entirely within the operator-private **finance module** (`modules/finance/`,
stripped from the public `poindexter` mirror per `scripts/sync-to-github.sh`).
No proxy, no new container, no schema change, no new runtime dependency (httpx is
already used by `MercuryClient`). One new operator-tunable config key.

## Design

### 1. Reword the auth-lost alert — `modules/finance/probes.py` (auth_lost branch)

Lead with the real cause, include the egress IP, demote the token theory to a
conditional fallback:

- **title:** `Mercury poll: auth lost (likely IP-allowlist miss)`
- **detail:** 401 → almost always the residential IP rotated off the token's
  allowlist, _not_ a bad token. **Current worker egress IPv4: `<ip>`.** Fix: add
  it at Mercury → Settings → API → IP allowlist; clears on the next hourly poll
  (or force one). If `<ip>` is _already_ allowlisted, then suspect the token →
  re-mint + `poindexter settings set mercury_api_token <t> --secret`.

When the egress IP could not be fetched, substitute a fallback line carrying the
manual command (`docker exec poindexter-worker python -c "import httpx;
print(httpx.get('https://api.ipify.org').text)"`) instead of `<ip>`.

### 2. Reword the client 401 message — `modules/finance/mercury_client.py`

The `MercuryAuthError` raised in `_get` on 401/403 becomes IP-first: "Mercury
401 — usually the source IP isn't in the token's allowlist; if the IP is
allowlisted, the token may be revoked / wrong scope." This string lands in
`finance_poll_runs.error_message` and any dashboard that renders it, so the
correct hypothesis shows **everywhere**, not just on Telegram.

### 3. Egress-IP fetch — best-effort, injectable seam

- New helper `_default_egress_ip_fetch(pool) -> str | None`: reads
  `finance_egress_ip_echo_url` (default `https://api.ipify.org`) via the existing
  `_read_setting`, does `httpx.AsyncClient().get(url, timeout≈5s)`, returns the
  stripped body or `None` on any error/timeout.
- `run_finance_poll_staleness_probe` gains
  `ip_fetch_fn: Callable[[Any], Awaitable[str | None]] | None = None`, defaulting
  to the helper — the **same injection pattern** as the existing `notify_fn` /
  `now_epoch_fn`.
- The fetch runs **only** on the auth_lost branch (zero cost on healthy polls).
- **Fail-open:** a `None`/exception result never blocks the page; the alert fires
  with the fallback line. The lookup is a convenience, never a gate.

### 4. Config key

`finance_egress_ip_echo_url` — default `https://api.ipify.org`. Operator-tunable
(DB-first config; an operator behind a different reflector can repoint it).
Seeded following the finance module's existing settings-seed pattern (extend the
module settings-seed migration, idempotent `ON CONFLICT DO NOTHING`). Confirm the
exact seed location during implementation.

## Testing — `tests/unit/modules/finance/test_finance_probe.py`

- `auth_lost` + stub `ip_fetch_fn` → `"203.0.113.9"`: notify spy's detail
  contains `203.0.113.9` and `allowlist`; does **not** lead with `revoked`.
- `auth_lost` + `ip_fetch_fn` → `None`: alert still fires; fallback `docker exec`
  text present.
- `auth_lost` + `ip_fetch_fn` raises: alert still fires (fail-open); no exception
  propagates.
- Regression: healthy / stale-but-not-auth paths do **not** call `ip_fetch_fn`.

## Out of scope

- Egress proxy / static-IP node / Tailscale exit node.
- Automating the Mercury allowlist update (not API-writable).
- Surfacing the egress IP on `/api/finance/healthcheck` or a Grafana panel — a
  possible future nicety, not needed for the alert.

## References

- Memory `reference_mercury_api.md` — rotation log; IPv4-only container egress.
- `modules/finance/probes.py`, `mercury_client.py`, `jobs/poll_mercury.py`.
- `scripts/sync-to-github.sh` — confirms `modules/finance/` + `docs/superpowers/`
  are stripped from the public mirror.
