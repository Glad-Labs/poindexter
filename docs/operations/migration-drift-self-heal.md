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
makes the sync step a graceful no-op that falls back to a plain restart, and the
worker still runs the dev checkout (so a clone reset wouldn't change its code).

**Why the worker repoint matters.** The migration-drift probe resets the deploy
_clone_ and restarts the _worker_. That only resolves drift if the worker
actually runs from the clone. By default the worker (and the other 4
src-mounting services) bind-mount the live dev checkout, so the relocation is
the step that closes the loop. It's controlled by one env var,
`POINDEXTER_DEPLOY_ROOT` (see the comment at the worker's `:/app` mount in
`docker-compose.local.yml`): unset → dev checkout (default, what fresh operators
get); set → the deploy clone.

```bash
# 1. Create the dedicated deploy clone at ~/.poindexter/deploy/glad-labs-stack
bash scripts/setup-deploy-checkout.sh

# 2. Recreate the brain so the /host-deploy RW mount attaches
docker compose -f docker-compose.local.yml up -d --force-recreate brain-daemon

# 3. Keep the clone advancing to origin/main as merges land (fetch + reset).
#    Registers a Windows Scheduled Task that runs every 10 min and syncs now:
pwsh scripts/deploy-checkout-sync.ps1 -Install

# 4. Repoint the stack's runtime code mounts at the clone, then recreate the
#    src-mounting services so they pick up the new mount. Set the var in .env
#    so it persists across `docker compose up` / reboots:
echo 'POINDEXTER_DEPLOY_ROOT=C:/Users/<you>/.poindexter/deploy/glad-labs-stack' >> .env
# Recreate every service whose mount references POINDEXTER_DEPLOY_ROOT. These are
# compose SERVICE names (not container names): worker, pipeline-bot,
# prefect-worker, the two voice agents (src/cofounder_agent mounts) + gpu-exporter
# (brain mount). --no-deps so dependencies aren't bounced.
docker compose -f docker-compose.local.yml up -d --force-recreate --no-deps \
  worker pipeline-bot prefect-worker \
  voice-agent-livekit voice-agent-claude-code gpu-exporter

# 5. Enable genuine self-heal
poindexter settings set migration_drift_auto_recover_enabled true   # if not already
poindexter settings set migration_drift_auto_sync_enabled true
```

**Rollback of the repoint:** remove the `POINDEXTER_DEPLOY_ROOT` line from `.env`
and re-run the step-4 `up --force-recreate` — the mounts fall back to the dev
checkout. `docker restart` keeps a container's existing mount, so the probe's
restart is unaffected either way.

**Note — normal (non-migration) deploys after the repoint:** the 10-min sync
keeps the clone's _code_ at origin/main, but a running service only loads new
code on its next restart. Migrations self-heal (the probe restarts the worker);
other code changes load on the service's next natural/manual restart. Making the
sync also restart services (continuous deploy) is a deliberate future step, not
wired here.

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

`poindexter settings set migration_drift_auto_sync_enabled false` disables the sync step
(restart-only recovery remains). `poindexter settings set
migration_drift_auto_recover_enabled false` disables recovery entirely (probe
falls back to detect + single notify per drift-count change). The `/host-deploy`
mount is inert when sync is off and can stay mounted.

## See also

- `feedback_self_heal_not_suppress` (operator design note)
  — the principle this implements.
- [`docs/operations/migrations.md`](migrations.md) — migration conventions.
- `brain/migration_drift_probe.py` — the implementation + its docstring.
