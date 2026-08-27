# Authoring Prometheus alert rules that can actually fire

Rules live in `services/prometheus_rule_builder.py` (`DEFAULT_RULES`), are
rendered to `rules/dynamic.yml` every 5 minutes by `RenderPrometheusRulesJob`,
and are tunable per-threshold via `app_settings.prometheus.threshold.*`.

This page is about the two ways a rule can look completely correct and still
never fire. Both have cost us a real incident.

## Rule 1 — put the duration in the QUERY, not in `for:`

**A Prometheus restart discards every pending `for:` clock.** It is in-memory
state. When Prometheus comes back, an alert whose condition is still true
re-enters `pending` and must serve the entire duration again.

That would be academic if Prometheus were stable. It isn't — measured over 14
days on 2026-08-27:

|                                    |               |
| ---------------------------------- | ------------- |
| Prometheus restarts                | 28 in 14 days |
| **Median uptime between restarts** | **1.28 h**    |
| Longest uptime                     | 100.5 h       |

The driver is `poindexter-docker-watchdog` recreating the observability tier
(prometheus / grafana / alertmanager together). That is normal operation, not
an incident — so a rule must be written to survive it.

How much of a `for:` actually survives:

| `for:` | uptime windows long enough |
| ------ | -------------------------- |
| 5 m    | 82 %                       |
| 10 m   | 74 %                       |
| 30 m   | 59 %                       |
| 1 h    | 56 %                       |
| 2 h    | 41 %                       |
| 48 h   | **11 %**                   |

A long `for:` is **not** the conservative choice. It makes a rule _less_ likely
to ever fire.

**The fix is to express the window in the query.** Range vectors, `offset`, and
`*_over_time` are evaluated against persisted TSDB, so they are exact the
instant Prometheus is back:

```promql
# BAD — 48h of in-memory clock, wiped by every restart
last_over_time(poindexter_posts_total{status="published"}[1h] offset 24h)
  == last_over_time(poindexter_posts_total{status="published"}[1h])
for: 48h

# GOOD — the window is the offset; `for:` is only a debounce
last_over_time(poindexter_posts_total{status="published"}[1h] offset 48h)
  == last_over_time(poindexter_posts_total{status="published"}[1h])
for: 15m
```

`_FOR_CLAUSE_CEILING_SECONDS` (2 h) enforces this for new rules;
`test_no_rule_exceeds_the_for_clause_ceiling` fails the build if you exceed it.

> **Worked example — `NoPublishedPostsRecently`.** In the shape above-left it
> needed a drought to coincide with a 48 h uptime window (~11 % of them). Of
> the two qualifying droughts in the trailing 14 days it caught one and stayed
> silent through a **61-hour publishing outage** (08-18 09:18 → 08-20 22:28) —
> in a content business, with a dead-man switch that looked healthy.

## Rule 2 — a raw gauge with a long `for:` cannot survive a bursty signal

Separately from restarts: Prometheus resets the pending clock on **any** single
evaluation where the expression stops matching. A signal that rattles across
the threshold therefore never accumulates.

`PoindexterHostSwapExhausted` read `node_memory_SwapFree_bytes` raw with
`for: 2h`. A thrashing host reclaims in bursts, so swap-free bounced
0 % → 7 % → 0 % → 12 % every few minutes. Longest unbroken sub-threshold run at
native 1 m resolution: **71 minutes**. The rule sat `pending` for 8 h 15 m and
never fired while the box froze into a hard reboot.

Average the window first, then use a short `for:`:

```promql
100 * avg_over_time(node_memory_SwapFree_bytes[2h]) / node_memory_SwapTotal_bytes < 5
for: 30m
```

- Detection latency is **window + `for:`**, not `for:` alone. Swap hitting 0 %
  takes ~1.9 h to drag a 2 h mean under threshold, so 30 m ≈ 2.4 h end-to-end.
  Keep `for:` well under the window or you are slower than what you replaced.
- **`min`/`max_over_time` do not fix this** — they are still blip-sensitive
  (one good sample and `max_over_time` won't fire). "Mostly below X for N
  hours" is an average or `quantile_over_time`, not a min/max.

## Rule 3 — this is a different problem from scrape holes

The restart-gap policy atop `DEFAULT_RULES` predates both incidents and is
about **scrape holes**: a worker deploy blanks that target's series, so instant
selectors go empty. Its remedy is `last_over_time(...[1h])` on each operand.

That is a real and separate failure. Do not assume a rule hardened against one
is safe from the other — the swap rule's own comment argued that a
node_exporter metric made its long `for:` safe, which is true for holes and
false for restarts. A rule can be immune to holes and still unable to fire.

| symptom                | cause                          | fix                               |
| ---------------------- | ------------------------------ | --------------------------------- |
| operand goes empty     | scrape hole (exporter restart) | `last_over_time` on both operands |
| clock keeps restarting | Prometheus restart             | window in the query, short `for:` |
| clock keeps restarting | value blips over threshold     | `avg_over_time`, short `for:`     |

## Verifying a rule can fire — before shipping it

Do not reason about this; replay it. Query the alert's own expression against
the period it was supposed to catch and measure the longest unbroken run:

```bash
# if the longest run is shorter than `for:`, the rule provably never fires
curl -s "http://localhost:9091/api/v1/query_range" \
  --data-urlencode "query=<the rule expr>" \
  --data-urlencode "start=$(date -d '14 days ago' +%s)" \
  --data-urlencode "end=$(date +%s)" --data-urlencode "step=60"
```

Also check for the tell-tale symptom directly — an alert stuck `pending` for
hours is this bug, and it is **invisible in `alert_events`**, which only records
firings:

```promql
ALERTS{alertstate="pending"}
```

## Related

- [`host-oom-protection.md`](host-oom-protection.md) — the 2026-08-27 freeze the
  swap rule failed to catch.
- [`../architecture/findings-routing.md`](../architecture/findings-routing.md) —
  the delivery half (findings → `alert_events` → dispatcher), including the two
  throttles that are easy to confuse.
