# Observability & Self-Healing Audit — Scoping the "Doctor" Feature

**Date:** 2026-05-29
**Author:** Claude (read-only audit; no code touched)
**Motivating failure (Exhibit A):** the content pipeline has generated nothing since its
frequency was raised on 2026-05-28, and nothing alerted Matt or auto-recovered.

---

## Executive Summary

Poindexter already has an unusually deep observability stack for a solo-operator project:
the brain daemon runs ~25 DB-backed health probes plus ~15 dedicated watchdog probes
(Prefect stuck-flow, compose drift, migration drift, SMART, backup, Docker port-forward,
PR staleness, GlitchTip triage, etc.), a Prometheus/Alertmanager + Grafana-rule alert plane
(15 Grafana DB rules + 4 Prometheus infra rules), a meta-watchdog (`silent_alerter`), and an
LLM "firefighter" triage path (`firefighter_service.py` + `/api/triage`). Self-healing is real:
the probe loop restarts containers, the Prefect probe can auto-crash stuck runs, the migration/
compose probes can auto-recover.

The gap is **not coverage of named failures** — it's **detection of the absence of expected
output**, **delivery-plane fragility**, and the **lack of a single reasoning surface** that ties
together the 40+ independent signals. Exhibit A is the textbook case: at least TWO Grafana rules
("Pipeline Stalled" #8, "Zero Published Posts This Week" #12) and `probe_publish_rate` /
`probe_pipeline_throughput` should have fired. That they didn't points squarely at the
**delivery plane** (alert*dispatcher / notify_operator / Grafana→webhook), the exact failure
class the `silent_alerter` meta-watchdog was built for — which means the meta-watchdog itself
may be silent, or the "frequency increase" produced a \_stalled-but-not-stopped* state the
threshold rules don't catch (queued runs piling behind a held concurrency slot looks "alive").

**Top findings:**

1. **Silent failures hide in the delivery plane, not detection.** Detection is dense; delivery
   is a single chain (probe → `notify_operator`/`alert_events` → `alert_dispatch_loop` →
   Telegram/Discord). If that loop dies or secrets are unreadable, _every_ alert goes dark and
   only `silent_alerter` (one probe, on the private business-probes path, default 60-min cadence,
   itself dependent on the same `notify_fn`) is supposed to notice.
2. **No "expected throughput" SLO.** Probes detect "0 posts in 3 days" (`publish_rate`) and "no
   tasks in 48h" (`pipeline_stalled`). A frequency _increase_ that yields a _slowdown_ (e.g. 1
   post/day where 6 were expected) trips none of these. There is no rule comparing actual vs
   _configured_ cadence (`prefect_content_flow_cron`).
3. **The Prefect cron + concurrency=1 model is a single point of starvation.** One stuck/PENDING
   run holds the only slot; every scheduled run queues behind it and the pipeline goes idle. This
   exact failure happened twice (romantic-harrier 35h, smoky-chowchow 50h) and drove the creation
   of `prefect_stuck_flow_probe`. But that probe pages only at warning severity and auto-crash is
   **opt-in/off by default**, so recovery still waits on a human.
4. **Broad exception-swallowing is pervasive and intentional** ("best-effort, never crash the
   cycle"). Dozens of `except Exception: logger.debug(...)` sites in the brain. Individually
   defensible; collectively they mean a sick subsystem can log at DEBUG forever and never escalate.
5. **The brain's reasoner is shallow + has a track record of misdiagnosis.** It pattern-matched
   the romantic-harrier stall into a wrong "Ollama unresponsive" guess; it misread the 2026-05-20
   quality alert as "outdated worker build." Memory `feedback_verify_brain_triage_before_acting`
   already codifies the distrust. A doctor must cross-check, not single-source.
6. **No service inventory ↔ probe/dashboard coverage map.** Some compose services (Tempo,
   Pyroscope, Loki, pgAdmin, LiveKit, SDXL) have no liveness probe; the brain monitors a fixed
   `SERVICES` dict (site, api, worker, openclaw, exporters). Coverage is ad-hoc, accreted per-incident.
7. **Issue #429 (cross-store DataFabric) is the missing substrate for any real doctor.** Today the
   brain has Postgres-only visibility; it cannot read Prometheus/Loki/Tempo/ClickHouse to reason
   over logs+metrics+traces together. Every "LLM doctor" issue (#51, #340) is blocked on this.

**Doctor recommendation: HYBRID — deterministic check-graph first, LLM diagnostician second,
gated on #429 DataFabric.** Build a rule-based `doctor` (a registry of `(check, expected,
known_fix)` triples that runs deterministically and self-heals known failures) as the reliable
core. Layer the existing `firefighter_service` LLM on top as the _diagnostician_ for the residual
"red but unknown" cases, reading cross-store context via #429. Matt leans ML-first, but the
delivery/recovery path is exactly where determinism matters most — an LLM that can hallucinate a
fix should never be the _only_ thing standing between a stalled pipeline and a human.

Full audit below. **File:** `docs/audits/2026-05-29-observability-doctor-audit.md`.

---

## Part 1 — Current-State Inventory

### 1.1 Health checks / probes

**Brain DB-backed probes** — `brain/health_probes.py` (`PROBES` dict, ~25 probes, each on its own
schedule via `PROBE_SCHEDULES`). Results write to `brain_knowledge`; 3 consecutive failures →
`notify_operator`. Notable ones:

- Pipeline health (P0): `stuck_tasks` (in_progress >4h), `approval_queue`, `failed_task_spike`,
  `worker_error_rate` (reads worker `/api/health` task_executor block).
- Business continuity (P1): `publish_rate` (0 posts/3d), `cost_freshness`, `podcast_health`,
  `newsletter_health`.
- Quality (P2): `quality_trend`, `topic_quality` (rejection-driver attribution), `pipeline_throughput`
  (7d vs prior 7d, fires only on >50% drop), `embeddings_freshness`, `traffic_anomaly`.
- Infra: `db_ping`, `ollama_models`, `content_gen` (real Ollama generate), `grafana_datasources`,
  `public_site`, `disk_space`, `gpu_temperature`, `r2_connectivity`, `scheduled_tasks`.
- Self-heal map `REMEDIATIONS`: `worker_error_rate`/`stuck_tasks`/`public_site` → restart
  `poindexter-worker`; `grafana_datasources` → restart grafana. 15-min cooldown per probe.

**Brain watchdog probes** (separate modules, dispatched every cycle, internally cadence-gated):
`prefect_stuck_flow_probe`, `compose_drift_probe`, `migration_drift_probe`, `backup_watcher`,
`smart_monitor`, `docker_port_forward_probe`, `gate_auto_expire_probe`, `gate_pending_summary_probe`,
`glitchtip_triage_probe`, `pr_staleness_probe`, `discord_bot_probe`, `mcp_http_probe`,
`operator_url_probe`. Each wired in `brain/brain_daemon.py::run_cycle` behind a `_HAS_*` import flag;
a boot-time audit (`_audit_brain_module_imports`, #504) pages if any expected module fails to import.

**HTTP health endpoints:** worker `/api/health` (`src/cofounder_agent/main.py`), MCP HTTP server
OAuth-discovery endpoint, openclaw `:18789/status`. Brain probes these from a fixed `SERVICES` dict.

**Heartbeats:** brain writes (a) a `~/.poindexter/heartbeat` + `/tmp/brain_heartbeat` file every cycle
for an OS-level watchdog, (b) a `brain_decisions` row every cycle, (c) a `brain.cycle_heartbeat`
`audit_log` row every cycle (added 2026-05-27 so "no heartbeat in N min" is detectable rather than
absence-of-noise). Grafana rule #11 fires if `brain_decisions` goes stale >15 min.

### 1.2 Alerting

- **Prometheus/Alertmanager** — `infrastructure/prometheus/alerts/infrastructure.yml` (4 rules:
  WorkerDown, WorkerUnhealthy, PostgresDown, OllamaDown) + `postgres-connections.yml`.
  `alertmanager.yml` routes ALL alerts to a single worker webhook
  (`/api/webhooks/alertmanager`), which persists to `alert_events` and dispatches in Python.
  `repeat_interval: 4h`, inhibition suppresses warnings under criticals.
- **Grafana DB rules** — `infrastructure/grafana/provisioning/alerting/alert-rules.yml`, 15 rules
  against the `local-brain-db` datasource: high error rate, stale tasks, embedding lag, DB size,
  cost spike, content-quality drop, traffic anomaly, **pipeline stalled (#8)**, worker offline,
  ollama down, brain heartbeat stale, **zero published posts this week (#12)**, GPU metrics stale,
  GPU temp, disk space.
- **Delivery plane** — brain `alert_dispatch_loop` (30s cadence) polls `alert_events` for
  undispatched rows → `notify` (Telegram + Discord #ops). `notify_operator` is the no-DB failsafe
  (reads env vars hydrated from app_settings). `operator_paged` audit rows track successful pages.
- **LLM triage** — `services/firefighter_service.py` + `routes/triage_routes.py` (`POST /api/triage`).
  Wired into the dispatcher (#347): an alert can spawn an LLM "firefighter" that produces a
  diagnosis paragraph quote-replied under the original Telegram/Discord alert (`send_followup`).
  Coalescing + AI-assisted escalation added in PR #301.
- **Meta-watchdog** — `brain/business_probes.py::probe_silent_alerter`: pages if no
  `alert_events.received_at` AND no `operator_paged` in `silent_alerter_quiet_hours` (6h) **while**
  error/critical probe events are firing. Explicitly does NOT self-heal (human decides the fix).
- **GlitchTip** (self-hosted Sentry, `:8080`) — SDK auto-init in `main.py` when `sentry_dsn` set;
  `glitchtip_triage_probe` auto-resolves known noise + pages on novel high-count issues.

### 1.3 Metrics / observability stack

- **Prometheus** (`:9091`) scrapes worker `/metrics` (`services/metrics_exporter.py`),
  windows_exporter, nvidia-smi-exporter. Gauges include `poindexter_worker_up`,
  `poindexter_postgres_connected`, `poindexter_ollama_reachable`, auto-cancelled task count.
- **Grafana** (`:3000`) — 8 dashboards (`infrastructure/grafana/dashboards/*.json`): mission-control,
  pipeline, auto-publish-gate, cost-analytics, observability-merged, system-health-merged,
  integrations-admin, qa-rails.
- **Loki** (`:3100`) logs, **Tempo** (`:3200`) traces, **Pyroscope** (`:4040`) CPU profiles from
  worker/brain/voice (`enable_pyroscope`), **Langfuse** (`:3010`) LLM traces. Brain probes emit
  per-probe OTel child spans (#176). Note: Prefect-subprocess telemetry had to be re-wired manually
  in `content_generation.py` (#477 / Phase-5 finding #6) because flow subprocesses skip `main.py` lifespan.

### 1.4 Self-healing / watchdogs / anticipation

- Container restarts via `health_probes.REMEDIATIONS` + brain `restart_service` (docker restart in
  container; PowerShell script on host). Cooldown-guarded, restart-cap-guarded (port-forward probe).
- Prefect stuck-flow auto-crash (`prefect_stuck_flow_auto_crash`, **default false**).
- Migration-drift auto-recover, compose-drift auto-recreate (both opt-in app_settings).
- `auto_remediate` (brain, every cycle): auto-cancels stuck in_progress >180m+grace, auto-rejects
  awaiting_approval >7d, flags pipeline idle >48h, flags failure-rate spikes.
- `openclaw doctor --fix` run every 15 min (host-side channel healing).
- OS-level brain watchdog reads the heartbeat file (`scripts/brain-watchdog.sh`,
  `scripts/docker-watchdog.ps1`).
- **Anticipation pattern**: the opportunistic-work interface exists conceptually but the brain
  watchdog does NOT implement it yet — that's open issue **#181**.

### 1.5 Audit trail & scheduling

- `audit_log` — canonical record: `operator_paged`, `brain.cycle_heartbeat`,
  `probe.prefect_stuck_flow_detected`, `event_type='finding'` (per #461), QA passes, rejections.
- `pipeline_tasks` — task queue; `content_tasks` is a **backcompat VIEW** over it with
  INSERT/UPDATE/DELETE redirect triggers (verified — the topic_discovery_signals queue check reads
  the same rows, so there is NO table-mismatch bug there).
- **Scheduling**: content pipeline = Prefect deployment `content-generation`
  (`scripts/deploy_content_flow.py`), cron `prefect_content_flow_cron` (default `*/2 * * * *`),
  work pool `content-pool`, **concurrency=1**. The flow claims one `pending` row per tick
  (FOR UPDATE SKIP LOCKED); empty queue → clean no-op exit. Topic discovery
  (`jobs/topic_discovery_signals.py` → `services/topic_discovery.py`) refills `pending` rows on
  signals: queue-low (<2 pending), manual trigger, staleness (>6h), rejection-streak.

---

## Part 2 — Issue Map (9 issues)

| #       | Title (gist)                                                                                                                       | Theme                                                  | Type                                                                                                                           |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| **34**  | Lessons-learned vector DB — store compact embeddings/summaries of failures, query before generating                                | Learning loop / quality self-correction                | Feature (new capability)                                                                                                       |
| **51**  | AI educator/help system — LLM + RAG over codebase/schema/state to answer "how does X work / why did Y happen"                      | **LLM-doctor adjacent** (diagnostic reasoning surface) | Feature; partially unblocked (RAG substrate `rag_engine.py` exists)                                                            |
| **181** | Brain watchdog needs anticipation-pattern interface                                                                                | Self-healing / opportunistic work                      | Gap (architectural debt)                                                                                                       |
| **340** | Brain as alert decision-maker (replace dumb threshold dispatch) — escalate/aggregate/suppress + feedback loop + proactive          | Alerting intelligence                                  | Feature; Phase A ~shipped via firefighter (#347/#301); **B (👍/👎 feedback) + C (proactive metric-driven alerts) NOT shipped** |
| **411** | Auto-provision Uptime Kuma starter monitors on first boot                                                                          | External uptime monitoring                             | Gap (container up, zero monitors — "worst of both worlds")                                                                     |
| **429** | Cross-store DataFabric — brain query helpers for ClickHouse/Prometheus/Loki/Tempo/Pyroscope                                        | Observability substrate                                | Feature; **blocking prerequisite for any real LLM doctor**                                                                     |
| **440** | Anomaly-detection probe (rolling 7-day baseline, 3-sigma) for throughput/cost/error/queue/GPU                                      | Detection (statistical)                                | Feature (the "expected vs actual" gap, Finding #2)                                                                             |
| **461** | Brain findings_dispatcher — route `audit_log` `event_type='finding'` rows to channels per app_settings policy                      | Detection→delivery routing layer                       | Feature/epic; Phase 0 (emit) done, Phase 1 (dispatch) open                                                                     |
| **520** | Wire traffic analytics into Grafana (Cloudflare GraphQL + ViewTracker + brain signals); analytics as a _signal the system acts on_ | Coverage gap + revenue-signal loop                     | Bug+Feature (page_views table/beacon don't exist; CLAUDE.md aspirational)                                                      |

**Clustering for the doctor:**

- _Detection gaps_: #440 (statistical anomaly), #520 (traffic blind spot).
- _Delivery/routing_: #461 (findings dispatcher), #340-A (triage/suppress — partly done).
- _Intelligence_: #51 (RAG explainer), #340-C (proactive), #34 (learn from failures).
- _Substrate_: #429 (cross-store read) — gates #51/#340-C/#440-rich.
- _Self-healing architecture_: #181 (anticipation interface).
- _External coverage_: #411 (Uptime Kuma).

---

## Part 3 — Gap Analysis & Doctor Design

### 3.1 What is NOT monitored / has no resolution path

1. **Actual-vs-configured throughput.** No rule reads `prefect_content_flow_cron` to compute
   _expected_ posts/day and alert on the shortfall. `publish_rate` (3d) and `pipeline_throughput`
   (>50% drop) are coarse and lag by days — exactly why Exhibit A's frequency-increase-then-stall
   went unnoticed. **Fix: #440 baseline probe + an explicit cadence SLO.**
2. **Queued-behind-a-slot "alive but idle".** With concurrency=1, a held slot looks healthy to
   worker/ollama/db probes; only `prefect_stuck_flow_probe` catches it, at _warning_ severity with
   auto-crash off. **Fix: make stuck-flow auto-crash default-on once tuned; add a "scheduled runs
   queued > N" rule.**
3. **The delivery plane itself.** If `alert_dispatch_loop` dies or notify secrets are unreadable,
   all 30+ probes go dark. `silent_alerter` is the only backstop and it (a) lives on the private
   business-probes path, (b) runs at 60-min cadence, (c) calls the _same_ `notify_fn` it's checking.
   **Fix: out-of-band delivery health check (e.g. a Prometheus "dead man's switch" alert that fires
   when the brain STOPS emitting a known heartbeat metric, routed through a path independent of the
   brain's own dispatcher).**
4. **Service coverage holes.** Tempo, Loki, Pyroscope, pgAdmin, LiveKit, SDXL, Prefect-server have
   no brain liveness probe (Prefect only indirectly via stuck-flow). **Fix: #411 Uptime Kuma starter
   monitors + a coverage map kept in app_settings.**
5. **Traffic signal blind spot (#520).** `page_views` table and ViewTracker beacon don't exist;
   `traffic_anomaly` probe and Grafana rule #7 query an empty table and silently pass.

### 3.2 Where silent failures hide

- **Pervasive `except Exception: logger.debug(...)`** in the brain (intentional "never crash the
  cycle"). A subsystem can fail every cycle at DEBUG and never escalate. The #455 sweep upgraded
  several of these to WARNING — but the pattern is the default. Auditable list: `auto_remediate`,
  `update_system_metrics`, `generate_daily_digest`, `log_electricity_cost`, `self_maintain`, every
  watchdog-probe wrapper in `run_cycle` (`logger.warning(... probe failed ...)` then continue).
- **Probe results stored but not aggregated.** Each probe writes `brain_knowledge` / `audit_log`
  independently. Nothing computes a _system health score_ or notices "5 unrelated probes degraded
  at once" — the signal of a systemic (not local) problem.
- **`noDataState` inconsistency** in Grafana rules: pipeline-stalled (#8) and zero-published (#12)
  use `noDataState: Alerting` (good — empty result fires), but quality/cost/traffic use
  `noDataState: OK` (a broken query silently passes).
- **Best-effort post-pipeline actions** (`content_generation.py`): webhook/notify side-effects
  failing only log a warning; the operator-notification of a completed post can vanish.

### 3.3 Doctor design options

**Option A — Rule-based doctor (deterministic check-graph).**
A registry of checks `(name, query/probe, expected_invariant, known_remediation, severity)`. Runs
deterministically, self-heals known failures, never hallucinates. This is essentially formalizing

- unifying what's scattered today across `health_probes`, the watchdog probes, `auto_remediate`,
  and the Grafana rules into ONE inspectable graph with a single `poindexter doctor` CLI entry
  (`feedback_cli_first`). Plus a system-health _score_ and "N probes degraded simultaneously"
  correlation.

* _Pros:_ reliable, auditable, free (no LLM), testable, matches `feedback_calculated_vs_generated`
  (deterministic where reliability matters). Directly fixes Findings #1–#4.
* _Cons:_ only catches failures someone thought to encode; no novel-failure reasoning.

**Option B — LLM-based doctor (#51 shape).**
LLM + RAG over codebase/schema/logs/metrics/decision-history. Given "red but unknown" state, it
reasons over cross-store context to diagnose and propose a fix. The `firefighter_service` is a
v0.5 of this already.

- _Pros:_ handles novel failures, explains _why_ (the #51 value prop), can ingest cross-store
  context, fits `feedback_design_for_llm_consumers` + ML-first lean.
- _Cons:_ **blocked on #429** (today it's Postgres-only); the brain reasoner already has a
  documented misdiagnosis track record (`feedback_verify_brain_triage_before_acting`); an LLM as
  the _sole_ gate between a stall and a human is a reliability hazard.

**Option C — Hybrid (RECOMMENDED).**
Deterministic check-graph (A) is the always-on core: runs every cycle, self-heals known failures,
maintains the health score, and owns the delivery-plane dead-man's-switch. When a check goes "red"
with NO known remediation (or the same red persists past N cycles after self-heal attempts), it
escalates to the LLM diagnostician (B / firefighter), which reads cross-store context via #429,
produces a ranked diagnosis + _proposed_ fix, and pages the operator with both. Auto-execution of
LLM-proposed fixes stays opt-in and gated (mirror `prefect_stuck_flow_auto_crash`'s posture).
Feed every confirmed diagnosis into the #34 lessons vector DB so repeat failures resolve from
memory, not fresh reasoning.

**Recommendation: Hybrid (C).** Rationale grounded in the audit: detection is already dense, so the
highest-leverage work is (1) a deterministic, unified, _correlated_ check layer that closes the
expected-vs-actual and delivery-plane gaps (the actual cause of Exhibit A), and (2) reusing the
existing firefighter LLM as the diagnostician for the residual unknowns — but never as the only
safety net. This honors Matt's ML-first lean while keeping the recover/deliver path deterministic
where it must be reliable.

### 3.4 Prioritized next steps

1. **(P0, deterministic) Delivery-plane dead-man's-switch. ✅ DONE 2026-05-30 (#524).** Out-of-band
   alert (Prometheus `absent()`/staleness on a brain-emitted heartbeat metric) that fires through a
   path NOT owned by the brain dispatcher. Directly addresses why Exhibit A was silent.
   Implementation (three independent axes — own process, own token, own route):
   - **Metric:** worker `/metrics` now exposes
     `poindexter_brain_cycle_heartbeat_timestamp_seconds` (the epoch of the latest
     `brain.cycle_heartbeat` audit_log row). The series is `.clear()`-ed on no-row / DB-error so
     `absent()` can fire. (`src/cofounder_agent/services/metrics_exporter.py`)
   - **Rule:** static `BrainDeliveryDeadMansSwitch` in
     `infrastructure/prometheus/alerts/deadmans-switch.yml` (the safety-net `alerts/` dir, NOT the
     DB-rendered `rules/` dir) — fires on `absent(...)` for 10m OR `time() - <gauge> > 900`.
   - **Delivery:** a SECOND Alertmanager receiver `dead-mans-switch-telegram` using NATIVE
     `telegram_configs` (bypasses the worker webhook + Python dispatch loop entirely), with a route
     matching `delivery_plane=dead_mans_switch` ABOVE the default. The config is a `.tmpl` (no real
     chat_id in the public mirror); `RenderAlertmanagerConfigJob` substitutes
     `app_settings.telegram_chat_id` and reloads Alertmanager via `/-/reload`. The bot token is
     written to a bind-mounted file by `brain/prometheus_secret_writer.py` from
     `app_settings.telegram_bot_token`.
2. **(P0) Cadence SLO rule.** Compute expected posts/tasks from `prefect_content_flow_cron`; alert
   on shortfall within hours, not days. Closes the frequency-increase-then-slowdown gap.
3. **(P1) Flip `prefect_stuck_flow_auto_crash` to default-on** (threshold is now well-tuned across
   two captured incidents) + add "scheduled runs queued behind held slot > N" detection.
4. **(P1) #440 anomaly-detection probe** (rolling 7-day, 3-sigma) — the statistical backbone of the
   doctor's "expected vs actual."
5. **(P1) Unify into `poindexter doctor`** — single CLI/registry over existing probes + a system
   health score + "N probes degraded at once" correlation; standardize `noDataState: Alerting` and
   upgrade silent DEBUG-swallow sites that gate page-worthy state.
6. **(P2) #429 DataFabric** — unblocks the LLM diagnostician reading logs+metrics+traces.
7. **(P2) Promote firefighter → doctor diagnostician** behind #429; wire #34 lessons memory so
   confirmed diagnoses are recalled.
8. **(P2) Close coverage holes** — #411 Uptime Kuma starter monitors, #520 traffic signal,
   #461 findings dispatcher, #181 anticipation interface.
