# Deploy-drift canary — branch-drift probe (design)

- **Date:** 2026-06-02
- **Status:** Approved design, pending implementation
- **Issues:** Closes the canary half of [glad-labs-stack#942](https://github.com/Glad-Labs/glad-labs-stack/issues/942); part of umbrella [#936](https://github.com/Glad-Labs/glad-labs-stack/issues/936). Sibling to the already-shipped deploy script (PR #952).
- **Repo:** `Glad-Labs/glad-labs-stack` (source of truth; private). PR target is `origin/main`, never the public `poindexter` mirror.

## Problem

The `poindexter-worker`/brain/pipeline-bot/prefect-worker containers bind-mount the host checkout's source live. When that checkout is parked on a stale branch, merged work on `main` never reaches production — the months-long "merged ≠ deployed" drift that hid the #355 atom-cutover (the worker ran 13 commits behind `origin/main` on `feat/issue-auto-triage`).

The one canary meant to catch this — `poindexter_unapplied_migrations_count` (`services/metrics_exporter.py:517`) and the equivalent `pending` field in the worker's `/api/health` migrations block — is **structurally blind to branch-drift**. Both compute `unapplied = on_disk_migration_files − schema_migrations_rows`, where `on_disk` is globbed from the **bind-mounted host checkout**. If `origin/main` has a migration the host branch lacks, that file simply isn't on disk: `on_disk` doesn't count it, `applied` doesn't count it, and the difference is `0` → "all clear." A monitor that reads the same stale source as the bug it's meant to catch can never see that bug.

`brain/migration_drift_probe.py` (the auto-restart probe) shares the blindness — it reads `pending` from the worker's `/api/health`, which is the same on-disk-vs-DB glob.

## Root design principle

**Change the data source, not the diligence.** The canary must read its ground truth from `origin/main` — a source the host checkout cannot make lie. Two facts are needed, from two sources:

1. **What commit prod is actually running** — only obtainable from the host's `.git`. The brain is containerized with no `.git` and no baked commit SHA, so today it literally cannot answer this. We fix that with a **read-only `.git` mount** into the brain.
2. **What `origin/main` is, and how far ahead** — from the **GitHub API** (the codebase's established pattern for git state in containers, via the `gh_token` secret; see `brain/pr_staleness_probe.py` and the #294 dev_diary rewrite).

The read-only mount is deliberate. `git fetch` writes refs/objects, which `:ro` forbids — so the probe never fetches inside the container; it reads the local HEAD ref (pure read) and gets `origin/main` + the behind-count from GitHub. This keeps the security posture tight and sets a clean precedent; a separate read-write working-tree mount for future autonomous code development is explicitly out of scope here (YAGNI).

## Goals / non-goals

**Goals**

- Detect when the running prod checkout is behind `origin/main` (commit-level — catches code-only drift, not just schema drift) and page the operator.
- Survive the read-only constraint; no in-container `git fetch`.
- Mirror existing brain-probe conventions (DB-configurable, fail-loud, injectable test seams).

**Non-goals**

- Auto-remediation (no auto `git pull`/deploy from the brain). Alert-only — a checkout move can clobber WIP or pull breaking changes mid-pipeline. The alert points at `pwsh ./scripts/deploy-worker.ps1`.
- A new Prometheus gauge in v1 (`alert_events` + `audit_log` is how every brain probe surfaces; sufficient to "trip the canary"). Deferred to future work.
- The deploy script + its `ci-deploy-chain.md` doc — already shipped (PR #952).

## Component: `brain/branch_drift_probe.py`

Standalone module modeled on `brain/pr_staleness_probe.py`. Only stdlib + `asyncpg` + `httpx` (+ the `git` binary via `subprocess`). Entry point `run_branch_drift_probe(pool, *, git_runner=None, http_client_factory=None, notify_fn=None, now_fn=None)` with all I/O behind injectable seams for unit tests. Module-level dedup state with a `_reset_state()` test hook.

**Per-cycle lifecycle** (gated by `branch_drift_probe_enabled` + an internal `branch_drift_poll_interval_minutes` cadence gate, mirroring pr-staleness):

1. **Local HEAD.** `git --git-dir=<branch_drift_git_dir> rev-parse HEAD` → `local_head` SHA; `git ... rev-parse --abbrev-ref HEAD` → branch name. Pure read; no network. Git errors (missing mount, not a repo) → fail loud (step 5).
2. **origin/main truth.** `GET /repos/{branch_drift_repo}/commits/main` with `Authorization: Bearer {gh_token}` → `main_sha`. Private repo → unauth/401 fails loud.
3. **Compare.**
   - `local_head == main_sha` → **no drift.** Emit `probe.branch_drift_ok` audit, reset dedup, return `ok=True`.
   - else `GET /repos/{branch_drift_repo}/compare/{local_head}...{main_sha}` → `status`, `ahead_by` (commits on main missing from prod = the "behind" count), `behind_by`. A `404` (local HEAD unknown to GitHub — unpushed local commit) is **not** a probe failure: treat as drift with an uncomputable count.
4. **Trip the canary** (behind count > 0, or not-on-main with uncomputable count):
   - One `alert_events` row, `severity=warning`, so the dispatcher routes per `feedback_telegram_vs_discord`. Body: branch name, `local_head` short SHA, `main_sha` short SHA, N commits behind, and the remedy command `pwsh ./scripts/deploy-worker.ps1`.
   - `probe.branch_drift_detected` audit row.
   - Dedup on the `(local_head, main_sha)` pair (persisted to `alert_dedup_state`, like pr-staleness) so it pages once per drift state until it changes or `branch_drift_dedup_hours` elapses — not every cycle.
   - Return `ok=False` so the brain cycle's probe-failure count reflects reality.
5. **Fail loud** (git binary error, `gh_token` missing, GitHub 5xx/timeout): emit `probe.branch_drift_failed` audit at `severity=warning`, return `ok=False`. No silent fallback.

### Optional enrichment (cheap, include if trivial)

When behind, also list `services/migrations/` on `origin/main` via the GitHub Contents API and diff filenames against `SELECT max(name) FROM schema_migrations` to report "…including M unapplied migration(s)" in the alert body — directly ties the page to the #355 failure mode. Gate stays purely commits-behind; this only enriches the message.

## Infra changes

**`docker-compose.local.yml`** — brain-daemon `volumes:` add:

```yaml
# Read-only host .git so brain/branch_drift_probe.py can read the running
# checkout's HEAD SHA (the one fact nothing else in a container can supply)
# and detect when prod has fallen behind origin/main. Top-level mount —
# NOT the forbidden ./.git:/app/.git:ro worker mount (that one broke under
# the worker's :ro /app via overlayfs child-mount rejection, #348). The
# brain's /app is the image filesystem, not a :ro bind, and the brain runs
# as root, so a read-only .git mount here is safe. Read-only on purpose:
# the probe never fetches/writes; it reads local HEAD and gets origin/main
# from the GitHub API.
- ./.git:/host-git:ro
```

Leave the worker's existing "do NOT re-add this mount" note intact — it remains true for the worker.

**`brain/Dockerfile`**

- Add `git` to the existing `apt-get install -y --no-install-recommends` line; do **not** remove it in the cleanup step.
- Add `branch_drift_probe.py` to the `COPY` list (line ~65) and to the `/app/brain/` mirror-cp block (lines ~71–95), matching the established pattern.

**Deploy** — because brain code is image-baked (not bind-mounted) and the Dockerfile/compose change too: `docker compose -f docker-compose.local.yml build brain-daemon && docker compose -f docker-compose.local.yml up -d brain-daemon`. A plain restart is **not** enough for this change.

## Config (seed migration)

`YYYYMMDD_HHMMSS_seed_branch_drift_probe_app_settings.py`, mirroring the pr-staleness seed (`INSERT ... ON CONFLICT DO NOTHING`):

| key                                  | default                     | purpose                                       |
| ------------------------------------ | --------------------------- | --------------------------------------------- |
| `branch_drift_probe_enabled`         | `true`                      | master switch                                 |
| `branch_drift_poll_interval_minutes` | `15`                        | internal cadence gate (brain cycle is 5 min)  |
| `branch_drift_repo`                  | `Glad-Labs/glad-labs-stack` | source-of-truth repo                          |
| `branch_drift_dedup_hours`           | `6`                         | re-page interval for an unchanged drift state |
| `branch_drift_git_dir`               | `/host-git`                 | git dir path inside the brain container       |

Reuses the existing `gh_token` secret (no new secret).

## Wiring: `brain/brain_daemon.py`

Add the import (flat + `brain.`-qualified shim, like the siblings) and call `run_branch_drift_probe(pool)` in `run_cycle`, wrapped in the same per-probe try/except that increments the cycle's probe-failure count. Runs every cycle; the inner cadence gate decides whether to do the real GitHub round-trip.

## Testing

`src/cofounder_agent/tests/unit/services/test_brain_branch_drift_probe.py`, injecting fake `git_runner`, `http_client_factory`, `notify_fn`, `now_fn`:

- **on-main** (`local_head == main_sha`) → no alert, `probe.branch_drift_ok`, `ok=True`.
- **N behind** → exactly one `alert_events` row + `probe.branch_drift_detected`; second cycle with same `(head, main)` is dedup-suppressed. **This is the literal #942 acceptance test: "a branch behind origin/main trips the canary."**
- **unpushed HEAD** (compare 404) → degraded alert ("not on main, lag uncomputable"), still `ok=False`, not a probe failure.
- **fail-loud**: missing `gh_token` / GitHub 5xx / git binary error / missing `.git` mount → `probe.branch_drift_failed`, `ok=False`, no alert_events spam.
- **cadence gate**: second call within the poll interval does no GitHub round-trip.

## Acceptance criteria (#942, canary half)

- [x] A branch behind `origin/main` trips the canary → covered by the "N behind" unit test + live verification on deploy.
- [x] (Deploy-script half already done by PR #952 — to be reflected on the issue when this lands.)

## Rollout

1. Land code + seed migration + tests on `claude/magical-joliot-9b0197`, PR to `glad-labs-stack`.
2. On merge + host-side deploy: `docker compose build brain-daemon && up -d brain-daemon`.
3. Live-verify: confirm `/host-git` is readable in-container, force a synthetic behind-state (or check the audit log on the first real cycle), confirm one Telegram/Discord page + `probe.branch_drift_detected` audit row, and confirm `branch_drift_probe_enabled=true` in prod app_settings.

## Risks & mitigations

- **Re-triggering the overlayfs `.git` breakage** → mitigated: top-level `/host-git` in the brain (whose `/app` is not a `:ro` bind), not `/app/.git` under the worker's `:ro /app`.
- **Stale local `origin/main` ref** → avoided entirely: never read the local `origin/main` ref; GitHub API is the authority for main + behind-count.
- **`gh_token` scope/expiry** → fail-loud `probe.branch_drift_failed` surfaces it rather than silently reporting "no drift."
- **False page right after a deploy** (brain cycle races the checkout move) → the cadence gate + `(head,main)` dedup absorb a single transient; once on main, `local_head == main_sha` clears it.

## Future work (out of scope)

- Prometheus gauge `poindexter_commits_behind_main` + Grafana panel reading the `probe.branch_drift_*` audit events.
- A deliberate read-write working-tree mount for autonomous code development (the forward-looking reason `.git` access was approved) — its own design when that need is concrete.
