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
  (default ``3``). This is the native Prefect cap on how many
  ``content_generation_flow`` runs execute simultaneously. Each run
  loads an LLM + SDXL onto the single 5090, so the value is a direct
  VRAM lever (Glad-Labs/poindexter#578 stress test: 3 concurrent sits
  at a stable ~60% VRAM; 5 pins the GPU at ~98% and risks OOM).
- ``content_flow_max_concurrency`` — hard safety ceiling for this GPU
  (default ``3``). The deploy **fails loud** if the requested
  concurrency exceeds this ceiling, so a fat-fingered value can't
  silently exhaust VRAM (``feedback_no_silent_defaults``). Operators
  who upgrade the GPU raise this ceiling to unlock higher concurrency.

The deployment can be re-applied at any time without disrupting
in-flight runs — Prefect updates the deployment metadata in place.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Make the repo-root brain/ package importable. When poetry runs this
# script from src/cofounder_agent/ the brain/ module — which lives one
# level up at the repo root — isn't on sys.path. Mirror the pattern
# from src/cofounder_agent/migrations/apply_migrations.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Point Prefect at the local server BEFORE importing prefect.* — the
# settings snapshot is taken at import time, so setting this later
# (e.g. inside main()) silently routes us to an in-memory ephemeral
# server on a random port instead of the running container on :4200.
os.environ.setdefault("PREFECT_API_URL", "http://localhost:4200/api")

from prefect.client.orchestration import get_client  # noqa: E402
from prefect.client.schemas.schedules import CronSchedule  # noqa: E402
from prefect.types.entrypoint import EntrypointType  # noqa: E402

from services.flows.content_generation import content_generation_flow  # noqa: E402

logger = logging.getLogger(__name__)


DEFAULT_CRON = "*/2 * * * *"  # every 2 minutes
DEFAULT_WORK_POOL = "content-pool"
# Safe default for the single 5090 (Glad-Labs/poindexter#578 stress test):
# 3 concurrent content flows sit at a stable ~60% VRAM with healthy
# headroom; 5 pin the GPU at ~98% and risk OOM. The work-pool default
# was historically 1 (TaskExecutor-serialization parity); 3 is the
# documented safe production value.
DEFAULT_CONCURRENCY = 3
# Hard safety ceiling for this GPU. ``resolve_safe_concurrency`` fails
# loud rather than clamping silently when the requested concurrency
# exceeds this — a misconfigured deploy aborts instead of pinning VRAM.
DEFAULT_MAX_CONCURRENCY = 3


def resolve_safe_concurrency(*, requested: int, ceiling: int) -> int:
    """Validate the requested work-pool concurrency against the safety ceiling.

    Both values come from ``app_settings`` (``prefect_content_flow_concurrency``
    and ``content_flow_max_concurrency``) — DB-configurable, never hardcoded.

    Fails loud (``ValueError``) when:

    - either value is not a positive integer, or
    - ``requested`` exceeds ``ceiling`` — the Glad-Labs/poindexter#578 guard.
      Five concurrent content flows pin the single 5090 at ~98% VRAM; the
      ceiling stops a fat-fingered value from silently exhausting it. An
      operator who upgrades the GPU raises ``content_flow_max_concurrency``
      to unlock higher concurrency, keeping the cap DB-tunable.

    Returns the requested value unchanged when it is within the ceiling.
    """
    if requested <= 0:
        raise ValueError(
            "prefect_content_flow_concurrency must be a positive integer, "
            f"got {requested!r}"
        )
    if ceiling <= 0:
        raise ValueError(
            "content_flow_max_concurrency must be a positive integer, "
            f"got {ceiling!r}"
        )
    if requested > ceiling:
        raise ValueError(
            f"prefect_content_flow_concurrency={requested} exceeds the safety "
            f"ceiling content_flow_max_concurrency={ceiling}. On the single "
            "5090, >3 concurrent content flows risk ~98% VRAM / OOM "
            "(Glad-Labs/poindexter#578). Lower the requested concurrency, or "
            "raise content_flow_max_concurrency if your GPU has the headroom."
        )
    return requested


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

    cron = await _resolve_setting("prefect_content_flow_cron", DEFAULT_CRON)
    work_pool = await _resolve_setting(
        "prefect_content_flow_work_pool", DEFAULT_WORK_POOL,
    )
    try:
        requested_concurrency = int(
            await _resolve_setting(
                "prefect_content_flow_concurrency", str(DEFAULT_CONCURRENCY),
            )
        )
    except ValueError:
        requested_concurrency = DEFAULT_CONCURRENCY
    try:
        max_concurrency = int(
            await _resolve_setting(
                "content_flow_max_concurrency", str(DEFAULT_MAX_CONCURRENCY),
            )
        )
    except ValueError:
        max_concurrency = DEFAULT_MAX_CONCURRENCY

    # Fail loud if the requested concurrency exceeds the safety ceiling —
    # a misconfigured deploy aborts here rather than pinning the 5090 at
    # ~98% VRAM (Glad-Labs/poindexter#578). No silent clamp.
    concurrency = resolve_safe_concurrency(
        requested=requested_concurrency, ceiling=max_concurrency,
    )

    await _ensure_work_pool(work_pool, concurrency)

    # In an async context Prefect's ``Flow.to_deployment`` is a
    # ``sync_compatible``-decorated method that returns a coroutine
    # producing a ``RunnerDeployment``. Await it, THEN call
    # ``.apply()`` (also async in 3.x).
    #
    # ``entrypoint_type=MODULE_PATH`` is critical for same-machine
    # workers: it tells the worker to ``import services.flows.content_generation``
    # instead of trying to "download flow code from storage" into a temp
    # dir. With FILE_PATH (the default), the worker creates an empty
    # workdir then errors out trying to load the entrypoint file. With
    # MODULE_PATH, the worker uses its existing PYTHONPATH — which the
    # ``poetry run prefect worker start`` command + the eventual
    # prefect-worker compose service both have wired correctly.
    deployment = await content_generation_flow.to_deployment(
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
        entrypoint_type=EntrypointType.MODULE_PATH,
    )
    deployment_id = await deployment.apply()

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
