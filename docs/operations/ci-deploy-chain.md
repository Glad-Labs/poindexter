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
Glad-Labs/poindexter (private GitHub, source of truth)
    │
    ├─→ GitHub Actions (several workflows — there is no single ci.yml)
    │       required checks: unit-tests.yml (job test-backend,
    │       backend pytest) + migrations-smoke.yml + mcp-server-tests.yml
    │       (job mcp-server-tests, the mcp-server uv suite), on every PR +
    │       push to main (expensive steps short-circuit on docs-only or
    │       unrelated changes — see "CI minutes / cost discipline" below)
    │       non-required, paths-gated: playwright-e2e.yml (frontend
    │       E2E), security.yml, grafana-panels-lint.yml,
    │       rerank-import-guard.yml (real sentence-transformers import
    │       on pyproject/poetry.lock changes)
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

Vercel watches `Glad-Labs/poindexter` (the private origin),
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
- `.github/workflows/rerank-import-guard.yml` — non-required, paths-gated
  to `src/cofounder_agent/{pyproject.toml,poetry.lock}`. Installs
  `--extras rerank` and imports the real cross-encoder stack
  (`from sentence_transformers import CrossEncoder`, what `rag_engine.py`
  uses) so a version-skew re-lock that would silently degrade the reranker
  to passthrough reddens the PR instead. Shifts the worker image's
  build-time assertion (`src/cofounder_agent/Dockerfile:73`) left to PR
  time — the `dependency-review` auto-merge path never builds the image.
- `.github/workflows/sync-to-public-poindexter.yml` — auto-mirror
  from glad-labs-stack to poindexter on every push to main.
- `scripts/sync-to-github.sh` — filter that runs inside the sync
  workflow. Strips operator-only files before pushing the public
  subset.
- `.github/workflows/release-please.yml` — Release Please on
  `Glad-Labs/poindexter` (the source repo — NOT the public
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
  - **`.gitleaks.toml` carries one repo-local rule on top of the
    bundled set** (`[extend] useDefault = true` keeps the defaults):
    `github-app-token-stateless`. gitleaks' own `github-app-token` rule
    is `(ghu|ghs)_[0-9a-zA-Z]{36}`, which only matches the CLASSIC
    opaque installation token. GitHub began rolling installation tokens
    over to a stateless `ghs_<APPID>_<JWT>` shape on 2026-04-27 (~520
    chars, two dots, charset `[A-Za-z0-9._-]`) — the `_` and `.` break
    that 36-char alphanumeric run, so a leaked modern token scanned
    **clean** on both this gate and the `gitleaks protect --staged`
    pre-commit hook, which share these rules. The rule is **additive**
    on purpose: the bundled rule stays enabled so classic-token
    coverage stays owned upstream, and the repo-local regex requires
    the two-dot JWT tail so it does not double-report classic tokens.
    Pinned by `tests/unit/scripts/test_gitleaks_app_token_rule.py`,
    which reads the shipped config and exercises the regex without
    needing the gitleaks binary. The same `ghs_` gap was closed in the
    five Python scrubbers (`logger_config`, `rag_scrub`,
    `taps/claude_code_sessions`, `scripts/regen-app-settings-doc.py`,
    `scripts/ops_sessions/pro_freshness.py`) — in the two that also
    carry a JWT pattern the `ghs_` entry must stay **above** it, or a
    stateless token is only half-scrubbed and keeps a live
    `ghs_<APPID>_` prefix.
  - **The `gitleaks` job carries a positive control**
    (`scripts/ci/gitleaks_canary.py`, run as a step so it inherits that
    job's required-check gating). The scan proves nothing was _found_;
    the canary proves the scanner can still _find_. It writes one pinned
    credential per shape we care about to a temp dir, scans it with the
    repo's own `.gitleaks.toml`, and fails when any expected rule stops
    firing — plus negative prose samples that must stay clean, so an
    over-broad new rule is caught before it buries real findings. This
    is the control that would have caught the stateless-token gap four
    months earlier. Two design points, both learned the hard way:
    the corpus is **pinned, never generated** (detection is
    byte-sensitive — `aws-access-token` accepts one 20-char value and
    rejects a near neighbour one character apart, so a reseed silently
    flips cases and the canary then fails for reasons unrelated to the
    rules); and every `expected_rule` was determined **empirically**
    against the pinned binary, since several documented guesses were
    wrong. On a gitleaks upgrade, re-verify the _sample_ before editing
    a rule. The script declares `# scan-floor-exempt:` because it builds
    its own corpus rather than walking the repo tree.
- **What is actually REQUIRED on `main`** (15 checks as of
  2026-08-28; classic branch protection, `strict: false`):
  `migrations-smoke`, `test-backend`, `mcp-server-tests`,
  `backend-lint`, `syntax-check`, `gitleaks — secret scan`,
  `public-mirror-safety`, `semgrep`, `docs-link-rot`,
  `phantom-poindexter-set`, `Trivy — filesystem vuln scan`,
  `Trivy — Dockerfile + IaC config`, `Lint third-party Actions for SHA
pins`, `Lint shell + PowerShell scripts`, `poetry check --lock
(src/cofounder_agent)`. The last six were promoted from advisory on
  2026-08-28 — they already ran on every PR, so gating them cost zero
  extra CI minutes and only changed whether a red result blocks.

  **What can be promoted is decided by one mechanical rule.** A
  workflow-level `paths:` filter means the workflow does not run at all
  on an unrelated PR, so its check is _never created_ and a required
  check sits pending forever — that is the required-check hang. A
  job-level `if:` (the `needs: changes` pattern in `security.yml`)
  always reports, as `skipped`, and GitHub counts a skipped required
  check as satisfied. So `if:`-gated jobs are safe to require;
  `paths:`-gated workflows are not, until they are converted to the
  always-run + job-level `if:` shape. `integration-db`, `ports-lint`,
  `rerank-import-guard` and `grafana-panels-lint` are the reasonable
  Tier-2 candidates for that conversion; `docker-build` and
  `playwright-e2e` are deliberately left advisory (too expensive per PR
  for the signal).

  **Gating is not a rot cure-all** — it fixes "red and ignored" and
  "PR job wedged", and does nothing for the other shapes. A scheduled
  job has no PR to block (those need a dead-man's switch — the
  benchmarks→Grafana ingest is the pattern). A check that is green
  because it scanned nothing needs a scan floor. And a check that is
  green because its _rule_ went blind to a changed credential format
  needs a positive control — see the `gitleaks` canary above, and
  `reference_nongating_ci_jobs_rot_invisibly` for the full taxonomy.

- **Scheduled workflows have a dead-man's switch**
  (`brain/scheduled_workflow_watch.py`, 2026-08-28). Nothing gates a
  cron: when a scheduled workflow starts failing — or stops firing —
  no check anywhere changes colour. The 2026-08-25 sweep found
  `benchmarks` had never once passed in 71 runs and the weekly
  `playwright-e2e` never in 11. The probe emits an edge-triggered
  `scheduled_workflow_stale` finding (warn → Discord via the findings
  router) in two distinct modes, because they diagnose differently:
  **`stale`** (last successful _scheduled_ run older than its window)
  and **`never_green`** (scheduled runs exist, none has ever passed).

  **Runs are filtered to `event=schedule`, and that filter is the
  whole point.** `security`, `unit-tests`, `release-please` and
  `console-contract-drift` also run on pushes and PRs — query their
  last successful run unfiltered and you get today's push, so a cron
  dead for three weeks reports perfectly healthy. Without the filter
  the watchdog would itself be a "green while checking nothing" check.

  Config is `app_settings.scheduled_workflows`, and it ships **empty**:
  a useful default would have to name this operator's repos, and a
  `Glad-Labs/poindexter` literal in `settings_defaults.py` would
  reach the public mirror and trip the private-repo leak guard. Set
  `max_age_hours` to roughly 1.5x the cron period — GitHub's scheduler
  is best-effort and routinely runs late, so a window equal to the
  period produces false alarms. The operator list for this install:

  | workflow                     | cron          | `max_age_hours` |
  | ---------------------------- | ------------- | --------------- |
  | `benchmarks.yml`             | `0 7 * * *`   | 30              |
  | `console-contract-drift.yml` | `0 8 * * *`   | 30              |
  | `regen-app-settings-doc.yml` | `13 6 * * *`  | 30              |
  | `sync-claude-md.yml`         | `17 6 * * *`  | 30              |
  | `release-please.yml`         | `0 8 * * *`   | 30              |
  | `unit-tests.yml`             | `0 9 * * *`   | 30              |
  | `runner-healthcheck.yml`     | `0 */6 * * *` | 12              |
  | `playwright-e2e.yml`         | `0 6 * * 1`   | 192             |
  | `security.yml`               | `17 6 * * 1`  | 192             |

  Not assessed (no finding) when httpx is missing, no `gh_token` is
  configured, the workflow 404s, the API errors, or the workflow has no
  scheduled runs at all — mirroring `data_freshness_probe`'s zero-rows
  rule, so an operator who never enabled a cron is never alarmed about
  it. Self-throttled to
  `scheduled_workflow_watch_interval_minutes` (default 60) rather than
  riding the brain's 5-minute cycle, since each target costs two
  GitHub API calls.

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
(`Glad-Labs/poindexter`), not the public mirror.

The public mirror has `allow_force_pushes: true` in its branch
protection — the mirror is rebuilt from scratch on every sync, so
force-push protection on a derived branch would just keep the mirror
permanently stale. Public-side CI (test-backend, migrations-smoke,
mcp-server-tests, Mintlify Deployment, link-rot) still has to pass on
the resulting commit.

### When the mirror sync fails, it files ONE issue and closes it itself

The sync workflow opens a GitHub issue on `glad-labs-stack` when it goes
red, so a frozen mirror shows up in notifications instead of sitting
unnoticed on a derived branch nobody watches.

The title is **stable** (`⚠️ poindexter mirror sync FAILED`, no run id)
and that is load-bearing. The sync fires on every push to `main` and
stays broken until the cause is fixed, so a per-run title filed one
issue per push — 11 issues for one expired PAT (2026-06-13), 7 for one
sample webhook URL (2026-08-28), 20 issues across 3 real incidents, all
closed by hand. `scripts/ci/notify_operator_issue.py` now comments on
the open issue instead, and a later green sync closes it. One incident,
one issue, no manual cleanup. The same helper backs the `lint-main` and
semgrep ratchet guards.

**Three causes, and the run log tells them apart immediately:**

| Symptom in the log                                                  | Cause                                                                                        | Fix                                                                                     |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `[sync] Leak guard FAILED`                                          | Our `check_public_mirror_safety.py` found an operator-private pattern in a public-bound file | Rephrase the value or add the file to `_STRIP_FILES`                                    |
| `GH013: Repository rule violations` / `Push cannot contain secrets` | **GitHub's** push protection on the receiving repo                                           | Rephrase the credential-shaped string, or strip the file in `scripts/sync-to-github.sh` |
| `403` / `not authorized` on the push                                | The `glad-labs-mirror-sync` App lost access                                                  | Re-check the install's Contents + Workflows write                                       |

The second one is the trap: it is **not** the leak guard, so our guard
passing tells you nothing about it. On 2026-08-28 the log read
`[sync] Leak guard passed.` immediately before GitHub rejected the push
over a sample Slack webhook URL in a vendored semgrep rule file, and
the issue text sent the reader to re-run the guard that had already
passed. Do not click GitHub's "allow this secret" unblock URL to get
past it — that ships the value.

## Deploying the local worker (bringing prod up to `main`)

The worker / brain / pipeline-bot / prefect-worker containers **bind-mount
the deploy clone** (`POINDEXTER_DEPLOY_ROOT`, defaulting to
`~/.poindexter/deploy/glad-labs-stack`) — **not** this dev checkout. The deploy
clone is what the running pipeline actually executes. A merge to `main` does
**not** reach the worker until the deploy clone is synced and the containers
restart. Leaving the deploy clone behind is how production silently drifts
behind `main`.

> **Every repo-shipped bind mount must be anchored to
> `${POINDEXTER_DEPLOY_ROOT:-.}`.** A bare `./foo` resolves to the compose
> _project directory_ — this dev checkout — so the container runs whatever is
> in your working tree rather than what was deployed, with no error and no
> gap in any metric. On 2026-07-26 that silently kept a merged GPU-exporter fix
> from ever running (`gpu-exporter` mounted `./scripts/nvidia-smi-exporter.py`
> while its own sibling mount was anchored) and left merged Grafana dashboard
> JSON dark until this checkout happened to be pulled
> (Glad-Labs/poindexter#922, #923). `:-.` makes the anchor a no-op wherever the
> variable is unset, so there is no cost to it.
>
> Runtime-**written** paths are the deliberate exception and stay bare:
> `infrastructure/prometheus/secrets` (the brain daemon writes it; all three
> consumers must agree on one root) and `infrastructure/grafana/provisioning`
> (mounted rw, its `alerting/` subtree written by Grafana and the worker).
> `scripts/ci/compose_mount_deploy_root_lint.py` enforces the split in CI;
> add a justified entry to its `RUNTIME_WRITTEN_EXEMPT` if a new path
> genuinely belongs on the written side.

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
running worker within ~10 min on its own. `poindexter-brain-daemon` is
image-baked rather than bind-mounted (poindexter#456), so a restart can't reload
it — instead the same task **rebuilds the brain image** whenever the synced diff
touches `brain/` (`start-stack.sh build brain-daemon`), and the compose-apply
step recreates the container onto the fresh image, so brain code edits
auto-deploy too. `deploy-worker.ps1` remains the tool for an _immediate_ deploy
(skip the wait) and is still required for dependency / base-image changes
elsewhere (which need `docker compose build`) and for `poindexter-prefect-worker`
bootstrap-level changes.

**Overlapping restarts coalesce instead of stacking.** The sync skips its
bounce for any container whose current process already started _after_ the
pass's `git reset` (bind-mounted code ⇒ it is already running the new tree), so
a manual post-merge `docker restart` and the next scheduled cycle no longer
double-bounce the worker. The worker service also sets `stop_grace_period: 75s`:
uvicorn only honors a SIGTERM received mid-startup once lifespan startup
(~40-55 s) completes, and Docker's default 10 s stop window used to SIGKILL the
half-started process whenever restarts collided (observed 5x during the
2026-07-07 overnight merge train).

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

## The ops-session wrapper is a third deploy surface

"Deployed" means four surfaces across three trees on this host, each with its
own sync mechanism:

| Surface                                           | Tree                                                                          | Synced by                                                                                                       |
| ------------------------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Public site                                       | Vercel build of `glad-labs-stack`                                             | Vercel, on push to `main`                                                                                       |
| Worker / brain / pipeline containers              | `~/.poindexter/deploy/glad-labs-stack`                                        | `deploy-checkout-sync.sh` (10-min timer; `reset --hard` + `clean -fd`)                                          |
| claude.ai phone connector (`poindexter-mcp-http`) | `~/.poindexter/deploy/glad-labs-stack` (`mcp-server/` + its in-clone `.venv`) | same `deploy-checkout-sync.sh` pass (2026-08-16): unit restart on `mcp-server/**`, `uv sync` on lockfile change |
| Ops-session wrapper + shared payload              | `~/glad-labs-website` (the working checkout)                                  | `run-session.sh`'s own ff-only pre-flight (2026-08-15)                                                          |

The third row was the gap: the systemd session units exec `run-session.sh`
out of the **working checkout**, and until 2026-08-15 nothing auto-updated it —
PR #3228's fetch retry merged but the deployed wrapper kept running the old
code, 3 commits behind, until a human fast-forwarded it. The worktree-session
_payloads_ were immune (fresh worktree off `origin/main` each run), but the
wrapper itself and the non-worktree sessions' `scripts/ops_sessions/*.py` were
not.

The fix is deliberately **not** pointing the sessions at the deploy clone, for
two reasons: the poetry venv is keyed to the working checkout's package path
(the deploy clone resolves to no env at all), and the deploy clone's design
contract is "nothing else ever edits it" — sessions create worktrees and hold
CWDs, and its 10-min `reset --hard` would race them. Instead the wrapper
self-updates with `git merge --ff-only origin/main`, guarded to skip when the
checkout is dirty (tracked files), off `main`, or diverged. **Never point
`deploy-checkout-sync.sh` at a working checkout** — its reset+clean is only
safe on the dedicated clone; the working checkout holds stashes and agent
worktrees, and ff-only is the strongest sync it may ever receive. Unit-template
(`poindexter-session@.service`) changes are the residual manual step: re-run
`sudo bash scripts/linux/install-session-timers.sh`, which renders and
installs the unit + timers. Details in
[scheduled-agents.md](scheduled-agents.md).

**The phone connector was a fourth tree until 2026-08-16.**
`poindexter-mcp-http.service` (the claude.ai connector, :8004) used to exec
`mcp-server/http_server.py` from the operator checkout — outside every sync
mechanism above — so a merged `mcp-server/**` change silently never reached
the phone surface (PR #3247 needed a manual FF + `systemctl restart` by
hand). Unlike the ops sessions, the connector had no reason to stay on the
working checkout: its uv venv lives at `mcp-server/.venv` relative to
wherever it runs (nothing is keyed to the checkout path), and the server
never writes to its tree, so the deploy clone's `reset --hard` cannot race
it. The fix therefore moved it INTO the clone rather than pointing any sync
at the working checkout:

- The unit template (`infrastructure/systemd/poindexter-mcp-http.service`)
  now points `WorkingDirectory`/`ExecStart` at
  `~/.poindexter/deploy/glad-labs-stack/mcp-server`. The venv lives inside
  the clone — `.venv/` is gitignored, so the sync's `reset --hard` +
  `clean -fd` spare it — and `setup-deploy-checkout.sh` seeds it (best-effort
  when `uv` is on PATH).
- `deploy-checkout-sync.sh` grew a connector step: any `mcp-server/**` diff
  restarts the unit; a `pyproject.toml`/`uv.lock` diff — or a missing venv —
  runs `uv sync` first, because `ExecStart` uses `.venv/bin/python` directly
  and the venv never self-updates. Unit management is plain `systemctl` as
  root, else `sudo -n systemctl` (the sync's user needs passwordless sudo —
  same posture as docker-watchdog's `systemctl restart docker`). Hosts
  without the unit installed skip the step entirely; a failed connector step
  withholds the deploy marker like every other step, so the pass retries
  next cycle. Env seams: `SYNC_MCP_UNIT` (unit name), `SYNC_UV_BIN` (uv
  path — systemd's PATH lacks `~/.local/bin`, so the script probes the
  standard install dirs).

Unit-template changes for the connector remain manual, same as the session
units: copy the rendered template to `/etc/systemd/system`, then
`sudo systemctl daemon-reload && sudo systemctl restart poindexter-mcp-http`.

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
any CI run. The claude.ai connector runs from the same clone — if the bad
change touched `mcp-server/`, also
`sudo systemctl restart poindexter-mcp-http` after pinning. The 10-minute `deploy-checkout-sync.ps1` task will try to advance
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
