# Self-Healing

Poindexter tries to **fix problems before it pages a human**. Every alert
should have an auto-resolution path that is exhausted first; paging is the last
resort, not the first response. This doc describes the self-heal machinery: the
brain's liveness probes, the host Recovery Agent, the detect→act→escalate loop
they form, and the rule-driven **firefighter** that generalizes that loop across
every alert on the dispatch path.

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

## Deterministic firefighter (detect → act → verify → escalate)

The probes below are the _original_ self-heal: each one hard-codes its own
recovery for the surface it watches, on the 5-minute cycle. The **firefighter**
is the generalization — a rule-driven `detect → act → verify → escalate` loop
that runs on the **alert-dispatch path** (the brain's 30-second
`alert_dispatcher.poll_and_dispatch`), so _any_ alert can earn an auto-recovery
without a bespoke probe. It lives in `brain/remediation/` (`registry.py` /
`rules.py` / `engine.py`) and is wired into the dispatcher.

The loop, when an alert is about to page:

1. **Detect.** The dispatcher holds an `alert_events` row it's about to send.
2. **Match.** `engine.evaluate_for_dispatch` looks up a `remediation_rules` row
   for the alert (exact `alertname` first, then `match_regex` over
   alertname/fingerprint). A matched rule acts deterministically. **No rule →
   the gated LLM long-tail path** (Plan B, below); only if that abstains or is
   disabled does the alert page as usual, so the firefighter stays invisible to
   unconfigured alerts unless the long-tail engages.
3. **Act.** The matched rule names an action in the **action registry** (below).
   The engine runs it, writes a `remediation_action` row to `audit_log`, and — if
   the action ran OK — **holds the page**. The `alert_events` row is marked
   `remediating: <action> (run <id>)` instead of sent.
4. **Verify.** On a later poll cycle (once the rule's `verify_after_seconds`
   grace has elapsed), `engine.run_verify_scan` asks _did it work?_ The signal is
   `alert_dedup_state.last_seen_at`: the dispatcher bumps it every time the alert
   re-fires (even when suppressed), keyed by the same fingerprint the engine
   stored. Not advanced past the moment we acted → **resolved, silently**
   (`remediation_verify` row, `result=resolved`, no page). Advanced → **still
   firing → page now** (`result=still_firing`), because the fix didn't hold.
5. **Escalate.** An action that couldn't even run pages immediately — there's
   nothing to wait for. A tripped circuit breaker or rate cap pages as usual —
   the firefighter steps aside rather than hammering a broken thing.

Everything rides existing tables — no new state store. `remediation_rules` holds
the rules; `audit_log` (`event_type IN ('remediation_action','remediation_verify')`)
is both the durable history and the circuit-breaker's memory; `alert_dedup_state`
is the "still firing?" oracle.

### The action registry

`brain/remediation/registry.py` maps an `action_name` to an executor. Executors
**must be idempotent, reversible, and blast-radius-bounded, and must never raise**
into the loop (they return `ActionResult(status="failed", …)` instead). v1 ships
two, each wrapping a primitive the brain already owns:

| `action_name`        | Params                    | Does                                                                                                                |
| -------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `restart_container`  | `{"container": "<name>"}` | `docker restart <name>` (inspect-then-restart) via `brain_daemon.docker_restart_container`.                         |
| `run_auto_remediate` | _(none)_                  | Re-runs `brain_daemon.auto_remediate` — the stuck-`in_progress` / stale-`awaiting_approval` `pipeline_tasks` sweep. |

Adding an action = register one more executor in `ACTION_REGISTRY` (+ the
primitive it wraps). An unknown `action_name` is `skipped` — it never crashes the
loop.

### Safety guardrails

- **Master switch.** `ops_firefighter_enabled` (default `true`). Off → the loop
  is skipped entirely and every alert pages the old way.
- **Empty by default = inert.** Shipping enabled with **zero** `remediation_rules`
  rows is a no-op: nothing matches, nothing acts, everything pages as before.
  Rules are opt-in, one alert at a time.
- **Circuit breaker.** Per `(fingerprint, action_name)`: after
  `max_attempts_per_window` actions in `window_minutes` (rule-level override, else
  `ops_firefighter_max_attempts_per_window` / `ops_firefighter_window_minutes`)
  the firefighter stops acting and pages — a genuinely broken thing can't spin a
  restart loop. Counted from the `remediation_action` audit rows, so it survives a
  brain restart.
- **Global rate cap.** `ops_firefighter_max_actions_per_hour` (default `10`)
  across all actions — a backstop when many alerts fire at once.
- **Allowlist.** `ops_firefighter_action_allowlist` (CSV, default empty = every
  registered action allowed). Set it to shrink what can run without deleting rules.
- **Verify-then-page.** A successful action never silences an unfixed problem: if
  the alert is still firing after the grace window, it pages. Silence is earned
  only by the alert actually stopping.

### LLM long-tail — the un-ruled path (Plan B)

A rule covers a _known_ alert shape. For an alert with **no** rule, the
firefighter can still take a reasonable first action instead of paging blind: a
small **local** model (`ops_firefighter_model`, default `ollama/llama3.2:3b`)
picks one action from the registry catalog — or abstains. The selection never
runs in the brain (which ships asyncpg/httpx/urllib only, no LLM libs): the
brain POSTs the alert + the allowlisted catalog to the worker route
`POST /api/remediation/select`, the worker runs local Ollama and returns a
`{action_name, params, confidence, reason}` **constrained to the catalog it was
sent**, and the engine re-validates that pick before acting. Local-only by
policy — never a cloud LLM (`no_paid_apis`).

The path is gated — each gate that fails pages as usual, with **no inference
call**:

- **Master switch.** `ops_firefighter_llm_longtail_enabled` (default `true`).
  Off → only the deterministic rule path runs, even with the firefighter
  enabled.
- **Persistence.** A one-off blip pages without ever asking the model. The LLM
  path engages only once the alert is persistent —
  `repeat_count >= ops_firefighter_min_repeats` (default `2`) **or** it has been
  firing longer than `ops_firefighter_min_age_minutes` (default `10`).
- **Circular-dependency guard.** An alert whose name matches
  `ops_firefighter_llm_exclude_regex` (default
  `(?i)(ollama|gpu|vram|cuda|inference)`) is **never** sent to the model — you
  can't ask the LLM to fix the substrate it runs on. Those stay
  deterministic-rule-only.
- **Confidence.** A selection below `ops_firefighter_min_confidence` (default
  `0.6`) pages instead of acting.
- **Untrusted output.** The model's `action_name` must be one of the catalog
  names it was sent — re-validated on **both** the worker and the brain — so an
  off-list, malformed, low-confidence, or Ollama-unreachable selection degrades
  to _abstain → page_. Model quality affects the recovery _rate_, never
  _safety_.

A valid pick then runs the **same allowlist → circuit-breaker → rate-cap →
execute → verify** machinery as a rule, recorded with `source="llm"` (plus
`confidence` / `model`) in its `remediation_action` audit row. The **Rule vs LLM
source split** and **LLM selector hit-rate per model** panels on the System
Health dashboard track how much the long-tail is doing and whether the model is
good enough.

**The learning loop.** When an LLM-chosen action **resolves** (verified silent
success), the verify scan emits a `remediation_candidate_rule` **finding** —
surfaced on the Findings dashboard + `findings_list` — so you can promote the
proven fix to a durable `remediation_rules` row. Once promoted it runs
deterministically, with no inference call or persistence wait. A still-firing
attempt, or a rule-source resolve, emits nothing.

### Managing rules

Rules are **operational runtime state** — you add, tune, and **retire** them one
alert at a time as you learn which alerts are safely auto-recoverable — so they
live only in the `remediation_rules` table, seeded by **neither** the baseline
nor the operator overlay (a fresh install starts inert; see "Empty by default"
above). Manage them with `poindexter firefighter rule …`, a thin adapter over
`services.remediation_rules_service` (epic #1340 — the service owns the SQL, so
no hand-written `INSERT` is needed).

Discover which alerts are actually firing first (there's no CLI verb for the raw
`alert_events` feed):

```sql
SELECT DISTINCT alertname FROM alert_events ORDER BY 1;
```

Restart a wedged container when its liveness alert fires:

```bash
poindexter firefighter rule add \
  --action restart_container \
  --alert SomeSidecarDown \
  --param container=poindexter-<name> \
  --description "Restart <name> when its liveness alert fires; verify then page."
```

Kick the stuck-task sweep on demand (regex across a family of alerts):

```bash
poindexter firefighter rule add \
  --action run_auto_remediate \
  --match '(?i)task.*stuck' \
  --verify-after 180 \
  --description "Re-run the pipeline_tasks sweep when a stuck-task alert fires."
```

Inspect, tune in place, and retire — changes are live within one 30s dispatch
cycle (the loader re-reads the table each cycle; no brain restart):

```bash
poindexter firefighter rule list [--state enabled|disabled]
poindexter firefighter rule show 3            # or: --alert SomeSidecarDown
poindexter firefighter rule disable 3         # stop acting, keep the row
poindexter firefighter rule enable 3
poindexter firefighter rule rm 3              # or: --alert SomeSidecarDown (this sticks — nothing re-seeds it)
```

`add` **validates before it writes**: an unknown `--action`, a rule with neither
`--alert` nor `--match`, or a `restart_container` rule missing
`--param container=…` all fail loud — a rule the brain could never run is a
silent dead row, so it's rejected up front. Known actions today: `restart_container`,
`run_auto_remediate`. Optional per-rule circuit-breaker caps: `--max-attempts`,
`--window-minutes`, `--verify-after` (each falls back to the global default when
omitted).

**Only wire an action to an alert it can actually fix.** `restart_container`
targets **containers** — confirm the surface is one (`docker ps`) before pointing
a rule at it. Several _noisy_ surfaces are **host-native, not containers**, so a
`docker restart` can't touch them and the rule would act uselessly, then page:

- **Ollama** (`Ollama Unresponsive`) runs on the host
  (`host.docker.internal:11434`), not in a container.
- The **MCP HTTP server** (`mcp_http_server_unreachable`) is a host Scheduled
  Task — it already has the `mcp-http` host-recover path (see the Recovery Agent
  below).
- **image-gen / wan** inference servers are host-native too.

Route those through a probe + host Recovery Agent action, not a firefighter rule.
And note `run_auto_remediate` already runs unconditionally every brain cycle, so a
rule for it only adds an _on-demand_ re-run between cycles.

### Deploy order (rules come last)

The firefighter code is image-baked into the brain, and `remediation_rules` is
created by a worker-boot migration. After merging:

1. Worker boots → the `remediation_rules` table is created (empty).
2. Rebuild + recreate the brain so it has the firefighter code
   (`docker compose build brain-daemon && docker compose up -d brain-daemon`) —
   the 10-min deploy-checkout-sync does this automatically on a `brain/` change.
3. _Then_ add your first rules with `poindexter firefighter rule add`, verified,
   one alert at a time.

Until step 3 the firefighter is live but inert (enabled, no rules). The
**Self-Healing / Remediation** row on the System Health dashboard shows silent
auto-recoveries, still-firing/paged counts, actions-by-type, and the latest
actions once rules begin matching.

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

### Request timeout

`RecoveryHandler.timeout` is set (matches `TASK_TIMEOUT_SECONDS`, 30s) so a
client that connects and never completes a request line doesn't occupy its
handler thread forever — `handle_one_request()`'s blocking read now has a
bound, catches the resulting `TimeoutError` itself, and closes the connection
cleanly (logged, not a stack-trace dump). Note this bounds _thread lifetime
per silent connection_, not overall server responsiveness: `ThreadingHTTPServer`
already dedicates one thread per connection, so a hung connection was never
able to block a _concurrent_ request even before this fix — confirmed by
reproduction (20 simultaneous connect-and-silent clients, a fresh request
still served in the same call). The real value is preventing unbounded thread
accumulation under a sustained pattern of incomplete connections (a port
scanner, a misbehaving health-check client with no timeout of its own).

### Own-liveness watchdog

The "Poindexter Recovery Agent" Scheduled Task triggers **at logon, one-shot,
non-repeating** — nothing relaunches the agent if it crashes or wedges until
the next logon/reboot. A service can't reliably report its own liveness for
the purpose of restarting itself (if it's down, it can't answer the probe that
would trigger its own recovery), so this needs a genuinely separate process —
the same reason [`docker-watchdog.ps1`](../../scripts/docker-watchdog.ps1)
exists for the Docker engine.

`scripts/recovery-agent-watchdog.ps1` mirrors that pattern: a real `GET
/healthz` (not a port-open or process-alive check — those don't catch "port
held, TCP accepts, but no HTTP response ever completes"), a confirm-before-recycle
recheck (`-WedgeConfirmSeconds`, default 30s) so a transient blip doesn't
trigger an unnecessary restart, and `Stop-ScheduledTask` + `Start-ScheduledTask`
on the agent's task if still unhealthy. It needs to run **elevated**
(`-RunLevel Highest`, same as `docker-watchdog.ps1`'s own task) because
`Stop-ScheduledTask` against the (elevated) Recovery Agent task fails with
"Access is denied" from a non-elevated caller, even as the same Windows user.

Modes: bare invocation (one-shot check), `-Loop -IntervalSeconds N`, and
`-Install`/`-Uninstall` to register its own 5-minute repeating Scheduled Task
(`"Poindexter Recovery Agent Watchdog"`). Installing registers a persistent
elevated background task — run `-Install` yourself rather than scripting it
unattended.

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
- **Server-side `statement_timeout` on every connection** (`_create_brain_pool`,
  `BRAIN_DB_STATEMENT_TIMEOUT_MS`, default **60000 ms**). The complement to
  `command_timeout`: it does **not** help the 2026-06-29 wedged-proxy case (the
  statement never reaches Postgres, exactly as noted above), but it bounds the
  _orthogonal_ failure — a query that **does** reach Postgres and then runs long
  (a lock wait, a runaway seq scan). Postgres cancels it server-side, freeing
  the backend rather than leaving `command_timeout` to merely abandon the
  client's wait. Same bootstrap tier (env/constant), set before settings load.
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

### The `brain.cycle_heartbeat` is independent of the cycle

The cycle watchdog stops a hang from lasting forever, but a window remained:
`brain.cycle_heartbeat` — the row whose freshness drives the
`BrainDeliveryDeadMansSwitch` — was written only at the **end** of `run_cycle`. A
cycle cancelled by the watchdog never reached that write, so a _repeatedly_
hanging cycle could still let the row go stale and trip the switch even though
the daemon's loop was alive and recovering each time.

A dedicated **`heartbeat_loop`** task now refreshes that row (and the file
heartbeat the Docker healthcheck reads) on its own cadence —
`app_settings.brain_heartbeat_interval_seconds`, default **60s**, comfortably
under the switch's 900s stale threshold — regardless of cycle progress. This
re-points both the dead-man's switch and the container healthcheck at the right
question: _is the daemon's event loop alive?_ rather than _did the last full
cycle finish?_ A genuine total freeze stops `heartbeat_loop` too, so both
signals still fire on a real outage; a recoverable cycle hang no longer
masquerades as a dead brain. The loop runs alongside `alert_dispatch_loop` and
is restarted by the same death-watch (`_alert_dispatch_died`) if it ever exits.
Its cadence + hang-dump config are read **once at startup** — re-reading them on
the one loop that must survive a wedged DB would re-introduce a DB dependency on
the liveness path. The end-of-cycle write still happens too, carrying the full
probe stats (`kind="cycle"` vs the loop's `kind="liveness"`).

### Diagnosing a total freeze — faulthandler + py-spy

The cycle watchdog can only fire while the event loop still runs:
`asyncio.wait_for` delivers its `TimeoutError` _through_ the loop. A **sync
C-level hang** (a blocking call in a C extension that ignores cancellation)
freezes the single thread outright, so neither the watchdog nor `command_timeout`
can fire. Two out-of-loop diagnostics cover that last tier:

- **`faulthandler` (in-process).** `heartbeat_loop` re-arms
  `faulthandler.dump_traceback_later(...)` each tick from a separate C thread
  that fires even when the event loop is frozen. A normal tick re-arms (resets)
  the timer before it expires; only a tick that stalls past
  `app_settings.brain_hang_dump_seconds` (default **300s**, set _above_ the 240s
  cycle ceiling so recoverable hangs never trip it) lets it fire — dumping every
  thread's traceback to stderr → Loki, so the post-mortem shows the exact stuck
  frame instead of a silent ~37-min gap. Mirrors the worker's
  `worker_hang_dump_seconds` guard; `0` disables it.
- **`py-spy` (live, external).** The brain image bakes the `py-spy` CLI and the
  brain service is granted `CAP_SYS_PTRACE`, so a frozen-but-alive daemon can be
  sampled on demand without restarting it:

  ```bash
  docker exec poindexter-brain-daemon py-spy dump --pid 1
  ```

  This prints the native + Python stack of the wedged thread immediately —
  useful when the freeze is ongoing and you'd rather not wait for the
  faulthandler ceiling. (Host `kernel.yama.ptrace_scope` can still gate
  attachment; the capability is the in-container prerequisite.)

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
6. **Paging is deduped** — the cap-reached/POST-failed page only fires when the
   drifted-service set changes or an hour has passed since the last page
   (mirrors the notify-only path below). The per-cycle `audit_log` row from
   step 1 keeps recording every cycle regardless, so the Findings dashboard
   still shows the full timeline; only the Telegram/Discord page is throttled.
   Before this (fixed 2026-07-12), an unchanged persisting drift re-paged
   critical on every 5-min cycle indefinitely — one stuck service paged 15
   times in ~90 minutes.

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

## Prefect flow-run zombie reclaim (concurrency-slot wedge)

The `content-generation` deployment runs on `content-pool` at
**`concurrency_limit=1`** (one gemma draft at a time — the single-GPU VRAM
budget). One non-terminal flow run holds the only slot, so if a run gets stuck
in a non-terminal state the pipeline **silently halts**: pending tasks exist,
the worker is ONLINE and heartbeating, but nothing dispatches while cron-queued
runs pile up SCHEDULED behind the held slot.

`brain/prefect_stuck_flow_probe.py` is the working reclaim path. Every brain
cycle it queries Prefect for `content_generation` runs in the watched states and
force-terminalizes genuine zombies so the slot frees:

| Held state   | "stuck" when…                                                                                                      | Reclaim action      | Threshold setting                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------------------ | ------------------- | ---------------------------------------------------------------------------------------------- |
| `RUNNING`    | no graph-node progress (`pipeline_tasks.last_progress_at`) for `progress_stall_minutes`; NULL heartbeat → flat age | force **CRASHED**   | `prefect_stuck_flow_progress_stall_minutes` (20) / `prefect_stuck_flow_threshold_minutes` (30) |
| `PENDING`    | stranded (worker died between claim and fork) past the PENDING threshold                                           | force **CRASHED**   | `prefect_stuck_flow_pending_threshold_minutes` (5)                                             |
| `CANCELLING` | cancel requested but worker/process already dead, so `CANCELLING → CANCELLED` never completes, past the threshold  | force **CANCELLED** | `prefect_stuck_flow_cancelling_threshold_minutes` (10)                                         |

All three are gated by the single `prefect_stuck_flow_auto_crash` master switch
(default `true`); set it `false` for page-only. The probe also pages a distinct
`probe.prefect_queue_backlog_detected` signal when overdue SCHEDULED runs pile
up past `prefect_stuck_flow_queue_depth_threshold` **and** the slot-holder is not
progressing — the backlog symptom of a genuinely held slot.

**Why CANCELLING was added (2026-07-07).** A host/WSL/power event left two runs
wedged in `CANCELLING` (`qualified-corgi` 62 min, `inscrutable-gharial` 28 min).
A graceful `prefect flow-run cancel` on a run whose process is already dead
transitions it to CANCELLING but can never complete the kill-confirm to
CANCELLED — so it holds the slot forever. The RUNNING/PENDING-only scan was
blind to it: the probe kept paging the backlog symptom (03:19, 04:07) but could
not free the slot, and the pipeline stayed halted ~2h until an operator
force-cancelled. The fix teaches the probe to force-CANCELLED a stuck CANCELLING
run — the analogue of the RUNNING force-CRASHED path.

**Prefect-native backstop (considered, deliberately not pursued).** Prefect 3.7's
`Foreman` service only marks _workers_ offline on stale _worker_ heartbeats — it
never crashes a flow run — so there is no built-in flow-run zombie-crash to "turn
on". A Prefect Automation on a `prefect.flow-run.heartbeat`-absence trigger could
crash a stale _RUNNING_ run brain-independently, but it would be **redundant**:
the brain probe above already reclaims RUNNING zombies, and a heartbeat automation
would not catch the _CANCELLING_ wedge that actually caused the 2026-07-07 incident
(a cancelling run isn't emitting progressing heartbeats to go stale). The one
scenario a native backstop helps — the brain being down — is already paged loudly
by the deadmans-switch on `brain.cycle_heartbeat`. So the brain probe is the sole,
sufficient reclaim path. See `project_prefect_concurrency_zombie_stall` for the
full incident write-up.

## Settings reference

| Setting                                                       | Default                                    | Meaning                                                                                                                   |
| ------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `compose_drift_host_recover_enabled`                          | `true`                                     | Auto-heal compose drift via the host agent.                                                                               |
| `compose_drift_host_recover_cap_per_window`                   | `3`                                        | Max reapplies before escalating to a page.                                                                                |
| `compose_drift_host_recover_window_minutes`                   | `60`                                       | The rolling window for the cap.                                                                                           |
| `compose_drift_on_demand_services`                            | `wan-server,image-gen-server`              | CSV of services started on demand — exempt from the missing-container check.                                              |
| `compose_drift_active_profiles`                               | (empty)                                    | CSV of active compose `profiles:`. Services gated behind an unlisted profile are exempt from the missing-container check. |
| `compose_drift_auto_recover_enabled`                          | `false`                                    | Brain-side `docker compose up` — keep OFF on Windows hosts.                                                               |
| `mcp_http_probe_recovery_url`                                 | (empty)                                    | Recovery Agent endpoint, e.g. `http://host.docker.internal:9841/recover`. Shared by all host-recover probes.              |
| `mcp_http_probe_recovery_token`                               | secret                                     | Bearer token matching the agent's `poindexter_recovery_token`.                                                            |
| `scheduled_tasks_probe_watch_tasks`                           | (empty)                                    | CSV of host Scheduled Task names the `scheduled_tasks` probe checks via `GET /tasks`. Empty = advisory no-op.             |
| `offsite_backup_watch_enabled`                                | `true`                                     | Backup-freshness probe.                                                                                                   |
| `auto_embed_watch_enabled`                                    | `true`                                     | Embedder-freshness probe.                                                                                                 |
| `docker_port_forward_max_failed_recoveries_before_alert_only` | `1`                                        | Consecutive failed recoveries before a `restart` entry switches to alert-only (adaptive give-up).                         |
| `docker_port_forward_alert_only_backoff_minutes`              | `60`                                       | Minutes a container stays alert-only after the give-up trips, before one more restart is allowed.                         |
| `ops_firefighter_enabled`                                     | `true`                                     | Master switch for the deterministic firefighter. Off = every alert pages the old way.                                     |
| `ops_firefighter_max_attempts_per_window`                     | `3`                                        | Per-`(fingerprint, action)` circuit-breaker cap; a matched rule may override.                                             |
| `ops_firefighter_window_minutes`                              | `60`                                       | Circuit-breaker rolling window (minutes); a matched rule may override.                                                    |
| `ops_firefighter_verify_after_seconds`                        | `120`                                      | Grace before the verify scan judges an action resolved vs still-firing; a matched rule may override.                      |
| `ops_firefighter_max_actions_per_hour`                        | `10`                                       | Global cap on firefighter actions across all rules per hour.                                                              |
| `ops_firefighter_action_allowlist`                            | (empty)                                    | CSV of allowed `action_name`s; empty = every registered action allowed.                                                   |
| `ops_firefighter_llm_longtail_enabled`                        | `true`                                     | Master switch for the LLM long-tail (un-ruled) path. Off = deterministic rules only.                                      |
| `ops_firefighter_model`                                       | `ollama/llama3.2:3b`                       | Local Ollama model the worker uses to pick an action for an un-ruled alert.                                               |
| `ops_firefighter_min_repeats`                                 | `2`                                        | LLM path engages after an un-ruled alert repeats this many times…                                                         |
| `ops_firefighter_min_age_minutes`                             | `10`                                       | …or has been firing this long (either signal qualifies).                                                                  |
| `ops_firefighter_min_confidence`                              | `0.6`                                      | LLM selections below this confidence page instead of acting.                                                              |
| `ops_firefighter_llm_exclude_regex`                           | `(?i)(ollama\|gpu\|vram\|cuda\|inference)` | Circular-dependency guard — alertnames matching this regex never take the LLM path.                                       |

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
5. One-time: install the [own-liveness watchdog](#own-liveness-watchdog) —
   `.\scripts\recovery-agent-watchdog.ps1 -Install` from an elevated
   PowerShell prompt — so a crashed or wedged agent recovers within one 5-min
   cycle instead of waiting for next logon/reboot.

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
