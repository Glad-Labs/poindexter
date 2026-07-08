# DB clock skew (WSL2 wall-clock excursion)

**Symptom class:** Postgres (`poindexter-postgres-local`) reports a `now()` /
`clock_timestamp()` that is off real time — observed as a ~4h _future_ offset on
2026-07-08. Every DB-written timestamp is off by the same amount, silently.

## How you'll notice

- **A `db_clock_skew` finding pages Telegram** (critical) from
  `brain/clock_skew_probe.py` — the intended path.
- Grafana time-series panels go **empty for "last 1h/6h"** even though the
  pipeline is active (future-stamped rows fall outside the window).
- A real-time correlation during an investigation "doesn't line up" — DB event
  times disagree with wall time by a constant offset. (This exact skew caused a
  misdiagnosis on 2026-07-08.)

## Confirm it's real (30 seconds)

Compare Postgres's live clock to the host clock by epoch (timezone-independent).
From a host PowerShell:

```powershell
$pgEpoch   = [long](docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tAc "select extract(epoch from clock_timestamp())::bigint")
$hostEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
"clock_skew_seconds = $($pgEpoch - $hostEpoch)"
```

Normal is **0–2s** (call latency). A large value (e.g. ±14400 = ±4h) confirms
the skew. (If `psql` prompts for a password, prepend
`docker exec -e PGPASSWORD=<local-pg-password> …`; local-socket connections
normally use `trust` and need none.)

Cross-check that it's the **DB/VM clock**, not the OS clock:

```bash
docker exec poindexter-postgres-local date -u   # container OS clock (usually correct)
```

If `date -u` is correct but `clock_timestamp()` is off, you sampled a **moving**
clock at two instants — this is the WSL2 excursion (see below), not a
process-local offset.

## Root cause

Docker Desktop runs on the **WSL2** backend. The WSL2 VM's `CLOCK_REALTIME` can
jump on host **sleep/resume** — a well-known WSL bug where the VM momentarily
misapplies the host's local UTC offset, so the excursion magnitude equals your
local offset (EDT → +4h). Postgres reads live wall-clock via `gettimeofday()`,
so the jump lands directly in `now()` / `clock_timestamp()`. WSL/Hyper-V
TimeSync usually **resyncs it on its own** within minutes — the 2026-07-08
excursion self-healed without a container restart — which is why the probe
degrades to `unknown` (not a page) when it can't confirm, and only pages on a
_sustained_ skew.

This is host/infra, not a code or DB bug — there is nothing to fix inside
Postgres or the app. The probe exists to make it **loud** instead of silent.

## Remediation ladder

1. **Wait a beat + re-confirm.** WSL TimeSync often corrects a resume excursion
   within a few minutes. Re-run the confirm one-liner; if it's back to 0–2s,
   you're done (the probe will clear the finding on the next cycle).
2. **Force a resync — restart WSL** (fixes it deterministically; brief stack
   downtime). From host PowerShell:
   ```powershell
   wsl --shutdown
   ```
   then bring the stack back up (`bash scripts/start-stack.sh up -d`, or
   `docker compose -f docker-compose.local.yml up -d`). The VM re-syncs its
   clock from the host on restart. Confirm with the one-liner.
   > Restarting _only_ the postgres container will **not** help — the skew is
   > the shared VM clock, not that one container.
3. **Prevent recurrence.** Keep WSL current (resume-TimeSync fixes ship in WSL
   updates): `wsl --update`. If the host sleeps often, consider disabling
   sleep/hibernate on the always-on machine. Optionally ensure the Windows Time
   service is healthy (`w32tm /query /status`; `Start-Service w32time` if
   stopped) — though the host clock is typically already correct; the excursion
   is VM-side.

## The probe & its settings

`brain/clock_skew_probe.py` runs every 5-min brain cycle: it compares
`clock_timestamp()` to an **external** UTC reference (the HTTP `Date:` header
from `clock_skew_reference_url`) — external because a probe inside the same
WSL2 VM shares the skewing clock and would be blind to a VM-wide jump. On
`|skew| > clock_skew_threshold_seconds` it emits an edge-triggered
`db_clock_skew` finding (routed to Telegram via `findings.db_clock_skew.delivery`)
once per episode, re-paging only after `clock_skew_renotify_minutes`. Each cycle
also appends a row to `clock_skew_samples` for the Grafana "DB clock skew" panel
and self-prunes rows older than `clock_skew_sample_retention_days`.

| Setting                            | Default                      | Purpose                                             |
| ---------------------------------- | ---------------------------- | --------------------------------------------------- |
| `clock_skew_probe_enabled`         | `true`                       | Master switch                                       |
| `clock_skew_reference_url`         | `https://www.cloudflare.com` | HEAD target; its `Date:` header is the external UTC |
| `clock_skew_threshold_seconds`     | `120`                        | `abs(skew)` above this pages                        |
| `clock_skew_severity`              | `critical`                   | Finding severity → Telegram routing                 |
| `clock_skew_renotify_minutes`      | `60`                         | Re-page window for a sustained episode              |
| `clock_skew_sample_retention_days` | `30`                         | Self-prune horizon for `clock_skew_samples`         |

Tune via `poindexter settings set <key> <value>`. To silence temporarily (e.g.
a known reference-URL outage), set `clock_skew_probe_enabled false`.

If the reference URL itself is unreachable, the probe records a `status='unknown'`
sample and does **not** page — so an external outage never masquerades as a
clock skew.
