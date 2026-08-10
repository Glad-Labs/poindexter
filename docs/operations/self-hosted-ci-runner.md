# Self-hosted CI runner (unit-tests → $0 cloud minutes)

## Why

`glad-labs-stack` is a **private** repo, so every GitHub-hosted Actions minute
is billed. A 14-day sample projected to **~33,000 min/month against a 3,000-min
allowance** (~10× over), and the `unit-tests` workflow alone was **~60%** of it
(~2,100 wall-clock min over 3 days). The fix is to run that one heavy workflow
on Matt's always-on PC instead of in GitHub's cloud — self-hosted runner minutes
are **not** billed.

Scope is **capability-based, not job-shaped**: a job may use the seam if it
needs no Docker daemon. That covers the pytest jobs plus the lint / scan /
codegen jobs (`python-lint`, `mcp-server-tests`, `public-mirror-safety`,
`security`'s `gitleaks` / `action-pins` / `poetry-lock` /
`shell-line-endings` / `changes`, `ports-lint`, `phantom-poindexter-set`,
`regen-services-doc`, `console-unit`, `jest-unit`).

The set was widened on 2026-08-07 after a GitHub hosted-runner outage left
**19 jobs queued and 0 running** for hours while the self-hosted fleet sat
idle: only 5 jobs read the seam, and the required checks were not among them,
so the repo was blocked rather than degraded. The seam now covers six of the
seven required checks.

**Do not enumerate seam members from memory** — the list above will drift.
The authority is `grep -l 'vars.CI_RUNNER' .github/workflows/*.yml`.

**What must stay hosted, and why:**

| Job(s)                                                                                              | Reason                                                                                                          |
| --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `migrations-smoke`, `integration-db`, `grafana-panels-lint`, `regen-app-settings-doc`, `benchmarks` | declare `services:` — GitHub starts service containers through the Docker daemon, which the runners do not have |
| `docker-build` (`build-brain`, `build-worker`)                                                      | build images; `build-worker` has its own dormant `CI_RUNNER_DOCKER` seam                                        |
| `security`'s `trivy-fs`, `trivy-config`, `sbom`                                                     | third-party scanner actions whose Docker dependency is not verified, plus SARIF upload                          |
| `runner-healthcheck`                                                                                | it is the "is Matt's PC reachable?" probe — it cannot run on Matt's PC                                          |
| releases, mirror syncs, `triage-on-open`                                                            | carry publish credentials and gate nothing; a runner that executes raw PR code should not hold them             |
| `playwright-e2e`                                                                                    | needs browser binaries and system libs not proven on the runner image                                           |

`migrations-smoke` is the one **required** check that cannot fail over. It
needs a Postgres service container, and pointing it at the host's real
Postgres would aim CI at the production database.

**The Docker rule is enforced, not documented-and-hoped:**
`scripts/ci/ci_runner_seam_lint.py` fails CI if a seam job declares
`services:`/`container:`, uses a known Docker action, or shells out to
`docker`. It exists because the mistake shipped twice — `build-worker`
(#2920, red on every main push) and `benchmarks`, which carried
`services: postgres` on the seam behind a stale comment and failed its
nightly run for weeks without anyone noticing.

## How it fits together

```
 unit-tests.yml  ──reads──▶  vars.CI_RUNNER  ──set?──▶ runs on Matt's PC (self-hosted)
                                    │         ──unset?─▶ runs on ubuntu-latest (hosted)
                                    ▲
        runner-healthcheck.yml (hosted, cron) ──reconciles── live runner status
                                    │                        └─prunes─▶ offline registrations
                                    ▲
        docker-compose.local.yml: github-runner-1, github-runner-2
```

1. **The `runs-on` seam.** `unit-tests.yml` resolves its runner like this:

   ```yaml
   runs-on: ${{ vars.CI_RUNNER && fromJSON(vars.CI_RUNNER) || 'ubuntu-latest' }}
   ```

   When the repo variable `CI_RUNNER` is set to `["self-hosted","linux","x64"]`
   the job runs on the PC; when it's unset (the default, **and always on the
   public `poindexter` mirror**) it falls back to `ubuntu-latest`. The
   `&&`/`||` short-circuit means `fromJSON` never receives an empty string.

2. **The runners.** Two persistent containers (`github-runner-1`,
   `github-runner-2`) in `docker-compose.local.yml`, behind the `ci-runner`
   compose profile so they never start with the default stack. Image:
   `myoung34/github-runner`. They register under independent auto-generated
   `glads-pc-*` names sharing one label set, so GitHub dispatches each queued
   job to whichever is idle. Two is sized for the common case: a PR push plus
   the `main` push it triggers, which used to serialize on a single runner
   while `unit-tests` — a **required** check — sat "Queued".

3. **The self-healing control loop.** `runner-healthcheck.yml` runs **hosted**
   (it must — a "is the PC up?" probe can't run on the PC) every 6 hours. It
   reconciles `CI_RUNNER` against live runner status (see the control surface
   below), then prunes dead runner registrations (see below).

## One-time setup

### 1. Create the runner App (the only manual step)

GitHub doesn't allow App creation via API, so this is the one thing that needs
your hands. Create a **dedicated GitHub App** (Settings → Developer settings →
GitHub Apps → New GitHub App), named e.g. `glad-labs-ci-runner`:

- **Repository permissions:** **Administration: Read and write** (register/list
  self-hosted runners) + **Variables: Read and write** (the healthcheck flips
  `CI_RUNNER`). Nothing else.
- **Where can this be installed:** Only on this account → then **Install** it on
  `glad-labs-stack` only.
- **Generate a private key** (downloads a `.pem`) and note the numeric **App ID**.

App over PAT on purpose: an App key has no 1-year fine-grained-PAT expiry cliff
and mints short-lived (1h) installation tokens on demand — lower maintenance,
tighter least-privilege. A **dedicated** App (not the release-bot App) keeps a
key leak from crossing blast radius.

### 2. Wire the credentials into their two homes

The container (local) and the healthcheck workflow (cloud) each need the App.
The App ID is a plain number; only the private key is secret.

```bash
# Stash the private key at the canonical path the activation command reads:
mkdir -p ~/.poindexter
mv ~/Downloads/glad-labs-ci-runner.*.private-key.pem ~/.poindexter/ci-runner-app.pem

# a) container (local) — App ID + profile in bootstrap.toml. The stack reads
#    bootstrap.toml (not .env); start-stack.sh exports each key as an upper-case
#    env var. The multiline PEM is NOT put here — start-stack.sh sources it from
#    ~/.poindexter/ci-runner-app.pem at `up` time (step 3). compose_profiles
#    starts the runners with the stack.
cat >> ~/.poindexter/bootstrap.toml <<'TOML'
ci_runner_app_id = "123456"      # the numeric App ID (not secret)
compose_profiles = "ci-runner"
TOML

# b) the healthcheck workflow — App ID + private key as repo secrets
gh secret set CI_RUNNER_APP_ID          --repo Glad-Labs/glad-labs-stack --body '123456'
gh secret set CI_RUNNER_APP_PRIVATE_KEY --repo Glad-Labs/glad-labs-stack < ~/.poindexter/ci-runner-app.pem

# c) optional: routine Discord ping when CI auto-flips
gh secret set DISCORD_OPS_WEBHOOK_URL --repo Glad-Labs/glad-labs-stack --body "$DISCORD_OPS_WEBHOOK_URL"
```

### 3. Bring the runners up

`compose_profiles` includes `ci-runner`, so the runners come up with the rest of
the stack — and **`start-stack.sh` exports the multiline PEM into the container's
`APP_PRIVATE_KEY` for you** (it sources `~/.poindexter/ci-runner-app.pem`, the
same file-sourced-secret pattern it uses for the grafana webhook token). So the
normal bring-up is just:

```bash
bash scripts/start-stack.sh up -d                            # whole stack, runners included
# …or recreate just the runners:
bash scripts/start-stack.sh up -d --force-recreate github-runner-1 github-runner-2
docker compose -f docker-compose.local.yml ps github-runner-1 github-runner-2
# Confirm registration — expect two `online` rows:
gh api repos/Glad-Labs/glad-labs-stack/actions/runners --jq '.runners[] | {name, status, busy}'
```

**A `docker compose up` (and every deploy-checkout-sync) _recreates_ the
container — it does not merely `restart` it — so the env is rebuilt from scratch
each time.** That is exactly why `start-stack.sh` has to source the PEM itself: a
one-off manual `export` does **not** survive a recreate. Before this was
automated, a start-stack-driven deploy brought the runner up with an empty
`APP_PRIVATE_KEY` and the entrypoint crash-looped (`All of APP_ID,
APP_PRIVATE_KEY and APP_LOGIN must be specified`). If you ever bring the runners
up _without_ start-stack (direct compose), pass the key yourself:

```bash
CI_RUNNER_APP_PRIVATE_KEY="$(cat ~/.poindexter/ci-runner-app.pem)" \
  docker compose -f docker-compose.local.yml --profile ci-runner up -d github-runner-1 github-runner-2
```

### 4. Enable self-hosted

Trigger the control loop once — it sees the runners online and sets `CI_RUNNER`
for you:

```bash
gh workflow run runner-healthcheck.yml --repo Glad-Labs/glad-labs-stack
# …or set it directly:
gh variable set CI_RUNNER --repo Glad-Labs/glad-labs-stack --body '["self-hosted","linux","x64"]'
```

The next `unit-tests` run (open a PR or `gh workflow run unit-tests.yml`) will
execute on the PC. Confirm: the job log header shows the runner name `glads-pc…`
instead of `GitHub Actions`.

## Control surface

| Variable / action                             | Effect                                                                          |
| --------------------------------------------- | ------------------------------------------------------------------------------- |
| `CI_RUNNER` = `["self-hosted","linux","x64"]` | `unit-tests` runs self-hosted                                                   |
| `CI_RUNNER` unset                             | `unit-tests` runs on `ubuntu-latest`                                            |
| `CI_RUNNER_MODE` = `auto` (default/unset)     | healthcheck sets/unsets `CI_RUNNER` by live runner status (self-heal both ways) |
| `CI_RUNNER_MODE` = `on`                       | force self-hosted; healthcheck never reverts                                    |
| **`CI_RUNNER_MODE` = `off`**                  | **kill-switch** — force hosted; healthcheck never re-enables                    |

```bash
# Kill-switch — pin everything back to GitHub-hosted, durably:
gh variable set CI_RUNNER_MODE --repo Glad-Labs/glad-labs-stack --body off
# Resume self-healing:
gh variable set CI_RUNNER_MODE --repo Glad-Labs/glad-labs-stack --body auto
```

**PC-down behaviour:** in `auto` mode, if **all** runners go offline the
healthcheck unsets `CI_RUNNER` within its cron window (≤6h), so `unit-tests` — a
required check — falls back to hosted and PRs don't hang. One runner dying is
_not_ a failover event: the reconcile keys on "≥1 online", so the survivor keeps
serving jobs (at half throughput). For instant failover after a known outage,
run `gh workflow run runner-healthcheck.yml` (or bump the cron).

### Registration pruning

Each runner container **wipes its config and re-registers under a fresh random
name on every start** — that's what makes it self-recover from a corrupt-config
crash loop (see Troubleshooting). The cost is one permanently-offline
registration per container start: 22 had piled up by 2026-07-26.

They're functionally harmless (the reconcile counts only `status=="online"`),
but they bury the live runners in the API and in the repo's runner-settings
page — exactly where you look first when CI is stuck "Queued". So the same
6-hourly healthcheck deletes them after it reconciles. Safe by construction:
names are never reused, so an offline row can never come back to life. The step
warns rather than fails on a delete error — a red housekeeping step on the
workflow that keeps a required check unblocked is worse noise than the ghosts.

## Tuning

Knobs are declared in `docker-compose.local.yml`; you **set** them in
`~/.poindexter/bootstrap.toml`, not a `.env` — the operator stack has no `.env`
at all (`start-stack.sh`: "launch the Poindexter Docker stack without .env
files"). Same mechanism as the App ID in step 2 above: the script matches
`^\s*([a-z_]+)\s*=` and exports each key UPPER-CASED, so a lowercase
`ci_runner_cpus` becomes `CI_RUNNER_CPUS` for compose to substitute. A key set
only in the shell survives until the next reconcile and no further.

```bash
# Halve each runner's CPU cap, durably:
cat >> ~/.poindexter/bootstrap.toml <<'TOML'
ci_runner_cpus = "4"
TOML
bash ~/.poindexter/deploy/glad-labs-stack/scripts/start-stack.sh \
  up -d --force-recreate --no-deps github-runner-1 github-runner-2
```

Invoke `start-stack.sh` by absolute path from anywhere — it `cd`s to its own
`PROJECT_DIR` and `compose_project_name` is pinned, so there is no parallel-stack
fork. It must be the **deploy** checkout (`~/.poindexter/deploy/glad-labs-stack`),
not your dev tree or a worktree. Two costs to expect: a recreate **cancels any
in-flight self-hosted job** (a red check that is pure infra — re-run it with
`gh run rerun <id> --failed`), and each recreate leaves an `offline glads-pc-*`
registration behind, which the healthcheck prunes.

The budget is the **host** — 60 GB RAM / 32 threads, with the full Docker stack
resident. (Pre-Pop!_OS this section warned that the real cap was a 24 GB WSL2 VM
pinned by `.wslconfig`; that VM is gone, so size against the host directly.)

- **Resource caps:** `CI_RUNNER_CPUS` (default 8) and `CI_RUNNER_MEM` (default
  **8g**), applied to **each** runner. Memory: the services step runs xdist
  (`-n 4 --dist loadfile`), so peak is ~4 worker processes each importing the
  heavy tree (llama-index / langgraph / litellm), not the single subprocess the
  retired `--forked` mode used — 8g is sized for that and is why the worker
  count is fixed at 4 rather than `-n auto`. Raise `CI_RUNNER_MEM` and
  `-n` together or neither; workers get OOM-reaped otherwise
  (`node down: Not properly terminated` → pytest INTERNALERROR).
- **CPU, and why the default may be too high for you:** the cap is per runner,
  so two runners at the default commit **16 of 32 threads** whenever both are
  busy — on a box that is also running the production stack, Ollama and the
  image/video servers. The operator rig runs `ci_runner_cpus = "4"` for exactly
  that reason (2026-08-09): at 8 the pair pinned sustained all-core boost and
  pushed peak CPU package temp to ~96°C. Halving is close to free but not
  actually free — measured across real `unit-tests` runs (excluding the
  path-filtered sub-minute skips), the median went **3.3 min → 4.1 min** and the
  max **8.8 → 7.8**, against a `timeout-minutes: 25` budget. Roughly a quarter
  slower on the median, with ~6x headroom left; the step asks for 4 cores at
  `-n 4`, so it never wanted all 8. (Post-change sample is small, n=6.) Check
  your own headroom against that timeout before going lower.
  `CpuTemperatureHigh` / `CpuTemperatureBaselineDrift` (DB-rendered Prometheus
  rules) alert if cooling ever stops keeping up.
- **Concurrency / a third runner:** two cover a PR push plus the `main` push it
  triggers. If you routinely see jobs queue behind both, copy the
  `github-runner-2` block — new `container_name`, a new `ci-runner-N-cache`
  volume (**one cache volume per runner**: concurrent agents sharing a
  pip/poetry cache risks corrupting it, and the cache is only a speedup), and a
  matching entry in the top-level `volumes:` map. Watch host free RAM before
  going past three.
- **Failover latency vs cost:** the healthcheck cron (`0 */6 * * *`) bills ~1
  rounded-up minute per run (~120 min/month at 6h). Tighten for faster failover,
  loosen for fewer minutes.

## Troubleshooting

- **Job stuck "Queued" / "Waiting for a runner":** first check whether both
  runners are simply busy (two concurrent runs is the designed capacity — a
  third queues). If not, no online runner matches the labels:
  `docker compose -f docker-compose.local.yml logs github-runner-1 github-runner-2`;
  check the App private key is valid and the App install still has Administration
  access. Immediate unblock: `gh variable set CI_RUNNER_MODE off`.
- **Runner registers then immediately deregisters:** usually a bad App private
  key, or the App install is missing `Administration: write`.
- **Runner crash-loops with "already configured" / "Value cannot be null
  (Parameter 'configuredSettings')":** an ungraceful restart (Docker restart, PC
  sleep, a job killed mid-run) left a corrupt `/actions-runner/.runner`. The
  service `entrypoint` now wipes stale config on every start, so this
  self-recovers on the next restart. A container created _before_ that change
  needs one `bash scripts/start-stack.sh up -d --force-recreate github-runner-1`
  to adopt the new entrypoint.
- **A pile of `offline` `glads-pc-*` registrations:** expected between
  healthcheck runs — see [Registration pruning](#registration-pruning). Force an
  immediate sweep with `gh workflow run runner-healthcheck.yml`.
- **`unit-tests` red only on self-hosted, green on hosted:** a hosted-image
  assumption. The two `Free … disk` steps are already guarded behind
  `!vars.CI_RUNNER`; if a new step assumes the ubuntu image, guard it the same
  way.
- **Disk creep:** persistent runners accumulate caches. `docker exec
poindexter-ci-runner-1 df -h` (and `-2`); prune the `gladlabs-ci-runner-*-cache` volumes
  if needed.

## Extending to another workflow

To move another workflow onto the PC, add the **same seam** to its job(s) and
confirm the job has no hosted-image assumptions:

```yaml
runs-on: ${{ vars.CI_RUNNER && fromJSON(vars.CI_RUNNER) || 'ubuntu-latest' }}
```

Then run the guard locally before pushing:

```bash
python scripts/ci/ci_runner_seam_lint.py
```

It fails if the job declares `services:`/`container:`, uses a known
Docker-based action, or shells out to `docker` — the three ways a job stops
being seam-eligible. CI runs it too (security.yml's `action-pins` job), so a
violation cannot merge.

Two things to check that the lint cannot:

- **Actions must be native.** Everything currently on the seam uses only
  `actions/checkout`, `actions/setup-python`, `actions/setup-node`,
  `actions/upload-artifact` and `actions/create-github-app-token`, all proven
  on the fleet. A third-party action may be a Docker container action; if you
  cannot confirm it is native, leave the job hosted.
- **No PyYAML, no preinstalled toolchain.** The runner image is minimal —
  `python3 -c "import yaml"` fails there. A step that relies on a
  hosted-image convenience will break only once `CI_RUNNER` is set, which is
  exactly when nobody is watching.
- **`python` does not exist — only `python3`.** GitHub's hosted images ship a
  `python` shim; the runner image does not, so a bare `python foo.py` step
  dies with `python: command not found` (exit 127). `actions/setup-python`
  installs the shim, so jobs that use it are safe either way. The lint fires
  on the unguarded case. This is not hypothetical — it is exactly how
  `action-pins` failed the first time it ran on the fleet.

**Jobs that need the Docker daemon are NOT eligible for this seam.** The
runner containers mount no `/var/run/docker.sock`, so any `docker …` step or
GitHub Actions `services:`/`container:` block fails with "Cannot connect to
the Docker daemon". That is a security decision, not an oversight: these
runners execute raw PR code (dependabot branches included), and the socket
would let one compromised dependency drive the daemon that runs the
production stack — `docker run --privileged -v /:/host` is root on the host.
Precedent: `docker-build.yml`'s `build-worker` shipped on this seam (#2920),
stayed green only while the fleet was offline (hosted fallback), then went
red on every main push as soon as `runner-healthcheck` re-enabled
`CI_RUNNER`. It now builds hosted (measured ~3 min cold, well within hosted
disk) behind its own dormant `vars.CI_RUNNER_DOCKER` seam.

If a docker-daemon job ever genuinely outgrows hosted runners, provision a
**separate, isolated runner class** rather than mounting the socket into
these runners — and mind the label trap: GitHub dispatches a job to any
runner whose labels are a **superset** of its `runs-on` list, and every
Linux self-hosted runner automatically carries the system labels
`self-hosted` / `linux` / `x64`. A socketed runner registered with only
those labels would therefore also attract `unit-tests` jobs (raw PR code
again). Isolation means re-labeling in lockstep: give the pytest runners a
distinguishing custom label (compose `LABELS`), update the `CI_RUNNER`
variable **and** `runner-healthcheck.yml`'s `RUNNER_LABELS` to include it,
give the docker runner a different custom label, point `CI_RUNNER_DOCKER`
at that — and only then mount
`- /var/run/docker.sock:/var/run/docker.sock` on the docker runner alone.

## Security (load-bearing)

A self-hosted runner must **never** serve a public repo — a fork PR would run
arbitrary code on this machine. The public mirror `Glad-Labs/poindexter` has no
`CI_RUNNER` variable and no runner App, so its `unit-tests` seam resolves to
`ubuntu-latest` by construction and `runner-healthcheck` no-ops there (it's
guarded to `github.repository == 'Glad-Labs/glad-labs-stack'`). **Do not**
hardcode `self-hosted` in any workflow, and do not register these runners
against `poindexter`.
