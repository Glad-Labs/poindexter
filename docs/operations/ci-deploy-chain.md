# How Poindexter itself is tested and deployed

**Last Updated:** 2026-06-19

> **What this doc is.** A transparency record of how Poindexter (the
> project, not your self-host) is tested and shipped to [gladlabs.io](https://www.gladlabs.io).
> Kept here so that contributors can understand why a PR fails CI,
> what gets run on every push, and how changes reach production.
>
> **What this doc isn't.** A recipe for setting up your own CI. If
> you want to run Poindexter on your own infrastructure, the only
> supported deployment is `poindexter setup` then
> `bash scripts/start-stack.sh` on a single machine.

## The flow

```
Glad-Labs/glad-labs-stack (private GitHub, source of truth)
    │
    ├─→ GitHub Actions (several workflows — there is no single ci.yml)
    │       required checks: unit-tests.yml (job test-backend,
    │       backend pytest) + migrations-smoke.yml + mcp-server-tests.yml
    │       (job mcp-server-tests, the mcp-server uv suite), on every PR +
    │       push to main (expensive steps short-circuit on docs-only or
    │       unrelated changes — see "CI minutes / cost discipline" below)
    │       non-required, paths-gated: playwright-e2e.yml (frontend
    │       E2E), security.yml, grafana-panels-lint.yml
    │
    ├─→ Vercel (auto-deploy on push to main)
    │       └─→ www.gladlabs.io
    │
    └─→ sync-to-public-poindexter.yml (auto, filtered)
            │
            └─→ Glad-Labs/poindexter (public GitHub mirror)
                    │
                    ├─→ public-side CI checks
                    │       (test-backend, migrations-smoke,
                    │        mcp-server-tests, Mintlify Deployment,
                    │        link-rot)
                    │
                    └─→ Release Please runs for versioning only
                        (no deploy)
```

Vercel watches `Glad-Labs/glad-labs-stack` (the private origin),
NOT the public `poindexter` repo. The public repo has no deploy
workflow — Release Please is the only thing producing artifacts.

The cross-repo sync is automatic: GitHub Actions workflow
`.github/workflows/sync-to-public-poindexter.yml` runs on every push
to `origin/main` and mirrors the filtered subset to the public repo
in ~30s, authenticating with a dedicated GitHub App
(`glad-labs-mirror-sync`, installed on poindexter with Contents +
Workflows read+write; secrets `MIRROR_SYNC_APP_ID` +
`MIRROR_SYNC_APP_PRIVATE_KEY` on glad-labs-stack). Migrated
2026-06-13 from a fine-grained PAT that silently expired and froze
the mirror (which had itself replaced an SSH deploy key 2026-05-09).
Just `git push origin main` and the public mirror updates itself.

`scripts/sync-to-github.sh` strips private files (web/public-site,
web/storefront, mcp-server-gladlabs, marketing, premium dashboards,
writing_samples, gladlabs-config, .shared-context, CLAUDE.md,
`scripts/bootstrap.sh`, and select internal docs — audits, plans,
and the operator-only finance / CI-runner runbooks; the rest of
`docs/` ships to Mintlify) before pushing.

The sync filter also performs content-level rewrites:

- **docs.json**: operator-branded `gladlabs.io` URLs are rewritten to
  poindexter-neutral GitHub URLs so OSS forks don't inherit operator branding.
- **CHANGELOG.md**: lines mentioning operator-private values — private
  finance-module `app_settings` keys, Tailnet hostnames, or hardware-cost
  figures — are redacted before the mirror push.
- **Operator-name regex**: the leak guard uses
  `[Mm]atthew (?:[A-Z]\.\s+)?[Gg]ladding` (with optional middle-initial
  group) to catch both the plain and middle-initial forms of the operator
  name. Added in the 2026-05-27 security audit — the middle-initial form
  was slipping past the old `[Mm]atthew [Gg]ladding` pattern.

**Bypass:** include `[skip-public-sync]` in the commit message to
keep a particular commit private (in-progress branches, sensitive
WIP).

## Debugging "Vercel is failing"

If you see a notification that Vercel deploy failed:

1. **Check the Vercel dashboard** — the deploy runs directly from
   the `glad-labs-stack` repo via Vercel's GitHub integration, not
   via a GitHub Actions workflow.
2. **Common build failure:** `next.config.js` rejects localhost
   URLs in production. Set `SKIP_ENV_VALIDATION=true` in Vercel
   env vars, or ensure `NEXT_PUBLIC_API_BASE_URL` is set to a
   real URL (or left empty for static-only builds).
3. **If tests fail locally:** reproduce with
   `docker exec poindexter-worker python -m pytest tests/unit/ -q`.
   Frontend: `cd web/public-site && npm run test`.

## Local vs CI environment differences

A few tests pass in CI but fail inside the worker container because
the worker runs with `ENVIRONMENT=production` set and some
middleware evaluates that at import time. Tests that depend on the
`brain` module or `sentry-sdk` are skipped in Docker (the modules
aren't available in the worker container). See the `skipif`
decorators in `test_database_service.py` and
`test_sentry_integration.py`.

## Key files

- `.github/workflows/unit-tests.yml` — backend pytest, exposed as the
  `test-backend` status check. One of the **three** branch-protection
  required checks; a `detect-changes` step short-circuits the
  expensive pytest steps on docs-only changes while still reporting
  green (a required check must always report — see "CI minutes / cost
  discipline" below). No deploy step.
- `.github/workflows/migrations-smoke.yml` — applies every migration
  against a clean Postgres + pgvector. Another branch-protection
  required check; fires on every PR + push to main.
- `.github/workflows/mcp-server-tests.yml` — runs the `mcp-server/`
  pytest suite (its own `uv` venv) as the `mcp-server-tests` status
  check, the **third** branch-protection required check. mcp-server
  imports across the repo boundary (`services.*`, `modules.content.api`,
  and `brain.*` via `sys.path`), but no workflow ran its suite — so a
  shared-code refactor could merge a red mcp-server lane (PR #1663 did
  exactly that; the breakage sat latent on main until #1742). A
  `detect-changes` step gates the `uv` install + pytest on changes under
  `mcp-server/**`, `src/cofounder_agent/**`, or `brain/**` while still
  reporting green on unrelated PRs. Runs on the public mirror too (the
  tested code ships there), where it is non-required.
- `.github/workflows/playwright-e2e.yml` — frontend E2E (Playwright),
  `paths:`-gated to `web/public-site/**`. Non-required. The frontend
  Jest unit run + JS lint are **hook-only**, not run in CI (see the
  workflow header).
- `.github/workflows/security.yml` / `grafana-panels-lint.yml` —
  non-required scans: gitleaks / trivy / sbom + path-specific lints,
  and the paths-gated Grafana panel lint, respectively.
- `.github/workflows/sync-to-public-poindexter.yml` — auto-mirror
  from glad-labs-stack to poindexter on every push to main.
- `scripts/sync-to-github.sh` — filter that runs inside the sync
  workflow. Strips operator-only files before pushing the public
  subset.
- `.github/workflows/release-please.yml` — Release Please on
  `Glad-Labs/glad-labs-stack` (the source repo — NOT the public
  mirror; running it on the force-rebuilt mirror broke versioning,
  see the workflow header). Versioning only. **Runs daily at 08:00
  UTC** (was `on: push` to main) so a day's `feat:`/`fix:` commits
  batch into one release instead of one-per-merge — the per-merge
  cadence 3×-amplified Actions-minute usage (each release commit
  re-ran the full suite AND re-triggered this workflow).
  `workflow_dispatch` cuts an ad-hoc release immediately.
- `.github/workflows/regen-app-settings-doc.yml` — nightly regen of
  `docs/reference/app-settings.md` against a clean Postgres seeded
  by the baseline migration. Opens a single PR on
  `chore/regen-app-settings-doc` when the file drifts; the branch
  is force-pushed every run so the PR always reflects the latest
  regen. Per [poindexter#439](https://github.com/Glad-Labs/poindexter/issues/439).
- `.github/workflows/regen-services-doc.yml` — PR-time drift guard
  for `docs/reference/services.md`. Path-gated to
  `src/cofounder_agent/services/**` + `modules/content/**`; regenerates
  the catalog in-place and fails if the checked-in copy drifts. Unlike
  `regen-app-settings-doc`, this needs no DB — the generator is pure
  stdlib. Non-required.
- `.github/workflows/integration-db.yml` — runs the
  `tests/integration_db/` tier against an ephemeral pgvector Postgres.
  These tests require a live database (migration round-trips, settings
  seeding, claim-pending-task) and were silently omitted from CI before
  this workflow. Path-gated to the backend tree; non-required (pending
  a stable green track record to promote to required).
- `.github/workflows/jest-unit.yml` — frontend Jest gate for
  `web/public-site/**`. Path-gated; non-required (can't use the
  always-run + internal-skip pattern that required checks need, because
  the file-change detection itself is the skip condition).
- `.github/workflows/public-mirror-safety.yml` — pre-merge leak guard.
  Runs the same pattern checks as the sync-time guard in
  `scripts/sync-to-github.sh` on every PR + push, so authors fix leaks
  before they merge rather than after the sync freezes the mirror.
- `.github/workflows/phantom-poindexter-set.yml` — rejects any file that
  uses the bare top-level `set` subcommand form (which does not exist) instead
  of the correct `poindexter settings set <key>`. No path filter (the bad
  string can appear in any file). Non-required; fast (<2 s).
- `.github/workflows/sync-claude-md.yml` — daily (06:17 UTC) sync of
  repo-derivable stats in `CLAUDE.md` (file counts, dashboard count,
  latest migration name). Opens a PR on `chore/sync-claude-md` when the
  file drifts. DB-derived counts (posts, embeddings) require a prod-DB
  probe and are NOT updated by this workflow.
- `.github/workflows/triage-on-open.yml` — stamps the `type:` label
  implied by a new issue's conventional-commit title prefix (feat / fix /
  chore / docs / refactor). Zero-LLM; runs in both repos via the sync filter.
- `.github/workflows/release-mirror-to-public.yml` — fires on every
  published GitHub Release on `glad-labs-stack` and creates a matching
  tag + release on `Glad-Labs/poindexter` so the public Releases page
  stays in sync. Without this, the public mirror's releases froze at
  v0.1.1 while the source ran ahead.
- `.github/workflows/release-poindexter-to-pypi.yml` — publishes the
  `poindexter` CLI package to PyPI on `poindexter-v*.*.*` tag pushes.
  Uses PyPI Trusted Publishing (OIDC) — no API token stored in Secrets.
  Manual dispatch targets TestPyPI.
- `.github/workflows/runner-healthcheck.yml` — hosted-only control loop
  (must run in GitHub's cloud, not on Matt's PC). Every 6 hours it probes
  the self-hosted runners and sets or clears the `CI_RUNNER` repo variable.
  When `>=1` self-hosted runner is online, `unit-tests` runs there ($0
  minutes). When none are online, it clears `CI_RUNNER` so `unit-tests`
  falls back to `ubuntu-latest` and a PR's required check can still pass.
  Override via `CI_RUNNER_MODE` repo var (`auto` / `on` / `off`).
- `src/cofounder_agent/tests/` — Python unit tests (pytest). The
  `test-backend` check runs the full backend suite (several thousand
  cases; the exact count drifts as agents add tests, so it is not
  pinned here).
- `web/public-site/next.config.js` — has a `validateEnv` check that
  rejects localhost URLs in production. `SKIP_ENV_VALIDATION=true`
  bypasses for local dev.

## CI minutes / cost discipline

Actions minutes are billable on this private repo, and a high PR +
push-to-main volume (nightly scheduled agents, release commits, docs
bots, dependabot) multiplies fast. The rules that keep the bill down:

- **`test-backend`, `migrations-smoke`, and `mcp-server-tests` are the
  branch-protection required checks.** Required checks can't be
  `paths:`-filtered — a skipped required check never reports, so it would
  block the PR forever; they keep firing and gate their _expensive steps_
  instead (see the `detect-changes` step in `unit-tests.yml` /
  `mcp-server-tests.yml`). Every other workflow is non-required and is
  `paths:`-filtered freely.
- **`playwright-e2e` is `paths:`-gated** to `web/public-site/**` +
  the playwright config + root `package*.json`. A backend/docs/infra
  change skips the Chromium build entirely (those specs only exercise
  the static Next.js site, so they can't regress on a backend change).
- **`security.yml` classifies changed paths first** (the `changes`
  job), then runs only the relevant file-specific jobs (`trivy-config`
  / `action-pins` / `shell-line-endings` / `poetry-lock`). `gitleaks`
  / `trivy-fs` / `sbom` always run — a secret or CVE can land in any
  file. The weekly baseline + manual `workflow_dispatch` scans run
  every job regardless.
- **`grafana-panels-lint` is `paths:`-gated** to
  `infrastructure/grafana/**` + the lint script + migrations — the
  model the others copy.
- **Release Please batches daily** rather than per-merge (see Key
  files above).
- **Deferred:** a GitHub **merge queue** (would run the heavy suite
  once at merge instead of PR-then-post-merge-on-main) is intentionally
  NOT adopted yet — a merge queue amplifies flaky failures (an evicted
  entry rebuilds everything behind it), so it waits until the unit
  suite is reliably green. **CodeQL** is moving to advanced setup
  (PR + weekly schedule, `paths-ignore` for docs/infra) to drop its
  per-push-to-main scan — tracked as the fast-follow to this sweep.

### Coverage (#995)

Coverage reuses the **existing** `test-backend` matrix in
`unit-tests.yml` — we do **not** add a second test job or a parallel
coverage workflow (that would duplicate the per-dir/`--forked` split and
drift as test dirs are added). But it is **gated to the nightly schedule
(`cron: 0 9 * * *`) + manual `workflow_dispatch` only** — NOT every PR.
A job-level `COV` env var holds `--cov=cofounder_agent --cov-append
--cov-report=` on those events and is **empty on push/PR**, so every
pytest step appends `$COV`: on a PR that expands to nothing (lean ~8m
run), on the nightly run it turns on coverage. The `Initialize coverage
data` / `Coverage report` / `Upload coverage.xml artifact` steps are
likewise gated to schedule/dispatch. **Why gated, not per-PR:**
coverage instrumentation across the `--forked` split roughly _doubled_
`test-backend` (8m → 17m). Your nightly agents open several backend PRs
a day, so paying that on every PR would erode the CI-minutes win — a
once-a-day trend line gives the signal without the per-PR tax.

**Coverage is ADVISORY right now — it never fails the build.** There is
deliberately **no `--cov-fail-under`** yet:

- `test-backend` is a **required** branch-protection check. A blind
  `--cov-fail-under` would block every PR before we even know the
  current percentage.
- The plan is a **ratchet, not a target**: read the baseline % from the
  first few CI runs (the `Coverage report` step log / the `coverage-xml`
  artifact), then set `--cov-fail-under=<baseline>` and bump it upward
  over time as coverage improves. The number only ever goes up — a PR
  that drops below the current floor fails; one that holds or improves
  passes. This avoids gating on the long tail while still catching
  regressions once a floor is set.

Until the floor is set, the signal is the printed total % and the
uploaded `coverage.xml` — "N tests pass" plus "X% of `cofounder_agent`
is exercised", instead of just the test count.

## The public release repo is separate

`github.com/Glad-Labs/poindexter` is the open-source release repo.
It gets a filtered snapshot via the auto-sync workflow above. It
does NOT auto-deploy anywhere. Vercel watches the private origin
(`Glad-Labs/glad-labs-stack`), not the public mirror.

The public mirror has `allow_force_pushes: true` in its branch
protection — the mirror is rebuilt from scratch on every sync, so
force-push protection on a derived branch would just keep the mirror
permanently stale. Public-side CI (test-backend, migrations-smoke,
mcp-server-tests, Mintlify Deployment, link-rot) still has to pass on
the resulting commit.

## Deploying the local worker (bringing prod up to `main`)

The worker / brain / pipeline-bot / prefect-worker containers **bind-mount
the deploy clone** (`POINDEXTER_DEPLOY_ROOT`, defaulting to
`~/.poindexter/deploy/glad-labs-stack`) — **not** this dev checkout. The deploy
clone is what the running pipeline actually executes. A merge to `main` does
**not** reach the worker until the deploy clone is synced and the containers
restart. Leaving the deploy clone behind is how production silently drifts
behind `main`.

The canonical one-command deploy:

```powershell
pwsh ./scripts/deploy-worker.ps1
```

It refuses on a dirty tree, tag-backs-up any unpushed commits on the current
branch, checks out `main` in the dev checkout, fast-forwards to `origin/main`,
**syncs the deploy clone** (`deploy-checkout-sync.ps1`) so the containers get
the new code, verifies both checkouts are at `origin/main`, then restarts the
pipeline containers and waits for the worker healthcheck and
`poindexter_worker_up=1`. There is **no image rebuild** — app code is
bind-mounted from the deploy clone, so a sync + restart is the deploy
(dependency / base-image changes still need `docker compose build`).

> **Split-brain fix (glad-labs-stack#1295).** Before this fix, `deploy-worker.ps1`
> only fast-forwarded the dev checkout and left the deploy clone lagging up to
> 10 minutes behind `origin/main`. The script now explicitly syncs the deploy
> clone before restarting containers, and verifies the deploy clone HEAD matches
> `origin/main` before proceeding.

**Routine Python merges now auto-deploy.** The 10-min `deploy-checkout-sync.ps1`
scheduled task (above) bounces `poindexter-worker` + `poindexter-pipeline-bot`
whenever it advances the deploy clone, so a merged code change reaches the
running worker within ~10 min on its own. `deploy-worker.ps1` remains the tool
for an _immediate_ deploy (skip the wait) and is still required for dependency /
base-image changes (which need `docker compose build`) and for
`poindexter-prefect-worker` / `poindexter-brain-daemon` bootstrap-level changes.

**Confirming the sync task actually ran.** The task runs hidden/non-interactive
and the Windows TaskScheduler/Operational history log is disabled by default, so
a green `0x0` "Last Run Result" is **not** proof it synced — it can skip every
cycle (e.g. a stuck Prefect flow tripping the gap guard) and still report
success. Each run therefore persists its own proof-of-work:

- `~/.poindexter/deploy-checkout-sync.log` — timestamped narration of every
  `git fetch`/`reset`/`clean` and `docker restart`, rotated to `.log.1` past
  `POINDEXTER_DEPLOY_LOG_MAX_BYTES` (default 5 MB).
- `~/.poindexter/deploy-checkout-sync.status.json` — one machine-readable object
  (`result`, `head`, `previousHead`, `restarted[]`, `timestamp`) for a Grafana
  textfile collector / phone check. `result` ∈ `deployed` | `synced-no-change` |
  `synced-norestart` | `baseline-recorded` | `flow-gap-skip` | `error`.

```powershell
pwsh ./scripts/deploy-checkout-sync.ps1 -Status    # task state + clone HEAD + last status + log tail
pwsh ./scripts/deploy-checkout-sync.ps1 -SelfTest  # exercise the logging/rotation/status plumbing (no git/docker)
```

Do **not** trust merged == deployed without one of these: compare
`git -C ~/.poindexter/deploy/glad-labs-stack rev-parse HEAD` against `origin/main`,
or read the status file.

**Deploy-drift canary (glad-labs-stack#942).** Because the worker / brain
bind-mount the deploy clone, "merged on main" does not mean "running in prod"
until you run the deploy above. The brain's `branch_drift_probe` closes that
loop: every ~15 min it reads the deploy clone's HEAD from a read-only `.git`
mount (`${POINDEXTER_DEPLOY_ROOT:-.}/.git:/host-git:ro` on the brain-daemon
container — **pointing at the deploy clone, not the dev checkout**, per
glad-labs-stack#1295), compares it to `origin/main` via the GitHub API
(`gh_token`), and pages the operator (Telegram / Discord) when prod is behind.
It is **alert-only**; the remedy it points at is
`pwsh ./scripts/deploy-worker.ps1`. Tunables (in `app_settings`):
`branch_drift_probe_enabled`, `branch_drift_poll_interval_minutes`,
`branch_drift_repo`, `branch_drift_dedup_hours`, `branch_drift_git_dir`.
Deploying the canary itself requires a brain image rebuild
(`docker compose build brain-daemon && up -d brain-daemon`), since the
`.git` mount + the `git` binary are new.

## Fast rollback (pin deploy clone to a known-good SHA)

The durable rollback path is `git revert` + CI + full sync — ~30+ minutes.
For a production incident where you need the worker back on known-good code
immediately, use the SHA-pin path instead:

```powershell
# 1. Find the last known-good SHA
git log --oneline origin/main | head -10
# Pick the SHA immediately before the bad commit.

# 2. Pin the deploy clone to that SHA
git -C ~/.poindexter/deploy/glad-labs-stack reset --hard <known-good-sha>

# 3. Restart the affected containers (skips the automated sync's flow-run guard)
docker restart poindexter-worker poindexter-pipeline-bot

# 4. Verify the worker is healthy
curl -s http://localhost:8002/api/health | python -m json.tool

# 5. Check the worker is on the pinned SHA
git -C ~/.poindexter/deploy/glad-labs-stack rev-parse --short HEAD
```

**What this does:** the containers bind-mount the deploy clone, so resetting
the clone and restarting the containers immediately loads the old code without
any CI run. The 10-minute `deploy-checkout-sync.ps1` task will try to advance
the clone again on its next cycle — stop the scheduled task while the incident
is live:

```powershell
# Suspend automated sync while you're pinned
Disable-ScheduledTask -TaskName 'Poindexter-DeployCheckoutSync'

# Re-enable once the revert commit has merged and you're ready to roll forward
Enable-ScheduledTask -TaskName 'Poindexter-DeployCheckoutSync'
```

**Follow-up:** file a `git revert` PR as the durable fix. Re-enable the
scheduled task only after the revert has merged and CI is green — otherwise
the sync will overwrite your pin on the next cycle.

> **poindexter-prefect-worker** is not pinned by `docker restart`. Each Prefect
> flow spawns a fresh subprocess that re-imports `/app`; to pin the Prefect
> worker to old code you would also need to reset before the next flow run
> fires. In practice: drain in-flight flows (`poindexter tasks list --status
in_progress`) and reset the deploy clone before the scheduler claims the next
> pending task.

## If you're self-hosting Poindexter

You don't need any of this. Your deployment is:

```bash
poindexter setup --auto
bash scripts/start-stack.sh
```

CI is useful if you fork and want PR checks, but the stock setup
has no notion of "deploy." The worker container is your production.
