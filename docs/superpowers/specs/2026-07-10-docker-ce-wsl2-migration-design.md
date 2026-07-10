# Design: Migrate the container tier off Docker Desktop to native docker-ce in WSL2

- **Date:** 2026-07-10
- **Status:** ❌ **NO-GO** — Phase 0 validation failed 2026-07-10 (see "Phase 0 result" below); shelved. Bare-metal Linux (#295) is the durable cure.
- **Author:** Claude (with Matt)
- **Area:** Host infrastructure (WSL2 / Docker), self-healing watchdog, host tooling
- **Scope decision:** **Container tier only** (Approach B). Host-native GPU
  servers and Windows Task-Scheduler orchestration stay on Windows.

## Phase 0 result — NO-GO (2026-07-10)

Ran against a throwaway `dockerce-spike` Ubuntu-24.04 distro (docker-ce 29.6.1,
docker-compose v5.3.1, nvidia-container-toolkit 1.19.1). **Verdict: NO-GO** —
both networking mechanisms this design depends on are non-functional on this box.

| Gate                                       | Result                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GATE 3 — GPU in container**              | ✅ **PASS** — `docker run --gpus all … nvidia-smi -L` lists both the RTX 5090 and 3090 under docker-ce. GPU-PV works as expected.                                                                                                                                                                                                                                                             |
| **Mirrored networking**                    | ❌ **Won't initialize** — `networkingMode=mirrored` → WSL `CreateVm/ConfigureNetworking/0x8007054f`, falls back to `None`. Matches the 06-30 "none" observation. Prime suspect: **Tailscale** (documented WSL-mirrored conflict; not removable — it's core to the setup). Not confirmed: stopping Tailscale to retest was deferred when the effort was shelved.                               |
| **GATE 1 — Windows host → container port** | ❌ **FAIL in every mode** — NAT + DD-running `0/100`; mirrored-fallback-None `0/50`; **clean NAT with Docker Desktop fully stopped** (service + GUI + docker-desktop distro all down, verified `DDback=0 svc=Stopped`) **still `0/40`**. WSL `localhostForwarding` does not forward the docker-ce distro's published ports to Windows `localhost` on this box, independent of Docker Desktop. |
| **Distro-IP direct**                       | ✅ `192.168.34.40:18099` → HTTP 200. The container serves to Windows over the NAT network; only `localhost`-forwarding is broken → a host-side `netsh portproxy` (`localhost:PORT` → `distro-IP:PORT`) _would_ restore `localhost` reachability.                                                                                                                                              |
| **GATE 2 — container → host Ollama**       | ❌ FAIL under NAT (`host-gateway` ≠ Windows loopback; needs the Ollama `0.0.0.0`-bind lever). Not testable under mirrored (didn't init).                                                                                                                                                                                                                                                      |

**Why NO-GO:** the design's core premise — docker-ce makes `localhost:5433` work
without the DD proxy — is falsified here for both NAT and mirrored. The only
viable path is a `netsh portproxy` layer, but the distro's NAT IP changes each
WSL boot, so it needs an admin boot-task to re-resolve the IP and rebuild ~18
portproxy rules — re-introducing a fragile host-side proxy, the opposite of the
clean win intended.

**Decision (Matt, 2026-07-10):** shelve; keep the status quo. The original wedge
is low-impact and self-heals (alert-only #1990 + CLI token cache #2017). The
durable cure remains **bare-metal Linux ([#295](https://github.com/Glad-Labs/glad-labs-stack/issues/295))**.

**If ever revived:** (1) root-cause the mirrored init failure first — stop
Tailscale and retry `networkingMode=mirrored`; if that's the cause, pursue a
Tailscale/mirrored coexistence config for the clean path. (2) The Phase 0
validation script `scripts/wsl-migration/validate-docker-ce.sh` has two bugs
found in execution: GATE 1 probes `localhost` _inside_ the distro (the wedge is
the Windows-host boundary — it must be probed from Windows), and it uses an
invalid container name `_v_http` (Docker names must start alphanumeric). Fix
before reuse. Stack was fully restored and the spike distro destroyed after
testing.

## Problem

Docker Desktop's host-side port proxy (`com.docker.backend` on the WSL2 NAT
backend) periodically wedges a single published host port: it accepts the TCP
SYN, sends SYN/ACK, and never completes the handshake — so
`localhost:<port>` from Windows dies while the container stays healthy and
in-container (compose-net) clients are unaffected. On the DB port `5433` this
strands host-native tooling (the `poindexter` CLI, host `pytest` DB fixtures,
the `postgres` MCP).

This has recurred ~5 times in ~3 weeks (2026-06-22, -29, -30, and again
2026-07-10). The self-heal chain is complete and working as designed —
`docker_port_forward_probe` correctly declines to `docker restart` the DB
(a restart cannot fix a host-side proxy and once hung the brain daemon) and
escalates **alert-only** (Glad-Labs/glad-labs-stack#1990), the CLI reports the
condition honestly (#2017 + the `cli_token_cache.json` ride-through), and the
brain has a cycle watchdog (#1991). But every mitigation is a workaround: the
wedge itself keeps returning because **Docker Desktop's proxy is structurally
in the host↔container path**, and the documented cures (`wsl --shutdown`, full
DD restart) are disruptive, have _failed to clear `5433` specifically_ in past
episodes, and — because `wsl --shutdown` crash-restarts Postgres with no
checkpoint — reset `pg_stat` counters and blind autovacuum (#2239).

### Live evidence captured 2026-07-10 (this triage)

- `Test-NetConnection 127.0.0.1:5433` and `localhost:5433` both fail; the
  socket table shows a pile of `SynReceived`/`SynSent` on `::1` and
  `127.0.0.1:5433` (the proxy-wedge fingerprint).
- Trigger: `poindexter-postgres-local` was **recreated ~7 h earlier**
  (`RestartCount=0`, fresh container); DD's proxy failed to re-bind the host
  listener on recreate. Host booted 3 days prior — not a reboot artifact.
- Other host ports (`:8002`) were fine → **localized single-port** wedge, not a
  subsystem collapse.
- Internal Postgres healthy (`pg_isready` → accepting connections); the entire
  compose-net business tier (worker, brain, pipeline, Grafana) was unaffected.
- Active victims were all host-native python: a host `pytest` run and `pythonw`
  background processes (the `postgres` MCP), stuck in `SynSent` to `:5433`.

## Root cause

The wedge lives in Docker Desktop's Windows-side userspace port proxy
(`com.docker.backend`), which sits between Windows `localhost:<port>` and the
container inside the `docker-desktop` WSL2 distro. No `.wslconfig` change fixes
it on Docker Desktop: `networkingMode=mirrored` was tried 2026-06-30 and made it
worse (DD ignores mirrored — its proxy stays in front — and host→container
dropped 100%). The durable cure is to **remove Docker Desktop's proxy from the
path** by running the engine as **native docker-ce inside a standard WSL2 Linux
distro**, where port publishing rides WSL's own networking (and, optionally,
mirrored mode, which native docker-ce honors) instead of `com.docker.backend`.

This spec is the "real cure" foreshadowed in
[`2026-06-21-postgres-host-port-wedge-self-heal-design.md`](2026-06-21-postgres-host-port-wedge-self-heal-design.md)
and in the operator's own `.wslconfig` comments.

## Current-state inventory (feasibility spike, 2026-07-10)

**Host / WSL / GPU**

- WSL 2.6.3.0, kernel 6.6.87.2, Windows 11 24H2 (26200). Docker Desktop
  **4.81.0**, engine 29.6.1 (WSL2 backend), context `desktop-linux`.
- **Only one WSL distro exists: `docker-desktop`** (DD-managed). No
  general-purpose Linux distro yet — the migration is greenfield, not
  modify-in-place.
- `.wslconfig`: `memory=32GB, processors=16, swap=8GB,
autoMemoryReclaim=gradual`, **NAT** mode, `localhostForwarding=true`.
- GPUs: **RTX 5090 (32 GB) + RTX 3090 (24 GB)**, driver 610.62. GPU-PV works
  today — host `nvidia-smi` sees both, and containers use the GPU through WSL2's
  `/dev/dxg`. A fresh WSL2 distro inherits the same path (this is why docker-ce
  in WSL is viable where a full VM's DDA passthrough would not be).

**Container tier — 18 published host ports (the consumer surface to preserve)**

```
8002  worker            3000  grafana          5433  postgres-local (→5432)
3010  langfuse-web       8080  glitchtip-web    9091  prometheus (→9090)
9093  alertmanager       3100  loki             3200  tempo (+4317-4318 OTLP)
4200  prefect-server     4040  pyroscope        3002  uptime-kuma (→3001)
5003  postiz (→5000)     18443 pgadmin (→80)    8001  speaches (→8000)
9836  image-gen-server   9840  wan-server       7880-7882 livekit
```

**Volume footprint — 37 volumes / ~46 GB, but most is disposable**

- **Precious (must migrate, ~2.5 GB logical):** `gladlabs-postgres-local-data`
  holds `poindexter_brain` (1.24 GB), `prefect` (1.2 GB), `langfuse` (14 MB),
  plus the Postiz DB volume. **The DB is also carrying ~15 leftover
  `poindexter_unit_*` / `poindexter_test_*` / `poindexter_e2e_*` / `flatten_*`
  junk databases** (~250 MB) that the migration should simply not copy.
- **Useful (best-effort copy):** grafana (dashboards are repo-provisioned, so
  mostly annotations/users), glitchtip-db, langfuse-clickhouse + minio,
  uptime-kuma, postiz-uploads.
- **Disposable (start fresh):** prometheus, loki, tempo, pyroscope,
  promtail-positions, alertmanager-data, ci-runner cache, voice-agent-cache.

**Host-native tier that stays on Windows (out of scope, but must remain reachable)**

- Task-Scheduler services: primary Ollama `:11434` + 3090-pinned vision Ollama
  `:11435` (`ollama-vision-gpu1.ps1`, `OLLAMA_KEEP_ALIVE=-1`), the
  `nvidia-smi`/GPU exporters, MCP-HTTP, recovery-agent. Containers reach the
  Ollama instances today via `host.docker.internal:11434/11435`, routed by the
  DB setting `plugin.llm_provider.litellm.config.model_api_base_overrides`.
- Orchestration that assumes Docker Desktop: `scripts/docker-watchdog.ps1`
  ("Docker Engine Watchdog" task), `scripts/start-stack.sh`,
  `scripts/deploy-worker.ps1`, and the brain's `docker_port_forward_probe` /
  `compose_drift_probe`.

## Target architecture

| Concern                                        | Today                                         | After                                                                                                |
| ---------------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Engine                                         | Docker Desktop in the `docker-desktop` distro | **docker-ce** in a new `Ubuntu-24.04` WSL2 distro (systemd)                                          |
| Host↔container path                            | `com.docker.backend` proxy (**wedges**)       | WSL-native port publishing (NAT relay first; mirrored optional post-cutover) — **no DD proxy**       |
| Published ports                                | `5433, 8002, 3000…` via DD                    | **identical ports** via docker-ce → host tooling zero-touch                                          |
| GPU                                            | 5090/3090 via `/dev/dxg` → containers         | same `/dev/dxg` via `nvidia-container-toolkit` in the new distro                                     |
| Host-native Ollama `:11434/:11435` + exporters | Windows Task Scheduler                        | **unchanged** — containers reach them via `host.docker.internal:host-gateway` (or mirrored loopback) |
| Orchestration                                  | DD watchdog + `wsl --shutdown`                | watchdog targets `wsl -d Ubuntu -- systemctl … docker`                                               |

**Design property — same ports = zero-touch consumers.** docker-ce republishes
the identical host ports, so `bootstrap.toml`'s `localhost:5433` DSN, the CLI,
the MCP servers, and host `pytest` need **no config change**. The only rewire is
host _orchestration_.

### Networking sequencing (important constraint)

`.wslconfig` networking mode is **global** and **mirrored is incompatible with a
running Docker Desktop** (DD ignores it and breaks — verified 2026-06-30). During
the parallel-run soak DD must keep working as the rollback, so it needs NAT.
Therefore:

1. Run docker-ce **under the existing NAT** first. docker-ce does **not** use
   `com.docker.backend`; its published ports ride WSL's own port forwarding, a
   separate mechanism from the wedgy DD proxy — so **NAT + docker-ce may already
   be wedge-free** without mirrored.
2. Adopt **mirrored only at/after DD retirement** (Phase 3+), as an enhancement
   if NAT + docker-ce proves insufficient — never while DD is the rollback.

Phase 0 measures whether NAT + docker-ce is wedge-free; mirrored is a
documented fallback, not a prerequisite.

## Phased plan

Each phase is independently reversible. **Docker Desktop stays installed and
serving prod until Phase 3 cutover, and installed-but-stopped through the soak.**

### Phase 0 — Validation spike (go/no-go gate, zero real data)

Stand up a throwaway `Ubuntu-24.04` distro with docker-ce + nvidia-container-
toolkit under the **current NAT** (DD untouched). Prove, or stop:

1. **Wedge-free host publish** — a throwaway container publishing a test port is
   reachable from Windows `localhost` reliably under connection churn, with **no
   `com.docker.backend` in the path** (docker-ce, not DD).
2. **Container → host-native Ollama** — a container can reach `:11434` and
   `:11435` on the Windows host (via `--add-host=host.docker.internal:host-gateway`
   under NAT; note the resolved address for the settings update in Phase 3).
3. **GPU-in-container** — `docker run --gpus all … nvidia-smi` lists both GPUs.

If (1) fails under NAT → test mirrored in the throwaway distro (DD stopped for
the test window) before deciding. If (2) or (3) fail → abort with findings; the
migration is not viable as specced. **No precious data is touched in Phase 0.**

### Phase 1 — Stand up docker-ce in parallel

Install `Ubuntu-24.04`, docker-ce + compose v2 + `nvidia-container-toolkit`,
enable systemd (`/etc/wsl.conf [boot] systemd=true`) and the docker service.
Mount/checkout the repo inside the distro (or reuse the Windows checkout via
`/mnt/c`, decided in the plan). DD remains the live prod engine.

### Phase 2 — Tiered volume migration

- **Precious:** `pg_dumpall --globals-only` (roles/passwords for the
  `poindexter` role) + per-DB `pg_dump` of `poindexter_brain`, `prefect`,
  `langfuse`, and the Postiz DB → restore into a fresh docker-ce Postgres
  (same image tag as the compose pin → same major version). **Do not** copy the
  ~15 junk `*_unit_*/test/e2e/flatten_*` DBs.
- **Useful:** `tar` each volume out of the DD engine and into the docker-ce
  volume (grafana, glitchtip-db, langfuse-clickhouse + minio, uptime-kuma,
  postiz-uploads). Best-effort; failures are non-blocking.
- **Disposable:** create empty; the stack repopulates (prometheus/loki/tempo/
  pyroscope/promtail/alertmanager/caches).

### Phase 3 — Cutover

Bring the full stack up under docker-ce on the **same published ports** via
`start-stack.sh` (run inside the distro). `bootstrap.toml`'s `localhost:5433`
DSN is unchanged and now resolves proxy-free. Verify: a real content-pipeline
task end-to-end, host CLI (`poindexter tasks list`), GPU in the worker,
Grafana + alerts. Update `model_api_base_overrides` only if Phase 0 showed the
container→Ollama address changed (e.g. `host.docker.internal` alias vs mirrored
loopback). **Stop — do not uninstall — the DD stack.** Optionally flip to
mirrored now (DD is stopped, so its NAT dependency is moot).

### Phase 4 — Rewire host orchestration

- `docker-watchdog.ps1`: recover the docker-ce engine
  (`wsl -d Ubuntu -- sudo systemctl restart docker`) instead of DD +
  `wsl --shutdown`.
- Brain `docker_port_forward_probe`: its premise (a DD proxy that wedges) no
  longer applies to the DB entry — simplify to a plain liveness probe or retire
  the DB watch entry. `compose_drift_probe` points at the same compose file
  under the new engine.
- `start-stack.sh` / `deploy-worker.ps1`: target the docker-ce context; confirm
  the deploy-checkout-sync task still applies cleanly.

### Rollback

DD is retained (installed, volumes intact) through a soak period. Rollback =
start the DD stack; it already owns `localhost:5433`, DSN unchanged. Near-instant
and complete for the soak window.

## What this fixes vs. what it does not (honest scope)

- **Fixes:** the DD _port-proxy_ wedge class in **both** directions —
  host-tool→container (`localhost:5433/8002/…`) and container→host-Ollama
  (`host.docker.internal:11434/11435`). Removes the main _reason_ for
  `wsl --shutdown` (clearing the proxy), so fewer forced WSL-VM crashes and
  fewer #2239 autovacuum-blinding stat resets.
- **Does not fix:** a WSL2 _VM-level_ hang (the unconfirmed `dxg`/GPU-churn
  hypothesis in the `project_vram_oversub_docker_crashes` memory). That is a separate
  class; this migration does not claim to cure it. The `docker-watchdog.ps1`
  big-hammer recovery stays relevant for full-VM wedges.

## Risks & mitigations

| Risk                                                                      | Likelihood | Mitigation                                                                                                                                                               |
| ------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| NAT + docker-ce still wedges (needs mirrored)                             | Low–med    | Phase 0 measures it; mirrored is a tested fallback applied post-DD-retirement                                                                                            |
| Container can't reach host-native Ollama                                  | Med        | Phase 0 gate #2; `--add-host=host-gateway` or mirrored loopback; `model_api_base_overrides` is a DB setting, cheap to repoint                                            |
| GPU-in-container regression                                               | Low        | Phase 0 gate #3; toolkit is standard CUDA-on-WSL                                                                                                                         |
| Postgres logical restore drift (roles, extensions e.g. pgvector/pgcrypto) | Med        | `--globals-only` for roles; ensure the docker-ce Postgres image ships the same extensions the compose pin does; verify with a row-count + extension check before cutover |
| systemd-in-WSL service reliability                                        | Low        | Supported on 2.6.3; validate `docker.service` survives distro restart in Phase 1                                                                                         |
| Disk: new distro vhdx needs ~50 GB                                        | Low        | Host has headroom; DD's ~90 GB reclaimed only after soak                                                                                                                 |
| mirrored breaks the DD rollback if flipped early                          | —          | Sequencing rule: mirrored only after DD is stopped                                                                                                                       |

## Open decisions (for Matt)

- **Soak length** before uninstalling DD and reclaiming ~90 GB (proposed: ~1
  week of wedge-free `localhost:5433` under normal churn).
- **DD end-state:** uninstall after soak, or keep installed-but-stopped as a
  permanent fallback.
- **Repo location for the docker-ce stack:** reuse the Windows checkout via
  `/mnt/c` (simplest, but `/mnt/c` I/O is slower) vs a distro-native clone
  (faster, another checkout to keep in sync). Resolve in the implementation plan.

## Success criteria / definition of done

1. `localhost:5433` stays reachable under connection churn for the full soak
   period (no `SynReceived` wedge).
2. A content-pipeline task runs end-to-end under docker-ce with GPU.
3. Host CLI, host `pytest` DB fixtures, and the `postgres` MCP all reach the DB
   without the workaround.
4. Grafana, alerts, and self-heal are green; `docker-watchdog.ps1` recovers the
   docker-ce engine in a drill.
5. DD stopped; after soak, optionally uninstalled and its space reclaimed.

## References

- [`2026-06-21-postgres-host-port-wedge-self-heal-design.md`](2026-06-21-postgres-host-port-wedge-self-heal-design.md)
  — the self-heal chain this supersedes as the durable cure.
- [`docs/operations/self-healing.md`](../../operations/self-healing.md) §
  "Docker port-forward recovery — restart vs alert-only".
- Glad-Labs/glad-labs-stack #1814, #1990, #2017, #1991 (self-heal chain, merged).
- Glad-Labs/glad-labs-stack #295 — umbrella: Linux migration for Matt's
  workstation (this container-tier move is a bounded subset / on-ramp).
- Memory: `project_vram_oversub_docker_crashes` (five documented wedge
  episodes + the mirrored-under-DD dead end).
