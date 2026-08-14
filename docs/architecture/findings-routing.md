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
