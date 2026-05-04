"""Migration 0145: create ``pipeline_atoms`` table.

Phase 2 of the dynamic-pipeline-composition spec — write-through cache
of every ``ATOM_META`` declaration discovered at startup. Architect-LLM
queries this catalog (and the live registry) when composing graphs;
operator dashboards query it for "what blocks do we have?" panels.

Source of truth is the Python module: each atom file declares
``ATOM_META: AtomMeta`` and the registry walker collects them at
startup. This table is the **cache**, refreshed on each app boot —
DO NOT hand-edit rows. Re-running discovery rewrites them.

Schema motivation:

- ``name`` is the globally-unique slug ("atoms.narrate_bundle") matching
  the architect-LLM's catalog text exactly.
- ``meta`` JSONB is the full :func:`AtomMeta.to_jsonb()` payload —
  inputs, outputs, retry, fallback, capability_tier. The architect
  reads from this column when composing; we keep it JSONB rather than
  exploding into many columns because the schema will evolve.
- ``capability_tier`` is duplicated from meta JSONB into a top-level
  column so dashboards / cost projections can group/filter without
  unrolling JSONB.
- ``last_seen_at`` lets us detect dead atoms (file deleted, ATOM_META
  removed) — discovery sweeps stamp it; rows older than the most
  recent sweep are stale candidates for the operator to clean up.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Implements: Glad-Labs/poindexter#360.
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
        if await _table_exists(conn, "pipeline_atoms"):
            logger.info("Migration 0145: pipeline_atoms already exists — skipping")
            return
        await conn.execute(
            """
            CREATE TABLE pipeline_atoms (
              id                BIGSERIAL PRIMARY KEY,
              name              TEXT NOT NULL UNIQUE,
              type              TEXT NOT NULL,
              version           TEXT NOT NULL,
              description       TEXT NOT NULL DEFAULT '',
              capability_tier   TEXT,
              cost_class        TEXT NOT NULL DEFAULT 'compute',
              meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
              last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_pipeline_atoms_type ON pipeline_atoms (type)"
        )
        await conn.execute(
            "CREATE INDEX idx_pipeline_atoms_capability_tier "
            "ON pipeline_atoms (capability_tier) WHERE capability_tier IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX idx_pipeline_atoms_cost_class "
            "ON pipeline_atoms (cost_class)"
        )
        logger.info(
            "Migration 0145: created pipeline_atoms table + type/capability/cost indexes"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS pipeline_atoms")
        logger.info("Migration 0145 down: dropped pipeline_atoms table")
