"""Migration 0143: add ``template_slug`` column to pipeline_tasks.

Wires the v1 POC dispatch from `content_router_service` to
:class:`services.template_runner.TemplateRunner`. When set, the task
routes through the LangGraph-based template by slug; when NULL, the
task continues through the legacy chunked StageRunner flow (canonical
12-stage default).

Nullable — backward-compatible. No data migration; existing tasks have
``template_slug = NULL`` and keep using the legacy path.

Spec:
``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Implements: Glad-Labs/poindexter#359.
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


async def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2)",
            table, column,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "pipeline_tasks"):
            logger.info("Migration 0143: pipeline_tasks missing — skipping")
            return
        if await _column_exists(conn, "pipeline_tasks", "template_slug"):
            logger.info("Migration 0143: template_slug already present — skipping")
            return
        await conn.execute(
            "ALTER TABLE pipeline_tasks ADD COLUMN template_slug TEXT"
        )
        # Lightweight index — operator filtering by template_slug is a
        # natural CLI / Grafana query. Partial index keeps it cheap when
        # most rows have NULL template_slug (legacy flow).
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_template_slug "
            "ON pipeline_tasks (template_slug) WHERE template_slug IS NOT NULL"
        )
        logger.info(
            "Migration 0143: added pipeline_tasks.template_slug column + "
            "partial index (template_slug IS NOT NULL)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "pipeline_tasks"):
            return
        await conn.execute(
            "DROP INDEX IF EXISTS idx_pipeline_tasks_template_slug"
        )
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS template_slug"
        )
        logger.info("Migration 0143 down: removed pipeline_tasks.template_slug")
