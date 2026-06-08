# Migration-drift genuine self-heal

**Issue:** [Glad-Labs/poindexter#228](https://github.com/Glad-Labs/poindexter/issues/228)
**Probe:** `brain/migration_drift_probe.py`
**Status:** ships **dark** — `migration_drift_auto_sync_enabled` defaults to `false`.

## What this is

The brain's migration-drift probe watches the worker's `/api/health`
`components.migrations.pending` count. When the worker has shipped a migration
file the runner hasn't applied, `pending > 0`.

The original recovery was "`docker restart poindexter-worker` and hope." That
can't fix drift whose root cause is a **stale or polluted checkout** — a restart
just re-runs the same wrong bytes. On 2026-06-07 a stray untracked migration
scaffold in the live checkout made every restart no-op, and the probe paged
~131 times in a day.

The first "fix" suppressed the alert: a circuit breaker that stopped both
restarting and paging once a restart no longer helped. That was rejected:

> "I don't like suppressing alerts, that's not a proper fix. … the migration
> drift probe should be able to sync main and restart, not just fire a restart
> repeatedly. Suppression may be okay once we exhaust all other options and the
> system genuinely can't resolve it." — Matt

So the probe now **resolves the root cause** before it ever suppresses.

## How the genuine self-heal works

On each 5-minute brain cycle, when drift is detected **and**
`migration_drift_auto_recover_enabled=true`:

1. **New episode?** If the `pending` count changed (a new migration arrived),
   reset the attempt counters so the episode gets its full retry budget.
2. **Exhausted?** If attempts ≥ `migration_drift_recover_max_attempts`,
   **page once** (critical) + audit `probe.migration_drift_recover_suppressed`,
   then stay quiet until the count changes/clears. This is the **last resort**,
   reached only after real resolution attempts failed.
3. **Backoff gate.** After attempt _N_, wait `2^(N-1)` brain cycles before
   attempt _N+1_ (attempt 1 runs immediately). Prevents a restart-loop.
4. **Resolve + restart (one attempt):**
   - If `migration_drift_auto_sync_enabled=true`, resync the **dedicated deploy
     checkout** mounted at `migration_drift_deploy_checkout_path` (default
     `/host-deploy`): `git reset --hard origin/main` + `git clean -fd`. This
     wipes any stray untracked file and advances past a stale checkout, so the
     restart applies **correct** migration files. Audited as
     `probe.migration_drift_synced` / `…_sync_failed`.
   - `docker restart poindexter-worker` (the runner applies migrations on boot).
5. **Re-check.** Worker healthy + `pending == 0` → audit
   `probe.migration_drift_recovered`, reset counters, done. Drift persists →
   audit `probe.migration_drift_recover_attempt_failed` and let the next cycle's
   backoff/exhaustion logic decide. **No page on a single failed attempt.**

Restart-process failures and a worker that never comes back healthy still page
**immediately** — those are genuine infra faults, not "retry might help."

### Why a _dedicated_ deploy checkout

The recovery is a hard `git reset`. Running that on the checkout where a human
or a scheduled agent might have uncommitted work would clobber it, and gating on
"is work active?" is a race. A checkout that **nothing else ever touches**
collapses the race: `reset --hard` there is always safe. The network `fetch`
runs on the host (where git creds live); the in-container probe only resets to
the already-fetched `origin/main` — local, auth-free, network-free.

## Settings (seeded dark)

Seeded by migration `20260608_020831_seed_migration_drift_auto_sync_settings.py`
(`INSERT … ON CONFLICT DO NOTHING`, never clobbers an operator value):

| Key                                    | Default        | Meaning                                           |
| -------------------------------------- | -------------- | ------------------------------------------------- |
| `migration_drift_auto_sync_enabled`    | `false`        | Resync the deploy checkout before restarting.     |
| `migration_drift_deploy_checkout_path` | `/host-deploy` | In-container path of the deploy checkout.         |
| `migration_drift_recover_max_attempts` | `3`            | Attempts per episode before page-once + suppress. |

Pre-existing companion knob: `migration_drift_auto_recover_enabled` (the master
switch for restart-based recovery; also defaults `false`).

## Cutover (operator steps)

Until these are done, the system is a behavior no-op: an empty `/host-deploy`
makes the sync step a graceful no-op that falls back to a plain restart.

```bash
# 1. Create the dedicated deploy clone at ~/.poindexter/deploy/glad-labs-stack
bash scripts/setup-deploy-checkout.sh

# 2. Recreate the brain so the /host-deploy RW mount attaches
docker compose -f docker-compose.local.yml up -d --force-recreate brain-daemon

# 3. (optional) keep origin/main fresh for the probe to reset to.
#    Schedule this however you like (Task Scheduler / cron / a brain job):
git -C ~/.poindexter/deploy/glad-labs-stack fetch origin main --prune

# 4. Enable genuine self-heal
poindexter set migration_drift_auto_recover_enabled true   # if not already
poindexter set migration_drift_auto_sync_enabled true
```

## Verify

```bash
# The deploy checkout is a real work tree at origin/main:
git -C ~/.poindexter/deploy/glad-labs-stack rev-parse --short HEAD

# Brain sees the mount:
docker exec poindexter-brain-daemon git -C /host-deploy rev-parse --is-inside-work-tree

# Watch the audit trail during/after an episode:
#   probe.migration_drift_detected → _synced → _recovered   (happy path)
#   … → _recover_attempt_failed (x N) → _recover_suppressed  (exhausted)
```

Query recent probe audits:

```sql
SELECT timestamp, event_type, details->>'detail'
  FROM audit_log
 WHERE source = 'brain.migration_drift_probe'
 ORDER BY timestamp DESC
 LIMIT 20;
```

## Roll back

`poindexter set migration_drift_auto_sync_enabled false` disables the sync step
(restart-only recovery remains). `poindexter set
migration_drift_auto_recover_enabled false` disables recovery entirely (probe
falls back to detect + single notify per drift-count change). The `/host-deploy`
mount is inert when sync is off and can stay mounted.

## See also

- `~/.claude/projects/C--Users-mattm/memory/feedback_self_heal_not_suppress.md`
  — the principle this implements.
- [`docs/operations/migrations.md`](migrations.md) — migration conventions.
- `brain/migration_drift_probe.py` — the implementation + its docstring.
