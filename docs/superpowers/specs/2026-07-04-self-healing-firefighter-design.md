# Self-Healing Firefighter — Design Spec

- **Date:** 2026-07-04
- **Status:** Approved design — ready for implementation plan
- **Owner:** Matt (operator)
- **Supersedes:** the diagnosis-only `ops_triage` firefighter (`/api/triage`), disabled 2026-07-04 (`ops_triage_enabled=false`)

## Problem

The alert pipeline pages the operator (Discord + Telegram) but does not _fix_ anything.
The LLM addition that shipped earlier (`ops_triage`) only produced a **diagnosis paragraph**
appended to the alert — it fired a second message, doubled the reading, and took no action.
That was disabled. This spec defines the replacement: a firefighter that performs **operational
recovery** — it picks a bounded, reversible action, applies it, verifies the result, and pages
**only if the problem is still broken after the attempt**.

Today the codebase has a split personality on remediation:

- A handful of **hardcoded** conditions self-heal autonomously (site/API down → `restart_service`).
- The generic `alert_actions` / `plugin.remediation.<alertname>` table is **dry-run only** — it
  logs the _intended_ action and never executes.

This design unifies both behind one dispatcher and adds an LLM fallback for the long tail.

## Goals

1. Autonomously recover from transient and known-shaped operational failures — no operator action.
2. Cut alert noise: successful recoveries are **silent** (audit + Grafana), not paged.
3. Cover the long tail: an alert with no codified rule still gets a reasonable first response.
4. Get smarter over time: successful novel fixes become **candidate rules** the operator promotes.
5. Stay safe under autonomy: every action is idempotent, reversible, blast-radius-bounded,
   verify-gated, and circuit-broken.

## Non-goals

- **No code-level bug-fixing.** The firefighter never edits source or opens PRs. That is a
  different system (the disabled `alert-triage` / `issue-resolver` scheduled agents) and is
  explicitly out of scope.
- **No cloud LLM.** The selector runs on **local Ollama only** — never Anthropic, never a cloud
  tier (`no_paid_apis`).
- Not a replacement for deterministic monitoring. It sits _downstream_ of the existing alert
  pipeline, not in place of it.

## Key decisions (operator-approved)

| Decision       | Choice                                            | Rationale                                                                                            |
| -------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Fix type       | **Operational recovery only**                     | Bounded/reversible actions make autonomy safe.                                                       |
| Engagement     | **Rules-first, LLM for the long tail**            | Deterministic where known; LLM only for un-ruled persistent alerts.                                  |
| Autonomy       | **Autonomous apply → verify → page-on-fail**      | Maximum self-heal; safety carried by structural guardrails, not a human gate.                        |
| Selector model | **Local Ollama `llama3.2:3b`** (DB-pinned)        | Constrained selection over ~6 actions; small is enough; swappable by setting.                        |
| Rollout        | **Ship enabled** (`ops_firefighter_enabled=true`) | Self-heal from day one. Seeded rules mirror today's behavior, so no behavior change for known cases. |

## Architecture

Lives in the **brain daemon**, extending the existing `alert_dispatcher` poll loop. The brain
already owns `restart_service`, `auto_remediate`, and the alert cycle — this is an enhancement, not
a parallel service. (The LLM _selection_ step is the one exception that reaches into the worker; see
the LLM-fallback bullet.)

**Delivery — two sequential plans.** v1 (Plan A, the deterministic core) ships the registry, the
`remediation_rules` table, rule-matching, verify-then-page, the circuit breaker, and the two
executors that have real brain primitives today — `restart_container` (arbitrary docker container)
and `run_auto_remediate` (stuck-task / stale-approval cleanup). Plan B adds the LLM long-tail (the
worker selector route, the brain selector client, and the candidate-rule learning loop) on top. Each
plan is independently shippable; the remaining allowlist actions land as their primitives are wired.

Three pieces:

### 1. Action Registry (the new seam) — `brain/remediation/registry.py`

A map `action_name → async executor(params, ctx) → ActionResult`. Each executor is a thin wrapper
over a primitive that already exists. The registry is the single choke point through which **both**
the deterministic and LLM decision paths execute — so the decision-maker is swappable behind it.

`ActionResult = {status: "ok" | "failed" | "skipped", detail: str, latency_ms: int}`

`ctx: RemediationContext` carries `pool`, `http_client`, `site_config`, `logger`, and the triggering
alert. Executors **never raise into the poll loop** — failures return `status="failed"`.

**Admission criterion for any action:** it must be idempotent, reversible, and blast-radius-bounded.

### 2. Decision layer (two sources, one dispatcher)

- **Deterministic:** `remediation_rules` declarative table (same pattern as the other five
  data-plane tables — `external_taps`, `qa_gates`, …). The currently-hardcoded self-heals are
  seeded here so they become data-driven and auditable.
- **LLM fallback (Plan B):** the selector runs on the **worker**, not in the brain (the brain image
  is asyncpg/httpx/urllib only — it cannot import `dispatch_complete`). The brain POSTs the alert +
  the registry allowlist to a new worker route `POST /api/remediation/select` over the same OAuth
  HTTP path it already uses for `/api/triage`; the worker runs local Ollama (`ops_firefighter_model`)
  via `dispatch_complete` and returns a schema-validated `{action_name, params, confidence, reasoning}`
  **constrained to the allowlist the brain sent**. Off-list / malformed / low-confidence → no-op +
  page. Worker/Ollama unreachable → page.

### 3. Persistence — reuse existing rails

- Every attempt and every verify result → `audit_log` rows (append-only), correlated by a
  `remediation_run_id` (uuid).
- A successful **LLM-chosen** fix for an un-ruled alert → `emit_finding('remediation_candidate_rule', …)`
  → flows into the existing **Findings** dashboard + `findings_list` triage, where the operator
  promotes it to a `remediation_rules` row (the learning loop).
- Circuit-breaker state is **computed from `audit_log`** (count recent attempts of `(alert, action)`
  in-window) — no new state table.

**Net new surface:** one table, one registry module + executors, the dispatcher wiring, and the
Grafana row. Everything else reuses existing primitives and rails.

## The action allowlist

Initial registered actions (each wraps a tested primitive):

| `action_name`                    | Wraps                                                    | Params                      | Notes                                                                              |
| -------------------------------- | -------------------------------------------------------- | --------------------------- | ---------------------------------------------------------------------------------- |
| `restart_container`              | `restart_service` (docker restart)                       | `{service, grace_seconds?}` | The Ollama-restart rule uses this (deterministic — see circular-dependency guard). |
| `recover_host`                   | recovery-agent `POST /recover` (port 9841)               | `{target}`                  | Host-level recovery on Matt's PC.                                                  |
| `rerun_job`                      | Prefect / job trigger                                    | `{job_name}`                | e.g. re-run a failed `run_taps`.                                                   |
| `clear_checkpoint`               | poisoned-LangGraph-checkpoint clear (from stale-reclaim) | `{task_id}`                 | Unwedges a task stuck mid-graph.                                                   |
| `requeue_batch` / `expire_batch` | topic-batch reset (`auto_remediate` logic)               | `{batch_id}`                | e.g. a topic batch stuck 39h.                                                      |
| `run_auto_fix_job`               | existing findings `auto_fix` jobs                        | `{fix_job}`                 | e.g. `FixBrokenExternalLinksJob`.                                                  |

Adding a new action = register one executor + (optionally) seed one `remediation_rules` row.

## Data model

### `remediation_rules` (new table)

| Column                      | Type                               | Notes                                                                                 |
| --------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------- |
| `id`                        | serial PK                          |                                                                                       |
| `alertname`                 | text NULL                          | Exact match on the alert name…                                                        |
| `match_regex`               | text NULL                          | …or a regex over `source:dedup_key` / alertname. At least one of the two is required. |
| `action_name`               | text NOT NULL                      | Must be a registry key (validated at seed/insert).                                    |
| `params`                    | jsonb NOT NULL DEFAULT `'{}'`      |                                                                                       |
| `enabled`                   | boolean NOT NULL DEFAULT `true`    | Per-rule kill switch.                                                                 |
| `max_attempts_per_window`   | int NULL                           | NULL → inherit global default.                                                        |
| `window_minutes`            | int NULL                           | NULL → inherit global default.                                                        |
| `verify_after_seconds`      | int NULL                           | NULL → inherit global default.                                                        |
| `description`               | text NOT NULL DEFAULT `''`         | `app_settings_value_not_null` discipline.                                             |
| `created_at` / `updated_at` | timestamptz NOT NULL DEFAULT now() |                                                                                       |

Table DDL ships in a timestamped migration; **seed rows ship in `baseline.seeds.sql`**
(`seed_data_in_baseline`), pre-populated with today's hardcoded self-heals.

### `audit_log` rows

- At execution: `event_type='remediation_action'`, `details = {remediation_run_id, alert:{name,
severity, fingerprint, repeat_count}, action_name, params, source:"rule"|"llm", rule_id?, model?,
confidence?, reasoning?, execution:{status, detail, latency_ms}}`.
- At verify: `event_type='remediation_verify'`, `details = {remediation_run_id, result:"resolved"|
"still_firing", checked_at}`.

## Control flow

Extends the existing dispatcher poll loop. Per firing alert this cycle:

```
if not ops_firefighter_enabled:                       → page as today (unchanged)

rule = match_enabled_rule(alert)                      # remediation_rules
if rule:
    action, params, source = rule.action_name, rule.params, "rule"
elif alert.is_persistent and ollama_available:        # persistence + inference gates
    sel = llm_select(alert, registry.allowlist)       # local ollama, JSON-constrained
    if sel is invalid / off-list / sel.confidence < ops_firefighter_min_confidence:
        → page, stop
    action, params, source = sel.action_name, sel.params, "llm"
else:
    → page (no rule; or Ollama down), stop

if action not in ops_firefighter_action_allowlist:    → page "action disabled", stop
if circuit_breaker_tripped(alert, action):            → page "auto-remediation exhausted", stop
if global_actions_this_hour >= ops_firefighter_max_actions_per_hour: → page "rate cap", stop

run_id = uuid4()
result = registry.execute(action, params, ctx)
audit_log.write("remediation_action", run_id, alert, action, params, source, result)
schedule_verify(run_id, alert, action, after = rule_or_global.verify_after_seconds)
```

**Persistence trigger** (LLM path only): an un-ruled alert qualifies when
`repeat_count >= ops_firefighter_min_repeats` **or** it has been firing longer than
`ops_firefighter_min_age_minutes`. Most alerts self-resolve on the first cycle and never reach the
model.

**Verify loop.** Pending verifies are `remediation_action` rows with no matching
`remediation_verify` row; the dispatcher re-checks each on the first cycle after
`verify_after_seconds` has elapsed. It re-evaluates the alert's firing state via the same signal
that produced it — a subsequent firing of the same fingerprint newer than the action timestamp means
_still broken_; its absence means _resolved_.

- **Resolved** → write `remediation_verify(resolved)`. **No page.** If `source="llm"`,
  `emit_finding('remediation_candidate_rule', …)`.
- **Still firing** → write `remediation_verify(still_firing)`, **page** ("attempted
  `<action>`, still firing after Ns"), and increment the circuit breaker for `(alert, action)`.

## Error handling & safety

- **Circuit breaker** — per `(alert, action)`, computed from `audit_log`: more than
  `max_attempts_per_window` (default 3) inside `window_minutes` (default 60) → trip, stop, page once.
  Kills restart-loops.
- **Global backstop** — `ops_firefighter_max_actions_per_hour` (default 10) across all actions.
- **Ollama-down degradation** — availability reuses the brain's existing Ollama health signal
  (falling back to treating a selector-call timeout / connection-refused as unavailable); unavailable
  → skip the LLM path, page. Never blocks the cycle.
- **Circular-dependency guard** — "restart Ollama" (and any infra whose failure would take Ollama
  down) is a **deterministic rule**, never the LLM path — the model is never required to fix the
  thing it runs on.
- **Executor isolation** — executors return `status="failed"` rather than raising; a bad executor
  cannot crash the poll loop.
- **LLM output is untrusted** — validated against a strict schema (`action_name` ∈ registry keys,
  params well-formed, confidence present) via `maybe_unwrap_json` + pydantic; anything off →
  no-op + page. Model quality affects recovery _rate_, never _safety_.

## Config surface

Defaults in `settings_defaults.py` (`seed_data_in_baseline`); nothing in `.env`.

| Setting                                | Default                       | Purpose                                                           |
| -------------------------------------- | ----------------------------- | ----------------------------------------------------------------- |
| `ops_firefighter_enabled`              | `true`                        | Master switch. **Ships enabled.**                                 |
| `ops_firefighter_model`                | `llama3.2:3b`                 | Local Ollama selector model.                                      |
| `ops_firefighter_min_repeats`          | `2`                           | LLM path engages after an un-ruled alert repeats this many times… |
| `ops_firefighter_min_age_minutes`      | `10`                          | …or has fired this long.                                          |
| `ops_firefighter_min_confidence`       | `0.6`                         | LLM selections below this → page, don't act.                      |
| `ops_firefighter_max_actions_per_hour` | `10`                          | Global backstop.                                                  |
| `ops_firefighter_action_allowlist`     | `''` (empty = all registered) | CSV of enabled `action_name`s — per-action-type kill switch.      |

Global defaults for the per-rule columns (`max_attempts_per_window=3`, `window_minutes=60`,
`verify_after_seconds=120`) also live in `settings_defaults.py`.

## Observability

`grafana_everything` — panels ship in the same PR, 960px portrait.

A **"Self-Healing / Remediation"** row on the **System Health** board (not a new board), sourced from
`audit_log`:

- Auto-recoveries (silent successes) rate — the "how much it's quietly handling" stat.
- Attempted-but-failed (paged) count · circuit-breaker trips · actions-by-type.
- **Rule vs LLM source split** and **LLM selector hit-rate per model** — answers "is `llama3.2:3b`
  good enough?" empirically and drives the model-eval decision.
- Latest-actions table (alert · action · source · outcome · verify).

The **candidate-rule loop** rides the existing **Findings** dashboard + `findings_list` triage — no
new surface.

## Testing plan

TDD, RED-first (`docs_and_tests_default`). Decision logic is pure over an **injected registry**;
executors are mocked in unit tests (we never actually `docker restart` in a unit test — executors
are thin wrappers over primitives that already carry their own tests).

1. **Registry dispatch** — known action → right executor + params; unknown action → structured
   no-op, never raises.
2. **Rule match** — enabled matches; disabled skipped; no-match → None.
3. **LLM output validation (safety-critical)** — valid → acts; off-list `action_name` → reject+page;
   malformed JSON → reject+page; confidence < min → reject+page. A mocked model returning garbage
   executes **nothing**.
4. **Persistence trigger** — non-persistent un-ruled alert → LLM path not invoked; persistent →
   invoked.
5. **Circuit breaker** — N in window OK; N+1 → trip+page, no execution (seeded from `audit_log`).
6. **Global rate cap** — over cap → no execution + page.
7. **Verify loop** — resolved → success + no page (+ candidate-rule finding on LLM source); still
   firing → page + breaker increment.
8. **Ollama-down** — LLM path skipped → page, loop continues (no raise).
9. **Master switch off** — pages as today, zero remediation behavior.
10. **Action-allowlist gate** — a registered but not-allowlisted action → reject+page.

Docs updated in the same PR: `docs/operations/self-healing.md` (the new detect → **act** → verify →
escalate loop) and `docs/integrations/webhook_alertmanager_dispatch.md` (the remediation hook is now
live, not dry-run).

## Rollout

- Ships **enabled**. Seeded `remediation_rules` = today's hardcoded self-heals, so known-case
  behavior is unchanged; only the LLM long-tail path is genuinely new, and it is conservative by
  construction (persistence + confidence + Ollama gates + bounded actions + verify + breaker).
- Brain image **bakes** `brain/`, so deploy = rebuild + restart the brain container
  (`rebuild_authority`), not just a restart.
- **Public-mirror / consumer-install note:** this ships default-on to Poindexter and consumer
  stacks. Adopters can dial it back via `ops_firefighter_action_allowlist`, per-rule `enabled`, or
  the master switch. Every action is reversible/bounded, so the default-on blast radius is capped by
  design.

## Future (out of scope now)

- Optional shadow/dry-run graduation mode (per-`(alert,action)` track-record before going live) —
  the auto-publish edit-distance pattern, if autonomous-apply proves too eager.
- Champion–challenger model eval on the selector (`model_eval_loop`) driven by the per-model
  hit-rate panel.
- Auto-promotion of high-confidence, repeatedly-correct candidate rules (currently operator-gated).
