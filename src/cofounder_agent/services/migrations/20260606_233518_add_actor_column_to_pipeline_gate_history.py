"""Add actor column to pipeline_gate_history (Glad-Labs/poindexter#656).

``pipeline_gate_history`` previously had no attribution column, making it
impossible to distinguish human approvals from automated ones (auto_publish,
QA rejections). This migration adds:

    actor VARCHAR(100) NOT NULL DEFAULT 'system'

DEFAULT 'system' is backward-safe — existing rows get an unknown-actor
marker. New writes supply the concrete actor:
  - 'human'          — operator via CLI / MCP / REST approval routes
  - 'auto_publish'   — automated approval by the quality-score gate
  - 'multi_model_qa' — automated rejection by the QA rail aggregator

The column enables Grafana panels and CLI output to show WHO (or WHAT)
approved/rejected each gate event.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_gate_history
                ADD COLUMN IF NOT EXISTS actor VARCHAR(100) NOT NULL DEFAULT 'system'
            """
        )
        logger.info("Migration add_actor_column_to_pipeline_gate_history: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_gate_history DROP COLUMN IF EXISTS actor"
        )
        logger.info("Migration add_actor_column_to_pipeline_gate_history: reverted")
