# Design: Self-heal the Postgres host-port-proxy wedge

- **Date:** 2026-06-21
- **Status:** Approved design (pending written-spec review)
- **Author:** Claude (with Matt)
- **Area:** `brain/` self-healing watchdog

## Problem

After the 2026-06-21 cutover that moved the stack's Postgres host publish to
`5433`, host-side tooling periodically fails to reach the DB with:

```
ConnectionError: unexpected connection_lost() call   # inside asyncpg _create_ssl_connection
```

This presents as an SSL error but is **not** SSL: asyncpg's first wire step is
an `SSLRequest`, and the socket dies mid-handshake. It is a transport failure.
It has recurred at least twice in ~24h (once ~10h post-cutover, once the next
morning on a container that had been healthy for 2h). The manual lever is
`docker restart poindexter-postgres-local`.

Symptoms (verified 2026-06-21 during diagnosis):

- `poindexter` CLI, the `postgres` MCP, and host scripts all fail with
  `connection_lost` / "connection was closed in the middle of operation".
- Forcing IPv4 (`127.0.0.1`) fails identically → not the IPv6/`localhost`→`::1`
  dual-publish trap.
- `sslmode=disable` fails identically → not SSL/cert/auth/pg_hba.
- The container is healthy and publishing `0.0.0.0:5433->5432` + `[::]:5433`,
  and **in-container clients (`postgres-local:5432`) are unaffected** — the
  whole stack keeps running.

## Root cause

Docker Desktop runs on the **WSL2 NAT backend** (this host's `.wslconfig` has
`[wsl2]` with `localhostForwarding=true` and no `networkingMode`, so NAT is the
default). The Windows `localhost:5433` → WSL2 `localhostForwarding` relay →
`com.docker.backend` proxy → container chain periodically wedges: it accepts the
host TCP connection, then drops it as soon as protocol bytes flow. This is a
known WSL2 NAT-mode fault, not specific to Postgres. `docker restart` of the
container re-spawns the per-port relay and clears the wedge.

Only **host-published** ports are affected; the internal Docker bridge
(`<service>:<port>`) never touches the relay, which is why containers stay
healthy throughout.

## Goal / non-goals

**Goal:** the Postgres host-port wedge auto-resolves without operator action,
the same way the brain already auto-resolves this exact wedge for HTTP sidecars.

**Non-goals:**

- Eliminating the WSL2 NAT relay (e.g. `networkingMode=mirrored`). That is the
  "make it never happen" option with host-wide blast radius; explicitly
  deferred (see Future work). This spec self-heals the wedge instead, per the
  chosen approach.
- Re-routing host tools through the container network. Considered, rejected as
  more invasive (CLI ergonomics, MCP rerouting, bootstrap-before-stack
  chicken/egg).

## Chosen approach: extend `brain/docker_port_forward_probe.py`

That probe (Glad-Labs/poindexter#222) already encodes the full detect-and-recover
loop for the **identical** Windows wslrelay wedge:

- Probes each watched service from inside the network **and** from
  `host.docker.internal`.
- **internal-OK + external-FAIL** → stuck port forward → `docker restart`.
- both-FAIL → real outage → no restart, audit only (let other monitoring page).
- Rolling restart cap (default **3 per 60 min**) per container; on cap-trip or
  post-restart-still-failing it writes `alert_events` (Telegram-routed).
- `audit_log` row every cycle (`docker_port_forward_recovered`, etc.).
- Brain container already has `/var/run/docker.sock` mounted + docker CLI;
  `_restart_container` already handles the Windows `CREATE_NO_WINDOW` rule.
- Live every brain cycle (`brain_daemon.py` ~L2565) and a **required** module.

The only reason Postgres isn't covered: the probe is **HTTP-only**
(`_http_probe` issues a `GET` and expects 2xx). Postgres speaks no HTTP.

**Rejected alternative — a new standalone DB-wedge probe.** It would duplicate
~90% of this file (cap, audit emitters, restart wrapper, alert plumbing,
Windows handling) for one new probe type. Violates "enhance, don't dup".

## Design detail

### 1. Credential-free Postgres reachability check

New stdlib-only helper (no asyncpg, no credentials):

```python
import socket, struct

_PG_SSL_REQUEST = struct.pack("!ii", 8, 80877103)  # libpq SSLRequest

def _pg_probe(host: str, port: int, timeout_seconds: float) -> bool:
    """True iff Postgres answers the SSLRequest handshake within timeout.

    Healthy Postgres replies 'S' (SSL) or 'N' (no SSL). A wedged Docker
    host-port proxy accepts the TCP connection then drops it on first byte
    (empty recv / ConnectionReset) -> False. Mirrors asyncpg's first wire
    step -- the exact byte exchange that fails during the wedge. We read the
    one negotiation byte and close; we never send credentials.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds) as s:
            s.settimeout(timeout_seconds)
            s.sendall(_PG_SSL_REQUEST)
            reply = s.recv(1)
            return reply in (b"S", b"N")
    except Exception:
        return False
```

Rationale:

- Tests exactly the layer that breaks (the handshake), not just TCP liveness —
  a plain `connect()` is insufficient because the wedge accepts TCP.
- `S`/`N` is answered before the startup message, so it is independent of
  `pg_hba`/auth — no credentials needed, matching the HTTP probe's
  "reachability, not correctness" philosophy.
- Rejected fuller alternative (`asyncpg.connect()` + `SELECT 1`): drags
  DSN/credential construction for two endpoints into the brain for zero added
  detection power.

### 2. `probe_type` dispatch (backward compatible)

Add an optional `probe_type` to watch-list entries:

- `"http"` — **default**; all 12 existing entries keep current behavior with no
  edit (parser defaults the field when absent).
- `"postgres"` — uses `_pg_probe` against `(internal_hostname, port)` and
  `(host.docker.internal, host_port)`.

`_parse_watch_list` learns the optional field (validates to `"http"` when
absent/unknown). `_check_one_service` branches once on `probe_type` to pick the
probe fn and build endpoint descriptors; **all downstream logic — wedge
classification, restart, cap, audit, alerts — is unchanged.**

`run_docker_port_forward_probe` and `_check_one_service` gain an injectable
`pg_probe_fn` (defaults to `_pg_probe`) alongside the existing `http_probe_fn`,
so tests can supply canned outcomes.

### 3. The Postgres watch entry

```json
{
  "container": "poindexter-postgres-local",
  "internal_hostname": "postgres-local",
  "port": 5432,
  "host_port": 5433,
  "probe_type": "postgres"
}
```

Internal probe → `postgres-local:5432`; external probe →
`host.docker.internal:5433`.

### 4. Restart policy

Reuse the existing shared **3 restarts / 60 min** rolling cap (no per-entry
override, no schema addition). On wedge: `docker restart
poindexter-postgres-local`. This briefly bounces the DB for the whole stack;
verified harmless on 2026-06-21 — dependent containers' asyncpg pools reconnect
without the containers cycling (worker/prefect/brain uptimes were unaffected
across the manual restart). After 3 trips in an hour the cap suppresses further
restarts and pages Telegram, which correctly distinguishes "transient wedge"
from "DB is actually sick".

`docker_port_forward_recovery_wait_seconds` is already seeded at **45s** (the
setting description's "5s" is stale), ample for Postgres to accept connections
post-restart (~1-2s observed). No change needed.

## Settings touched

- **`docker_port_forward_watch_list`** — append the Postgres entry.
  - **Fresh installs:** edit the seeded value in
    `services/migrations/0000_baseline.seeds.sql` (seeds live in baseline, never
    in a new migration).
  - **Existing prod:** one-time live update via `poindexter settings set
docker_port_forward_watch_list '<json>'` (CLI-first; the row already exists
    so the baseline `ON CONFLICT DO NOTHING` will not update it). Done as the
    final rollout step, after the new brain code is deployed.

No new settings keys. No new migration file.

## Rollout sequencing (order matters)

Brain code is **image-baked** (`brain/*.py` is COPYed into the image; a
rebuild+recreate is required to deploy — not a bind-mount restart).

1. Merge code (probe + seed edit + tests + docs) to `origin/main` via PR; deploy
   clone auto-syncs.
2. Rebuild + recreate the brain container so the new probe code is live.
3. **Then** add the Postgres entry to the live `docker_port_forward_watch_list`.

If step 3 preceded step 2, the old HTTP-only probe would read the Postgres entry
as a dead HTTP service → "both fail → service_down → **no restart**, audit noise
only" — safe but useless. Hence last.

## Testing

Extend `tests/unit/brain/test_docker_port_forward_probe.py`:

- **Wedge** (`probe_type=postgres`, internal pg-probe OK, external FAIL) →
  `restart_fn` called once; status `recovered` when post-restart probe passes.
- **Both-down** (internal + external FAIL) → no restart; status `service_down`.
- **Healthy** (both OK) → no restart; status `ok`.
- **Cap** → 4th wedge in-window suppresses restart, emits cap alert.
- **HTTP entries unchanged** → existing tests still pass with the default
  `probe_type`.
- **`_pg_probe` framing** → unit test against a fake socket: sends exactly the
  8-byte SSLRequest; returns True on `b"S"`/`b"N"`, False on `b""`/reset/timeout.

## Docs

- `docs/operations/self-healing.md` — Postgres host-port wedge is now
  auto-recovered; note the manual lever as fallback.
- `docs/reference/app-settings.md` — document the watch-list `probe_type` field
  and the Postgres entry.

## Observability

The probe already writes `audit_log` (`docker_port_forward_recovered`), which
already feeds Grafana — Postgres rebinds appear there for free. Verify the
relevant panel renders the new container; add/extend a row if it doesn't (every
metric gets a panel). Cap-trip / recovery-failure already raise `alert_events`
→ Telegram.

## Files touched

- `brain/docker_port_forward_probe.py` — add `_pg_probe` helper; teach
  `_parse_watch_list` the optional `probe_type`; add a dispatch branch +
  injectable `pg_probe_fn` in `_check_one_service` /
  `run_docker_port_forward_probe`; refresh the probe description string.
- `src/cofounder_agent/services/migrations/0000_baseline.seeds.sql` — append the
  Postgres entry to the seeded `docker_port_forward_watch_list` value.
- `src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py` —
  new Postgres-path tests + `_pg_probe` framing test.
- `docs/operations/self-healing.md`, `docs/reference/app-settings.md` — doc
  updates.
- **Live prod (operational, not code):** `poindexter settings set
docker_port_forward_watch_list '<json>'`, run after the new brain image is
  deployed.

## Risks & rollback

- **Risk:** restarting the shared DB is more impactful than an HTTP sidecar.
  Mitigated by the empirical "absorbed cleanly" finding + the 3/60 cap +
  both-down guard (never restarts a genuinely-down DB).
- **Risk:** false-positive wedge detection restarting a healthy DB. Mitigated:
  detection requires internal-OK **and** external-FAIL — a healthy proxy passes
  the external SSLRequest, so the restart branch can't fire.
- **Rollback:** set `docker_port_forward_probe_enabled=false` (kills the whole
  probe) or remove the Postgres entry from the watch list (kills only the
  Postgres coverage) — both live, no redeploy.

## Future work (not in scope)

If the wedge frequency stays high despite self-heal, revisit eliminating the NAT
relay via `networkingMode=mirrored` in `.wslconfig` (host-wide; removes the
per-port proxy for all published ports). The `audit_log` rebind frequency is the
data that would justify that larger change.
