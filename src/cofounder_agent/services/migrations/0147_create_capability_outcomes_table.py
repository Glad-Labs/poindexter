"""Migration 0147: create ``capability_outcomes`` table.

Phase 2 of the dynamic-pipeline-composition spec — the outcome
feedback loop. Every TemplateRunner run lands one or more rows here
keyed by (template_slug, atom_name, capability_tier, model_used)
along with the run outcome (ok/halted/failed, elapsed_ms,
quality_score). The router (and future capability_router) reads
aggregated stats from this table to bias model selection toward
combinations that produce good results in production.

Schema motivation:

- One row per atom invocation, NOT per template run, so the router
  can score individual (atom, model) pairs. A template that uses
  three different LLMs across three nodes generates three rows.
- ``capability_tier`` is the abstract tier the atom requested
  ("dev_diary_narrator", "cheap_critic"). The router resolves it to
  a concrete model at execution time; we store both so we can
  retrospectively re-tier a model that started at one level and
  ended up serving another.
- ``quality_score`` is the post-run score from quality_service when
  available (so the loop closes only on outputs that are evaluated).
  NULL when the run halted before scoring.
- ``ok`` / ``halted_at`` / ``failure_reason`` mirror
  TemplateRunSummary so we can audit "this model halted at QA gate
  N% of the time" without a separate query.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Implements: Glad-Labs/poindexter#358.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if await _table_exists(conn, "capability_outcomes"):
            logger.info("Migration 0147: capability_outcomes already exists — skipping")
            return
        await conn.execute(
            """
            CREATE TABLE capability_outcomes (
              id                BIGSERIAL PRIMARY KEY,
              task_id           TEXT,
              template_slug     TEXT NOT NULL,
              node_name         TEXT NOT NULL,
              atom_name         TEXT,
              capability_tier   TEXT,
              model_used        TEXT,
              ok                BOOLEAN NOT NULL,
              halted            BOOLEAN NOT NULL DEFAULT false,
              failure_reason    TEXT,
              elapsed_ms        INTEGER NOT NULL DEFAULT 0,
              quality_score     NUMERIC(5,2),
              metrics           JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_capability_outcomes_template "
            "ON capability_outcomes (template_slug, created_at DESC)"
        )
        await conn.execute(
            "CREATE INDEX idx_capability_outcomes_atom_model "
            "ON capability_outcomes (atom_name, model_used) "
            "WHERE atom_name IS NOT NULL AND model_used IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX idx_capability_outcomes_tier "
            "ON capability_outcomes (capability_tier, ok) "
            "WHERE capability_tier IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX idx_capability_outcomes_task "
            "ON capability_outcomes (task_id) WHERE task_id IS NOT NULL"
        )
        logger.info(
            "Migration 0147: created capability_outcomes table + 4 indexes"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS capability_outcomes")
        logger.info("Migration 0147 down: dropped capability_outcomes table")
