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

### 1. Create the PAT (the only manual step)

GitHub doesn't allow PAT creation via API, so this is the one thing that needs
your hands. Create a **fine-grained PAT**:

- **Resource owner:** Glad-Labs · **Repository access:** Only select →
  `glad-labs-stack`
- **Repository permissions:**
  - **Administration:** Read and write _(register/list self-hosted runners)_
  - **Variables:** Read and write _(the healthcheck flips `CI_RUNNER`)_

Copy the `github_pat_…` value.

### 2. Wire the token into its two homes

```bash
# a) container registration — in the stack .env
echo 'GH_RUNNER_PAT=github_pat_xxx' >> .env
echo 'COMPOSE_PROFILES=ci-runner'  >> .env      # make the runners start with the stack

# b) the healthcheck workflow — as a repo secret (same PAT value)
gh secret set CI_RUNNER_ADMIN_PAT --repo Glad-Labs/glad-labs-stack --body 'github_pat_xxx'

# c) optional: routine Discord ping when CI auto-flips
gh secret set DISCORD_OPS_WEBHOOK_URL --repo Glad-Labs/glad-labs-stack --body "$DISCORD_OPS_WEBHOOK_URL"
```

### 3. Bring the runners up

```bash
docker compose -f docker-compose.local.yml --profile ci-runner up -d
docker compose -f docker-compose.local.yml ps github-runner-1
# Confirm registration:
gh api /repos/Glad-Labs/glad-labs-stack/actions/runners --jq '.runners[] | {name, status, busy}'
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
free, less when SDXL spikes). Size the runner to fit that, not the host.

- **Resource caps:** `CI_RUNNER_CPUS` (default 8) and `CI_RUNNER_MEM` (default
  **8g**) for the one runner. `unit-tests` is serial (`pytest --forked` runs one
  subprocess at a time, no xdist), so real peak is only a few GB; 8g is generous
  and fits the free headroom. Lower `CI_RUNNER_MEM` if SDXL/Wan and CI ever
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
  check the PAT hasn't expired (fine-grained PATs expire — rotate in both homes).
  Immediate unblock: `gh variable set CI_RUNNER_MODE off`.
- **Runner registers then immediately deregisters:** usually a bad/expired PAT
  or missing `Administration: write`.
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
`CI_RUNNER` variable and no admin PAT, so its `unit-tests` seam resolves to
`ubuntu-latest` by construction and `runner-healthcheck` no-ops there (it's
guarded to `github.repository == 'Glad-Labs/glad-labs-stack'`). **Do not**
hardcode `self-hosted` in any workflow, and do not register these runners
against `poindexter`.
