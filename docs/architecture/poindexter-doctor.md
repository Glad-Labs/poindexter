# `poindexter doctor` — unified health check-graph (#527)

**Status:** design + v1 plan (deterministic-only). Phase 1 keystone of the
[roadmap](#) / the 2026-05-29 observability-doctor audit (Option C: deterministic
check-graph first, LLM diagnostician second).

## Problem

Detection is dense — ~27 DB-backed probes (`brain/health_probes.py::PROBES`) plus
the watchdog `Probe` Protocol classes — but the signals are **scattered**: each
probe writes its own `brain_knowledge` row and pages independently. Nothing
computes a single health score, nobody notices "5 unrelated probes degraded at
once" (systemic vs local), and a root failure (DB down) produces a _symptom
storm_ (every DB-backed probe also reds) instead of one root-cause report. There
is no single surface an operator can read from their phone, or a CLI that exits
nonzero in a script.

## Prior art (what we borrow)

- **OpenJarvis** `src/openjarvis/cli/doctor_cmd.py`: a uniform
  `CheckResult(name, status: ok|warn|fail, detail)`, each check a small function,
  `_run_all_checks()` aggregating, **registry-driven** enumeration
  (`_check_engines` iterates the EngineRegistry, not a hardcoded list), a
  `--json` mode, and **exit-code** semantics. We adopt the result type, the
  registry-driven aggregation, `--json`, and exit codes. OpenJarvis is a flat
  list with no score/correlation and is diagnose-only — we go further.
- **OpenClaw** `openclaw doctor --fix` (already invoked from
  `brain_daemon._run_openclaw_doctor` every 15 min): a single command that
  _diagnoses AND fixes_ known issues, `--fix` as the toggle, and the
  "looks-up-but-actually-degraded" insight. We adopt the `--fix` toggle, wired to
  our existing `REMEDIATIONS`.

**The moat we build (neither has):** a dependency **check-graph** for root-cause
correlation, a **health score**, and the hybrid LLM-diagnostician escalation.

## Architecture (v1, deterministic-only)

### Data source — read, don't re-run

The brain already runs every probe each cycle and persists the result to
`brain_knowledge` (`entity='probe.<name>'`, `attribute='health_status'`,
`value=<json result>`, `source='health_probe'`, `updated_at`). v1 doctor is an
**aggregator/reasoner over those persisted results** — it does NOT re-invoke
probes (avoids duplicating execution + cross-package probe imports from the CLI;
results are ≤5 min fresh). A `--live` re-run is a documented v1.1 follow-up.

> **Brain-freshness meta-check:** the doctor's first check reads the newest
> `brain_knowledge`/`brain.cycle_heartbeat` timestamp. If it's stale (> 2×cycle),
> the brain itself may be down — the doctor says so loudly and marks all probe
> results "stale" rather than reporting a falsely-healthy snapshot. This surfaces
> the #524 dead-man's-switch condition in the doctor view.

### `CheckResult` (borrowed shape)

```
@dataclass
class CheckResult:
    name: str
    status: str        # "ok" | "warn" | "fail" | "stale" | "suppressed"
    detail: str
    age_seconds: float          # how old the underlying probe result is
    metrics: dict = {}
    remediation: str | None = None   # REMEDIATIONS key if one exists
    root: str | None = None          # set when suppressed by an upstream failure
```

Normalization: a probe dict `{ok: false, severity: "critical", ...}` →
`status="fail"`; `ok:false` + `warning` → `"warn"`; `ok:true` → `"ok"`. Protocol
`ProbeResult` maps the same way.

### Static dependency graph (decided: explicit `depends_on`)

A small, explicit map in the doctor module — chosen over inferring from
probe category because it's debuggable and there are only a handful of roots:

```
DEPENDS_ON = {
    # everything DB-backed depends on db_ping
    "stuck_tasks": ["db_ping"], "approval_queue": ["db_ping"],
    "publish_rate": ["db_ping"], "cadence_slo": ["db_ping"], ...
    # generation depends on ollama
    "content_gen": ["ollama_models"], "quality_score": ["ollama_models"],
    # pipeline throughput depends on the worker being up
    "pipeline_throughput": ["worker_error_rate"], ...
}
```

**Root-cause rule:** when a node's upstream dependency is `fail`, a failing node
is marked `status="suppressed"`, `root=<dep>` — it's reported under the root, not
as an independent alarm. Roots (`db_ping`, `ollama_models`, `worker_error_rate`)
have no `depends_on` and surface directly.

### Health score + correlation

- **Score (0–100):** start at 100; subtract weighted penalties — `fail` on a
  P0/root check costs most, `warn` least, `suppressed`/`stale` don't double-count.
  Weights are `app_settings`-tunable (`doctor_weight_*`), defaults in code.
- **Correlation:** count of **independent** (non-suppressed, non-dependent)
  checks that are `fail`/`warn`. If ≥ `doctor_systemic_threshold` (default 3)
  distinct subsystems degrade at once → flag `systemic=true` ("not a local blip").

### CLI surface (`poindexter doctor`)

- `poindexter doctor` → human table: score, systemic flag, then checks grouped
  ok / warn / fail / suppressed(root) / stale.
- `poindexter doctor --json` → the full structured result (LLM/automation
  consumable, per `feedback_design_for_llm_consumers`).
- **Exit codes:** `0` healthy, `1` degraded (any warn/fail), `2` critical (a root
  failed or systemic). Scriptable.
- `poindexter doctor --fix` → for each `fail` check that has a `REMEDIATIONS`
  entry, run the existing remediation (reuses `health_probes._try_remediation` /
  `restart_service`), then re-read and report. Diagnose-only without `--fix`
  (OpenClaw posture). Customer-facing actions are never auto-fixed.

### Hybrid LLM hook (deferred to Phase 2, gated on #429)

When a check is `fail` with **no** `REMEDIATIONS` entry (or stays red past N
doctor runs after a `--fix`), the design escalates to the existing
`firefighter_service` LLM for a ranked diagnosis. v1 ships the deterministic core
and leaves a typed seam (`_escalate_unknown(checks) -> None` no-op) so the LLM
path lands cleanly once #429 DataFabric gives it cross-store (logs/metrics/traces)
reads. **No LLM in v1** — the recover/deliver path stays deterministic
(`feedback_calculated_vs_generated`).

## Files (v1)

- **New** `src/cofounder_agent/services/doctor.py` — `CheckResult`, `DEPENDS_ON`,
  `load_check_results(pool)` (reads `brain_knowledge`), `build_graph()` /
  root-cause suppression, `score()`, `correlate()`, `run_doctor(pool) ->
DoctorReport`. Pure-ish + unit-testable (feed fake `brain_knowledge` rows).
- **New** `src/cofounder_agent/poindexter/cli/doctor.py` — the `click` command
  (`--json`, `--fix`), wired in `cli/app.py` via `main.add_command(doctor_group,
name="doctor")`. `--fix` imports the brain remediation helper.
- **New** `tests/unit/services/test_doctor.py` — score math, root-cause
  suppression (DB-down suppresses dependents), correlation threshold, stale-brain
  meta-check, `--json` shape, exit-code mapping. Strict fixtures per the new
  CONTRIBUTING testing conventions (assert the contract, not a mock echo).
- **Optional v1** Grafana: a single "System Health Score" stat panel reading the
  doctor score (the doctor can also persist its score to `brain_knowledge` /
  `audit_log` so Grafana + Mission Control show it). Keep behind the core if it
  bloats the PR.

## Out of scope for v1 (tracked as follow-ups)

- `--live` probe re-run.
- The LLM diagnostician (Phase 2 / #429).
- Auto-running the doctor as a brain probe that pages on score drop (v1 is
  on-demand CLI + the existing per-probe pages already fire).

## Definition of done

- `poindexter doctor` aggregates ALL `PROBES` + Protocol probes from real
  `brain_knowledge`, shows score + systemic flag + root-cause grouping.
- DB-down (root) shows ONE root failure with dependents suppressed under it, not
  a 10-alarm storm.
- `--json` parses; exit codes correct; `--fix` triggers a real `REMEDIATIONS`
  action on a red check and re-reports.
- Stale `brain_knowledge` → doctor flags brain-down, doesn't report false-healthy.
- Tests green; no operator info in public-bound files.
