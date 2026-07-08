# DB clock-skew probe — design

**Issue:** _to file — `Glad-Labs/poindexter` (OSS; the probe ships in the public mirror)_
**Date:** 2026-07-08
**Status:** Approved (external-absolute reference)
**Surfaced by:** operator investigation 2026-07-08 — `poindexter-postgres-local`
`now()` read ~4h in the future during a window, self-resolving without a
restart. The same skew corrupted a real-time correlation the day it happened
(the `podcast_scaffold_dump` misdiagnosis on 2026-07-08).

## Problem

The stack has **no detector for a wrong database wall-clock.** When
`poindexter-postgres-local` returns a `now()`/`clock_timestamp()` that is
offset from real time, every DB-written timestamp is off by the same amount,
and the damage is silent:

- Grafana time-series panels query by DB timestamps against Grafana's (real)
  clock. Rows stamped in the future fall outside "last 1h/6h" windows — panels
  silently miss current data (dashboard blind spots that look identical to "no
  activity").
- Any code comparing a Python `datetime.utcnow()` cutoff (real) against a DB
  timestamp (skewed) breaks — stale-task sweeps, retention windows, cost
  windows, findings routing.
- `audit_log` / `pipeline_versions` / findings timestamps drift, corrupting
  any real-time correlation during an investigation.

Intra-DB comparisons (`now()` vs a stored timestamp) stay self-consistent, so
the DB "looks fine" from inside — which is exactly why it goes unnoticed.

## Investigation (2026-07-08)

Confirmed by direct measurement (epoch comparison, timezone-independent):

- The container OS clock was correct (`docker exec … date -u`), and so was the
  worker container and the Windows host.
- Postgres config was clean: stock `postgres:16`, no `TZ`/`PGTZ`/`FAKETIME`/
  `LD_PRELOAD`, no `/etc/ld.so.preload`, no `libfaketime` in the image;
  `TimeZone`/`log_timezone` = `Etc/UTC`.
- By the time of the check, `clock_timestamp()` matched the container clock to
  within call latency — **the skew had self-resolved without a restart**
  (container `StartedAt` unchanged, ~22h uptime).

**Root cause:** a transient host-level **WSL2 `CLOCK_REALTIME` excursion**
(Docker Desktop runs on the WSL2 backend). Postgres reads live wall-clock via
`gettimeofday()`, so `now()`/`clock_timestamp()` reflected the excursion at the
instant sampled. The container `date` reads, taken at different instants,
looked correct — creating the illusion of a Postgres-process-specific offset.
A `+4h` excursion equals the host's EDT offset (UTC−4): WSL2's well-known
resume bug momentarily misapplies the host's local UTC offset to the VM clock,
so the excursion magnitude tracks the local offset rather than being random NTP
drift. Self-healing without a restart proves it was the **shared VM clock**
(no process-local latch could recover in place), and that WSL/Hyper-V TimeSync
resynced it.

This is intermittent and host-triggered (sleep/resume), so there is no code
fix in the DB or the app. The durable answer is **detection**: make the skew
loud so it can never again silently corrupt data or a diagnosis.

## Goals

1. Detect when `poindexter-postgres-local`'s wall-clock is off real time beyond
   a tunable threshold, and page the operator once per episode.
2. Do **not** false-page on ordinary host sleep/resume (the common case where
   the clock briefly moves then lands correct).
3. Provide continuous visibility (a skew-over-time signal at ~0 in health) and
   a runbook for remediation.
4. Every tunable in `app_settings`; graceful no-op when disabled or when the
   reference is unreachable.

## Non-goals (YAGNI)

- **Auto-remediation.** The effective fix is host-side WSL resync; the only
  in-reach lever (`wsl --shutdown`) would kill the whole stack including the
  brain, and `w32tm /resync` wouldn't help (the host clock is already correct;
  `w32time` was even stopped during the incident). v1 detects + pages + points
  at the runbook. A **non-destructive** host-Recovery-Agent WSL-resync action
  behind a default-off flag is noted as possible Phase 2.
- **Sub-second accuracy.** We are detecting hour-scale excursions; a ~1s
  reference (HTTP `Date:` header) is ample against a 120s threshold.
- **NTP client dependency.** HTTP `Date:` over 443 is firewall-friendly and
  needs no new dependency (brain already ships `httpx`). True NTP (UDP 123) is
  a documented alternative, not v1.
- **Multi-endpoint reference quorum.** One configurable reference URL with
  graceful "unknown" on failure. Multi-URL fallback is easy future hardening.

## Approach — reference clock

A probe running inside a container in the **same WSL2 VM** as Postgres shares
the VM `CLOCK_REALTIME`. So the naive "compare DB `now()` to the probe's own
`datetime.utcnow()`" is **blind to this exact bug** — both move together during
a VM-wide jump (Δ≈0). The reference must come from **outside** the VM wall-clock.

Three references were weighed:

|                                     | Mechanism                                                                                                                   | Catches it?                                           | Downside                                                                                                                                             |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. External absolute** _(chosen)_ | Each cycle, fetch authoritative UTC (HTTP `Date:` header, HEAD to a configurable URL) and compare to PG `clock_timestamp()` | Yes — fires only when the DB clock is genuinely wrong | One lightweight HTTPS HEAD / 5 min; unreachable ref → "unknown" (no alert)                                                                           |
| B. Pure-local monotonic-jump        | Compare PG `CLOCK_REALTIME` progression vs the probe's `CLOCK_MONOTONIC` across cycles                                      | Detects a jump _event_                                | `CLOCK_MONOTONIC` excludes suspend → **every legit sleep/resume looks like a jump** → pages on every wake; can't tell "now correct" from "now wrong" |
| C. Hybrid (B → confirm with A)      | Monotonic detects a discontinuity, then one external check confirms wrongness                                               | Yes, most robust                                      | A+B complexity for marginal gain                                                                                                                     |

**Decision: A.** Simplest that is also _correct_ — it does not false-page on
legitimate resume (post-resume the clock is right, matches external, stays
silent) and only fires when the DB clock is actually wrong. B's resume-noise is
disqualifying; C's extra robustness only helps during a simultaneous
external-ref outage _and_ a skew, which is rare and self-corrects next cycle.

## Design

A new brain probe, mirroring `data_freshness_probe` / `compose_drift_probe`
(standalone module under `brain/`, stdlib + `httpx`, `_read_setting` pattern,
never raises, degrades to a structured "unknown" rather than crashing the
cycle).

### Component 1 — the probe (`brain/clock_skew_probe.py`)

`async def run_clock_skew_probe(pool) -> dict` — one pass per 5-min brain cycle:

1. If `clock_skew_probe_enabled` is false → return `{ok, detail:"disabled"}`.
2. `pg_epoch = SELECT extract(epoch from clock_timestamp())` (the clock that
   corrupts data).
3. `ref_epoch` = parse the `Date:` header from a `HEAD` to
   `clock_skew_reference_url` (via `httpx`, short timeout,
   `email.utils.parsedate_to_datetime`). On any failure (httpx missing,
   timeout, missing/unparseable header) → emit **no** finding, record a
   `status="unknown"` sample, return `{ok:True, status:"unknown"}`.
4. `skew_seconds = pg_epoch − ref_epoch`.
5. Record a sample row (Component 3) for the time-series.
6. Edge-triggered decision (Component 2): `abs(skew_seconds) >
clock_skew_threshold_seconds` → skewed, else healthy.

The probe also computes `pg_vs_container = pg_epoch − time.time()` as a
free, no-alert secondary signal (VM-internal consistency) carried in the
finding/sample `extra` — useful when reading an incident, never a trigger.

### Component 2 — edge-triggered finding

Reuses the findings pipeline: on the healthy→skewed **edge** (state in
`brain_knowledge`, entity `clock_skew_watchdog`), emit an `audit_log` finding:

- `event_type='finding'`, `source='clock_skew_probe'`, `severity` from
  `clock_skew_severity` (default `critical`).
- `details.kind = 'db_clock_skew'` (dot-free, so
  `findings.db_clock_skew.delivery` policy attaches — routed to Telegram by
  default per `feedback_telegram_vs_discord`; a wrong DB clock is a
  data-integrity emergency).
- `dedup_key = 'db_clock_skew'`, `extra = {skew_seconds, threshold,
reference_url, pg_utc, ref_utc, pg_vs_container}`.
- Title/body name the measured skew, the reference, and point at the runbook +
  the manual one-liner.

Re-page on the same episode only after `clock_skew_renotify_minutes` (default
60), mirroring `compose_drift`'s renotify window so a persistent skew is not
silent for hours but does not spam every 5 min. skewed→healthy logs an info
recovery and clears state.

### Component 3 — skew time-series (`clock_skew_samples`)

The brain is headless (no `/metrics` endpoint), so the continuous signal
reaches Grafana through Postgres (Grafana's Postgres datasource), the same way
`gpu_metrics` / `cost_logs` do. New table (timestamped migration — schema DDL
only, per the migrations convention):

```sql
CREATE TABLE IF NOT EXISTS clock_skew_samples (
    id            BIGSERIAL PRIMARY KEY,
    sampled_at    timestamptz NOT NULL DEFAULT now(),
    skew_seconds  double precision,          -- NULL when status='unknown'
    reference_url text,
    status        text NOT NULL              -- 'ok' | 'skewed' | 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_clock_skew_samples_sampled_at
    ON clock_skew_samples (sampled_at DESC);
```

The probe appends one row per cycle and **self-prunes**
(`DELETE … WHERE sampled_at < now() - make_interval(days =>
clock_skew_sample_retention_days)`, default 30) — no `retention_policies`
seeding-on-prod dependency; the table stays ~288 rows/day.

> `sampled_at DEFAULT now()` uses the (possibly skewed) DB clock — acceptable:
> the chart's own x-axis riding the skew during an incident is itself a visible
> tell, and the skew value plotted is measured against the external reference
> regardless.

### Component 4 — Grafana panel

A "DB clock skew (seconds)" time-series panel on an existing board (System
Health or Observability — decided at implementation by fit), querying
`clock_skew_samples` with `$__timeFilter(sampled_at)`, a threshold marker at
`clock_skew_threshold_seconds`, and null-gaps for `unknown`. The finding also
appears on the Findings board automatically. _(Scope-gated: can ship in a
follow-up PR if v1 is kept to probe + alert + runbook.)_

### Component 5 — self-heal posture

v1 = detect + page + runbook (honest: no safe auto-fix today —
`feedback_self_heal_not_suppress` permits paging once auto-heal options are
exhausted, and here they are either destructive or ineffective). Deferred
Phase 2: a non-destructive WSL clock resync via the host Recovery Agent (the
same agent `compose_drift`/`mcp_http_probe` already drive), behind a
default-off flag, before paging.

### Wiring

`brain/brain_daemon.py`: import `run_clock_skew_probe` with the flat/package
fallback + `_HAS_CLOCK_SKEW_PROBE` guard (mirroring the `data_freshness` import
block), and add a try/except invocation block in `run_cycle` storing into
`probe_results["clock_skew"]`.

## Config (app_settings — `settings_defaults.py`, in-code fallback drift-guarded by test)

| Key                                | Default                      | Purpose                                         |
| ---------------------------------- | ---------------------------- | ----------------------------------------------- |
| `clock_skew_probe_enabled`         | `true`                       | Master switch                                   |
| `clock_skew_reference_url`         | `https://www.cloudflare.com` | HEAD target; `Date:` header is the external UTC |
| `clock_skew_threshold_seconds`     | `120`                        | `abs(skew)` above this = skewed                 |
| `clock_skew_severity`              | `critical`                   | Finding severity → Telegram routing             |
| `clock_skew_renotify_minutes`      | `60`                         | Re-page window for a persistent episode         |
| `clock_skew_sample_retention_days` | `30`                         | Self-prune horizon for `clock_skew_samples`     |
| `findings.db_clock_skew.delivery`  | `telegram`                   | Per-kind delivery policy row                    |

## Testing (contract tests + docs, house defaults — inject `pool` + spies)

- Skew above threshold on the healthy→skewed edge → **one** finding emitted;
  a second skewed cycle within the renotify window → **no** duplicate.
- Skew below threshold → no finding; a sample row with `status='ok'`.
- Reference unreachable / missing `Date:` header → `status='unknown'`, **no**
  finding (no false page during an external outage).
- `clock_skew_probe_enabled=false` → no-op.
- skewed→healthy → info recovery, state cleared, next skew re-pages.
- Self-prune deletes rows older than the retention horizon.
- In-code `DEFAULT_*` fallback matches the `settings_defaults.py` seed
  (drift guard, mirroring `test_data_freshness_probe`).

## Rollout

- Migration adds `clock_skew_samples` (idempotent — `CREATE TABLE IF NOT
EXISTS`).
- `settings_defaults.py` seeds the new keys + the delivery policy on next boot.
- The probe begins sampling on the next brain cycle after the brain image /
  bind-mount picks up the new module; restart the brain to activate.
- Fully reversible: `clock_skew_probe_enabled=false` disables it; the table is
  self-pruning and tiny.

## Open questions

None blocking. Two operator-facing choices are defaulted with rationale
(external reference URL — a highly-available neutral endpoint, tunable; 120s
threshold — well below the 4h excursion, comfortably above real-world
sub-second jitter) and are reversible at review. One scope decision surfaced
for the operator: ship the Grafana time-series panel (Component 4) in this PR
or defer to a follow-up.
