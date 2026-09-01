# Retention backlog — the correctness signal

_poindexter#933. Origin: [Glad-Labs/glad-labs-stack#2871](https://github.com/Glad-Labs/glad-labs-stack/pull/2871)._

`retention.checkpoint_prune` failed to prune ~20,000 rows **for months** while
reporting success on every single run. Nothing was broken in a way anything
could see:

| signal                                 | during the bug                     |
| -------------------------------------- | ---------------------------------- |
| `last_error`                           | `NULL` — the handler never errored |
| `last_run_at`                          | current — ran every 6h on schedule |
| `total_deleted`                        | 15,807 — non-zero, looked healthy  |
| `last_run_deleted`                     | `0` — same as "nothing to do"      |
| "All retention policies" Grafana panel | all green                          |

The policy did exactly what it was told, and what it was told was wrong.

**Every signal we had was a _liveness_ signal.** Liveness answers "did it run?"
A misconfigured policy and an idle one both run, both succeed, and both delete
nothing — so they produce byte-identical telemetry. And since ~20 of the
enabled policies legitimately sit at `deleted=0` on most runs, `deleted=0` can
never itself be the alarm.

The missing question is a _correctness_ one: **how many rows should this policy
have removed and hasn't?** A correct policy drains its backlog to ~0 every run.
A broken one accumulates.

## The contract: handlers declare the invariant

Each retention handler registers a backlog expression next to itself
(`services/integrations/retention_backlog.py`):

```python
@register_backlog("ttl_prune")
def ttl_prune_backlog(row: Mapping[str, Any]) -> BacklogQuery | None:
    ...
```

Co-located deliberately — whoever writes the next handler sees the neighbouring
backlog function and has to answer the question it asks.

### Measure the invariant, not the policy's own predicate

This is the part worth getting right, and the reason the expression lives with
the handler rather than being reverse-engineered by the probe.

For **`ttl_prune`** the policy row _is_ the invariant: "rows older than
`ttl_days` matching `filter_sql` should be gone." Counting what its own `WHERE`
clause matches is exactly the right question.

For **`checkpoint_prune`** it is not — and this is the whole lesson. That
handler's bug lived _in its own `terminal_statuses` config_, which omitted
`rejected` and `rejected_final`, the two largest terminal buckets. A backlog
computed from the policy's own predicate would have returned **0 throughout the
outage** and caught nothing.

So its expression states what should be true independently of the policy's
configuration — phrased as "the task is **not active**" rather than as a second
copy of the terminal list:

```sql
WHERE pt.status <> ALL ($3::text[])          -- active statuses
  AND pt.updated_at < now() - make_interval(days => $1)
```

Phrasing it as the complement means a **newly added terminal status counts as
backlog automatically**, which is precisely the drift a copied list cannot
detect. Being generous with the active list makes the probe under-report rather
than false-alarm — the correct direction for an alarm to be wrong in.

> **The handler enforces the policy; the backlog expression states the
> invariant. The two disagreeing is the signal.**

The join is built by **concatenation** (`prefix || task_id`), the same way the
handler builds thread_ids, rather than by regex-stripping the prefix back off.
That keeps the two in step, avoids interpolating operator config into a regex,
and lets the planner hash-join: measured on the live 7,123-row table, the
strip-and-match form took **9.5s** and this one takes **0.05s** for the
identical answer.

## Persistence is the signal, not magnitude

A single non-zero reading means nothing. `live_activity` has a 2-day TTL and
high inflow; measured live, it held **1,699 overdue rows immediately after a run
that deleted 2,953**. That is inflow, not failure.

`ProbeRetentionBacklogJob` therefore reports a policy only once its backlog has
stayed above `retention_backlog_row_threshold` for
`retention_backlog_consecutive_probes` consecutive readings. A correct policy
drains between probes; only a broken one can hold a backlog across them.

Readings are stored as `retention_backlog_sample` rows in `audit_log` — the same
store the job-metrics sink already uses, so the history comes for free without a
new table. A policy missing from a prior sample breaks its streak: it was not
measured then, and an unmeasured reading is not a breaching one.

## Unmonitored is not healthy

Handlers with no backlog expression are counted and named in the probe's result
and in the finding body — **never folded into a passing zero**. `count` is
`None` for both "the handler declares no expression" and "the measurement
failed", and callers must not coerce that to 0.

A policy silently exempt from the correctness check would recreate the exact
blind spot the probe exists to close.

As of the initial rollout, 23 of 29 enabled policies are measured and 6 are
unmonitored (`embeddings_collapse`, `embeddings_orphan_prune` ×3, `downsample`
×2). Those handlers each need their own invariant, and the finding names them
every time it fires until they have one.

A `dry_run` policy declares no backlog on purpose: it deletes nothing by
design, so a backlog is meaningless as a fault signal and would alarm forever.

## Settings

| key                                    | default | meaning                                         |
| -------------------------------------- | ------- | ----------------------------------------------- |
| `retention_backlog_probe_enabled`      | `true`  | master switch                                   |
| `retention_backlog_row_threshold`      | `100`   | rows above which a reading counts as breaching  |
| `retention_backlog_consecutive_probes` | `3`     | consecutive breaching readings before a finding |

Routing is `findings.retention_backlog.*` — Discord, 12h cooldown, advisory
`warn`. A policy that runs clean but does not drain is a slow leak, not an
outage.

## Related

- #932 — orphaned checkpoints (a checkpoint whose task row is gone entirely).
  Deliberately **not** counted in `checkpoint_prune`'s backlog: the handler
  cannot reach them, so including them would report a backlog that pruning can
  never drain.
