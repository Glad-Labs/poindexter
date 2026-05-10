# Prefect Cutover (Glad-Labs/poindexter#410)

The content pipeline's dispatch layer (polling, retry, heartbeat, stale-task
sweep) lived in `services/task_executor.py` since 2026-04. This doc tracks
the staged rollout that moves it to the Prefect server already running
locally at `http://localhost:4200`.

The pipeline body itself didn't change. Lane C
([`docs/architecture/langgraph-cutover.md`](langgraph-cutover.md)) already
moved orchestration to LangGraph + the `canonical_blog` template; this lift
only changes WHO calls the pipeline (a Prefect deployment instead of a
homegrown asyncio polling daemon) and WHEN (a Prefect schedule instead of
`_process_loop`'s 5-second poll).

## Why

`services/task_executor.py` is ~1,500 LOC of:

- A polling loop (`_process_loop`) that wakes every `poll_interval` seconds
- A claim helper that grabs `pipeline_tasks` rows where `status='pending'`
- Heartbeat tracking (`_last_task_started_at`, idle alert thresholds)
- A retry loop (`_auto_retry_failed_tasks`) that resurrects failed tasks
- A stale-task sweep (`_sweep_stale_tasks`) that times out stuck `in_progress` rows

Every one of those is core Prefect functionality. Trading the homegrown
daemon for Prefect means future bugs go to the Prefect community instead
of to Matt's late nights, and the operator UI moves from custom metrics
endpoints to `http://localhost:4200`.

## How dispatch works today

```
TaskExecutor (asyncio loop, every 5s)
   │
   ├─ throttle check → if approval queue full, sleep
   │
   ├─ get_pending_tasks(limit=10)
   │
   ├─ for each task:
   │     ├─ claim (UPDATE status='in_progress')
   │     ├─ heartbeat (every 30s while processing)
   │     └─ call process_content_generation_task(task_id, ...)
   │              │
   │              ▼
   │         (Lane C path: TemplateRunner + canonical_blog)
   │
   ├─ stale-task sweep (every poll_interval × N)
   └─ auto-retry sweep (every poll_interval × M)
```

## How dispatch works post-cutover

```
Prefect server (cron */2 * * * *)
   │
   ├─ enqueue content_generation_flow run
   │
   └─ Prefect work pool (concurrency=1)
        │
        ▼
   content_generation_flow
      │
      ├─ claim_pending_task (FOR UPDATE SKIP LOCKED)
      │   ├─ if queue empty → exit clean ({"claimed": False})
      │   └─ if claimed     → continue
      │
      ├─ call process_content_generation_task(task_id, ...)
      │
      └─ flow run completes (Prefect retries on failure;
                              stuck runs killed by work-pool timeout)
```

The Prefect run-state machine handles heartbeat / retry / stale-task
sweep natively. No polling daemon, no homegrown code for any of those
concerns.

## Cutover stages

### Stage 0 — Phase 0 ship (2026-05-10)

Already shipped:

- `services/flows/content_generation.py` — Prefect flow + claim helper
- `scripts/deploy_content_flow.py` — registers flow with the local
  Prefect server, creates `content-pool` work pool with cron schedule
- `app_settings.use_prefect_orchestration` (default `'false'`) — cutover seam
- `TaskExecutor._process_loop` short-circuits when the flag is `'true'`
- 8 unit tests covering claim semantics + flow dispatch shape

Both daemons can run side-by-side without double-claiming because the
TaskExecutor short-circuits when the flag is on. Nothing observable
changes for the operator until Stage 1.

### Stage 1 — single-task smoke (PROVEN 2026-05-10)

`smoke-docker-001` reached `state=COMPLETED` in 98.7s via the
docker-based prefect-worker — full pipeline path (Ollama draft →
originality check → link scrubber → quality 76/100 → URL validation
→ SDXL featured image → finalize) running through Prefect dispatch
end-to-end. The dispatch overhead vs. the legacy poll-loop is in the
noise.

To repeat the smoke after a fresh install:

```bash
# 1. Register the deployment (uses module-path entrypoint so the
#    worker imports services.flows.content_generation directly
#    from its PYTHONPATH — no source-storage download required).
cd src/cofounder_agent
poetry run python -m scripts.deploy_content_flow

# 2. Verify deployment landed.
curl -s -X POST http://localhost:4200/api/deployments/filter \
    -H "Content-Type: application/json" -d '{}' | python -m json.tool

# 3. Start the prefect-worker compose service.
docker compose -f docker-compose.local.yml up -d prefect-worker

# 4. Verify the worker is ONLINE in the pool.
curl -s -X POST http://localhost:4200/api/work_pools/content-pool/workers/filter \
    -H "Content-Type: application/json" -d "{}"

# 5. Trigger one flow run via the REST API (CLI's success-message
#    print crashes on Windows cp1252 — known issue, irrelevant on
#    the docker side but the deployment-run CLI runs from the host).
DEPLOYMENT_ID=$(curl -s -X POST http://localhost:4200/api/deployments/filter \
    -H "Content-Type: application/json" -d '{}' | python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
curl -s -X POST "http://localhost:4200/api/deployments/$DEPLOYMENT_ID/create_flow_run" \
    -H "Content-Type: application/json" -d '{
      "name": "manual-smoke",
      "parameters": {"topic": "Why local Ollama beats cloud LLMs",
                      "task_id": "manual-smoke-001", "target_length": 900}
    }'

# 6. Watch the flow run reach a terminal state in the Prefect UI
#    (http://localhost:4200) or via the API.
```

For a fast diagnostic that bypasses Prefect entirely and surfaces
flow-body errors directly (useful when the worker subprocess is
hiding the real exception), use the direct-call helper:

```bash
cd src/cofounder_agent
PREFECT_LOGGING_TO_API_ENABLED=False \
    poetry run python -m scripts.smoke_content_flow_direct
```

The smoke test should produce a `pipeline_tasks` row that lands in
`awaiting_approval` exactly like the existing TaskExecutor path. For
runs with a fake task_id (the smoke pattern), DB updates log
non-fatal "Update returned no row" warnings — that's expected.

### Stage 2 — A/B canary (recommended ~24h)

Flip the global flag:

```sql
UPDATE app_settings
SET value = 'true'
WHERE key = 'use_prefect_orchestration';
```

TaskExecutor's poll loop short-circuits on the next cycle (within 5s).
Prefect's deployment owns dispatch. Watch the **QA Rails dashboard**
([`/d/qa-rails`](http://localhost:3000/d/qa-rails)) for 24h:

- Approval-rate gauge tracks ±5pp of the prior week
- `qa_pass_completed` events keep flowing at the expected cadence
- Per-reviewer stats table looks the same as the LangGraph cutover stress test
- No spike in `qa_reviewer_failure` or `rag_engine_fallback`

If anything regresses, revert with:

```sql
UPDATE app_settings SET value = 'false' WHERE key = 'use_prefect_orchestration';
```

TaskExecutor resumes its poll loop on the next cycle.

### Stage 3 — production cutover

Leave `use_prefect_orchestration='true'` indefinitely. Default-flip the
seed migration so new installs get Prefect by default. TaskExecutor
becomes a no-op poller that just ticks `last_poll_at` for the metrics
endpoint.

### Stage 4 — legacy code removal (~7 days post-Stage 3)

Once `use_prefect_orchestration='true'` has been the production path
for a week with no regressions:

- Delete `services/task_executor.py` (~1,500 LOC)
- Delete `services/pipeline_throttle.py` (replaced by Prefect work-pool concurrency)
- Move `_notify_*` helpers to `services/integrations/operator_notify.py` (already mostly there)
- Remove `task_executor_idle_alert_threshold_seconds` + related app_settings rows

The `auto_publish_threshold` / `require_human_approval` checks live in
`content_router_service` already, so no logic loss.

## Tunables

All operator-configurable via `app_settings`:

| Setting                            | Default        | What it does                                             |
| ---------------------------------- | -------------- | -------------------------------------------------------- |
| `use_prefect_orchestration`        | `false`        | Cutover seam. `true` = Prefect owns dispatch.            |
| `prefect_content_flow_cron`        | `*/2 * * * *`  | Schedule for content_generation_flow                     |
| `prefect_content_flow_work_pool`   | `content-pool` | Work pool name                                           |
| `prefect_content_flow_concurrency` | `1`            | Max concurrent flow runs (mirrors today's serialization) |

The throttle bug from `Glad-Labs/glad-labs-stack#345` becomes a
work-pool concurrency limit — operator-tunable in the Prefect UI without
touching `app_settings` or code.

## Ground truth

- Flow + claim helper: `services/flows/content_generation.py`
- Deployment script: `scripts/deploy_content_flow.py` (module-path entrypoint
  - sys.path repo-root fix for `brain.bootstrap` import)
- Diagnostic smoke runner: `scripts/smoke_content_flow_direct.py`
- Worker container: `prefect-worker` service in `docker-compose.local.yml`
  (reuses `Dockerfile.worker` for matching dep tree + code mount)
- Cutover seam: `services/task_executor.py:_process_loop` (short-circuit branch)
- Migration: `services/migrations/20260510_182824_seed_prefect_cutover_flag.py`
- Tests: `tests/unit/services/flows/test_content_generation_flow.py` (8 cases)
- Issue: `Glad-Labs/poindexter#410`
