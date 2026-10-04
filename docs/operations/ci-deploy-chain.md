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
2. **A `CANCELED` deploy is a skip, not a failure.** The
   `ignoreCommand` in `web/public-site/vercel.json` (and its twin in
   `web/storefront/vercel.json`) diffs HEAD against
   `VERCEL_GIT_PREVIOUS_SHA`, the commit of the **last successful
   deployment**, over `:/web/public-site …`, and skips the build when
   nothing the site is built from changed since then; Vercel lists that
   as `CANCELED`. It used to diff `HEAD^ HEAD`, which lost a site change
   for good whenever its own build didn't run and a docs-only commit
   landed on top. It builds, never errors, when there is no previous
   deploy or the shallow clone can't reach it, and it always builds a
   redeploy of the live commit. The command needs `.git`, so
   `web/public-site/.vercelignore` must never list it: while it did,
   every production deploy ERRORed (glad-labs-stack#2338). Tests:
   `web/public-site/__tests__/vercel-ignore-command.test.js`.

   **To ship an environment-variable change, redeploy the deployment
   marked _Current_**, not the newest one in the list. Vercel gives the
   command no way to tell a dashboard redeploy from a push, so a
   redeploy of a newer docs-only commit is skipped like the push was.

3. **The build needs no backend URL.** The site reads the R2 static
   export and never calls the worker, which has no public ingress.
   `next.config.js` used to require `NEXT_PUBLIC_API_BASE_URL` for
   production builds (`SKIP_ENV_VALIDATION` bypassed its localhost
   check). That check is gone, and neither variable does anything if a
   Vercel env still sets it.
4. **If tests fail locally:** reproduce with
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

## Which test trees CI runs

`test-backend` runs pytest once per directory, from two trees.
`scripts/ci/unit_test_dirs_lint.py` (a step of `test-backend`, and of
`lint-main` on pushes to main) fails when a `test_*.py` in either is named by
no pytest step that can fail the job:

| Tree                                                                           | Its steps run from    | Pytest config                        | `$COV` |
| ------------------------------------------------------------------------------ | --------------------- | ------------------------------------ | ------ |
| `src/cofounder_agent/tests/unit/`                                              | `src/cofounder_agent` | `src/cofounder_agent/pyproject.toml` | yes    |
| `tests/` at the repo root (today `tests/scripts/`, the image bake-off harness) | the repo root         | the root `pyproject.toml`            | no     |

Run the root tree by hand with `python -m pytest tests/ -q` from the repo
root, in any env that has pytest and Pillow (the backend poetry env does).

Why the second tree is listed at all: nothing ran it from 2026-07-13, when it
was added, until 2026-09-28, and three of its eleven tests were red from
2026-07-17. `BakeoffModel` had gained a required `revision` field, and
fixtures that built it positionally shifted every later argument by one. The
lint read only the `src/cofounder_agent` tree, and running it by hand failed
first: the root pytest config passed `--load-dotenv`, which no installed
plugin provides, so pytest exited 4 (`unrecognized arguments`) before it
collected a test. Three things now keep it honest:

- **The lint holds both trees to one rule**, and a tree named in its `SUITES`
  must exist and hold a test file (a lint that read nothing has not passed).
  Adding a third tree means adding it to `SUITES` and giving it a step; a tree
  the lint does not name is invisible to it. When a directory has no step, the
  lint prints the step to paste.
- **The root step carries no `$COV`.** `$COV` is empty on PRs and set on the
  nightly, whose `--cov-fail-under=1` measures the `src/cofounder_agent`
  packages. Nothing under `tests/` imports them, so the run reads 0% and fails
  with every test green. A `$COV` there would pass every PR and fail the
  nightly, so `test_unit_test_dirs_lint.py` pins its absence.
- **`^tests/` is in the `detect-changes` pattern.** A path under the root tree
  starts with `tests/`, so neither `^src/cofounder_agent/` nor `^scripts/`
  matched it, and a PR that edited only a test there skipped every pytest
  step. `test_ci_runs_when_its_inputs_change.py` derives the trees from the
  pytest steps and fails when a test file in one of them would not trigger.

Still outside this lint: `scripts/test_*.py`, `mcp-server-voice/tests/` and
the pytest suites CI runs through other workflows (`mcp-server-tests.yml`,
`integration-db.yml`, `benchmarks.yml`).

## Key files

- `.github/workflows/unit-tests.yml` — backend pytest, exposed as the
  `test-backend` status check. One of the **three** branch-protection
  required checks; a `detect-changes` step short-circuits the
  expensive pytest steps on docs-only changes while still reporting
  green (a required check must always report — see "CI minutes / cost
  discipline" below). No deploy step. Which test trees it runs, and the
  lint that keeps that list complete: "Which test trees CI runs" above.
- `.github/workflows/migrations-smoke.yml` — applies every migration
  against a clean Postgres + pgvector. Another branch-protection
  required check; fires on every PR + push to main. Because it has no
  changed-paths gate it also carries the static, no-DB lints that must
  see every PR, including `module_launch_paths_lint.py`. That lint
  resolves every `python -m` launch string that names a backend module
  (compose `command:`, Dockerfile `CMD`, shell launchers, units,
  runbooks, docstrings) against the tree, and refuses the flat roots
  poindexter#1046 retired. An interpreter resolves `-m` only at launch,
  so a stale one fails at container start and nowhere earlier: four
  voice launch strings kept the flat spelling behind the parked `voice`
  profile until 2026-09-28.
- `.github/workflows/mcp-server-tests.yml` — runs the `mcp-server/`
  pytest suite (its own `uv` venv) as the `mcp-server-tests` status
  check, the **third** branch-protection required check. mcp-server
  imports across the repo boundary (`services.*`, `modules.content.api`,
  and `brain.*` via `sys.path`), but no workflow ran its suite — so a
  shared-code refactor could merge a red mcp-server lane (PR #1663 did
  exactly that; the breakage sat latent on main until #1742). A
  `detect-changes` step gates the `uv` install + pytest on changes under
  `mcp-server/**`, `src/cofounder_agent/**`, or `poindexter/brain/**` while still
  reporting green on unrelated PRs. Runs on the public mirror too (the
  tested code ships there), where it is non-required.
- `.github/workflows/playwright-e2e.yml` — frontend E2E (Playwright),
  `paths:`-gated to `web/public-site/**`. Non-required. The frontend
  Jest unit run + JS lint are **hook-only**, not run in CI (see the
  workflow header).
- `.github/workflows/security.yml` / `grafana-panels-lint.yml` —
  non-required scans: gitleaks / trivy / sbom + path-specific lints,
  and the paths-gated Grafana panel lint, respectively.
- `.github/workflows/python-lint.yml` — three Python gates, no `paths:`
  filter. `backend-lint` (full ruff rule set over the backend) and
  `syntax-check` (ruff E9 over every `.py` in the repo) are required.
  `type-check` is the mypy ratchet described in the next section.
  It's non-required until it has a run history.
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
  `src/cofounder_agent/poindexter/services/**` + `modules/content/**`; regenerates
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
- `.github/workflows/release-poindexter-to-pypi.yml` — builds the one
  `poindexter` distribution (`src/cofounder_agent/pyproject.toml`) on every
  `v*.*.*` tag the mirror re-creates, installs the wheel into a clean venv and
  runs `poindexter --help`, then publishes to PyPI while the repo variable
  `POINDEXTER_PYPI_RELEASE` is `true` (set 2026-09-11; first real release 0.136.0).
  Uses PyPI Trusted Publishing (OIDC) — no API token stored in Secrets.
  A manual dispatch takes `target` (testpypi = dry run, pypi = publishes).
- `.github/workflows/runner-healthcheck.yml` — hosted-only control loop
  (must run in GitHub's cloud, not on Matt's PC). Every 6 hours it probes
  the self-hosted runners and sets or clears the `CI_RUNNER` repo variable.
  When `>=1` self-hosted runner is online, `unit-tests` runs there ($0
  minutes). When none are online, it clears `CI_RUNNER` so `unit-tests`
  falls back to `ubuntu-latest` and a PR's required check can still pass.
  Override via `CI_RUNNER_MODE` repo var (`auto` / `on` / `off`).
  Clearing the variable only reaches runs created afterwards, so when none
  are online it also force-cancels and re-runs runs already queued for the
  self-hosted labels (`scripts/ci/recover_stranded_runs.py`; see
  `self-hosted-ci-runner.md`, "Recovering runs already queued for dead
  runners").
- `src/cofounder_agent/tests/` — Python unit tests (pytest). The
  `test-backend` check runs the full backend suite (several thousand
  cases; the exact count drifts as agents add tests, so it is not
  pinned here).

## Type checking: the mypy ratchet

Until 2026-09-28 no workflow ran mypy. `npm run type:check` existed and
nothing enforced it, so type errors landed silently. The first full run
reported **74 errors in 33 files** (907 source files checked). That is
too many to fix before gating, and a zero-tolerance gate would never
have gone green. So mypy is gated the way bandit and semgrep are: the
existing errors are grandfathered, only a **net-new** error fails, and
nothing files an issue.

- **What runs.** The `type-check` job runs `scripts/ci/mypy_lint.py`
  against `scripts/ci/mypy_baseline.json`, keyed per file per mypy
  error code: `{"src/.../alert_sync.py": {"assignment": 6, ...}}`. There
  are no line numbers, so an edit above an old error doesn't churn the
  baseline. Keying per code means a new `[arg-type]` can't ride in
  behind a fixed `[assignment]` in the same file.
- **When it fails.** It prints each file and code over its baseline and
  lists every current error of that code in that file. At least one of
  them is new, but a count can't say which. Fix it. If it's a false
  positive, suppress it at its line with
  `# type: ignore[<code>]  # <why>`, scoped to the one code. A bare
  `# type: ignore` hides every code on the line, including the next real
  one.
- **When you fix errors.** The lint stays green and lists the entries
  now below baseline. Lock the win in with
  `python scripts/ci/mypy_lint.py --update-baseline`. That flag only
  lowers counts and drops entries. It refuses when the tree has an
  error the baseline doesn't allow.
- **When growth is legitimate**, for a file move (its errors reappear
  under a new key) or a mypy or typed-dependency bump that changes what
  mypy reports, use `--update-baseline --allow-growth`, and say which
  in the commit message. The baseline ships in the public mirror, so a
  file the mirror strips must never enter it. Fix or `type: ignore` an
  error there instead.
- **Run it in the backend's environment.** mypy runs as
  `sys.executable -m mypy`, so the interpreter you launch the lint with
  is the environment it checks. Locally that's
  `npm run type:check:ratchet`, or the backend venv's `python`. Measured
  on the same tree:

  | environment                                                         | result                                                                                                                                                                |
  | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | backend env (`poetry install --no-root --extras "pipeline qa rag"`) | 74 errors in 33 files. A developer venv and a CI-faithful venv agree line for line.                                                                                   |
  | mypy alone                                                          | 203 errors in 68 files. 130 are `Class cannot subclass "BaseModel" (has type "Any")`, because every missing library is `Any`, and 2 of the 74 real errors go missing. |

  So the job installs `poetry.lock` with the same extras as
  `unit-tests.yml` (a test derives them from there). It installs into its
  **own in-project venv** (`POETRY_VIRTUALENVS_CREATE` /
  `POETRY_VIRTUALENVS_IN_PROJECT`), not the runner's interpreter. A
  self-hosted runner keeps one interpreter across jobs, and another job
  adds torch and sentence-transformers to it, which would change what
  mypy sees from run to run. Cost: about a minute on the self-hosted
  runners. A cold mypy run is ~30 s, and on a hosted runner the install
  adds ~30 s more.

- **A run that didn't complete is a failure, never a pass.** That covers
  mypy exit 2 (a syntax error, a module found twice, a crash), a
  non-zero exit with nothing parseable, and an `error:` line without a
  location or an error code. It also covers a parsed error count that
  disagrees with mypy's own `Found N errors in M files` line, which is
  what turns a change in mypy's output format into a red job instead of
  a quiet undercount. The job also fails when mypy checked fewer than
  450 files (`lib_scan_floor`). `tests/unit/scripts/test_mypy_lint.py`
  runs the real mypy on a four-file tree, so the parser is checked
  against what the installed mypy actually prints.
- **One config.** The repo-root `pyproject.toml` `[tool.mypy]` is the
  only one, and it's what `npm run type:check` and the lint both use.
  The package's `src/cofounder_agent/pyproject.toml` used to carry a
  stricter table that nothing invoked. mypy's config discovery picked
  it up for any bare `mypy` run from that directory, where it stopped on
  package-base errors. Forced past those, it reported 663 errors (427
  `no-untyped-call`, 63 `unreachable`, 100 import errors) to the root
  config's 74. It's gone, and a test fails if one comes back. Tightening
  strictness is a separate decision: make it in the root config and
  re-baseline with `--allow-growth` in the same PR. For scale,
  `warn_unreachable` alone adds 62 errors.

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
  (`poindexter/brain/scheduled_workflow_watch.py`, 2026-08-28). Nothing gates a
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

  A workflow with no scheduled runs at all is not assessed and raises
  nothing — mirroring `data_freshness_probe`'s zero-rows rule, so an
  operator who never enabled a cron is never alarmed about it.
  Self-throttled to `scheduled_workflow_watch_interval_minutes`
  (default 60) rather than riding the brain's 5-minute cycle, since each
  target costs two GitHub API calls. A throttled cycle reports the last real
  pass's verdict, so a failing or blind watchdog reads as failing on every
  brain cycle, not one in twelve. When no verdict is recorded yet (the first
  cycle after an upgrade, or a lost row), the cycle runs a real pass instead
  of reporting ok. The upgrade to this behaviour read ok from 23:24 to 23:56
  UTC on 2026-09-25 while `playwright-e2e` was stale.

  **A watch list it cannot use pages too** (2026-09-28). Only an empty
  value (`''` or `[]`, the default) means "not configured". Anything else
  that cannot be used as written reports `ok=False` and pages once per
  episode, under its own `failure_episode:_config` key:
  - **Invalid JSON, or JSON that is not a list**, leaves nothing watched. The
    page quotes the parser's error with the text around it, and names the
    usual mistakes: a single object not wrapped in `[ ]`, or a list stored
    as a JSON string (encoded twice).
  - **An entry it has to ignore**: a repo that is not `owner/name`, a
    workflow that is not a bare `.yml`/`.yaml` file name, a `max_age_hours`
    that is not a finite number above 0, or a repeat of an earlier entry.
    The page names each ignored entry by position and says why (five at
    most; the brain log has them all), and the valid entries are still
    checked. `NaN` and `Infinity` used to be accepted as windows, and a
    workflow with either could never go stale.

  Every entry is either watched or named in a page. Fix the list with
  `poindexter settings set scheduled_workflows '<json>'`, which replaces
  the whole value. The page repeats when the problem changes, when it
  reached no channel, and on the
  `scheduled_workflow_watch_failure_repage_hours` reminder. A change is an
  edit that moves the JSON error, changes an ignored entry, or leaves no
  valid entry at all. An edit that leaves the problem as it was is not
  news. One info note follows when the list is usable again, or emptied. The
  list is parsed on every brain cycle, silently. Its WARNING is logged once
  per real pass, and a throttled cycle reports that pass's verdict. Each
  failing pass writes a `probe.scheduled_workflow_watch_config_failed` audit
  row, and the fix writes `probe.scheduled_workflow_watch_config_recovered`.
  A failed read of the setting reports `ok=False`. It used to report "no
  workflows configured", which could also sweep every open repo episode
  closed. Until
  2026-09-28 every case above read as "no workflows configured", ok, with a
  WARNING per dropped entry on every 5-minute cycle and no page. The
  watchdog watched nothing, or less than the operator believed, and
  reported healthy: the `gh_token` incident's failure class, one layer up.

  **When the watchdog itself cannot read the runs**, it pages once per
  failure episode per repo (`poindexter/brain/failure_episode.py`, shared
  with the branch-drift canary and the PR staleness probe), and the pass
  reports `ok=False`. So does any pass that assessed nothing.
  - **Loud failures page when the episode opens.** These are a `gh_token`
    that GitHub rejects (401), forbids (a 403 that is not a rate limit) or
    that cannot see the private repo, a missing token or httpx, a redirect
    (the repo moved), and a watched workflow that does not exist. The
    watchdog needs **Actions (read)** on the repo, which on a fine-grained
    token is its own permission, separate from the Contents (read) the
    branch-drift canary needs. The page repeats only when the failure
    changes, when a replaced `gh_token` fails too, or when the last page
    reached no channel, plus a reminder every
    `scheduled_workflow_watch_failure_repage_hours` (24, `0` = never).
  - **A 404 is read against the rest of the repo.** GitHub answers 404 both
    for a private repo the token cannot see and for a workflow file name
    that does not exist. When every watched workflow in the repo 404s, the
    page blames the token (and names a misspelled repo as the alternative).
    When some 404 while others answer, the token can see the repo, so the
    page names the missing workflow files. With only one workflow watched
    in a repo it names both causes. A pass where some 404 and nothing
    answers proves neither, so it is treated as transient.
  - **Transient failures stay in the log and audit_log.** These are 5xx,
    timeouts, DNS failures and rate limits. They page only if they last
    `scheduled_workflow_watch_transient_failure_page_hours` (6, `0` =
    never) without a break.

  One recovery note follows on the first clean pass after a page, and a
  workflow that went stale meanwhile is reported on that same pass. Every
  failing pass writes a `probe.scheduled_workflow_watch_failed` audit row,
  and the recovery writes `probe.scheduled_workflow_watch_recovered`.
  Taking a failing repo out of `scheduled_workflows` (or leaving it only
  invalid entries) closes its episode, since it will never be checked
  again. A closing note is sent if the episode paged, and
  `probe.scheduled_workflow_watch_unwatched` is written either way. If the
  list does not parse, nothing is closed, so a JSON typo cannot end every
  episode at once. The sweep only closes repo episodes. It never touches
  the watch list's own `_config` episode, which closes when the list is
  fixed. Until
  2026-09-25 each of these failures only left the target "not assessed"
  and the pass reported ok. From 2026-09-23 23:13 UTC the replaced
  `gh_token` could not see the repo, all nine workflows 404'd on every
  pass, and every brain cycle reported the watchdog ok. The last one to
  report a problem was at 22:51 UTC, while `playwright-e2e` was still
  visibly stale.

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

### The mirror runs every lint step for real, on the stripped tree

On the mirror, `unit-tests` degrades pytest to `--collect-only` (the
`$PYTEST` env in `unit-tests.yml`). Every `python scripts/ci/<lint>.py` step
still runs for real, against a tree the sync has already stripped. A lint
whose verdict depends on which files exist can therefore disagree with itself
between the two repositories. Nothing gates on the mirror's result, so a red
run there just stays red. That happened from 2026-09-20 to 2026-09-28.
`comment_reference_lint` read every comment citing a stripped file as a dead
reference. `settings_phantom_read_lint` read an allowlist entry whose only
reader is stripped as stale. The first failing step ended the job, so none of
the lint steps after it ran on the mirror in that time.

Three rules came out of it:

- **A lint that must behave differently on the mirror asks
  `scripts/ci/lib_public_mirror.py`.** It compares `GITHUB_REPOSITORY` with
  the mirror's name. That is the only repository name the sync's `org/name`
  byte rewrite leaves unchanged; a check against the source repository's name
  is inverted by it. A local run is strict. `comment_reference_lint` uses it
  to count, not fail, references to paths absent from the mirror's tree, and
  refuses `--update-baseline` there.
- **No lint may ship a stripped file's name.** That rules out allowlist
  entries, baseline keys and reason strings alike, because the name is the
  disclosure. Fix the finding in the stripped file, or make the stripped
  file comply; do not list it.
- **The source repository simulates the mirror before merge.** A stack-only
  unit test (the sync strips it) copies the tracked tree into a scratch
  repository and runs `scripts/sync-to-github.sh` there, with a local bare
  repository as its `github` remote. It then runs every lint step the
  post-sync workflows would run on the mirror. It fails if any of them fails,
  or if a `scripts/ci/*.json` baseline names a stripped file. A job guarded by
  `github.repository != 'Glad-Labs/poindexter'` is skipped there, as Actions
  skips it. A script that cannot run from a plain checkout (a live Postgres, a
  downloaded binary) declares `# mirror-tree-exempt: <reason>` in its own
  source. The test runs in `test-backend`'s scripts step, a required check,
  so a change that would turn the mirror red cannot merge green.

To reproduce one lint's mirror behaviour locally, run it with
`GITHUB_REPOSITORY=Glad-Labs/poindexter` set.

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
touches `poindexter/brain/` (`start-stack.sh build brain-daemon`), and the compose-apply
step recreates the container onto the fresh image, so brain code edits
auto-deploy too. `deploy-worker.ps1` remains the tool for an _immediate_ deploy
(skip the wait) and is still required for dependency / base-image changes
elsewhere (which need `docker compose build`) and for `poindexter-prefect-worker`
bootstrap-level changes.

**A rebuilt image goes into service once, by compose-apply.** `up -d`
recreates a container whose rebuilt image has different content — compose
compares the platform manifest digest it recorded on the container
(`com.docker.compose.image`) with the one the tag now names — and leaves the
container running when the rebuild changed nothing. The sync's step 6a-bis then
checks every rebuilt service with the same comparison
(`scripts/linux/deploy_health_gate.py recreate-plan`, logged as
`not recreating <svc>: compose-apply already recreated …`) and force-recreates
only a service compose left on the previous image: normally none. A repair
shows in the status file as `recreated after compose-apply: <svc>`, and a
failed one withholds the marker so the next pass retries.

Do not judge this by image IDs. Under the containerd image store every build
mints a new ID, because the OCI index it names carries a fresh attestation
manifest, around an unchanged platform manifest; an ID comparison calls every
no-op rebuild stale. That false positive made step 6a-bis force-recreate every
rebuilt service from 2026-09-22 to 09-28, so each one compose had just recreated
started a second time about a minute later — two brain restarts per brain
deploy. `verify_deploy_identity.py` (step 6d) uses the same comparison as
6a-bis, so the two cannot disagree.

A rebuilt service that is stopped and was not started by the pass belongs to a
parked compose profile (voice). It is neither recreated, because naming it in
`up --force-recreate` enables its profile and starts it, nor health-gated,
because the gate would read `exited` as a broken image. The status file says
`left parked: <svc>`, and compose recreates it from the fresh image when the
profile comes back.

Parked is decided from _liveness_ before the plan runs (2026-09-28), so it
holds on every path. A rebuilt service is live if `start-stack.sh ps -q <svc>`
showed it running before the pass touched it, or after compose-apply. That
query is project-scoped, lists running service containers only, and leaves
out `compose run` one-offs. Only live services reach the plan, the
force-recreate and the gate. Before this, three fallbacks could still start a
parked service. When the plan could not run, the step recreated every rebuilt
service "to be safe". When a docker call failed, or two containers answered to
the label, the plan itself said "recreate to be safe". And when compose-apply
failed there was no plan at all, so the gate watched the parked service and
its rollback started it. A service whose state cannot be read is neither
recreated nor gated, and the pass fails with `service-state` and retries. This
is also why the profile-gated services now have REBUILD_MAP entries of their
own: `demo-recorder` with the Dockerfile.worker services, and
`voice-agent-claude-code` with `voice-agent-livekit`. They are rebuilt and
left parked.

**Overlapping restarts coalesce instead of stacking.** The sync skips its
bounce for any container whose current process already started _after_ the
deploy clone reached the tree being deployed (bind-mounted code ⇒ it is already
running that tree). "Reached" is the pass's own `git reset`. If the clone was
already current because something else moved it (the brain's migration-drift
probe, a pass that reset and then failed, a hand fast-forward), it is the time
git's reflog gives for HEAD's last move. So a container the same pass has just
recreated (compose-apply, or the rebuilt-service recreate) is not restarted
again seconds later. And a restart that came after the clone moved, such as the
drift probe's own `docker restart poindexter-worker`, is not repeated by the
next cycle. Until 2026-09-28 the check ran only after the pass's own reset, so
a pass that found the clone current restarted worker, and since
glad-labs-stack#4144 pipeline-bot, right after recreating them on a dependency
bump. The worker
service also sets `stop_grace_period: 75s`:
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

**When the canary itself cannot run**, it pages once per failure episode
(`poindexter/brain/failure_episode.py`, shared with the PR staleness probe).
The episode is kept in `brain_knowledge`, so a brain restart does not page
again.

- **Credential and configuration failures page when the episode opens.** These
  are a `gh_token` that GitHub rejects (401), forbids (a 403 that is not a rate
  limit) or that cannot see the private repo, plus a missing token or `.git`
  mount. For a repo the token cannot see, GitHub answers 404 on
  `/commits/main`, not 403. The canary needs **Contents (read)** on the repo.
  The page repeats only when the failure changes, when a replaced `gh_token`
  fails too, or when the last page reached no channel. There is also a reminder
  every `branch_drift_failure_repage_hours` (24, `0` = never).
- **Transient failures stay audit-only.** These are 5xx, timeouts, DNS failures
  and rate limits. They page only if they last
  `branch_drift_transient_failure_page_hours` (6, `0` = never) without a break.

One recovery note follows on the first clean pass. Every failing pass still
writes a `probe.branch_drift_failed` audit row, and the recovery writes
`probe.branch_drift_recovered`. Until 2026-09-25 every GitHub error was
audit-only, so a replaced token that could not see the repo left the canary
blind from 2026-09-23 23:37 UTC with nobody told.
Deploying the canary itself requires a brain image rebuild
(`docker compose build brain-daemon && up -d brain-daemon`), since the
`.git` mount + the `git` binary are new.

## The ops-session wrapper is a third deploy surface

"Deployed" means several surfaces across three trees on this host, each with
its own sync mechanism:

| Surface                                              | Tree                                                                                                       | Synced by                                                                                                                                   |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Public site                                          | Vercel build of `glad-labs-stack`                                                                          | Vercel, on push to `main`                                                                                                                   |
| Worker / brain / pipeline containers                 | `~/.poindexter/deploy/glad-labs-stack`                                                                     | `deploy-checkout-sync.sh` (10-min timer; `reset --hard` + `clean -fd`)                                                                      |
| claude.ai phone connector (`poindexter-mcp-http`)    | `~/.poindexter/deploy/glad-labs-stack` (`mcp-server/` + its in-clone `.venv`)                              | same `deploy-checkout-sync.sh` pass (2026-08-16): unit restart on `mcp-server/**`, `uv sync` on lockfile change                             |
| Host `poindexter` CLI                                | `~/.poindexter/deploy/glad-labs-stack`, via the editable `~/.poindexter/cli-venv`                          | code: nothing to sync (each call imports the clone); deps: `poetry sync` on lockfile change, by the launcher and the same pass (2026-09-28) |
| Ops-session wrapper + shared payload                 | `~/glad-labs-website` (the working checkout)                                                               | `run-session.sh`'s own ff-only pre-flight (2026-08-15)                                                                                      |
| The deploy driver itself (`deploy-checkout-sync.sh`) | `~/.poindexter/deploy/glad-labs-stack`, run through the installed launcher in `~/.poindexter/deploy-sync/` | itself: every fire runs the clone's committed copy, with a last-known-good copy for a broken merge (2026-09-28)                             |
| Docker watchdog (`docker-watchdog.sh`)               | `~/.poindexter/deploy/glad-labs-stack`                                                                     | the deploy pass; the timer reads the clone's current copy every fire (2026-09-28)                                                           |

The ops-session row was the gap: the systemd session units exec `run-session.sh`
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

Unit-template changes for the connector stayed manual until 2026-09-28 (copy the
rendered template to `/etc/systemd/system`, then `sudo systemctl daemon-reload &&
sudo systemctl restart poindexter-mcp-http`), like the session units'. Since
Glad-Labs/poindexter#4232, `install-deploy-sync.sh` re-renders the unit
onto the clone when the host already has it, and restarts it only when a
non-comment line changed; see
[Install and operate](#the-deploy-driver-runs-the-deploy-clone-with-a-last-known-good-fallback)
below. The session units' template is still the manual one.

## The host CLI is a fifth surface, and it runs the deploy clone

The `poindexter` command on the operator host is not a container, so none of the
surfaces above covered it until 2026-09-28 (Glad-Labs/poindexter#4156).
It ran out of a poetry venv editable-installed against the **working
checkout**: `~/.local/bin/poindexter` was a hand-written launcher that exec'd
the newest `~/.cache/pypoetry/virtualenvs/poindexter-*/bin/poindexter`, and
that venv's `poindexter.pth` pointed at `~/glad-labs-website/src/cofounder_agent`.
CLI groups that open their own DB pool and call service code in-process
(`media approve/reject`, `settings`, `tasks`, …) therefore ran whatever that
working tree held. On 2026-09-28 it held 2026-09-23's `main`, 148 commits
behind. That meant `media approve/reject` rebuilt the RSS feed without #4108's
shrink guard, and #4148's reject-rebuild would never have reached the operator
at all. The only thing that ever advances that tree is `run-session.sh`'s
ff-only pre-flight. It had skipped 34 runs in a row because the tree had
uncommitted edits, which is its correct behaviour, and that is why the fix
cannot depend on it.

**How it works now.**

- `~/.local/bin/poindexter` is a **symlink** to the deploy clone's
  `scripts/linux/poindexter-cli.sh`. The launcher, the sync logic it calls and
  the CLI code are all read from the clone on each call. A merged change to any
  of them reaches the operator on the next sync pass, and nothing needs
  reinstalling.
- The launcher execs `~/.poindexter/cli-venv/bin/poindexter`. That venv's
  `poindexter` package is editable-installed from
  `~/.poindexter/deploy/glad-labs-stack/src/cofounder_agent`, so **code** needs
  no sync step: every CLI call is a fresh process importing the clone's current
  files.
- **Dependencies** are what `scripts/linux/cli-venv-sync.sh` manages. It
  fingerprints `(pyproject.toml, poetry.lock, extras, project dir)` and runs
  `poetry sync --only main --extras "pipeline qa rag youtube"` into the venv
  when the fingerprint moves. It stamps the new fingerprint only after the
  synced env imports `poindexter` from the clone and `poindexter --help` exits 0. The extras are the worker image's set minus `rerank` (≈3 GB of CUDA torch)
  and `profiling`. With a warm poetry cache a lockfile bump syncs in seconds,
  and a full build takes about 10 s.
- Two callers keep it current, serialised on `flock(~/.poindexter/cli-venv.lock)`:
  - The **launcher** runs `--ensure` before every command, which costs about
    5 ms when the venv is current.
  - The **deploy-sync pass** runs the default mode as its step 10, on
    no-change passes too. On a deploy pass it runs **last**, after the marker
    and status are written. A `poetry.lock` change triggers both a worker-image
    rebuild and this sync, and the unit kills the whole pass at
    `TimeoutStartSec=900`, so an earlier slot could cost the marker and a second
    round of rebuilds and force-recreates. It is bounded by
    `SYNC_CLI_VENV_TIMEOUT_SEC` (300 s; the longest recent pass took ~400 s).

  A failed sync never fails the pass or withholds the marker: PyPI being
  unreachable is not a failed container deploy. It amends the status `detail`
  (`host CLI env sync failed (rc=N)`) without a second heartbeat, and the CLI
  keeps running the clone's code on its previous dependency set until a sync
  succeeds. After a failure
  the launcher backs off for 15 minutes per lockfile, so a box that cannot
  reach PyPI doesn't pay a failing sync on every command. The deploy-sync pass
  does not back off.

- It never runs another tree quietly. A missing clone or venv exits 127 with
  the fix in the message. A `PYTHONPATH` entry holding a `poindexter` package
  is honoured, because `PYTHONPATH=<worktree>/src/cofounder_agent poindexter …`
  is how you try a branch's CLI code, but the launcher names it on stderr.

**Why not the other two shapes.**

- **`docker exec poindexter-worker python -m poindexter …` for everything.**
  That ties the CLI to worker liveness. The worker restarts on every deploy,
  the CLI is the tool you recover it with, and an exec'd job dies with the
  worker. Several commands also need the host: `media open` (xdg-open), `game`
  and `backup` (the docker CLI), `setup` and `auth … --bootstrap` (they write
  `~/.poindexter/bootstrap.toml`), and every interactive prompt. The container
  is still the right place for commands that call the Ollama fleet or write
  embeddings, because app_settings points them at `host.docker.internal`, which
  resolves only there. That is a URL problem, not a staleness one.
- **Fast-forward the working checkout when it is clean.** That mechanism
  already exists in `run-session.sh`, and it is exactly what had been skipping
  for five days. A working tree is legitimately dirty for days. The CLI's
  behaviour must not depend on whether the operator is mid-edit, and the working
  checkout must never receive more than an ff-only merge.

**Install and operate.**

```bash
# one-time; the launcher it replaces is kept as ~/.local/bin/poindexter.pre-host-cli-<ts>
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/install-host-cli.sh
# where does the CLI run from, and is it current?
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/cli-venv-sync.sh --status
# retry a failed sync now, ignoring the launcher's backoff
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/cli-venv-sync.sh --force
```

`deploy-checkout-sync.sh --status` includes the same report. Log:
`~/.poindexter/cli-venv-sync.log`. Env seams: `POINDEXTER_CLI_VENV`,
`POINDEXTER_CLI_EXTRAS`, `POINDEXTER_CLI_PYTHON` (default `python3.13`),
`POINDEXTER_POETRY_BIN` (systemd's PATH has no `~/.local/bin`, so the standard
install dirs are probed), and `POINDEXTER_CLI_NO_SYNC=1` (the launcher skips
the dependency check; emergencies only).

The working checkout's own poetry venv is untouched. It is still the one to run
tests with, and its `bin/poindexter` still runs the working tree's CLI when that
is what you want.

**Step 10's call sites deploy themselves too.** They live in
`deploy-checkout-sync.sh`, which the unit ran out of the working checkout until
the next section's change, so the pre-warm first had to wait for that checkout
to be fast-forwarded. The driver now runs from the deploy clone like everything
else here. The CLI launcher's own `--ensure` never depended on either.

## The deploy driver runs the deploy clone, with a last-known-good fallback

`deploy-checkout-sync.sh` keeps every other surface above current, and until
2026-09-28 it was the one that went stale itself (Glad-Labs/poindexter#4172).
`poindexter-deploy-sync.service` ran it out of the operator's **working
checkout**. The unit said that was deliberate, so a broken merge could not
brick the syncer that would fix it. But the only thing that ever advanced that
tree was `run-session.sh`'s ff-only pre-flight, which correctly skips a dirty
checkout. It logged `checkout sync skipped: … has uncommitted tracked changes`
on 34 ops-session runs in a row. On 2026-09-28 the checkout was 148 commits
behind, and four merged driver fixes were not running: #4144 (rebuild every
`Dockerfile.worker` service), #3984 (`start-stack.sh` stdout is data), and
#4001 and #4085, the guards that stop a deploy from bouncing the worker through
a media render. The driver changed 21 times in the 30 days to 2026-09-28, so
the lag was never a rare case.

Running it straight from the deploy clone would have been the obvious fix, and
the unit's concern was real. The driver is what moves the clone. A merged driver
that dies before its `git fetch` and `reset` would never receive the commit that
fixes it, and the deploy path would stop until someone reset the clone by hand.

**How it works now.**

- The unit execs `~/.poindexter/deploy-sync/deploy-sync-launcher.sh`, an
  **installed copy** of `scripts/linux/deploy-sync-launcher.sh`, outside every
  git tree. It is a copy and never a symlink, because it is the one piece a
  merge must not be able to break.
- Each fire, the launcher stages the deploy clone's **committed** driver
  (`HEAD:scripts/linux/deploy-checkout-sync.sh`) into
  `~/.poindexter/deploy-sync/candidate.sh` and runs that. It stages the file so
  the pass's own `reset --hard` never changes the bytes being executed, and so
  a promotion keeps exactly the bytes that ran. A merged driver change therefore
  runs on the next fire, with no pull anywhere.
- It keeps `~/.poindexter/deploy-sync/last-known-good.sh`, the last copy that
  completed a clean pass, with its provenance in `last-known-good.meta`. When
  the merged copy is identical, which is the steady state, there is one run and
  nothing to judge. When it differs, the launcher judges the run by what it did
  to the clone, not by what it reported. The run "reached origin" if it fetched
  during its run (`FETCH_HEAD` rewritten and naming a commit; a failed fetch
  empties it) and the clone's `HEAD` is what it fetched.

| The merged copy…                                             | What happens                                                                                                                                                                                  |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| exits 0 and reached origin                                   | **promoted**: its staged bytes become the last-known-good copy                                                                                                                                |
| exits 0, fetched, but left the clone behind                  | a **deferral** (busy stack). Trusted, judged again next fire                                                                                                                                  |
| fails after reaching origin (e.g. an image rebuild failed)   | no promotion and **no fallback**: the clone is current, so a fix still arrives                                                                                                                |
| fails `bash -n`, is missing, or dies or hangs before syncing | if origin answers (`git ls-remote`), the **last-known-good copy runs in the same fire**. It fetches and resets onto whatever fixed the merged copy, which is promoted on its first clean pass |
| fails before syncing while origin does not answer            | no fallback, because neither copy could reach it. The next fire retries                                                                                                                       |

Every driver run gets `SYNC_DRIVER_TIMEOUT_SEC` (900 s, the unit's old per-pass
budget), so a hang is killed and judged like any other failure. The unit's
`TimeoutStartSec` is 1920 s, enough for a fire that runs both copies.

**It is never silent.** The launcher passes `DEPLOY_SYNC_DRIVER` (`merged` or
`last-known-good`; unset means a direct run, `direct`), the source commit, and
its reason for a fallback. The driver writes them into
`deploy-checkout-sync.status.json` (`driver`, `driverCommit`, and the reason in
`detail`) and into its `deploy_sync_run` heartbeat. The brain's `deploy_sync`
probe raises **`deploy_sync_driver_fallback`** (warning, Discord) whenever the
newest heartbeat came from the last-known-good copy. That matters because a
fallback pass reports `deployed` like any other: a quiet fallback would be the
original failure again, with merged driver code not running and nothing saying
so. The launcher's own decisions go into `deploy-checkout-sync.log` tagged
`[launcher]`.

**Why these rules.**

- **A deferral never triggers the fallback.** The merged driver's busy guard is
  the newer one. Falling back would let an older guard reset the clone and
  bounce the worker through a render the newer guard saw, which is exactly what
  #4001 and #4085 exist to stop.
- **Promotion needs evidence that the run fetched.** Otherwise a merged driver
  that exits 0 immediately would be promoted whenever the clone happened to be
  current already, and the fallback would then be a copy that does nothing.
- **The bytes that ran are promoted, not the clone's copy after the pass.** The
  pass may reset the clone onto a newer driver, and that one has not run yet.
- **No quarantine.** A merged copy that failed is tried again every fire rather
  than skipped until it changes. A transient failure (a git lock, a blip) must
  not strand a good driver behind the old one, and a copy that is really broken
  usually fails fast.
- **The seed is the driver this host already ran.** `install-deploy-sync.sh`
  seeds the last-known-good copy from the unit's previous `ExecStart` and
  never from the clone, because a fallback identical to the copy being judged
  could rescue nothing.

**Not covered, so nobody over-trusts it.**

- A merged driver that exits 0 without ever moving the clone (a busy guard that
  is always busy, a skipped reset) looks exactly like a deferral from the
  launcher. The brain's `branch_drift_probe` pages when the clone falls behind
  origin/main.
- The launcher itself does not self-update. That is deliberate: it is the
  bootstrap. When the clone's copy differs, every pass logs
  `launcher out of date` and adds it to the status detail, and `--report` says
  so. Re-run the installer, as for the unit files.
- It is no harder to tamper with than what the driver already ran. Until
  stack#4186 the worker, pipeline-bot and prefect-worker containers mounted
  the whole `~/.poindexter` read-write at `/root/.poindexter` (a legacy
  mount). That covered `deploy-sync/` and the deploy clone, whose
  `start-stack.sh`, health gate and identity check the driver executes on
  every pass. That mount is gone, and
  `scripts/ci/compose_poindexter_home_mount_lint.py` fails CI if any service
  mounts `deploy-sync/`, `cli-venv/`, `bootstrap.toml` or the whole directory
  again (see
  [worker-container-filesystem.md](../architecture/worker-container-filesystem.md#never-the-whole-directory)).
  Three containers can still write into the deploy clone (checked with
  `docker inspect` 2026-09-28). The brain mounts the whole clone for the
  migration-drift self-heal, and it holds the Docker socket anyway. Grafana
  mounts `infrastructure/grafana/provisioning` and the worker mounts its
  `alerting/` subdirectory. Both are bare `./` mounts, which resolve inside
  the clone because compose runs from it. The host executes nothing in either
  directory.

**The other host units.** The docker watchdog moved to the deploy clone in the
same change. Its timer reads the script on every fire, so a merged fix runs
after one deploy pass. It needs no last-known-good copy because it does not
deploy itself: the deploy sync delivers its fixes, and that sync is the part
protected against a broken merge. It also gained its first tests
(`test_docker_watchdog.py`).

**Long-running host daemons are restarted when their files change**
(Glad-Labs/poindexter#4188). A process reads its code once, at start, so
running it from the deploy clone only updates the files it will load next
time; a launcher buys it nothing. `poindexter-recovery-agent` already ran from
the clone and still ran pre-#4158 code a day after that merge, because nothing
restarted it. `poindexter-gpu-scraper` ran from the working checkout, so it ran
whatever that tree held when it started. The scraper now runs the clone too,
and step 8b of the deploy pass restarts either unit when a file it loads at
start changed:

| Path (regex)                                             | Unit                                |
| -------------------------------------------------------- | ----------------------------------- |
| `^scripts/gpu-scraper\.py$`                              | `poindexter-gpu-scraper.service`    |
| `^src/cofounder_agent/poindexter/brain/bootstrap\.py$`   | `poindexter-gpu-scraper.service`    |
| `^src/cofounder_agent/poindexter/(brain/)?__init__\.py$` | `poindexter-gpu-scraper.service`    |
| `^scripts/recovery-agent\.py$`                           | `poindexter-recovery-agent.service` |

The scraper imports `poindexter.brain.bootstrap` once, to resolve its DSN, and
the two package `__init__` files run on the way. The table is `HOST_DAEMON_MAP`
in the driver. `test_deploy_checkout_sync_host_daemons.py` loads each daemon's
module level and fails when a repo file it loaded has no entry, so the table
cannot fall behind a new import.

How the step behaves, and why:

- **Fail-soft.** A failed restart (no passwordless sudo, say) adds a note to
  the status detail, like the host CLI step, and never withholds the deploy
  marker. The connector step does withhold it, but on a host without the sudo
  grant a withheld marker would turn every pass into `error`.
- **Its own record, not the marker.** Each unit's record,
  `~/.poindexter/deploy-host-daemons/<unit>`, names the tree the unit runs, and
  the diff starts there. The marker moves on even when a restart failed, so a
  diff from the marker would drop the change for good. The step also runs on
  no-change passes, which is how a missed restart is retried, within one timer
  period. `--status` prints each record.
- **Once per tree.** A pass that retries some other failed step finds the
  record already on HEAD and restarts nothing.
- **Left alone:** a unit that is not installed (`LoadState`), that is not
  running (`systemctl restart` would start it), or whose process started after
  the clone reached HEAD (the installer restarted it, or the host rebooted).
- **Not run from this clone:** a restart would reload the other tree's code and
  prove nothing, so the unit is noted on every pass until `install-deploy-sync.sh`
  re-renders it. The installer renders the scraper, and the recovery agent where
  the host has it.
- **Mid-action:** a restart kills the unit's whole cgroup, so a unit with more
  than its main process in it waits for the next pass. The recovery agent runs
  its compose reapply as a fire-and-forget child and waits on
  `sudo systemctl restart` for host units, and killing either mid-way can
  strand containers or cut a recovery short.
- **No record yet** (the first pass with this step) is unknown, and unknown
  restarts once, as the container bounce does for a missing record.
- A restart onto broken code is caught downstream, not here:
  `PoindexterSystemdUnitRestartLooping` pages a `poindexter-*` unit stuck in
  `activating` for 10 minutes.

The deploy-sync user needs root or passwordless sudo for these restarts, the
same posture as the connector's. A narrow grant:

```text
<user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart poindexter-gpu-scraper.service, /usr/bin/systemctl restart poindexter-recovery-agent.service
```

**These units still run the working checkout** (audited 2026-09-28):

| Unit                                 | Shape                                    | Why it has not moved                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `poindexter-session@` (ops sessions) | oneshot                                  | By design; see the previous section. It is stale while the tree is dirty.                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `ollama-primary`                     | daemon: the wrapper execs `ollama serve` | The scraper's shape, but a restart unloads the resident model (an 18-22 GB re-read) and cuts every in-flight LLM call, so a deploy-time restart would need the busy guard the container bounce uses. Moving it is a decision about when Ollama may restart, not a path change. The recovery agent and the firefighter's `restart_host_service` restart it by unit name either way.                                                                                                                                                                |
| `ollama-vision`                      | daemon: the wrapper execs `ollama serve` | As above, and it holds the QA judge resident (`OLLAMA_KEEP_ALIVE=-1`), which a restart evicts for a 10-40 s reload. Wrapper edits have also been staged and run from the working checkout ahead of their merge (a staged `ollama-vision.sh` sat there on 2026-09-28), so moving it changes that workflow too.                                                                                                                                                                                                                                     |
| `poindexter-dr-backup{,-hourly}`     | oneshot timers                           | Mechanically the watchdog's shape: each fire reads the script, which is self-contained and resolves every path from `$HOME` and `DR_*`, so moving `ExecStart` would not change what gets backed up. Not moved, because the units carry per-host config (`DR_*`, `RequiresMountsFor=`) that this installer's render would overwrite, and whether disaster recovery takes merges unattended is the operator's call. Both timers are disabled while Tier 3 is parked ([backups.md](backups.md), 2026-08-27); re-arming them is the moment to decide. |

**Install and operate.**

```bash
# one-time, and again after any change to the launcher or to a unit template it renders
bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/install-deploy-sync.sh
# which copy runs, what the last-known-good copy is, recent launcher decisions
bash ~/.poindexter/deploy-sync/deploy-sync-launcher.sh --report
# the driver's --status, which includes that report
bash ~/.poindexter/deploy-sync/deploy-sync-launcher.sh --status
```

The installer copies the launcher, seeds the last-known-good copy, renders both
units (`User=` and `ExecStart=`; the repo templates ship generic
`/home/poindexter` paths), reloads and restarts both timers, then runs one pass
through the launcher and prints its report (`--no-start` skips the pass). Run
it as your login: it calls `sudo` itself for `/etc/systemd/system`.

It renders `poindexter-gpu-scraper.service` onto the clone as well (`User=`,
`WorkingDirectory=`, `ExecStart=`). On a host that already had the unit, it
`try-restart`s it, so a running scraper moves onto the clone at once and a
stopped one stays stopped. On a host that did not, it installs the unit without
enabling it: `gpu_metrics` is optional, and the scraper needs host
`python3-asyncpg` and `python3-httpx`. Until the installer has run, the deploy
pass notes on every pass that the scraper does not run from the deploy clone.

**It also refreshes the connector's and the recovery agent's units, on a host
that already has them** (Glad-Labs/poindexter#4232).
`poindexter-mcp-http.service` and `poindexter-recovery-agent.service` run from
the clone too, but their templates used to reach a host only by a hand render.
The installer renders the same three directives (`User=`, `WorkingDirectory=`,
`ExecStart=`; the connector's on `mcp-server/` and its in-clone `.venv`), keeps
every other template line, reloads and `try-restart`s. It never installs or
enables either unit, because each needs host setup it must not guess at: a uv
venv in the clone for the connector, and a bootstrap token plus the sudoers
grant for the agent. A host that lacks one gets a one-line note. Install it by
hand first (the template's header says how, and so does
[self-healing.md](self-healing.md) for the agent), then let the installer keep
it on the template.

- **A restart only follows a change to what systemd would run.** A unit whose
  non-comment lines changed is `try-restart`ed after the reload, so a running
  one picks up the new file and a stopped one stays stopped. One that differs
  only in comments, which is what most template edits are (#4188 and #4218 both
  were), is rewritten without a restart, and an identical one is left alone. A
  restart drops the connector's open sessions and kills a recovery action the
  agent has in flight, the thing step 8b waits for, so a re-run must not bounce
  either for nothing. On the operator host at #4232 both installed units matched
  their templates apart from comments, so the first refresh restarts neither.
  When a restart does follow, run the installer at a quiet moment: unlike step
  8b, it does not wait for the agent to be idle.
- **Host-specific values go in a drop-in.** The connector's template carries four
  `Environment=` lines (`POINDEXTER_API_URL`, `OLLAMA_URL`,
  `POINDEXTER_MCP_HTTP_HOST`, `POINDEXTER_MCP_HTTP_PORT`). The installer replaces
  every line of the installed file except the three directives with the
  template's, so a value that differs on one host belongs in
  `sudo systemctl edit poindexter-mcp-http.service`, which writes
  `/etc/systemd/system/poindexter-mcp-http.service.d/override.conf`. systemd
  reads a drop-in after the main file, so a same-named `Environment=` there wins,
  and the installer never touches a `.service.d/` directory, so the value
  survives every refresh. Carrying the installed file's `Environment=` lines
  into the render was rejected: a merge cannot tell a deliberate local value from
  a stale template default, so a default that changed in the template would never
  reach a host that still had the old one. Nothing is lost silently, though: when
  the installed unit differs from the template beyond those directives, the
  installer prints the lines it replaces. (Leave `POINDEXTER_MCP_HTTP_PORT`
  alone unless you also set the brain's `mcp_http_probe_base_url`; the probe
  defaults to `:8004`.)
- **The connector waits for its venv.** While the clone has no executable
  `mcp-server/.venv/bin/python`, the connector's unit is left exactly as it was
  and the installer prints the `uv sync --directory <clone>/mcp-server` that
  builds it. Moving `ExecStart` onto a missing interpreter and restarting would
  take a working connector down until the deploy pass's own `uv sync`. Re-run
  after building it.
- **A missing file refuses, before anything is written.** A host that has one of
  these units needs its template in the clone, and for the agent
  `scripts/recovery-agent.py`. A clone that lacks them does not block a host
  that never ran the units.

To back the driver launcher out, point the deploy-sync unit's `ExecStart` at a
checkout's driver again. The driver behaves the same when run directly (its
status says `driver: direct`):

```bash
sudo sed -i "s|^ExecStart=.*|ExecStart=$HOME/glad-labs-website/scripts/linux/deploy-checkout-sync.sh|" \
  /etc/systemd/system/poindexter-deploy-sync.service
sudo systemctl daemon-reload
```

## Automatic rollback: the post-deploy health gate

Since 2026-09-13 the deploy sync does not walk away from an image it rebuilt.
`scripts/linux/deploy_health_gate.py` (stdlib, system `python3`) runs in two
halves around the rebuild:

1. **snapshot**: before `start-stack.sh build`, record each rebuilt service's
   running container, image ref, image id and platform manifest, and **tag the
   image it runs as `<repository>:rollback-<service>`**. The snapshot file
   (`~/.poindexter/deploy-gate-snapshot.json`) records that tag as
   `rollback_ref`.
2. **verify**: after compose-apply (and after the bind-mount bounce), poll each
   unit until it is `healthy` (or, for services without a healthcheck, `running`
   for `deploy_health_gate_settle_seconds`). A definitive failure triggers the
   rollback. That means `restarting`, `exited`/`dead`, `unhealthy`, or a
   `RestartCount` of 2+ on the fresh container, and it applies to rebuilt
   services with a rollback image when `deploy_rollback_on_unhealthy=true`.
   The gate checks the rollback tag still names what the snapshot saw, runs
   `docker tag <repository>:rollback-<service> <image ref>` and a
   `--force-recreate` of that one service, then checks the new container runs
   exactly the preserved platform manifest. Only then does it count as rolled
   back. Either way a **critical** `alert_events` row carries the failed
   container's last 20 log lines (the traceback), so the page names the cause.
   A timeout without a verdict pages **warning** and does not roll back. A slow
   worker start is not a broken image.

A rolled-back service's sha is written to `~/.poindexter/deploy-rolled-back-sha`
and that service is **not rebuilt again at that sha**; the fix must merge as a
new commit, at which point the marker no longer matches and the normal path
resumes. Bounced bind-mount containers (`poindexter-worker`,
`poindexter-pipeline-bot`) are verified by name and paged, never rolled back —
their rollback is the manual pin below. `--no-gate` skips the step. The pass
still records its marker on a rollback (the sha was handled; the alert is the
follow-up), so the brain's `deploy_sync` probe does not read a rollback as a
broken deploy path.

Why: the sync rebuilt `chatterbox` on a merged change, recreated it, logged
"Pipeline now running …", and the container died on `ModuleNotFoundError` 507
times over eight hours. Nothing between "build succeeded" and "a downstream
probe noticed" had looked at the container.

**The gate and the game-mode re-park look only at the stack's own containers.**
Both find a service's container by its compose service label, which every
compose project on the host carries, and `docker ps` lists the newest first.
Throwaway projects from worktrees reuse the stack's service names. On
2026-09-28 a `seedorder-repro` project, run from the consumer compose file,
left exited `brain-daemon` and `worker` containers newer than the stack's.
Unscoped, the next brain rebuild would have snapshotted that container. It
could have gated it too, reading `exited` as a failed deploy and rolling the
stack's brain back over it. And the recreate check read "2 containers" as
"recreate to be safe". The re-park (step 6c) has the same lookup: it would
stop another project's running sidecar and leave the stack's GPU sidecars warm
for the rest of the game. So each pass asks compose for the stack's project
name, once and on first use
(`start-stack.sh config --format json --no-interpolate`, which prints no
environment values). It passes it to `snapshot`, `recreate-plan` and `verify`
as `--project`, and every container lookup filters on
`com.docker.compose.project`. If the name cannot be resolved, the pass logs
`[WARN] could not resolve the stack's compose project` and those lookups run
unscoped, as before.

**Why the rollback target is a tag, not an image id.** Until 2026-09-28 the
snapshot recorded the running image's id and the rollback re-tagged that id.
On this host that never worked. Docker 29 with the containerd image store
deletes the old image record as the build moves the tag, even while a container
still runs it, so `docker image inspect <old id>` and `docker tag <old id> …`
answer "No such image". Every rollback from 09-13 to 09-28 would have failed
with `rollback FAILED (docker tag failed: … No such image …)` and left the
broken image in service. Nothing noticed, because none fired (`grep "ROLLED
BACK"` over the deploy logs finds nothing), and the unit tests' fake answered
every `docker tag` with success. A tag taken before the build keeps the record,
and with it the content, through the build. This was measured on a throwaway
compose project, and the fix was verified there end to end: a broken build
rolled back, and the container ran the pre-build content again.

How the snapshot picks what to keep:

- The source is the image the container was created from, when that record
  still exists. Often it does not, even before this build. A rebuild that
  changed nothing mints a new image id around the same platform manifest,
  compose rightly leaves the container on the old one, and that record is
  gone. The image ref then names the same content, so it is tagged instead.
- The new tag is read back and recorded only if it names exactly the platform
  manifest the container runs. If no image holds that content any more (a build
  that compose-apply never applied), there is no rollback image. The gate then
  pages without rolling back rather than restoring something else.
- **Every pass logs one line per rebuilt service** saying what a failed gate
  could roll back to. A service it cannot roll back logs
  `[WARN] health gate: no rollback image for <svc>: <why>`, before the build
  rather than at the moment a rollback is needed.
- There is **one tag per service**, and every snapshot moves it. `docker tag`
  over an existing tag leaves the old image behind untagged, so the snapshot
  then deletes the image the tag held before. It does this only when no tag
  names that image and no container uses it, and never with force. Nothing
  accumulates. Each service keeps at most one previous image, until its next
  rebuild. The tag is per service rather than per image because services share
  image refs (`backup-daily`/`-hourly`/`-offsite` all run `poindexter-backup`)
  and need not all run the same build.
- A rollback leaves the failed build as an untagged (dangling) image, in case
  you want to inspect it. `docker image prune` removes it.

**Rolling a baked image back by hand** (for example after a problem the gate's
window did not catch, or when `deploy_rollback_on_unhealthy=false`). A page
that leaves a rebuilt service on the broken build (rollback disabled or failed,
or no verdict in time) prints these commands with the names filled in:

```bash
# what each rebuilt service ran before its last rebuild
docker image ls --filter 'reference=*:rollback-*'
docker tag glad-labs-website-brain-daemon:rollback-brain-daemon glad-labs-website-brain-daemon
bash ~/.poindexter/deploy/glad-labs-stack/scripts/start-stack.sh up -d --no-build --force-recreate brain-daemon
```

The next rebuild of that service (a new commit) moves it forward again.

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
`sudo systemctl restart poindexter-mcp-http` after pinning. The 10-minute
deploy sync will try to advance the clone again on its next fire. Its launcher
runs the pinned commit's driver, which fetches and resets straight back to
origin/main, so stop the timer while the incident is live:

```bash
# Suspend automated sync while you're pinned
sudo systemctl stop poindexter-deploy-sync.timer

# Re-enable once the revert commit has merged and you're ready to roll forward
sudo systemctl start poindexter-deploy-sync.timer
```

On the retired Windows host the same step was
`Disable-ScheduledTask -TaskName 'Poindexter-DeployCheckoutSync'`, and
`Enable-ScheduledTask` to resume.

**Follow-up:** file a `git revert` PR as the durable fix. Re-enable the
timer only after the revert has merged and CI is green — otherwise the sync
will overwrite your pin on the next cycle.

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
bash scripts/start-stack.sh up -d
```

CI is useful if you fork and want PR checks, but the stock setup
has no notion of "deploy." The worker container is your production.
