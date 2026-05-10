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

### Stage 1 — single-task smoke (operator-driven)

```bash
# 1. Register the deployment
cd src/cofounder_agent
poetry run python -m scripts.deploy_content_flow

# 2. Verify deployment landed
curl -s http://localhost:4200/api/deployments | python -m json.tool | grep content-generation

# 3. Trigger a single flow run from Prefect UI or CLI
poetry run prefect deployment run content_generation/content-generation \
    --param topic="Why local Ollama beats cloud LLMs" \
    --param task_id=manual-smoke-test

# 4. Watch the flow run complete in Prefect UI:
#    http://localhost:4200/deployments/deployment/<id>
```

The smoke test should produce a `pipeline_tasks` row that lands in
`awaiting_approval` exactly like the existing TaskExecutor path.

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
- Deployment script: `scripts/deploy_content_flow.py`
- Cutover seam: `services/task_executor.py:_process_loop` (short-circuit branch)
- Migration: `services/migrations/20260510_182824_seed_prefect_cutover_flag.py`
- Tests: `tests/unit/services/flows/test_content_generation_flow.py` (8 cases)
- Issue: `Glad-Labs/poindexter#410`
