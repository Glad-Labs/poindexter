# docker-ce WSL2 Migration Implementation Plan

> ## ⛔ SHELVED — DO NOT EXECUTE (2026-07-10)
>
> Phase 0 validation was a **NO-GO**: mirrored networking won't initialize on
> the target box (`ConfigureNetworking/0x8007054f` → falls back to `None`), and
> WSL `localhostForwarding` doesn't forward docker-ce ports to Windows
> `localhost` (proven `0/40` with Docker Desktop **fully stopped**). Only a
> fragile `netsh portproxy` fallback works — not worth it over the low-impact,
> self-healed status quo. Full findings in the spec's "Phase 0 result"
> ([`2026-07-10-docker-ce-wsl2-migration-design.md`](../specs/2026-07-10-docker-ce-wsl2-migration-design.md)).
> **Bare-metal Linux (#295) is the durable cure.** This plan is kept for
> reference only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the container tier off Docker Desktop to native docker-ce in a new Ubuntu WSL2 distro, eliminating the recurring `com.docker.backend` host-port-proxy wedge while keeping the Windows-native GPU servers and Task-Scheduler orchestration in place.

**Architecture:** A fresh `Ubuntu-24.04` WSL2 distro runs docker-ce (systemd-managed) with `nvidia-container-toolkit` for GPU. It stands up in parallel with Docker Desktop (DD) on **temporary** host ports, receives a tiered volume migration (logical `pg_dump` for precious DBs, `tar` for useful volumes, fresh for disposable), then cuts over to the **canonical** published ports once DD is stopped and releases them. Networking goes NAT during the parallel soak (DD needs NAT) and flips to mirrored at cutover. Host tooling is zero-touch because the ports are unchanged post-cutover.

**Tech Stack:** WSL2 (2.6.3), Ubuntu-24.04, docker-ce + compose-v2, nvidia-container-toolkit, systemd-in-WSL, PostgreSQL 16 (`pgvector/pgvector:pg16`), PowerShell (host orchestration), Python (brain probe).

## Execution model (READ FIRST)

This is **not** an autonomous agent loop. It runs against Matt's **live production box**. Therefore:

- **🔴 HUMAN GATE** steps (stopping DD, the DB cutover, `wsl --shutdown`, uninstalling DD) are executed by Matt, or by an agent only with Matt watching and an explicit go. Never batch past a 🔴.
- For host-provisioning steps there is no "unit test"; the **verification command + expected output** is the test. Run it and confirm the output before proceeding.
- Repo **code changes** (Phase 4: the watchdog and brain probe) get real tests where the logic is testable (the brain probe has pytest coverage; follow TDD there).
- **Phase 0 is a hard go/no-go.** If any Phase 0 gate fails and neither documented lever recovers it, STOP and re-plan — do not start Phase 1.

## Global Constraints

- **DD stays installed and serving prod until Phase 3 cutover, and installed-but-stopped through the soak.** It is the rollback. Do not uninstall before Phase 5.
- **Mirrored networking is incompatible with a running Docker Desktop** (verified 2026-06-30 — DD ignores it and host→container drops 100%). Never enable mirrored while DD must serve. Mirrored flips only at/after cutover.
- **Same canonical published ports post-cutover** (`5433, 8002, 3000, 3010, 8080, 9091, 9093, 3100, 3200, 4200, 4040, 3002, 5003, 18443, 8001, 9836, 9840, 7880-7882`) so `bootstrap.toml`, the CLI, MCP servers, and pytest need no config change.
- **During the parallel run the docker-ce stack uses TEMP ports** (canonical port + 10000, e.g. `15433`, `18002`) to avoid colliding with DD's live bindings.
- **Postgres image is `pgvector/pgvector:pg16`** on both sides — same major + pgvector; `pgcrypto` is available in the base image. Verify both extensions exist post-restore.
- **Precious data = `poindexter_brain`, `prefect`, `langfuse` (main Postgres) + the Postiz DB.** Do **not** copy the ~15 `poindexter_unit_*` / `poindexter_test_*` / `poindexter_e2e_*` / `flatten_*` junk databases.
- **Mirror-safety:** the plan and any operator-specific runbook live under `docs/superpowers/` (stripped from the public mirror). Committed helper scripts under `scripts/wsl-migration/` stay **parameterized/generic** (distro name + ports as args/env, no secrets) so they are safe to ship; if a script must embed operator specifics, add it to `scripts/sync-to-github.sh`.
- **Repo checkout for the stack:** distro-native clone used as `POINDEXTER_DEPLOY_ROOT` (fast I/O; matches the existing DEPLOY-checkout pattern). Not `/mnt/c` (slow bind-mount I/O for `src/cofounder_agent:/app`).
- **Commit style:** conventional commits; end messages with the Co-Authored-By trailer. All repo changes via this branch's PR (#2253).

---

## Phase 0 — Validation spike (go/no-go gate)

Goal: prove the three feasibility unknowns against a **throwaway** distro before touching any real data or DD.

### Task 0.1: Validation helper script (repo, mirror-safe)

**Files:**

- Create: `scripts/wsl-migration/validate-docker-ce.sh`

**Interfaces:**

- Produces: a script `validate-docker-ce.sh <ollama_host> <ollama_port>` that runs three gates and prints `GATE n: PASS|FAIL`.

- [x] **Step 1: Write the validation script**

```bash
#!/usr/bin/env bash
# validate-docker-ce.sh — Phase 0 gates for the Docker-Desktop -> docker-ce
# migration. Run INSIDE the target WSL distro (docker-ce active). Generic:
# takes the host Ollama endpoint as args so it carries no operator specifics.
set -uo pipefail
OLLAMA_HOST="${1:-host.docker.internal}"
OLLAMA_PORT="${2:-11434}"
fail=0

echo "== GATE 3: GPU-in-container =="
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L; then
  echo "GATE 3: PASS"; else echo "GATE 3: FAIL"; fail=1; fi

echo "== GATE 1: wedge-free host port publish (churn 200x) =="
docker run -d --rm --name _v_http -p 18099:80 nginx:alpine >/dev/null
sleep 3
ok=0; for i in $(seq 1 200); do
  curl -fsS -m 2 http://localhost:18099/ >/dev/null 2>&1 && ok=$((ok+1)); done
docker stop _v_http >/dev/null 2>&1
echo "  reachable $ok/200"
if [ "$ok" -ge 199 ]; then echo "GATE 1: PASS"; else echo "GATE 1: FAIL"; fail=1; fi

echo "== GATE 2: container -> host Ollama ($OLLAMA_HOST:$OLLAMA_PORT) =="
if docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:latest \
    -fsS -m 5 "http://$OLLAMA_HOST:$OLLAMA_PORT/api/tags" >/dev/null; then
  echo "GATE 2: PASS"; else echo "GATE 2: FAIL"; fail=1; fi

echo "== RESULT: $([ $fail -eq 0 ] && echo ALL-PASS || echo HAS-FAILURES) =="
exit $fail
```

- [x] **Step 2: Commit**

```bash
git add scripts/wsl-migration/validate-docker-ce.sh
git commit -m "chore(wsl-migration): phase-0 docker-ce validation gates

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 0.2: Throwaway distro + gates 1 & 3 under NAT (DD untouched)

**🔴 HUMAN GATE — creates a WSL distro; DD stays running on NAT.**

- [ ] **Step 1: Create a throwaway Ubuntu distro**

Run:

```powershell
wsl --install -d Ubuntu-24.04 --name dockerce-spike --no-launch
wsl -d dockerce-spike -- bash -lc "echo up"
```

Expected: `up` (distro boots). (If `--name` is unsupported on this WSL build, install `Ubuntu-24.04` and rename via export/import; note and continue.)

- [ ] **Step 2: Install docker-ce + nvidia-container-toolkit in the spike distro**

Run (inside the distro):

```bash
wsl -d dockerce-spike -u root -- bash -lc '
  apt-get update -qq && apt-get install -y ca-certificates curl gnupg
  install -m0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  printf "[boot]\nsystemd=true\n" > /etc/wsl.conf
'
wsl --terminate dockerce-spike
wsl -d dockerce-spike -u root -- bash -lc 'systemctl is-active docker || (systemctl start docker; sleep 3; systemctl is-active docker)'
```

Expected: final line prints `active`.

- [ ] **Step 3: Run gates 1 & 3**

Run:

```bash
wsl -d dockerce-spike -u root -- bash -lc 'cd /mnt/c/Users/mattm/glad-labs-website && bash scripts/wsl-migration/validate-docker-ce.sh'
```

Expected: `GATE 3: PASS` (both GPUs listed) and `GATE 1: PASS` (reachable 199-200/200 — proves docker-ce publishes to Windows `localhost` with no `com.docker.backend` in the path). GATE 2 will FAIL here (loopback-bound Ollama unreachable under NAT) — expected; validated under mirrored in 0.3.

### Task 0.3: Gate 2 under mirrored, in a DD-stopped window

**🔴 HUMAN GATE — briefly stops DD and flips mirrored, then reverts.**

- [ ] **Step 1: Stop DD and enable mirrored**

Run:

```powershell
# Quit Docker Desktop from the tray (or:)
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Copy-Item $HOME\.wslconfig $HOME\.wslconfig.bak-premirror -Force
Add-Content $HOME\.wslconfig "`n[wsl2-spike-test]`n"   # marker only
# Set mirrored:
(Get-Content $HOME\.wslconfig) -replace '(?m)^# NETWORKING.*$','' | Set-Content $HOME\.wslconfig
Add-Content $HOME\.wslconfig "networkingMode=mirrored"
wsl --shutdown
Start-Sleep 10
```

- [ ] **Step 2: Re-run validation (gate 2 focus)**

Run:

```bash
wsl -d dockerce-spike -u root -- bash -lc 'systemctl start docker; sleep 3; cd /mnt/c/Users/mattm/glad-labs-website && bash scripts/wsl-migration/validate-docker-ce.sh host.docker.internal 11434'
```

Expected: `GATE 2: PASS` (container reaches host Ollama `/api/tags`). **If FAIL under mirrored** → apply lever: set the host Ollama instances to bind all interfaces (`OLLAMA_HOST=0.0.0.0` in the user env + a Windows Firewall allow rule scoped to the WSL subnet), restart the Ollama tasks, re-test. Record which lever was needed (mirrored-alone vs mirrored+0.0.0.0) — Phase 3 depends on it.

- [ ] **Step 3: Revert to NAT + restart DD (restore prod)**

Run:

```powershell
Copy-Item $HOME\.wslconfig.bak-premirror $HOME\.wslconfig -Force
wsl --shutdown
Start-Sleep 10
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

Expected: DD relaunches; `docker ps` (default context) lists the prod stack within ~60s.

### Task 0.4: Go/No-Go record

- [ ] **Step 1: Record the decision**

Append a dated "Phase 0 result" block to the spec (`docs/superpowers/specs/2026-07-10-docker-ce-wsl2-migration-design.md`): gates 1/2/3 pass/fail, the Ollama-reach lever needed, and GO or NO-GO. Commit. Destroy the spike distro: `wsl --unregister dockerce-spike`. **If NO-GO, stop here.**

---

## Phase 1 — Stand up docker-ce (parallel to DD, NAT)

### Task 1.1: Provision the production distro

**🔴 HUMAN GATE — new distro; DD keeps serving.**

- [ ] **Step 1: Create `poindexter-dockerce` distro and install the engine**

Repeat Task 0.2 Steps 1-2 with distro name `poindexter-dockerce`. Verify:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc 'docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L'
```

Expected: both GPUs listed.

- [ ] **Step 2: Confirm systemd auto-starts docker across a distro restart**

Run:

```bash
wsl --terminate poindexter-dockerce
wsl -d poindexter-dockerce -u root -- bash -lc 'sleep 5; systemctl is-active docker'
```

Expected: `active` (no manual start needed — critical for the watchdog).

### Task 1.2: Distro-native DEPLOY checkout + bootstrap

**Files:**

- Uses: `~/.poindexter/bootstrap.toml` (host), `docker-compose.local.yml`

- [ ] **Step 1: Clone the repo inside the distro as the DEPLOY root**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc '
  git clone https://github.com/Glad-Labs/glad-labs-stack.git /opt/poindexter-deploy
  mkdir -p /root/.poindexter
  cp /mnt/c/Users/mattm/.poindexter/bootstrap.toml /root/.poindexter/bootstrap.toml
  ls -la /opt/poindexter-deploy/docker-compose.local.yml'
```

Expected: the compose file path lists. (Checkout ref should match the current prod DEPLOY ref — align in execution.)

- [ ] **Step 2: Verify bootstrap parses and env exports**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc 'cd /opt/poindexter-deploy && bash -c "set -a; source <(grep -E \"^[a-z_]+ *=\" ~/.poindexter/bootstrap.toml | sed -E \"s/ *= */=/\"); echo DATABASE_URL set: \${DATABASE_URL:+yes}"'
```

Expected: `DATABASE_URL set: yes`.

---

## Phase 2 — Tiered volume migration (DD live; docker-ce on TEMP ports)

### Task 2.1: Dump precious databases from the DD Postgres

**Files:** dump lands in a host dir reachable from both engines (`C:\Users\mattm\.poindexter\migration\`).

- [ ] **Step 1: Dump roles + the three precious main DBs (exclude junk)**

Run (host, DD context):

```powershell
$mig = "$HOME\.poindexter\migration"; New-Item -ItemType Directory -Force $mig | Out-Null
docker exec poindexter-postgres-local pg_dumpall -U poindexter --globals-only > "$mig\globals.sql"
foreach ($db in @('poindexter_brain','prefect','langfuse')) {
  docker exec poindexter-postgres-local pg_dump -U poindexter -Fc -d $db -f "/tmp/$db.dump"
  docker cp "poindexter-postgres-local:/tmp/$db.dump" "$mig\$db.dump"
}
Get-ChildItem $mig | Select-Object Name,Length
```

Expected: `globals.sql` + three `.dump` files (`poindexter_brain` ~1.2 GB, `prefect` ~1.2 GB, `langfuse` small). **No** `*_unit_*`/`test`/`flatten_*` files.

### Task 2.2: Bring up docker-ce Postgres on a temp port and restore

- [ ] **Step 1: Start ONLY Postgres under docker-ce on temp port 15433**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc '
  cd /opt/poindexter-deploy
  set -a; source <(grep -E "^[a-z_]+ *=" ~/.poindexter/bootstrap.toml | sed -E "s/ *= */=/"); set +a
  docker compose -f docker-compose.local.yml up -d postgres-local
  docker compose -f docker-compose.local.yml exec -T postgres-local sh -c "until pg_isready -U poindexter; do sleep 1; done" '
```

Note: temp-port publish is set by a Phase-2 compose override (`docker-compose.migrate.yml` mapping `15433:5432`) added in execution; do not publish `5433` while DD holds it.
Expected: `pg_isready` succeeds.

- [ ] **Step 2: Restore globals + the three DBs**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc '
  cd /opt/poindexter-deploy
  MIG=/mnt/c/Users/mattm/.poindexter/migration
  docker compose -f docker-compose.local.yml exec -T postgres-local psql -U poindexter -d postgres < "$MIG/globals.sql" || true
  for db in poindexter_brain prefect langfuse; do
    docker compose -f docker-compose.local.yml exec -T postgres-local createdb -U poindexter "$db" 2>/dev/null || true
    docker compose -f docker-compose.local.yml exec -T postgres-local pg_restore -U poindexter -d "$db" --no-owner < "$MIG/$db.dump"
  done '
```

Expected: restores complete (pg_restore may emit benign warnings; no fatal errors).

- [ ] **Step 3: Verify extensions + a row count sanity check**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc '
  cd /opt/poindexter-deploy
  docker compose -f docker-compose.local.yml exec -T postgres-local psql -U poindexter -d poindexter_brain -tAc "SELECT extname FROM pg_extension ORDER BY 1"
  docker compose -f docker-compose.local.yml exec -T postgres-local psql -U poindexter -d poindexter_brain -tAc "SELECT count(*) FROM posts"
  docker compose -f docker-compose.local.yml exec -T postgres-local psql -U poindexter -d poindexter_brain -tAc "SELECT count(*) FROM app_settings" '
```

Expected: `vector` and `pgcrypto` present; `posts` and `app_settings` counts match the DD side (cross-check with `docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tAc "SELECT count(*) FROM posts"`).

### Task 2.3: Copy the "useful" volumes (best-effort)

- [ ] **Step 1: Tar each useful volume out of DD and into docker-ce**

Run (per volume in: grafana-data, glitchtip-db-data, langfuse-clickhouse-data, langfuse-minio-data, uptime-kuma-data, postiz-uploads):

```powershell
$mig = "$HOME\.poindexter\migration"
$vols = @('gladlabs-grafana-data','gladlabs-glitchtip-db-data','glad-labs-website_langfuse-clickhouse-data','glad-labs-website_langfuse-minio-data','gladlabs-uptime-kuma-data','gladlabs-postiz-uploads')
foreach ($v in $vols) {
  docker run --rm -v "${v}:/from" -v "${mig}:/backup" alpine tar czf "/backup/$v.tgz" -C /from .
}
```

Then restore each into the matching docker-ce volume:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc '
  MIG=/mnt/c/Users/mattm/.poindexter/migration
  for v in gladlabs-grafana-data gladlabs-glitchtip-db-data glad-labs-website_langfuse-clickhouse-data glad-labs-website_langfuse-minio-data gladlabs-uptime-kuma-data gladlabs-postiz-uploads; do
    docker volume create "$v" >/dev/null
    docker run --rm -v "$v:/to" -v "$MIG:/backup" alpine sh -c "tar xzf /backup/$v.tgz -C /to" || echo "WARN: $v copy failed (non-blocking)"
  done '
```

Expected: each volume restored; warnings are non-blocking.

### Task 2.4: Postiz DB (separate container)

- [ ] **Step 1: Dump + restore the Postiz Postgres**

Run:

```powershell
docker exec poindexter-postiz-postgres pg_dump -U postiz -Fc -d postiz -f /tmp/postiz.dump 2>$null; docker cp poindexter-postiz-postgres:/tmp/postiz.dump "$HOME\.poindexter\migration\postiz.dump"
```

(Confirm the Postiz DB container name + role/db from the compose `postiz` block in execution; restore mirrors Task 2.2 into the docker-ce Postiz Postgres.)
Expected: `postiz.dump` present; restore completes.

---

## Phase 3 — Cutover (🔴 major human gate)

### Task 3.1: Quiesce writers

**🔴 HUMAN GATE.**

- [ ] **Step 1: Pause the pipeline to stop new DB writes**

Run:

```powershell
docker exec poindexter-prefect-worker bash -lc "echo pausing" ; docker stop poindexter-prefect-worker poindexter-worker poindexter-brain-daemon
```

Expected: those three stop (no new pipeline/brain writes). Grafana/observability may keep running.

- [ ] **Step 2: Final delta re-dump of `poindexter_brain` + `prefect`**

Re-run Task 2.1 Step 1 for `poindexter_brain` and `prefect` only, and Task 2.2 Step 2 restore (drop+recreate those two DBs first). This captures writes since the first dump.
Expected: fresh restore; re-verify `posts`/`app_settings`/`pipeline_tasks` counts match.

### Task 3.2: Stop the DD stack (release canonical ports)

**🔴 HUMAN GATE.**

- [ ] **Step 1: Bring the DD stack down (keep volumes)**

Run:

```powershell
cd C:\Users\mattm\glad-labs-website; bash scripts/start-stack.sh down
docker ps  # expected: no poindexter-* containers under DD
```

Expected: DD stack down; ports `5433/8002/...` released. DD app stays installed.

### Task 3.3: Flip mirrored + bring up docker-ce on canonical ports

**🔴 HUMAN GATE — `wsl --shutdown`.**

- [ ] **Step 1: Enable mirrored (if Phase 0 required it) and restart WSL**

Run:

```powershell
Copy-Item $HOME\.wslconfig "$HOME\.wslconfig.bak-precutover" -Force
# set networkingMode=mirrored (replace the NAT comment block)
wsl --shutdown; Start-Sleep 10
```

- [ ] **Step 2: Bring up the full stack under docker-ce on canonical ports**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc '
  cd /opt/poindexter-deploy
  export POINDEXTER_DEPLOY_ROOT=/opt/poindexter-deploy
  set -a; source <(grep -E "^[a-z_]+ *=" ~/.poindexter/bootstrap.toml | sed -E "s/ *= */=/"); set +a
  docker compose -f docker-compose.local.yml up -d '
```

(No temp-port override now — canonical `5433:5432` etc.)
Expected: all services start; `docker compose ps` shows them healthy within a few minutes.

### Task 3.4: Cutover verification

- [ ] **Step 1: Host tooling reaches the DB proxy-free**

Run:

```powershell
(Test-NetConnection 127.0.0.1 -Port 5433 -WarningAction SilentlyContinue).TcpTestSucceeded
docker exec poindexter-worker poindexter tasks list  # or host CLI: poindexter tasks list
```

Expected: `True`, and `tasks list` returns without `CredentialStoreUnreachable`.

- [ ] **Step 2: GPU + container→Ollama + a real pipeline task**

Run:

```bash
wsl -d poindexter-dockerce -u root -- bash -lc 'cd /opt/poindexter-deploy && docker compose -f docker-compose.local.yml exec -T worker nvidia-smi -L && docker compose -f docker-compose.local.yml exec -T worker curl -fsS -m5 http://host.docker.internal:11434/api/tags >/dev/null && echo OLLAMA-OK'
```

Expected: GPUs listed, `OLLAMA-OK`. Then enqueue one content task and confirm it completes end-to-end (Prefect UID at `localhost:4200`, a new `posts`/`pipeline_versions` row).

- [ ] **Step 3: Grafana + alerts green**

Verify `localhost:3000` renders and no false `docker_port_forward` / worker-down alerts fire.

### Task 3.5: Repoint Ollama override if needed

- [ ] **Step 1: Update `model_api_base_overrides` only if the reach-address changed**

If Phase 0 required the `0.0.0.0`-bind lever, update the setting:

```bash
docker exec poindexter-worker poindexter settings set plugin.llm_provider.litellm.config.model_api_base_overrides '{"ollama/qwen3-vl:30b":"http://host.docker.internal:11435"}'
```

Expected: setting persists; `qa.vision` routes to the pinned instance. If mirrored made `host.docker.internal:11435` reachable unchanged, no edit needed.

---

## Phase 4 — Orchestration rewire (repo commits; PR #2253)

### Task 4.1: Repoint `docker-watchdog.ps1` at docker-ce

**Files:**

- Modify: `scripts/docker-watchdog.ps1` (`$DOCKER_EXE`, `Test-DockerEngine`, `Start-DockerDesktop`→`Start-DockerCe`, `Invoke-HealthCheck`)

**Interfaces:**

- Produces: a watchdog whose health probe is `wsl -d poindexter-dockerce -- systemctl is-active docker` and whose recovery is `wsl -d poindexter-dockerce -u root -- systemctl restart docker` (then the existing `Reset-WslBackend` big-hammer for VM-level wedges).

- [ ] **Step 1: Replace the DD-specific engine control**

Change `$DOCKER_EXE` usage: health = `wsl -d $Distro -- systemctl is-active docker` returns `active`; light recovery = `wsl -d $Distro -u root -- systemctl restart docker`; keep `Reset-WslBackend` (`wsl --shutdown`) as the escalation for a fully-wedged VM; drop `Start-DockerDesktop`. Add a `$Distro = "poindexter-dockerce"` param.

```powershell
function Test-DockerEngine { (wsl -d $Distro -- systemctl is-active docker) -eq 'active' }
function Restart-DockerCe { Write-Log "WARN" "Restarting docker.service in $Distro"; wsl -d $Distro -u root -- systemctl restart docker; Start-Sleep 5 }
```

- [ ] **Step 2: Drill — kill docker in the distro, confirm recovery**

Run:

```powershell
wsl -d poindexter-dockerce -u root -- systemctl stop docker
powershell -File scripts\docker-watchdog.ps1 -RunOnce   # add a -RunOnce switch if absent
wsl -d poindexter-dockerce -- systemctl is-active docker
```

Expected: watchdog restarts docker; final `active`.

- [ ] **Step 3: Commit**

```bash
git add scripts/docker-watchdog.ps1
git commit -m "fix(watchdog): manage docker-ce in WSL distro, not Docker Desktop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4.2: Update the brain port-forward probe (TDD)

**Files:**

- Modify: `brain/docker_port_forward_probe.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py`

**Interfaces:**

- Produces: the DB watch entry no longer assumes a DD proxy. Post-migration `localhost:5433` is proxy-free, so the probe's DB entry becomes a plain liveness check (internal `pg_isready`) with no `docker restart` / `alert_only` proxy-wedge branch for the DB.

- [ ] **Step 1: Write the failing test**

```python
def test_db_entry_is_liveness_only_post_ce(monkeypatch):
    # Given a DB watch entry, the probe must NOT classify a host-port failure
    # as a proxy wedge (no com.docker.backend under docker-ce): it reports
    # liveness from the internal check only.
    probe = DockerPortForwardProbe(entries=[db_entry()])
    result = probe.evaluate(host_reachable=False, internal_reachable=True)
    assert result.status == "ok"        # internal healthy => ok
    assert result.recovery_action is None
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_docker_port_forward_probe.py::test_db_entry_is_liveness_only_post_ce -v`
Expected: FAIL.

- [ ] **Step 3: Implement — make the DB entry liveness-only**

Gate the proxy-wedge branch on a `proxy_backend` flag that is `False` for docker-ce; for DB entries with internal reachable, return `ok` / no recovery.

- [ ] **Step 4: Run the probe test module, expect PASS**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_docker_port_forward_probe.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add brain/docker_port_forward_probe.py src/cofounder_agent/tests/unit/brain/test_docker_port_forward_probe.py
git commit -m "fix(brain): DB port-forward watch is liveness-only under docker-ce (no proxy)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4.3: `start-stack.sh` + `deploy-worker.ps1` target docker-ce

**Files:**

- Modify: `scripts/start-stack.sh` (run inside the distro; `POINDEXTER_DEPLOY_ROOT` default), `scripts/deploy-worker.ps1` (rebuild via `wsl -d poindexter-dockerce -- docker compose build`)

- [ ] **Step 1: Make `start-stack.sh` distro-aware**

Add a guarded path: when `WSL_DISTRO_NAME=poindexter-dockerce`, use `POINDEXTER_DEPLOY_ROOT=/opt/poindexter-deploy`. Keep the DD path working for rollback.

- [ ] **Step 2: Point `deploy-worker.ps1` at docker-ce**

Replace the DD `docker compose build/up` invocation with `wsl -d poindexter-dockerce -u root -- bash -lc "cd /opt/poindexter-deploy && docker compose -f docker-compose.local.yml up -d --build poindexter-prefect-worker"`.

- [ ] **Step 3: Verify a worker rebuild**

Run `powershell -File scripts\deploy-worker.ps1` → Expected: worker image rebuilds and the container comes back healthy under docker-ce.

- [ ] **Step 4: Commit**

```bash
git add scripts/start-stack.sh scripts/deploy-worker.ps1
git commit -m "chore(stack): start-stack + deploy-worker target docker-ce distro

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4.4: deploy-checkout-sync → distro-native DEPLOY root

**Files:**

- Modify: the deploy-checkout-sync script/task backing `Poindexter-DeployCheckoutSync`

- [ ] **Step 1: Sync into the distro checkout**

Update the sync apply step to `git -C /opt/poindexter-deploy fetch && reset --hard` inside the distro (via `wsl -d poindexter-dockerce`), replacing the Windows-path apply. Verify one sync cycle updates `/opt/poindexter-deploy` and restarts the worker.

- [ ] **Step 2: Commit** (message: `chore(deploy): sync DEPLOY checkout into the docker-ce distro`).

### Task 4.5: Re-register the "Docker Engine Watchdog" scheduled task

- [ ] **Step 1: Point the task at the rewired watchdog**

Re-register via the existing `background-services.ps1` (or `schtasks /Change`) so "Docker Engine Watchdog" runs the docker-ce-aware `docker-watchdog.ps1 -Distro poindexter-dockerce`. Verify: `Get-ScheduledTask "Docker Engine Watchdog"` → `Ready/Running`; force one run; confirm no error in the watchdog log.

---

## Phase 5 — Soak + decommission

### Task 5.1: Soak (proposed ~1 week) with rollback ready

- [ ] **Step 1: Daily wedge check**

Run daily: `(Test-NetConnection 127.0.0.1 -Port 5433 -WarningAction SilentlyContinue).TcpTestSucceeded` under normal load + a churn loop (200x connect). Expected: no `SynReceived` wedge for the full soak.

- [ ] **Step 2: Rollback runbook (if the soak fails)**

Documented, near-instant: `wsl -d poindexter-dockerce -- .../start-stack.sh down`; restore `~/.wslconfig` (NAT) from `.bak-precutover`; `wsl --shutdown`; start DD; DD stack owns `5433` again (DSN unchanged). The DD volumes were never deleted.

### Task 5.2: Decommission DD (🔴 human decision, post-soak)

- [ ] **Step 1: After a clean soak — Matt's call: uninstall DD or keep stopped**

If uninstall: back up nothing further (data already migrated), uninstall Docker Desktop, `wsl --unregister docker-desktop`, reclaim ~90 GB. If keep-as-fallback: leave installed + AutoStart off. Record the choice in the spec.

---

## Self-Review

**Spec coverage:** Every spec section maps to tasks — root cause/mechanism → Phase 0 gates; inventory (ports/volumes/DB/host-native) → Global Constraints + Phase 2; target arch (same ports, mirrored-later, host-gateway) → Global Constraints + Phase 3; phased plan → Phases 0-5; orchestration rewire → Phase 4; rollback + soak → Phase 5; the "does not fix" caveat → `Reset-WslBackend` retained in Task 4.1. Open decisions: soak length (Task 5.1, ~1 week default), DD end-state (Task 5.2), repo location (Global Constraints — distro-native `/opt/poindexter-deploy`).

**Placeholder scan:** Runtime-discovered values (host-gateway resolution, exact Postiz container name/role) are shown as _commands to determine them_, not fake literals, and flagged for execution-time confirmation — not TODOs. No "add error handling"/"write tests for the above" placeholders.

**Type/name consistency:** distro name `poindexter-dockerce` and DEPLOY root `/opt/poindexter-deploy` are consistent across Phases 1-5; temp-port convention (`canonical+10000`) consistent; `validate-docker-ce.sh` gate names consistent between Task 0.1 and 0.2/0.3.

**Known execution-time confirmations (not gaps):** the Phase-2 temp-port compose override (`docker-compose.migrate.yml`) is created in execution; the Postiz container/role names are read from the compose `postiz` block; the brain probe's exact `evaluate()` signature is matched to the current source when writing Task 4.2's test.
