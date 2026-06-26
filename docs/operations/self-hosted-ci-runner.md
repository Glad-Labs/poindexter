# Self-hosted CI runner (unit-tests → $0 cloud minutes)

## Why

`glad-labs-stack` is a **private** repo, so every GitHub-hosted Actions minute
is billed. A 14-day sample projected to **~33,000 min/month against a 3,000-min
allowance** (~10× over), and the `unit-tests` workflow alone was **~60%** of it
(~2,100 wall-clock min over 3 days). The fix is to run that one heavy workflow
on Matt's always-on PC instead of in GitHub's cloud — self-hosted runner minutes
are **not** billed.

Scope is deliberately narrow: **only `unit-tests` moves to self-hosted.** Every
other workflow (jest-unit, integration-db, security, public-mirror-safety,
grafana-panels-lint, playwright-e2e, migrations-smoke, releases, syncs) stays on
`ubuntu-latest`. `migrations-smoke` in particular stays hosted as a cheap
always-available liveness check.

## How it fits together

```
 unit-tests.yml  ──reads──▶  vars.CI_RUNNER  ──set?──▶ runs on Matt's PC (self-hosted)
                                    │         ──unset?─▶ runs on ubuntu-latest (hosted)
                                    ▲
        runner-healthcheck.yml (hosted, cron) ──reconciles── live runner status
                                    ▲
        docker-compose.local.yml: github-runner-1  (the runner)
```

1. **The `runs-on` seam.** `unit-tests.yml` resolves its runner like this:

   ```yaml
   runs-on: ${{ vars.CI_RUNNER && fromJSON(vars.CI_RUNNER) || 'ubuntu-latest' }}
   ```

   When the repo variable `CI_RUNNER` is set to `["self-hosted","linux","x64"]`
   the job runs on the PC; when it's unset (the default, **and always on the
   public `poindexter` mirror**) it falls back to `ubuntu-latest`. The
   `&&`/`||` short-circuit means `fromJSON` never receives an empty string.

2. **The runner.** One persistent container (`github-runner-1`) in
   `docker-compose.local.yml`, behind the `ci-runner` compose profile so it
   never starts with the default stack. Image: `myoung34/github-runner`.

3. **The self-healing control loop.** `runner-healthcheck.yml` runs **hosted**
   (it must — a "is the PC up?" probe can't run on the PC) every 6 hours and
   reconciles `CI_RUNNER` against live runner status. See the control surface
   below.

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
#    starts the runner with the stack.
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

### 3. Bring the runner up

`compose_profiles` includes `ci-runner`, so the runner comes up with the rest of
the stack — and **`start-stack.sh` exports the multiline PEM into the container's
`APP_PRIVATE_KEY` for you** (it sources `~/.poindexter/ci-runner-app.pem`, the
same file-sourced-secret pattern it uses for the grafana webhook token). So the
normal bring-up is just:

```bash
bash scripts/start-stack.sh up -d                            # whole stack, runner included
# …or recreate just the runner:
bash scripts/start-stack.sh up -d --force-recreate github-runner-1
docker compose -f docker-compose.local.yml ps github-runner-1
# Confirm registration:
gh api repos/Glad-Labs/glad-labs-stack/actions/runners --jq '.runners[] | {name, status, busy}'
```

**A `docker compose up` (and every deploy-checkout-sync) _recreates_ the
container — it does not merely `restart` it — so the env is rebuilt from scratch
each time.** That is exactly why `start-stack.sh` has to source the PEM itself: a
one-off manual `export` does **not** survive a recreate. Before this was
automated, a start-stack-driven deploy brought the runner up with an empty
`APP_PRIVATE_KEY` and the entrypoint crash-looped (`All of APP_ID,
APP_PRIVATE_KEY and APP_LOGIN must be specified`). If you ever bring the runner
up _without_ start-stack (direct compose), pass the key yourself:

```bash
CI_RUNNER_APP_PRIVATE_KEY="$(cat ~/.poindexter/ci-runner-app.pem)" \
  docker compose -f docker-compose.local.yml --profile ci-runner up -d github-runner-1
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

**PC-down behaviour:** in `auto` mode, if the runner goes offline the healthcheck
unsets `CI_RUNNER` within its cron window (≤6h), so `unit-tests` — a required
check — falls back to hosted and PRs don't hang. For instant failover after a
known outage, run `gh workflow run runner-healthcheck.yml` (or bump the cron).

## Tuning

All in `docker-compose.local.yml` / `.env`. **The budget that matters is the
WSL2 VM, not the 64 GB host.** `.wslconfig` pins WSL2 to **24 GB / 16 procs**,
and the entire Docker stack lives in it — `docker info` shows the cap, and
`docker stats --no-stream` shows the stack already using ~14–15 GB (so ~8–9 GB
free, less when image-gen spikes). Size the runner to fit that, not the host.

- **Resource caps:** `CI_RUNNER_CPUS` (default 8) and `CI_RUNNER_MEM` (default
  **8g**) for the one runner. `unit-tests` is serial (`pytest --forked` runs one
  subprocess at a time, no xdist), so real peak is only a few GB; 8g is generous
  and fits the free headroom. Lower `CI_RUNNER_MEM` if image-gen/Wan and CI ever
  pressure the VM together.
- **Concurrency / a second runner:** one runner handles unit-tests fine — a
  second concurrent run just queues behind it. Only add a `github-runner-2` if
  you first give WSL2 more room: bump `memory=` in `~/.wslconfig` (you have
  64 GB physical — 36–40 GB is reasonable), `wsl --shutdown`, then copy the
  `github-runner-1` block (new `container_name` + a `ci-runner-2-cache` volume).
- **Failover latency vs cost:** the healthcheck cron (`0 */6 * * *`) bills ~1
  rounded-up minute per run (~120 min/month at 6h). Tighten for faster failover,
  loosen for fewer minutes.

## Troubleshooting

- **Job stuck "Queued" / "Waiting for a runner":** no online runner matches the
  labels. `docker compose -f docker-compose.local.yml logs github-runner-1`;
  check the App private key is valid and the App install still has Administration
  access. Immediate unblock: `gh variable set CI_RUNNER_MODE off`.
- **Runner registers then immediately deregisters:** usually a bad App private
  key, or the App install is missing `Administration: write`.
- **Runner crash-loops with "already configured" / "Value cannot be null
  (Parameter 'configuredSettings')":** an ungraceful restart (Docker/WSL restart,
  PC sleep, a job killed mid-run) left a corrupt `/actions-runner/.runner`. The
  service `entrypoint` now wipes stale config on every start, so this
  self-recovers on the next restart. A container created _before_ that change
  needs one `bash scripts/start-stack.sh up -d --force-recreate github-runner-1`
  to adopt the new entrypoint.
- **`unit-tests` red only on self-hosted, green on hosted:** a hosted-image
  assumption. The two `Free … disk` steps are already guarded behind
  `!vars.CI_RUNNER`; if a new step assumes the ubuntu image, guard it the same
  way.
- **Disk creep:** persistent runners accumulate caches. `docker exec
poindexter-ci-runner-1 df -h`; prune the `gladlabs-ci-runner-*-cache` volumes
  if needed.

## Extending to another workflow

Only `unit-tests` is in scope. To move another workflow later, add the **same
seam** to its job(s) and confirm the job has no hosted-image assumptions:

```yaml
runs-on: ${{ vars.CI_RUNNER && fromJSON(vars.CI_RUNNER) || 'ubuntu-latest' }}
```

If that workflow uses GitHub Actions `services:`/`container:` (e.g.
`integration-db`'s Postgres), also mount the host Docker socket into the
runners (`- /var/run/docker.sock:/var/run/docker.sock`) — it's intentionally
omitted today because `unit-tests` doesn't need it.

## Security (load-bearing)

A self-hosted runner must **never** serve a public repo — a fork PR would run
arbitrary code on this machine. The public mirror `Glad-Labs/poindexter` has no
`CI_RUNNER` variable and no runner App, so its `unit-tests` seam resolves to
`ubuntu-latest` by construction and `runner-healthcheck` no-ops there (it's
guarded to `github.repository == 'Glad-Labs/glad-labs-stack'`). **Do not**
hardcode `self-hosted` in any workflow, and do not register these runners
against `poindexter`.
