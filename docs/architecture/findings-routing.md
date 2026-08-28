# Findings routing — from `audit_log` to an operator page

A **finding** is a structured observation written by a background job or probe:
`emit_finding(...)` inserts an `audit_log` row with `event_type='finding'`. Detection and
delivery are deliberately separate — a job's only responsibility is to notice something and
record it. Everything after that is routing.

```
job/probe ──emit_finding()──> audit_log            (the finding IS this row)
                                  │
                       FindingsAlertRouterJob      (every 60s, watermarked)
                                  │  per-kind policy from app_settings
                                  ▼
                             alert_events
                                  │
                      brain/alert_dispatcher       (fingerprint dedup, channel pick)
                                  ▼
                        Telegram / Discord
```

The finding persists whether or not any delivery channel works. That shape exists because
the predecessor didn't have it: the old path filed Gitea issues, Gitea was decommissioned
2026-04-30, and every quality finding vanished silently for 8 days.

## Execution contexts — who wires the writer

`emit_finding` is sync fire-and-forget: it delegates to `audit_log_bg`, which needs the
module-global `AuditLogger` plus a running event loop. Three contexts provide it:

| Context                 | Who initialises the global logger                        |
| ----------------------- | -------------------------------------------------------- |
| Worker / scheduled jobs | `DatabaseService.initialize()` (worker lifespan)         |
| Prefect flow subprocess | same — the flow builds its own `DatabaseService` per run |
| `poindexter <cmd>` CLI  | `open_cli_pool()` in `poindexter/cli/_bootstrap.py`      |

The CLI row is the newest (2026-08-26): CLI commands used to open bare asyncpg pools with
no logger at all, so any CLI-invoked service that emitted a finding lost it — discovered
when `poindexter pro sync` dropped a `pro_delivery_action_needed` warn finding during the
first live Pro purchase. Every CLI pool now goes through `open_cli_pool` /
`close_cli_pool`; the close side detaches the sink and **drains in-flight writes before
closing the pool**, because a fire-and-forget write scheduled moments before teardown
otherwise dies on `InterfaceError('pool is closing')` (the GlitchTip #863 race —
`DatabaseService.close` drains for the same reason). Both halves are fail-soft: a broken
audit seam never breaks the command.

Still uncovered: contexts with no DB handle at all (unit tests, a CLI command that opens
no pool, `integrations.py`'s single-connection commands — a shared bare connection can't
safely take concurrent background writes). There a warn/critical finding drops **loudly**
(error-level log → GlitchTip) per #303, but does not persist.

## Per-kind policy

Four `app_settings` keys shape each kind, all optional:

| key                                | meaning                                                                     | unset default |
| ---------------------------------- | --------------------------------------------------------------------------- | ------------- |
| `findings.<kind>.delivery`         | `route` / `telegram` / `discord` / `log_only` / `auto_fix` / `github_issue` | `route`       |
| `findings.<kind>.fallback`         | channel used when `auto_fix` / `github_issue` couldn't act                  | `route`       |
| `findings.<kind>.min_severity`     | floor below which the kind doesn't page                                     | `warning`     |
| `findings.<kind>.cooldown_minutes` | minimum gap between pages **per (kind, source)**                            | `0` (off)     |

**`findings.default.*` is deliberately inert.** `_load_policies` skips every `default` key.
An unlisted kind must stay loud — a default that quiets kinds nobody opted in for is exactly
the silent-drop this subsystem exists to prevent.

## Two different throttles, and why both exist

This is the part worth understanding, because they fail in opposite directions.

**Dispatcher dedup** (`brain/alert_dispatcher`, `alert_dedup_state`) is keyed by
**fingerprint**, which the router derives from the finding's `dedup_key`:

```
finding:{source}:{dedup_key}      # or finding:{source}:{kind} when no dedup_key
```

It collapses _the same finding repeating_. Perfect for `media_drift` firing every 15 minutes
about the same drift.

**Per-kind cooldown** (this router) is keyed by **(kind, source)**. It exists because
fingerprint dedup is structurally blind to a kind whose subject changes on every fire:

```
stale_task_reclaimed   -> a different task_id every fire   -> new fingerprint every time
gpu_lock_timeout       -> a different holder every fire    -> new fingerprint every time
```

Every one of those is a genuinely-new fingerprint, so dedup correctly declines to collapse
them — and the operator gets paged for all of them. Measured over 30 days to 2026-08-09:
`gpu_lock_timeout` 183 fires, `hero_render_fallback` 90, `stale_task_reclaimed` 78.

That volume had a second-order cost. The `alert-triage` ops session reads alert history and
files GitHub issues, so the noise became **18 of 36 open issues** on the private repo —
which then collapsed to ~5 real causes under manual review. A throttle here is cheaper than
the triage it prevents.

### Why the key includes `source` (poindexter#1010)

Two kinds are **umbrellas over unrelated producers**, and they are the two loudest in the
system:

| kind               | fires/30d | spans                                                                         |
| ------------------ | --------: | ----------------------------------------------------------------------------- |
| `job_failure`      |       307 | `sync_cloudflare_analytics`, `run_taps`, `poll_mercury`, `chat_task_watch`, … |
| `qa_rail_degraded` |       203 | `ragas_eval`, `deepeval_g_eval`, `deepeval_faithfulness`, …                   |

Keyed on kind alone, cooling `job_failure` would let `run_taps` failing **mute an unrelated
`poll_mercury` failure** eleven minutes later — silencing a fault nobody ever saw. That is
worse than the noise it fixes, so both kinds were deliberately left uncooled when the
cooldown shipped, which left the loudest kinds in the system unthrottled.

**The policy surface is unchanged.** One `findings.<kind>.cooldown_minutes` still configures
the kind; it now means _one page per source per window_. There is deliberately no
`findings.<kind>.<source>.cooldown_minutes` — it would break `_load_policies` (which splits
on `.` and requires exactly three segments) and sources contain dots anyway
(`scheduler.run_taps`).

`source` was already on every row — the router writes it into `labels`, and `alertname` is
literally `f"{source}:{kind}"` — so this is a grouping change, not a data-model one.

### The producer side: pick both keys per subject (poindexter#1015)

#1010 fixed the router. It cannot fix a producer that emits everything under one identity —
and `job_failure` is exactly that, which is how a tap outage stayed invisible for a year.

`external_taps` failures reached the operator only through the scheduler's generic
`job_failure` escalation, whose `dedup_key` is `job-fail:run_taps` for all nine taps and
every failure mode. Over the 30 days to 2026-08-15 that key fired 53 times, **43 of them
`internal_rag`**. A chronically failing tap owned the fingerprint, so any other tap breaking
arrived behind it and the dispatcher collapsed it as a repeat.

The fix is a per-subject producer, `tap_failure`, which sets **both** identities per tap:

| key         | value             | throttle it governs                     |
| ----------- | ----------------- | --------------------------------------- |
| `dedup_key` | `tap-fail:<name>` | dispatcher fingerprint dedup            |
| `source`    | `tap.<name>`      | router cooldown, keyed `(kind, source)` |

Setting only one is a trap. A per-tap `dedup_key` under a shared `source` still lets
`findings.tap_failure.cooldown_minutes` mute one tap for another — rebuilding the masking
bug one level up, which is why the 12h cooldown on this kind is only safe alongside the
per-tap `source`.

**Rule for new producers: if a kind can describe more than one subject, the subject belongs
in both `dedup_key` and `source`.** A kind that names exactly one thing (`gpu_lock_timeout`
is always the GPU lock) needs neither.

#### Severity as a streak gate

`tap_failure` also shows the cheap way to keep a transient blip off the ops channel without
suppressing anything. `info` is **structurally** unroutable — the router's fetch floor
(`_ROUTABLE_SEVERITIES`) selects `warn`/`critical` only — so a producer can record at `info`
and escalate to `warn` once the fault proves itself:

- 1st consecutive failure → `info`: on the Findings board, never paged.
- Nth (`tap_failure_alert_after_consecutive`, default 2) → `warn`: routed.

That is what makes a one-off container-DNS `ConnectError` (2026-08-15, healed on the next
run) free while a genuinely dead tap still pages an hour later. It needs a real counter —
`external_taps.consecutive_failures` — not `last_run_status`, which is one bit.

`job_overlap_skipped` is the same gate on a different axis, and shows the trap in picking
one. A scheduler fire skipped because the previous run is still in flight used to page on
the **first** skip, on the premise that a skip means the job is wedged. That premise holds
only when the interval is a runtime budget. `dispatch_media_pipeline` polls every 5 minutes
but renders on the GPU for minutes, so a normal cycle skips 2-6 due fires by design — 145 of
the system's 191 `job_overlap_skipped` findings over the 7 days to 2026-08-27, the loudest
kind in the system, every one of them correct behaviour reported as a fault.

The gate is **opt-in per job** (`overlap_expected = True` on the Job class) rather than a
flat time threshold, because a flat threshold gets the other half wrong: for an _hourly_
job the first skip already means an hour has elapsed, so deferring it would have made the
2026-08-15 tap wedge slower to surface — the very incident the finding was built for. A
declaring job records `info` until blocked past `scheduler_overlap_alert_after_minutes`
(default 60) and then pages; a non-declaring job is unchanged. The streak resets when the
run ends, so "slow" never escalates and "never finishes" always does.

**The general rule both cases share:** when a kind fires on a subject that is sometimes
routine and sometimes a fault, the discriminator belongs in the _producer's severity_, not
in a router cooldown. A cooldown throttles a true signal to a survivable rate; severity
separates the true signal from the false one. Reach for a cooldown only once the finding
means the same thing every time it fires.

#### Declined is not failed

The same change stopped counting `GpuBusyError` / `GpuLockTimeoutError` as tap failures
(`tap_deferral_exception_types`). Those are the GPU scheduler correctly declining while the
operator is gaming or the video pipeline holds the lock — 16 of the 43 `internal_rag`
"failures" above. They record `last_run_status='deferred'`, never advance the streak, never
emit a finding, and no longer flip `RunTapsJob` to `ok=False`. `sentry_integration.py`
already draws this line the other way round (`DEFAULT_DROP_EXCEPTION_TYPES`); a finding
producer that pages on backpressure is reporting an outage that isn't one.

### Cooldown rules

1. **`critical` is never cooled.** Same floor `_delivery_for` and `_deliver_fallback` already
   hold for `log_only`. A cooldown that can swallow a critical page is a silent drop.
2. **Unset, `0`, negative, or unparseable means no cooldown.** Every failure mode resolves
   toward paging; an unparseable value logs a warning and routes. A typo must not become an
   accidental mute.
3. **Only the primary paging decision is cooled.** `auto_fix` and `github_issue` aren't pages
   and carry their own dedup, and a _fallback_ firing means the primary channel broke —
   an exceptional condition worth seeing every time.
4. **A cooled finding still advances the watermark.** It was handled, not deferred; it
   remains in `audit_log`, queryable and on the Findings dashboard.

### Where "last routed" lives

Nowhere new. This router is the only writer of `alert_events` rows with `category='finding'`,
so that category _is_ the routing history:

```sql
SELECT labels->>'kind' AS kind, MAX(received_at) AS last_routed
FROM alert_events
WHERE category = 'finding'
  AND received_at > NOW() - make_interval(mins => $1)
  AND labels->>'kind' IS NOT NULL
GROUP BY 1
```

One grouped query per cycle, bounded by the largest cooldown among the kinds actually in the
batch — a batch with no configured cooldowns skips the query entirely. It is self-healing:
prune `alert_events` and cooldowns simply reset, which is the safe direction.

A dedicated `findings_dispatch_state` table was tried and reverted (migration
`20260531_150000_drop_findings_dispatch_state_duplicate.py`) as a duplicate path. Deriving
the state avoids re-introducing it.

**In-batch stamping matters.** The map is updated in memory as rows route, so a backlog of
50 findings of one kind pages once rather than 50 times. Without it every row reads the same
pre-batch timestamp and the whole batch stampedes through the gate — the exact shape the
cooldown is meant to stop.

## Observability

`FindingsAlertRouterJob` returns `JobResult.metrics` with `routed` / `autofixed` / `filed` /
`suppressed` / `cooled` / `errors`. The scheduler writes these to `audit_log` as
`event_type='job_run'` rows (see [job-run-metrics.md](job-run-metrics.md)), so
"how much noise did the cooldown absorb" is a Grafana panel rather than a log grep. Without
that counter the feature is unfalsifiable — a working throttle and a kind that simply went
quiet look identical.

The **Findings — Probe Routing** dashboard (`/d/findings`) shows emitted vs pending-delivery
counts and the live `kind → delivery` policy join.

## Operator recipes

```bash
# What is this kind's policy right now?
poindexter settings list | grep '^findings\.gpu_lock_timeout\.'

# Throttle a noisy kind to one page an hour
poindexter settings set findings.gpu_lock_timeout.cooldown_minutes 60

# Turn a cooldown off (0 and unset are equivalent)
poindexter settings set findings.gpu_lock_timeout.cooldown_minutes 0

# Replay everything (reset the watermark)
poindexter settings set findings_alert_route_watermark 0
```

Turning a cooldown **up** is the wrong reflex for a kind that fires constantly — that hides a
real signal. Prefer fixing the cause, or set `delivery=log_only` if the kind is genuinely
informational; both leave the finding queryable. See `feedback_dont_silence_fix_dedup`.

## Related

- `src/cofounder_agent/services/jobs/findings_alert_router.py` — the router
- `src/cofounder_agent/utils/findings.py` — `emit_finding`, the emission contract
- `brain/alert_dispatcher.py` — channel selection + fingerprint dedup
- [job-run-metrics.md](job-run-metrics.md) — how `JobResult.metrics` reaches Grafana
- Glad-Labs/poindexter#461 (policy delivery), #551 (this cooldown)
