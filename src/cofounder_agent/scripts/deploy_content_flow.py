"""Register ``content_generation_flow`` with the local Prefect server.

Deploys the flow to a work pool with a cron schedule so Prefect can
dispatch one flow run per claim cycle. Run this once after Phase 0
ships; subsequent runs are no-ops (Prefect upserts by deployment id).

Usage::

    cd src/cofounder_agent
    poetry run python -m scripts.deploy_content_flow

Tunables (all read from ``app_settings`` at deploy time):

- ``prefect_content_flow_cron`` — schedule (default ``*/2 * * * *``)
- ``prefect_content_flow_work_pool`` — work pool name (default
  ``content-pool``)
- ``prefect_content_flow_concurrency`` — work-pool concurrency
  (default ``1`` — single-flow-per-cycle to mirror today's TaskExecutor
  serialization; bump after observing GPU contention is OK)

The deployment can be re-applied at any time without disrupting
in-flight runs — Prefect updates the deployment metadata in place.
"""

from __future__ import annotations

import asyncio
import logging
import os

from prefect.client.orchestration import get_client
from prefect.client.schemas.schedules import CronSchedule

from services.flows.content_generation import content_generation_flow

logger = logging.getLogger(__name__)


DEFAULT_CRON = "*/2 * * * *"  # every 2 minutes
DEFAULT_WORK_POOL = "content-pool"
DEFAULT_CONCURRENCY = 1


async def _resolve_setting(key: str, default: str) -> str:
    """Read ``app_settings.<key>``; fall back to ``default`` on any error.

    The deploy script runs from the operator shell, not the worker
    container, so we use ``brain.bootstrap`` to find the DB and skip
    the heavier ``DatabaseService`` plumbing.
    """
    import asyncpg

    from brain.bootstrap import resolve_database_url

    try:
        dsn = resolve_database_url()
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchval(
                    "SELECT value FROM app_settings WHERE key = $1", key,
                )
        finally:
            await pool.close()
        if row:
            return str(row)
    except Exception as exc:
        logger.warning(
            "[DEPLOY] could not read %s from app_settings (%s) — using default %r",
            key, exc, default,
        )
    return default


async def _ensure_work_pool(name: str, concurrency: int) -> None:
    """Idempotent — creates ``content-pool`` (process-type) if absent.

    Process pool runs flows in subprocesses on the same host as the
    Prefect worker. That's the right shape for now: workflow + LLM
    calls already share GPU/CPU on Matt's PC, so spawning a separate
    pool host adds complexity without isolation benefit.
    """
    async with get_client() as client:
        try:
            existing = await client.read_work_pool(name)
            if existing.concurrency_limit != concurrency:
                logger.info(
                    "[DEPLOY] updating %s concurrency: %s -> %s",
                    name, existing.concurrency_limit, concurrency,
                )
                from prefect.client.schemas.actions import WorkPoolUpdate
                await client.update_work_pool(
                    name, WorkPoolUpdate(concurrency_limit=concurrency),
                )
            else:
                logger.info(
                    "[DEPLOY] work pool %r already exists (concurrency=%d)",
                    name, concurrency,
                )
            return
        except Exception:
            pass  # pool doesn't exist yet
        from prefect.client.schemas.actions import WorkPoolCreate
        await client.create_work_pool(
            WorkPoolCreate(
                name=name,
                type="process",
                concurrency_limit=concurrency,
                description=(
                    "Content-generation flow pool (Glad-Labs/poindexter#410). "
                    "Runs one content_generation_flow per pending pipeline_tasks "
                    "row at the configured cron cadence."
                ),
            )
        )
        logger.info(
            "[DEPLOY] created work pool %r (concurrency=%d)", name, concurrency,
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Tell Prefect where the server is. The dev compose stack runs the
    # API on port 4200 of the host; from this script's vantage (host
    # shell), localhost is the right address. The Prefect worker
    # container has its own PREFECT_API_URL.
    if not os.environ.get("PREFECT_API_URL"):
        os.environ["PREFECT_API_URL"] = "http://localhost:4200/api"

    cron = await _resolve_setting("prefect_content_flow_cron", DEFAULT_CRON)
    work_pool = await _resolve_setting(
        "prefect_content_flow_work_pool", DEFAULT_WORK_POOL,
    )
    try:
        concurrency = int(
            await _resolve_setting(
                "prefect_content_flow_concurrency", str(DEFAULT_CONCURRENCY),
            )
        )
    except ValueError:
        concurrency = DEFAULT_CONCURRENCY

    await _ensure_work_pool(work_pool, concurrency)

    deployment_id = await content_generation_flow.to_deployment(
        name="content-generation",
        description=(
            "Drives one pipeline_tasks row through the canonical_blog "
            "LangGraph template per cron tick. Cutover seam for "
            "Glad-Labs/poindexter#410 — replaces TaskExecutor's poll "
            "loop with native Prefect dispatch."
        ),
        work_pool_name=work_pool,
        schedules=[CronSchedule(cron=cron)],
        tags=["poindexter", "content-pipeline", "issue-410"],
    ).apply()

    logger.info(
        "[DEPLOY] content_generation deployment registered (id=%s)\n"
        "  cron        : %s\n"
        "  work pool   : %s\n"
        "  concurrency : %d\n"
        "  Prefect UI  : http://localhost:4200/deployments/deployment/%s\n",
        deployment_id, cron, work_pool, concurrency, deployment_id,
    )


if __name__ == "__main__":
    asyncio.run(main())
