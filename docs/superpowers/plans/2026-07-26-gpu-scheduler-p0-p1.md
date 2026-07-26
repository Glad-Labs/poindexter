# GPU Scheduler P0+P1 Implementation Plan

> **For agentic workers:** implement task-by-task, failing test first. Steps use
> checkbox (`- [ ]`) syntax for tracking. Scope is **P0 (observe) + P1
> (contracts + admission) ONLY** — P2 caller migration and P3 deferred
> completion get their own plan after a P1 soak.

**Design:** `docs/superpowers/specs/2026-07-26-gpu-scheduler-queue-admission-design.md`
**Issue:** Glad-Labs/poindexter#914

**Goal:** land the scheduler's observation layer (rolling per-key duration
stats, observable wait queue, console/Grafana surfaces) and the admission
machinery (`max_wait_s`/`priority` contracts, ETA + per-card fit checks,
`GpuBusyError`) — **provably inert**: behavior is bit-identical for every
existing caller until P2 migrates them.

**Architecture recap:** the existing lock (asyncio + pg advisory, reentrancy
ContextVar, unload-before-render, 900s ceiling) is kept untouched as the
enforcement primitive. P0 adds passive recording around it; P1 adds a
decision step _before_ waiting that only activates for callers that opt in
via new kwargs — and no caller opts in within this plan.

## Global constraints

- New `app_settings` defaults go in `services/settings_defaults.py`
  `DEFAULTS` (+ metadata), NEVER in migrations. `''` = unset sentinel; NULL
  forbidden. Read via `SiteConfig` DI — in `gpu_scheduler.py` that means the
  existing `_sc()` / `_cfg_int` helpers, no new globals.
- New tables = one timestamped migration (`python scripts/new-migration.py`),
  `IF NOT EXISTS` bodies, `down()` provided. Run
  `scripts/ci/migrations_lint.py` before committing.
- The scheduler carries **no pool reference by design** — DB writes from the
  lock lifecycle use the lazy `resolve_database_url()` + short-lived asyncpg
  connection pattern `_record_task_session` already uses. Every such write is
  best-effort with a `# silent-ok:` annotation (lint_silent_excepts is a CI
  gate) and must never gate the lock lifecycle.
- **Key P0 correction to the design's ETA-source note:** `gpu_task_sessions`
  only records sessions that carry a `task_id`. The stats capture (Task A2)
  therefore records on **every** release — task-less background jobs
  included — into its own `gpu_lease_stats` upsert.
- Existing gpu_scheduler tests stay untouched and green — that is the
  behavior-neutrality regression guard. `gpu_sched_enabled` defaults `false`
  and gates ONLY the P1 admission step; P0 observability is unconditional
  (observability is never flag-gated — feedback_grafana_everything).
- New worker route ⇒ register in `_WORKER_ROUTES`
  (`utils/route_registration.py`) AND bump the count guard in
  `tests/unit/utils/test_route_registration.py` (36 at plan time — bump from
  whatever it is when you land). New service file ⇒ run
  `python scripts/regen-services-doc.py` **in the same PR** (CI gate).
- Console additions follow the wire-contract rule
  (console-api-wire-contract-mock-first): every new `PX.api` live method gets
  a `contracts.manifest.js` row and/or a dedicated vm-harness test; mock
  branch is honest-empty. `npm run test:console` is the gate.
- Tests run from `src/cofounder_agent` with the worktree-safe env:
  `~/.cache/pypoetry/virtualenvs/poindexter-backend-esjils9P-py3.13/bin/python -m pytest <path> -q -o addopts=""`.
- Conventional commits; end with
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## PR-A — P0: observe (behavior-neutral by construction)

### Task A1: schema — `gpu_lease_stats` + `gpu_queue`

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_add_gpu_scheduler_observability_tables.py`
- Test: `src/cofounder_agent/tests/integration_db/test_gpu_scheduler_tables.py`

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS gpu_lease_stats (
    owner       TEXT NOT NULL,          -- "ollama" | "image_gen" | "video"
    phase       TEXT NOT NULL,          -- caller phase label ('' when unset)
    samples     BIGINT NOT NULL DEFAULT 0,
    ewma_ms     DOUBLE PRECISION,       -- exponentially weighted mean
    p50_ms      DOUBLE PRECISION,       -- P² streaming quantile state
    p90_ms      DOUBLE PRECISION,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, phase)
);
CREATE TABLE IF NOT EXISTS gpu_queue (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pid          INT NOT NULL,           -- os.getpid() of the waiter's process
    owner        TEXT NOT NULL,
    model        TEXT,
    phase        TEXT,
    priority     TEXT NOT NULL DEFAULT 'pipeline',
    enqueued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gpu_queue_enqueued ON gpu_queue (enqueued_at);
```

**Steps:**

- [ ] Failing integration_db test: tables exist post-migration; `gpu_lease_stats`
      upsert on PK conflict updates in place; `gpu_queue` insert/delete
      round-trips.
- [ ] Write the migration (docstring cites #914 + the spec path). `down()`
      drops both.
- [ ] `python scripts/ci/migrations_lint.py` clean.

### Task A2: rolling-stats math + writer — `services/gpu_lease_stats.py`

**Files:**

- Create: `src/cofounder_agent/services/gpu_lease_stats.py`
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_lease_stats.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class LeaseStats:
    samples: int; ewma_ms: float | None; p50_ms: float | None; p90_ms: float | None

def fold_sample(prev: LeaseStats, duration_ms: float, *, alpha: float = 0.2) -> LeaseStats
    # PURE: EWMA + P²-style streaming quantile update. No I/O.

async def record_release(owner: str, phase: str, duration_ms: float) -> None
    # Lazy-connection upsert (the _record_task_session pattern).
    # Best-effort; # silent-ok on failure. Fire-and-forget from the caller.

async def read_stats(owner: str, phase: str) -> LeaseStats | None
```

**Steps:**

- [ ] Failing pure tests for `fold_sample`: first sample seeds all fields;
      EWMA converges toward a shifted mean; p50/p90 track a known
      distribution within tolerance; quantile state is monotone-safe on
      constant input.
- [ ] Implement `fold_sample` (pure, no numpy — a 5-marker P² implementation
      or a bounded-reservoir alternative; pick ONE and pin it with tests).
- [ ] Failing tests for `record_release`/`read_stats` against a FakePool-style
      stub (the `tests/unit/brain/_remediation_fakes.py` shape) asserting the
      upsert SQL folds via `fold_sample`, not raw overwrite.
- [ ] Implement; annotate the swallow (`# silent-ok: observability capture
    must never gate the lock lifecycle`).

### Task A3: capture hook in the release path

**Files:**

- Modify: `src/cofounder_agent/services/gpu_scheduler.py` (the `finally`
  block that already computes `duration` and calls `_record_task_session`)
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_scheduler_stats_capture.py`

**Steps:**

- [ ] Failing test: a completed `gpu.lock("ollama", phase="x")` session
      schedules exactly one `record_release("ollama", "x", ~duration)`
      (monkeypatch the module fn; drive the lock with the existing test
      harness patterns) — INCLUDING when `task_id is None`.
- [ ] Failing test: `record_release` raising never breaks release (lock
      reacquirable afterwards).
- [ ] Implement: `asyncio.create_task(record_release(...))` fire-and-forget
      next to the `_record_task_session` call, OUTSIDE the `if task_id:`
      guard. Existing suite untouched-green.

### Task A4: queue mirroring around the wait

**Files:**

- Modify: `services/gpu_scheduler.py` (the "GPU busy — waiting" branch and
  both acquire outcomes)
- Create: `src/cofounder_agent/services/gpu_queue_mirror.py` (insert/delete/
  reap helpers, lazy-connection, best-effort)
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_queue_mirror.py`

**Steps:**

- [ ] Failing tests (stub pool): waiter row inserted when the in-process lock
      is contended; deleted on acquire AND on timeout/cancel (finally
      semantics); `reap_stale(older_than_s)` deletes rows past the horizon
      (crash orphans — a process that died mid-wait leaves a row; each
      mirror write piggybacks one reap).
- [ ] Implement mirror; wire into `lock()` only in the contended branch
      (uncontended fast path stays zero-I/O).
- [ ] Behavior-neutrality: mirror failures logged (`# silent-ok`), never
      raised; existing timeout tests untouched-green.

### Task A5: read route — `GET /api/gpu/queue`

**Files:**

- Create: `src/cofounder_agent/routes/gpu_queue_routes.py`
- Modify: `utils/route_registration.py` (+ count-guard test)
- Test: `src/cofounder_agent/tests/unit/routes/test_gpu_queue_routes.py`

**Response shape:**

```json
{
  "holder":  {"owner": "...", "model": "...", "phase": "...", "held_for_s": 12.3} | null,
  "waiters": [{"owner": "...", "model": "...", "priority": "...", "waiting_s": 4.2}],
  "stats":   [{"owner": "...", "phase": "...", "p50_ms": ..., "p90_ms": ..., "samples": ...}]
}
```

Holder comes from the scheduler's in-process `_current_owner`/`_current_model`
(this process) — cross-process holder identity ships with the queue rows'
`pid`; the route is honest about a holder it can't see (`null` + waiters
still listed).

**Steps:**

- [ ] Failing route tests: 200 shape above (stub service), auth required
      (401 unauthed — the `_build_app` override pattern from
      `test_service_restart_routes.py`), empty state renders `holder: null,
    waiters: [], stats: []`.
- [ ] Implement (thin adapter — SQL lives in the mirror/stats services, per
      adapter-purity).
- [ ] Register in `_WORKER_ROUTES`; bump the manifest count guard with a
      dated docstring line.
- [ ] `python scripts/regen-services-doc.py` (new service files) — same PR.

### Task A6: console — GPU panel "holder / waiting" strip

**Files:**

- Modify: `src/cofounder_agent/console/js/api.js` (new `gpuQueue()` —
  live: `GET /api/gpu/queue`; mock: honest-empty `{holder:null,waiters:[],stats:[]}`)
- Modify: `console/js/app.jsx` (poll ~10s via `usePolledResource`),
  `console/js/panels.jsx` (strip under the GPU HUD: holder line + up to 3
  waiters + queue-empty state)
- Test: `console/js/__tests__/contracts/contracts.manifest.js` row
  (+ fixture) and/or `api.gpuqueue.test.js` (vm harness)

**Steps:**

- [ ] Contract row first (red): `gpuQueue` → `GET /api/gpu/queue`.
- [ ] Implement adapter + panel; mock branch honest-empty; no fabricated
      waiters (feedback_no_dummy_data).
- [ ] `npm run test:console` green (bump nothing else).

### Task A7: Grafana — queue depth + wait p90

**Files:**

- Modify: `infrastructure/grafana/dashboards/hardware-power.json` (two
  panels on the GPU row: `gpu_queue` depth over time, `gpu_lease_stats`
  p90 by owner/phase — postgres datasource)

**Steps:**

- [ ] Add panels (960px-portrait-friendly per feedback_grafana_everything);
      one-line CHARTER note in each description.
- [ ] `python scripts/ci/grafana_panels_lint.py` clean.

### PR-A exit criteria

- Full backend suite + console suite + all CI lints green; zero changes to
  any existing lock behavior test.
- On prod after deploy: `gpu_lease_stats` rows accumulate within one
  pipeline run; `/api/gpu/queue` shows a real holder during a render; the
  console strip renders it.

---

## PR-B — P1: contracts + admission (machinery live, provably inert)

### Task B1: pure admission calculator — `services/gpu_admission.py`

**Files:**

- Create: `src/cofounder_agent/services/gpu_admission.py`
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_admission.py`

**Interfaces:**

```python
class GpuBusyError(RuntimeError):
    def __init__(self, reason: str, eta_seconds: float | None): ...
    # reason ∈ {"eta_exceeds_budget", "no_fit"}

@dataclass(frozen=True)
class AdmissionInputs:
    max_wait_s: float | None      # None = legacy path, admission SKIPPED
    holder_key: tuple[str, str] | None   # (owner, phase) of current holder
    holder_elapsed_s: float | None
    holder_stats: LeaseStats | None      # from gpu_lease_stats
    eta_fallback_s: float
    free_gpu0_gb: float | None    # None = telemetry unavailable → fit check SKIPPED (fail-open)
    evictable_gpu0_gb: float      # 0.0 when unknown (conservative)
    headroom_gb: float
    model_estimate_gb: float | None      # None = unknown model → fit check SKIPPED

@dataclass(frozen=True)
class AdmissionDecision:
    action: str    # "grant" | "grant_after_unload" | "reject"
    reason: str | None
    eta_seconds: float | None

def decide(i: AdmissionInputs) -> AdmissionDecision   # PURE, exhaustive
```

**Steps:**

- [ ] Failing pure tests, exhaustively: `max_wait_s=None` ⇒ always grant
      (legacy inertness); ETA = `p90/1000 − elapsed` floored 0, fallback
      when stats None; `eta > max_wait_s` ⇒ reject with eta; fit math
      `estimate ≤ free − headroom` ⇒ grant, `≤ free + evictable − headroom`
      ⇒ grant_after_unload, else reject `no_fit`; every `None` telemetry
      input fails OPEN (grant) — admission can only ever be as strict as its
      data is real.
- [ ] Implement `decide` (no I/O, no imports beyond dataclasses/stats type).

### Task B2: per-card inputs — extend `gpu_registry`

**Files:**

- Modify: `src/cofounder_agent/services/gpu_registry.py`
- Modify: `scripts/nvidia-smi-exporter.py` (per-process metric; container
  built by `scripts/Dockerfile.gpu-exporter` — rebuild `poindexter-gpu-exporter`
  to apply)
- Test: extend `tests/unit/services/test_gpu_registry.py`

**Steps:**

- [ ] `gpu_registry.free_gb(gpu_index) -> float | None` from Prometheus
      `nvidia_gpu_memory_{total,used}_mib{gpu="0"}` (instant queries, memo
      TTL ≤ 15s, `None` on any failure — feeds the fail-open input).
- [ ] Exporter: add `nvidia_gpu_process_memory_mib{gpu,pid,process}` rows
      (nvidia-smi `--query-compute-apps` is already parsed row-wise
      post-#1956; extend, don't rewrite).
- [ ] `gpu_registry.evictable_ollama_gb(gpu_index) -> float` = sum of
      per-process rows on that card whose process matches the primary
      llama-server, **0.0 on any failure or if the metric is absent**
      (per the design's per-card mandate — never the `/api/ps` cross-card
      total). Unit tests stub the Prometheus payloads for: clean case,
      spilled-model case (rows on both cards → only gpu-0 share counted),
      metric-absent case (→ 0.0).

### Task B3: priority gate (in-process ordering)

**Files:**

- Modify: `services/gpu_scheduler.py` — replace the bare `asyncio.Lock`
  with an internal `_PriorityGate`
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_priority_gate.py`

**Semantics:** waiters park on per-waiter futures; release grants the head
of `sorted by (class_rank, effective_enqueue_ts)` where `effective_ts`
subtracts `gpu_sched_aging_seconds` per full aging period elapsed
(background promotes, never starves). All legacy callers enter as
`pipeline` ⇒ single class ⇒ strict FIFO ⇒ **identical to asyncio.Lock** —
that equivalence, plus the untouched existing suite, is the neutrality
proof.

**Steps:**

- [ ] Failing tests: single-class FIFO matches asyncio.Lock ordering under
      interleaved acquires; higher class jumps queue; aging promotes after
      the window; cancellation of a parked waiter removes it without
      wedging the grant chain; timeout path still raises
      `GpuLockTimeoutError` with the same finding; reentrancy ContextVar
      still passes through.
- [ ] Implement `_PriorityGate`; keep `locked()` shim for the "GPU busy —
      waiting" log branch.
- [ ] Full existing gpu_scheduler suite untouched-green (the point).

### Task B4: wire admission into `lock()`

**Files:**

- Modify: `services/gpu_scheduler.py` (`lock()` signature + pre-wait step)
- Test: `src/cofounder_agent/tests/unit/services/test_gpu_admission_wiring.py`

**Steps:**

- [ ] `lock()` gains `max_wait_s: float | None = None`,
      `priority: str = "pipeline"`. Docstring: contracts summary + pointer
      to the spec.
- [ ] Failing tests: with `gpu_sched_enabled=false` OR `max_wait_s=None`,
      admission is never consulted (spy on `decide`) — double inertness;
      with flag on + finite budget, a reject raises `GpuBusyError`
      pre-wait and emits a `gpu_admission_rejected` finding (dedup-keyed
      `owner:phase:reason`); `grant_after_unload` calls the existing
      eviction helper before proceeding; `max_wait_s` also caps the actual
      wait (min with the 900s ceiling).
- [ ] Implement: assemble `AdmissionInputs` (stats read, registry reads,
      `estimate_model_vram_gb` — reuse the dispatcher's arch-read helper;
      unknown ⇒ None), call `decide`, act. All assembly reads are
      individually fail-open to `None`/0.0.

### Task B5: settings + docs

**Files:**

- Modify: `services/settings_defaults.py`, `docs/operations/` (a short
  "GPU scheduler" section or the self-healing doc's sibling), the spec's
  status line → `phase 1 landed (inert)`

**Keys:** `gpu_sched_enabled=false` · `gpu_sched_eta_fallback_seconds=120` ·
`gpu_sched_aging_seconds=300` · `gpu0_headroom_gb=6`.

**Steps:**

- [ ] Seed keys (+ metadata descriptions written for LLM consumers).
- [ ] `python scripts/ci/settings_seed_value_drift_lint.py` clean.
- [ ] Docs + regen-services-doc (same PR).

### Task B6: full-surface verification

- [ ] Entire backend unit suite green; integration_db green; console suite
      green; ruff + mypy on touched files; lint_silent_excepts;
      migrations lint; grafana lint; route-count guard consistent.
- [ ] Grep-proof of inertness: no production call site passes `max_wait_s`
      (assert in a test: `grep -r "max_wait_s=" src/ --include="*.py"`
      matches only gpu_scheduler/gpu_admission/tests).

### PR-B exit criteria / soak

- Merge with `gpu_sched_enabled=false`. Flip to `true` on prod (still inert
  — no contract callers exist). Soak ≥3 days watching: `gpu_lease_stats`
  p90s look sane vs known render durations; `/api/gpu/queue` matches
  reality during a busy render; zero new findings kinds firing.
- P2 (caller migration, one group per PR, starting with the fail-soft
  rails) gets its own plan informed by the soak numbers.
