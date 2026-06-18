"""Migration: unify retention — seed all uncovered tables into retention_policies.

Resolves Glad-Labs/poindexter#699.

## Problem

Two retention systems ran side by side with conflicting windows:

  Table             retention_policies (declarative)  RetentionJanitor hard-delete
  audit_log         90d summarize-to-table             180d hard delete
  brain_decisions   90d summarize-to-table             365d hard delete
  gpu_metrics       30d raw downsample                  90d delete
  cost_logs         365d ttl_prune                     365d (duplicate, same window)

And several tables had zero coverage:

  external_metrics        ~140k rows / 34 MB, growing unbounded
  checkpoints             ~24 MB LangGraph run state, never cleared for completed runs
  checkpoint_blobs        (same)
  checkpoint_writes       (same)
  content_revisions       intermediate pipeline content, no retention
  atom_runs               per-atom diagnostic data, no retention
  pipeline_gate_history   HITL approval trail, no retention
  alert_events            Grafana alert events, no retention
  gpu_task_sessions       GPU session accounting, no retention

Also: RetentionJanitor._JANITOR_TARGETS contained 7 additional tables
(routing_outcomes, model_performance, webhook_events, task_status_history,
page_views, decision_log, post_performance) that had no retention_policies
row, meaning they only fired from the 24h janitor loop and never from the
6h declarative RunRetentionJob.

## Fix

This migration seeds retention_policies rows for:

1. All 7 janitor-only tables (preserving the janitor's default windows so
   existing behaviour is unchanged).
2. All 6 uncovered growth tables.
3. One ``checkpoint_prune`` policy (new handler) to periodically clear
   LangGraph checkpoints for completed/published/failed pipeline runs.
   This doubles as a mitigation for the checkpoint-poisoning failure mode
   where a later run's fresh start resumes a stale completed checkpoint.

After this migration, ``RetentionJanitor._JANITOR_TARGETS`` is emptied
(see companion change to ``services/retention_janitor.py``).

All policies seed with ``enabled = true``. The checkpoint_prune policy
has a 30-day TTL and only targets terminal-status tasks, so there is no
risk of discarding data for in-progress or approved runs.

Idempotent via ``ON CONFLICT (id) DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stable UUIDs — fixed so re-runs are idempotent
# ---------------------------------------------------------------------------

# Janitor-only tables (7)
_ROUTING_OUTCOMES_ID    = "a1b2c3d4-0001-4000-8000-000000000001"
_MODEL_PERFORMANCE_ID   = "a1b2c3d4-0001-4000-8000-000000000002"
_WEBHOOK_EVENTS_ID      = "a1b2c3d4-0001-4000-8000-000000000003"
_TASK_STATUS_HIST_ID    = "a1b2c3d4-0001-4000-8000-000000000004"
_PAGE_VIEWS_ID          = "a1b2c3d4-0001-4000-8000-000000000005"
_DECISION_LOG_ID        = "a1b2c3d4-0001-4000-8000-000000000006"
_POST_PERFORMANCE_ID    = "a1b2c3d4-0001-4000-8000-000000000007"

# New uncovered tables (6)
_EXTERNAL_METRICS_ID    = "a1b2c3d4-0002-4000-8000-000000000001"
_CONTENT_REVISIONS_ID   = "a1b2c3d4-0002-4000-8000-000000000002"
_ATOM_RUNS_ID           = "a1b2c3d4-0002-4000-8000-000000000003"
_GATE_HISTORY_ID        = "a1b2c3d4-0002-4000-8000-000000000004"
_ALERT_EVENTS_ID        = "a1b2c3d4-0002-4000-8000-000000000005"
_GPU_TASK_SESSIONS_ID   = "a1b2c3d4-0002-4000-8000-000000000006"

# Checkpoint cleanup (1)
_CHECKPOINT_PRUNE_ID    = "a1b2c3d4-0003-4000-8000-000000000001"


_INSERT_TTL_PRUNE = """
INSERT INTO retention_policies (
    id, name, handler_name, table_name, filter_sql,
    age_column, ttl_days, downsample_rule, summarize_handler,
    enabled, config, metadata
) VALUES (
    $1, $2, 'ttl_prune', $3, NULL,
    $4, $5, NULL, NULL,
    true, '{}'::jsonb, $6::jsonb
) ON CONFLICT (id) DO NOTHING
"""

_INSERT_CHECKPOINT_PRUNE = """
INSERT INTO retention_policies (
    id, name, handler_name, table_name, filter_sql,
    age_column, ttl_days, downsample_rule, summarize_handler,
    enabled, config, metadata
) VALUES (
    $1, $2, 'checkpoint_prune', 'checkpoints', NULL,
    'updated_at', $3, NULL, NULL,
    true, $4::jsonb, $5::jsonb
) ON CONFLICT (id) DO NOTHING
"""


async def up(pool) -> None:  # type: ignore[no-untyped-def]
    async with pool.acquire() as conn:

        # ------------------------------------------------------------------
        # Group 1: Janitor-only tables (preserve existing janitor defaults)
        # ------------------------------------------------------------------

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _ROUTING_OUTCOMES_ID,
            "routing_outcomes",
            "routing_outcomes",
            "created_at",
            365,
            '{"description": "ML feedback-loop routing signal — 1 year covers full seasonal cycles"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _MODEL_PERFORMANCE_ID,
            "model_performance",
            "model_performance",
            "created_at",
            365,
            '{"description": "ML feedback-loop model signal — 1 year covers full seasonal cycles"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _WEBHOOK_EVENTS_ID,
            "webhook_events",
            "webhook_events",
            "created_at",
            90,
            '{"description": "Incoming webhook trail — 90d is sufficient for replay/debug windows"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _TASK_STATUS_HIST_ID,
            "task_status_history",
            "task_status_history",
            "created_at",
            180,
            '{"description": "Task state transitions — 180d covers quarterly operational reviews"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _PAGE_VIEWS_ID,
            "page_views",
            "page_views",
            "created_at",
            180,
            '{"description": "Raw page-view beacons — 180d; aggregates live in post_performance"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _DECISION_LOG_ID,
            "decision_log",
            "decision_log",
            "created_at",
            365,
            '{"description": "Decision audit trail — 1 year retained for operational reviews"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _POST_PERFORMANCE_ID,
            "post_performance",
            "post_performance",
            "measured_at",
            180,
            '{"description": "Post-performance snapshots (~N_posts/day) — 180d for week-over-week; aggregates are the long-term view"}',
        )

        # ------------------------------------------------------------------
        # Group 2: Uncovered tables (no prior retention at all)
        # ------------------------------------------------------------------

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _EXTERNAL_METRICS_ID,
            "external_metrics",
            "external_metrics",
            "fetched_at",
            365,
            '{"description": "SEO/GA4/GSC tap data (~140k rows / 34 MB prod) — 365d retained for seasonal-cycle correlation; organic SEO lag is 3-6mo so 180d truncates the signal; aggregated in post_performance"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _CONTENT_REVISIONS_ID,
            "content_revisions",
            "content_revisions",
            "created_at",
            90,
            '{"description": "Intermediate pipeline draft versions — 90d; the published post is the permanent record"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _ATOM_RUNS_ID,
            "atom_runs",
            "atom_runs",
            "created_at",
            365,
            '{"description": "Per-atom run diagnostics (latency, cost, decision) — 365d retained as capability-router ML training signal; model/tier/quality/cost/latency per atom is the dataset for future routing policy learning"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _GATE_HISTORY_ID,
            "pipeline_gate_history",
            "pipeline_gate_history",
            "created_at",
            365,
            '{"description": "HITL gate approval/retry/reject decisions — 365d retained as human preference signal; approval/reject pairs are the raw RLHF dataset for future quality-model fine-tuning"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _ALERT_EVENTS_ID,
            "alert_events",
            "alert_events",
            "received_at",
            90,
            '{"description": "Grafana/Prometheus alert events — 90d; aged events have no operational value"}',
        )

        await conn.execute(
            _INSERT_TTL_PRUNE,
            _GPU_TASK_SESSIONS_ID,
            "gpu_task_sessions",
            "gpu_task_sessions",
            "started_at",
            180,
            '{"description": "GPU session cost accounting — 180d covers quarterly cost reviews"}',
        )

        # ------------------------------------------------------------------
        # Group 3: LangGraph checkpoint cleanup (new handler)
        # ------------------------------------------------------------------
        # Deletes checkpoints for terminal-status pipeline tasks (completed /
        # published / failed / cancelled) older than 30 days. This covers the
        # accumulation case the stale-task sweeper doesn't handle (the sweeper
        # clears poisoned mid-run checkpoints; this handler clears finished-run
        # accumulation). See retention_checkpoint_prune.py for config options.

        await conn.execute(
            _INSERT_CHECKPOINT_PRUNE,
            _CHECKPOINT_PRUNE_ID,
            "checkpoint_prune",
            30,
            '{"terminal_statuses": ["completed", "published", "failed", "cancelled"], "thread_prefixes": ["", "media-", "podcast-"], "batch_size": 1000}',
            '{"description": "LangGraph Postgres-checkpointer cleanup for terminal pipeline runs — clears checkpoint_writes / checkpoint_blobs / checkpoints for runs finished 30+ days ago"}',
        )

    logger.info(
        "Migration seed_retention_policies_unified: "
        "7 janitor-only + 6 uncovered + 1 checkpoint_prune policies seeded"
    )


async def down(pool) -> None:  # type: ignore[no-untyped-def]
    all_ids = [
        _ROUTING_OUTCOMES_ID,
        _MODEL_PERFORMANCE_ID,
        _WEBHOOK_EVENTS_ID,
        _TASK_STATUS_HIST_ID,
        _PAGE_VIEWS_ID,
        _DECISION_LOG_ID,
        _POST_PERFORMANCE_ID,
        _EXTERNAL_METRICS_ID,
        _CONTENT_REVISIONS_ID,
        _ATOM_RUNS_ID,
        _GATE_HISTORY_ID,
        _ALERT_EVENTS_ID,
        _GPU_TASK_SESSIONS_ID,
        _CHECKPOINT_PRUNE_ID,
    ]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retention_policies WHERE id = ANY($1::uuid[])",
            all_ids,
        )
    logger.info(
        "Migration seed_retention_policies_unified down: 14 policies removed"
    )
