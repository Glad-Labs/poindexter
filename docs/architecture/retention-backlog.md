# Retention backlog — the correctness signal

_poindexter#933. Origin: [Glad-Labs/poindexter#2871](https://github.com/Glad-Labs/poindexter/pull/2871)._

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

## Sample AFTER the run, not on a fixed clock

A backlog only means "failed to drain" if it is read once the policy has had
its chance to drain. Read _before_ a pass it means "accumulated since last
time", which for any healthy high-inflow policy is large by definition.

The first version of this probe scheduled itself `every 6 hours` and asserted
in a code comment that it was offset from `RunRetentionJob` "so a reading is
taken shortly after a pass". **It was not.** Both jobs registered as
`every 6 hours` from worker start and landed 26 minutes apart in the wrong
direction — retention at `:41`, the probe at `:15` — so every sample was taken
5h34m after the previous pass and 26 minutes before the next, at near-maximum
backlog.

It filed a false `retention_backlog` finding for `live_activity` on its third
reading (2738 → 2746 → 2751) while that policy was draining **completely**
every run: `last_run_deleted=2961`, `last_error` NULL. The probe built to
distinguish a broken policy from an idle one had begun reporting a healthy one
as broken, for the same underlying reason — it was measuring the wrong moment.

So phase is not something to assert and hope for. The probe now ticks every 30
minutes and records a reading for a policy **only** when:

- that policy has run within `retention_backlog_sample_window_minutes` (90), and
- it has not already been sampled for that pass — keyed on the policy's own
  `last_run_at`, so a fast tick cannot stack three readings of one post-run
  state and let the persistence rule call them a trend.

Two schedules can drift apart; a policy's own `last_run_at` cannot lie about
when it last ran. A tick where no policy is in-window records **nothing** and
says so — writing an empty sample would break every persistence streak.

A policy that has never run is not a backlog signal either: `RunRetentionJob`'s
own liveness covers a policy that is not executing at all, and reporting a
backlog for it would blame the wrong mechanism.

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

| key                                       | default | meaning                                                  |
| ----------------------------------------- | ------- | -------------------------------------------------------- |
| `retention_backlog_probe_enabled`         | `true`  | master switch                                            |
| `retention_backlog_row_threshold`         | `100`   | rows above which a reading counts as breaching           |
| `retention_backlog_consecutive_probes`    | `3`     | consecutive breaching readings before a finding          |
| `retention_backlog_sample_window_minutes` | `90`    | how soon after a policy's own run a reading still counts |

Routing is `findings.retention_backlog.*` — Discord, 12h cooldown, advisory
`warn`. A policy that runs clean but does not drain is a slow leak, not an
outage.

## Thread ids the prune cannot construct

`retention.checkpoint_prune` discovers checkpoints by building
`prefix || task_id`. Any thread named after something else is invisible to
**every** retention policy — there is no query path from the checkpoint back to
a policy decision.

That, not deleted task rows, is what produced the residue in
[#932](https://github.com/Glad-Labs/poindexter/issues/932). Measured on prod
2026-09-02, of 7,846 unreachable rows:

| thread shape                    | threads | rows  | cause                                    |
| ------------------------------- | ------- | ----- | ---------------------------------------- |
| `two_pass-{niche}-{topic[:32]}` | 28      | 7,763 | writer subgraph keyed on topic, not task |
| `media-<task>-revalidate-1`     | 1       | 10    | one-off suffixed thread                  |
| task-shaped, task row gone      | 1       | 6     | a genuine orphan                         |

The issue's diagnosis — checkpoints whose source `pipeline_tasks` row was
deleted — accounts for **6 of 7,846 rows**, and nothing prunes `pipeline_tasks`
at all. The writer's key was also not unique per run: 176 distinct 32-char
topic prefixes are shared by more than one task, the worst by 201 of them.

Keying the writer's inner graph on `task_id` fixes the dominant case at the
source and brings it under the existing handler via one new
`thread_prefixes` entry. **A new subgraph that invents its own thread id
inherits this problem** — if it is not `prefix || task_id`, nothing will ever
delete its checkpoints.

## Related

- #932 — orphaned checkpoints (a checkpoint whose task row is gone entirely).
  Deliberately **not** counted in `checkpoint_prune`'s backlog: the handler
  cannot reach them, so including them would report a backlog that pruning can
  never drain.
