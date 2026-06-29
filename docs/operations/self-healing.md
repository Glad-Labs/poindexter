# Self-Healing

Poindexter tries to **fix problems before it pages a human**. Every alert
should have an auto-resolution path that is exhausted first; paging is the last
resort, not the first response. This doc describes the self-heal machinery: the
brain's liveness probes, the host Recovery Agent, and the detect→act→escalate
loop they form.

## Principle: self-heal before paging

A probe that detects a problem should, where it safely can:

1. **Attempt recovery** (restart the container, re-run the launcher, reapply the
   compose spec).
2. **Bound the attempts** with a rolling cap so a genuinely broken thing can't
   trigger a restart loop.
3. **Escalate to a page** only when recovery fails or the cap is reached.

A _successful_ self-heal is recorded in `audit_log` (and surfaces on the
Findings / System Health dashboards) but does **not** page — the whole point is
that the operator doesn't have to care about transient failures.

## The detector / actor split

The brain daemon (`brain/`) runs as a Linux container. It can **detect**
almost anything (it has the Docker socket, the DB, and the network), but there
are host-level recovery actions it **cannot** perform itself:

- **`docker compose up` for Windows bind mounts.** A Linux container running
  `docker compose up` resolves relative bind sources against its own `/app`
  cwd and mangles Windows `C:\` paths to `/app/C:\...`. The daemon then
  auto-creates those as empty directories and silently wipes the service's real
  config. So the brain must not run compose-up itself on a Windows host.
- **Restarting a host process** that is launched by the OS scheduler (e.g. the
  MCP HTTP server's logon task).

For these, the brain **detects** and delegates the **act** to the host
**Recovery Agent**, which runs on the host where the binds and the scheduler
resolve correctly.

```
 ┌─────────────────────────┐         POST /recover            ┌──────────────────────┐
 │  brain probe (container) │  ──── {"service": "..."} ───▶    │  Recovery Agent (host)│
 │  detects the problem     │      Authorization: Bearer       │  port 9841            │
 └─────────────────────────┘                                   │  runs the host action │
            ▲                                                   └──────────┬───────────┘
            │ next cycle re-probes; cap escalates if unfixed              │
            └────────────────────────────────────────────────────────────┘
```

## The host Recovery Agent

`scripts/recovery-agent.py` — a stdlib-only HTTP server the brain POSTs to for
host-level recovery. It runs on the host (started windowless at logon by the
"Poindexter Recovery Agent" Scheduled Task via `scripts/recovery-agent.cmd`).

- **Bind:** `0.0.0.0:9841` (reachable from containers via
  `host.docker.internal`).
- **Auth:** `Authorization: Bearer <token>`, where the token is read from
  `POINDEXTER_RECOVERY_TOKEN` or the `poindexter_recovery_token` key in
  `~/.poindexter/bootstrap.toml`. The same token lives in `app_settings`
  (currently `mcp_http_probe_recovery_token`) so the brain probes can read it.
- **`GET /healthz`** → `200` liveness.
- **`POST /recover`** with `{"service": "<name>"}` → runs the action registered
  for that service.
- **`GET /tasks?name=<TaskName>&name=…`** (authenticated) → read-only status of
  the named host Scheduled Tasks: `{name, exists, enabled, state,
last_run_result}` per task. Lets the containerised brain see the host Task
  Scheduler it otherwise can't — see [Scheduled-tasks liveness](#scheduled-tasks-liveness).

### Action kinds

The agent's `SERVICES` registry maps each service name to an action kind:

| Service           | Kind      | Action                                                                        |
| ----------------- | --------- | ----------------------------------------------------------------------------- |
| `mcp-http`        | `task`    | `Start-ScheduledTask "Poindexter MCP HTTP"` — restart the MCP HTTP server.    |
| `compose-reapply` | `compose` | `start-stack.sh up -d --no-build` — reconcile drifted containers to the spec. |

Adding a recoverable surface = add a row to `SERVICES` (+ register the caller on
the brain side). The `compose` action is **fire-and-forget** (a reapply can take
well over a minute); the agent dispatches `start-stack.sh` and returns
immediately, and the calling probe confirms success on its next cycle.

`start-stack.sh` is located without hard-coding the clone's directory name:
`POINDEXTER_START_STACK` env → `~/.poindexter/deploy/*/scripts/start-stack.sh`
(the auto-synced deploy clone) → `~/.poindexter/scripts/start-stack.sh`. Git
Bash is resolved from `git`'s own location (never the PATH `bash`, which on
Windows is WSL and can't see Docker or the `C:\` binds).

## Liveness probes

The brain runs these every 5-minute cycle. Two patterns:

**HTTP/inspect probes** — actively check a surface, recover, cap, page:

| Probe                                        | Watches                                                                                       | Detect                                                                                                             | Recover                                                                                                                       | Escalate            |
| -------------------------------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| `brain/mcp_http_probe.py`                    | MCP HTTP server (`:8004`)                                                                     | `GET` discovery endpoint                                                                                           | launcher (host process) or host-recover (`mcp-http`)                                                                          | page after cap      |
| `brain/compose_drift_probe.py`               | container vs compose spec                                                                     | `docker inspect` vs YAML                                                                                           | host-recover (`compose-reapply`) — see below                                                                                  | page after cap      |
| `brain/health_probes.py` (`scheduled_tasks`) | host self-heal Scheduled Tasks                                                                | `GET /tasks` on the host agent                                                                                     | — (detect-only) — see below                                                                                                   | page after 3 cycles |
| `brain/docker_port_forward_probe.py`         | published host ports — 12 HTTP sidecars + Postgres `:5433` (`docker_port_forward_watch_list`) | internal-OK + external-FAIL: HTTP `GET`, or a credential-free libpq `SSLRequest` for `probe_type=postgres` entries | HTTP entry: `docker restart` → re-probe. DB entry, or any restart proven ineffective: **alert-only** (no restart) — see below | page after cap      |

**Heartbeat/freshness probes** — read the newest success row a service stamps in
`audit_log`; if it's too old, the service is wedged:

| Probe                           | Heartbeat event            | Stale → recover             | Escalate severity         |
| ------------------------------- | -------------------------- | --------------------------- | ------------------------- |
| `brain/offsite_backup_watch.py` | `offsite_backup_succeeded` | `docker restart` → re-check | critical (data-loss risk) |
| `brain/auto_embed_watch.py`     | `auto_embed_succeeded`     | `docker restart` → re-check | warning (search degrades) |

Minimal sidecar images (promtail, pyroscope) ship no shell or HTTP client, so an
in-container Docker `HEALTHCHECK` is impossible. Their liveness is an **external**
Prometheus rule instead (`up{job="..."} == 0` in
`infrastructure/prometheus/alerts/observability-sidecars.yml`).

## Docker port-forward recovery — restart vs alert-only

`docker_port_forward_probe` detects the Windows Docker Desktop / WSL2 NAT
host-port wedge — `host.docker.internal:<port>` is dead while the container is
healthy on the internal bridge — and recovers it. **The recovery action is not
a constant.** A `docker restart` re-spawns a stuck _per-container_ wslrelay
forward (the 2026-04-29 HTTP-sidecar incident), but it **cannot** fix a wedge
that lives in Docker Desktop's _host-side_ port proxy, and restarting a database
container severs every internal consumer's live connection.

That distinction was learned the hard way on **2026-06-29**: the host
port-publish subsystem broadly degraded (`:5433`, `:8002`, `:3000` all wedged);
the probe restarted `poindexter-postgres-local` 4+ times, every post-restart
re-probe still failed, and the connection churn hung the brain daemon and took
the alert plane down for ~45 min. (This falsified the 2026-06-21 design's
"restarting the shared DB is harmless" assumption.) So the probe now picks its
remedy per situation:

- **`recovery_action` per watch entry** (`"restart"` | `"alert_only"`). HTTP
  entries default to `restart`; database entries (`probe_type=postgres`) default
  to `alert_only` — the wedge is **detected but never restarted**. Instead the
  probe writes a `docker_port_forward_restart_skipped` `alert_events` row
  (`reason=db_recovery_policy`) and pages the operator with the only fix that
  works: clear the host proxy (restart Docker Desktop, or `wsl --shutdown` then
  relaunch). The default follows the structured `probe_type` field, not a
  container name, and an operator can override it per entry in
  `docker_port_forward_watch_list`.
- **Adaptive give-up** for `restart` entries. If a restart is accepted but the
  external re-probe still fails, the failure is counted; after
  `docker_port_forward_max_failed_recoveries_before_alert_only` (default **1**)
  consecutive failed recoveries the container switches to alert-only for
  `docker_port_forward_alert_only_backoff_minutes` (default **60**) instead of
  burning the restart cap on a remedy already proven ineffective. This is what
  protects HTTP entries (`:8002`, `:3000`) from the same host-side wedge. A
  healthy or successfully-recovered cycle resets the counter and clears the
  backoff (`reason=restart_ineffective_backoff` while it holds).

The rolling restart cap (3 per 60 min) still applies to the case it was built
for: a **flapping** forward that a restart genuinely recovers each cycle but
which keeps re-wedging.

## The watchdog's own liveness — cycle timeout + DB command timeout

The 2026-06-29 incident had a second half. The probe restarting the shared DB
was one failure; the other was that **the brain daemon then hung**. `run_cycle`
issued a query against the now-wedged Docker host-port proxy and the `await`
never returned — the daemon's single asyncio thread parked in `epoll_wait` for
~37 min. The main loop's `try/except` only catches **exceptions**; a hang raises
nothing, so the cycle never failed, the `brain.cycle_heartbeat` went stale, and
the only alert that fired was the out-of-band `BrainDeliveryDeadMansSwitch`
(Alertmanager-native, independent of the brain's own — also dark — dispatch
loop). A watchdog that can silently stop watching is the worst failure mode it
has, so the cycle now has two guards that convert a hang into a fast, accounted
failure:

- **Per-query `command_timeout` on the asyncpg pool**
  (`_create_brain_pool`). asyncpg's own **client-side** timer, so it fires even
  when the wedged proxy means the server never even sees the query — the exact
  2026-06-29 mechanism (a server-side Postgres `statement_timeout` would not,
  because the statement never arrives). Set by the
  `BRAIN_DB_COMMAND_TIMEOUT_SECONDS` env var (default **60s**). It is an
  env/constant, **not** an `app_settings` row, because the pool is created
  before settings can be read — same bootstrap tier as the database URL itself.
- **Whole-cycle ceiling** (`_run_cycle_with_watchdog`). `run_cycle` is wrapped
  in `asyncio.wait_for(..., timeout=cycle_timeout)`; a cycle that blows past the
  ceiling is **cancelled** (freeing the thread) and raises `TimeoutError`. This
  backstops any non-DB stuck `await` too. The ceiling is DB-tunable via
  `app_settings.brain_cycle_timeout_seconds` (default **240s**) — read at the
  top of each loop, so once settings are live an operator can retune it without
  a restart; it falls back to the `BRAIN_CYCLE_TIMEOUT_DEFAULT_SECONDS` constant
  if the read itself fails (and that read is, in turn, bounded by the pool's
  `command_timeout`). Generously above the normal sub-2-minute cycle so it only
  fires on a genuine hang, and below the 300s `CYCLE_SECONDS` interval so a hung
  cycle is abandoned before the next one is due.

A timed-out cycle is counted exactly like a raised one: the daemon stays
responsive, loops back round, and **retries next cycle** — so it self-recovers
the moment the wedge clears. If the hang is persistent, the same
`CYCLE_FAILURE_ALERT_THRESHOLD` (3 in a row) escalation that covers a
repeatedly-**raising** cycle pages the operator, and it pages through the
**stdlib `operator_notifier` failsafe** (`_page_operator_failsafe`, env-token
based) rather than the DB-backed `notify()` path — because a wedged DB is
exactly when that path can reach no one. The dead-man's switch remains the
ultimate backstop, but it should now never be the **only** thing that fires:
the brain notices its own stuck cycle first.

## Compose-drift host-recover

`compose_drift_probe` detects drift between `docker-compose.local.yml` and the
running containers (missing mounts / env / ports, changed image tag). On a
containerised brain it heals the drift through the host Recovery Agent:

1. Drift detected → per-service `audit_log` rows (always, for visibility).
2. If `compose_drift_host_recover_enabled` (default **true**) and the agent
   URL/token are configured and the rolling cap isn't exceeded:
   POST `{"service": "compose-reapply"}` → the agent runs `start-stack.sh up -d
--no-build` on the host, recreating only the config-hash-changed (drifted)
   services and leaving healthy containers (postgres, the brain) alone.
3. A successful dispatch is audit-logged, **not** paged. The next 5-min cycle
   re-probes: cleared → done; still drifted → another capped attempt.
4. **Cap reached** (`compose_drift_host_recover_cap_per_window` in
   `compose_drift_host_recover_window_minutes`) → critical page: the drift
   persists despite repeated reapplies, so a human is needed.
5. **POST failed** (agent down) → warning page: the recovery path itself is
   broken.

**Opt-in services are exempt from the missing-container check.** Two classes of
service are legitimately not running, so a missing container for them is never
drift (drift in their _other_ fields — mounts/env/ports/image — is still flagged
when they **are** up):

- **On-demand** services listed in `compose_drift_on_demand_services` (CSV,
  default `wan-server,image-gen-server`) — GPU-heavy backends the worker starts per
  job and lets exit.
- **Profile-gated** services whose compose `profiles:` are not in
  `compose_drift_active_profiles` (CSV, default empty). A `profiles:`-gated
  service only starts when the operator brings up its profile, so if that
  profile isn't active the container is _supposed_ to be absent. Empty default =
  every profiled service is treated as inactive (no false pages out of the box);
  list the profiles you actually run to restore crash-detection for their
  services. Incident 2026-06-21: `gpu-exporter` (`profiles: [linux-gpu]`)
  false-paged CRITICAL every cycle on the Windows host, where the host
  nvidia-smi exporter — not the profile-gated container — serves GPU metrics.

This is separate from `compose_drift_auto_recover_enabled` — the brain's _own_
`docker compose up` — which **stays off** on a Windows host because it mangles
the `C:\` binds (see the detector/actor split above).

## Scheduled-tasks liveness

The brain can't enumerate the host's Windows Task Scheduler from inside its
Linux container, so the host self-heal tasks themselves — the Recovery Agent,
the MCP HTTP launcher, the deploy-checkout sync, the Docker Engine watchdog —
were historically unwatched (the probe used to hard-fail with "needs
migration"). The `scheduled_tasks` probe (`brain/health_probes.py`) closes that
gap by asking the host Recovery Agent, which **can** see the scheduler:

1. Read the watch list from `scheduled_tasks_probe_watch_tasks` (CSV of host
   Scheduled Task names) plus the shared agent URL/token.
2. `GET /tasks?name=…` on the agent → per-task `{exists, enabled, state,
last_run_result}`.
3. Page (warning) when any watched task is **disabled** (`Settings.Enabled=False`
   or `State=Disabled` — the state `Set-ScheduledTask -Action` silently leaves a
   task in, taking the agent down with no alert), **missing**, or its **last run
   failed** (a result code outside the success set `{0, 1, 267009, 267011}`).
4. **Fail-open:** when the agent URL/token are unset _or_ the watch list is
   empty, the probe is advisory (`ok=true`) and never pages — an operator
   without the agent, or on a non-Windows host, sees no false alarms (mirrors
   compose-drift's host-recover fall-through).

Detection-only by design: the agent stays a dumb reflector (returns raw status;
the brain owns the page/no-page policy), and escalation is the brain's standard
probe debounce — page once after `ALERT_AFTER_FAILURES` consecutive failures,
then sit visibly-degraded. A human re-enables the task. (Auto-re-enable via a
new agent action is a possible future step — the agent already restarts tasks
for `mcp-http`.)

Example watch list:
`Poindexter Recovery Agent,Poindexter MCP HTTP,Poindexter-DeployCheckoutSync,Docker Engine Watchdog`.

## Settings reference

| Setting                                                       | Default                       | Meaning                                                                                                                   |
| ------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `compose_drift_host_recover_enabled`                          | `true`                        | Auto-heal compose drift via the host agent.                                                                               |
| `compose_drift_host_recover_cap_per_window`                   | `3`                           | Max reapplies before escalating to a page.                                                                                |
| `compose_drift_host_recover_window_minutes`                   | `60`                          | The rolling window for the cap.                                                                                           |
| `compose_drift_on_demand_services`                            | `wan-server,image-gen-server` | CSV of services started on demand — exempt from the missing-container check.                                              |
| `compose_drift_active_profiles`                               | (empty)                       | CSV of active compose `profiles:`. Services gated behind an unlisted profile are exempt from the missing-container check. |
| `compose_drift_auto_recover_enabled`                          | `false`                       | Brain-side `docker compose up` — keep OFF on Windows hosts.                                                               |
| `mcp_http_probe_recovery_url`                                 | (empty)                       | Recovery Agent endpoint, e.g. `http://host.docker.internal:9841/recover`. Shared by all host-recover probes.              |
| `mcp_http_probe_recovery_token`                               | secret                        | Bearer token matching the agent's `poindexter_recovery_token`.                                                            |
| `scheduled_tasks_probe_watch_tasks`                           | (empty)                       | CSV of host Scheduled Task names the `scheduled_tasks` probe checks via `GET /tasks`. Empty = advisory no-op.             |
| `offsite_backup_watch_enabled`                                | `true`                        | Backup-freshness probe.                                                                                                   |
| `auto_embed_watch_enabled`                                    | `true`                        | Embedder-freshness probe.                                                                                                 |
| `docker_port_forward_max_failed_recoveries_before_alert_only` | `1`                           | Consecutive failed recoveries before a `restart` entry switches to alert-only (adaptive give-up).                         |
| `docker_port_forward_alert_only_backoff_minutes`              | `60`                          | Minutes a container stays alert-only after the give-up trips, before one more restart is allowed.                         |

## Deploying the Recovery Agent

The agent is host-local. After changing `scripts/recovery-agent.py` / `.cmd`:

1. Copy them to the host (or re-point the "Poindexter Recovery Agent" Scheduled
   Task at the auto-synced deploy clone's copy so it updates on every sync).
2. Restart the Task; confirm `GET http://localhost:9841/healthz` → `200`.
3. Set `mcp_http_probe_recovery_url` +
   `mcp_http_probe_recovery_token` in `app_settings` if not already.
4. To enable host scheduled-task liveness checks, set
   `scheduled_tasks_probe_watch_tasks` to a CSV of the task names to watch (e.g.
   `Poindexter Recovery Agent,Poindexter MCP HTTP,Poindexter-DeployCheckoutSync,Docker Engine Watchdog`).
   Confirm with an authenticated `GET http://localhost:9841/tasks?name=Poindexter+MCP+HTTP`
   → per-task JSON status.

The brain probes are image-baked, so a probe code change needs an image rebuild,
not just a restart. The 10-min `deploy-checkout-sync.ps1` task does this
automatically — it rebuilds `brain-daemon` whenever a synced merge touches
`brain/`, then recreates the container onto the fresh image (see
`docs/operations/ci-deploy-chain.md`). For an immediate manual deploy:
`docker compose build brain-daemon && docker compose up -d brain-daemon`.

## Audit notes & known limitations

A periodic audit confirms every probe and recovery action still targets a
reachable endpoint — especially after infrastructure changes (a moved host
port, a retired container). Three structural facts keep most targets correct;
three known gaps are tracked here.

### Why most probe targets survive host changes

- **`localize_url()`** (`brain/docker_utils.py`) rewrites
  `localhost`/`127.0.0.1` → `host.docker.internal` at runtime, so a probe
  configured with a host-canonical URL reaches the host-published port from
  inside the container with no per-environment config.
- **Recovery actions key off container names** (`docker restart <name>`), not
  host ports, so a changed host-port publish never breaks a restart.
- **In-stack calls use compose service names** (`postgres:5432`,
  `prefect:4200`), independent of host-side port publishing.

The one place a stale host port can hide is the
`docker_port_forward_watch_list` setting, which carries explicit `host_port`
overrides — re-check that list after any host-port change.

### Known limitations

- **Operator-surface probing is host-routed and can false-positive on
  dual-stack services.** `operator_url_probe` reaches operator surfaces via
  `host.docker.internal`. A service published on both IPv4 (`0.0.0.0`) and IPv6
  (`[::]`) can have its IPv6 Docker proxy accept the TCP connection then drop it
  ("Server disconnected without sending a response") even when the service is
  healthy and reachable by its real in-network consumer (e.g. the trace store,
  Tempo, consumed through Grafana on the compose network). Treat a single such
  surface failing while its consumer works as a probe-path artifact, not an
  outage.
- **Operator-surface URLs drift when a backing container is retired.** Retiring
  or renaming a container leaves any operator-surface URL that named it stale,
  and the brain then pages "Operator surface unreachable" every cycle. When you
  retire a container, sweep the operator-surface URL settings in the same
  change.
