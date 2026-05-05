"""Migration 0156: drop pipeline_events table + trigger — split-migration phase 4.

Final phase of poindexter#366. Migrations 0154 / 0155 + the
brain_daemon / scheduling_service / template_runner refactors moved
every writer off ``pipeline_events`` and every reader onto its
intended source-of-truth (``pipeline_gate_history`` for gate state,
``pipeline_tasks.auto_cancelled_at`` for the restart-safe counter,
``audit_log`` for scheduling history). The Discord/notify_operator
fan-out replaces the "future EventBus listener" hope that the
template_runner write was preserving.

Pre-flight check before this migration runs in production:

  SELECT COUNT(*) FROM pipeline_events
   WHERE created_at > NOW() - INTERVAL '1 hour';

A non-zero count means a stale process is still writing — find and
restart it before applying. The migration is reversible
(``0059_pipeline_events_bus.py`` recreates the table on a down run),
but rolling forward after fresh data has appeared is still wasted
work.

The NOTIFY trigger goes too — no LISTEN listener exists anywhere
in the codebase, the wire was dead. The trigger function
``pipeline_event_notify()`` is dropped explicitly so a future
``pipeline_events`` resurrection has to go through migration 0059's
DDL again rather than silently relying on a leftover function.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # Defensive — surface any unexpected very-recent writes so we
        # don't silently drop in-flight signal. A non-zero count here
        # means a stale process is still alive; abort and notify.
        recent = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pipeline_events
             WHERE created_at > NOW() - INTERVAL '5 minutes'
            """
        )
        if recent and int(recent) > 0:
            raise RuntimeError(
                f"Migration 0156: pipeline_events has {recent} row(s) "
                "written in the last 5 minutes — a stale process is "
                "still writing. Find + restart it, then re-run."
            )

        await conn.execute(
            "DROP TRIGGER IF EXISTS trg_pipeline_event_notify ON pipeline_events"
        )
        await conn.execute("DROP FUNCTION IF EXISTS pipeline_event_notify()")
        await conn.execute("DROP TABLE IF EXISTS pipeline_events CASCADE")
        logger.info(
            "Migration 0156: dropped pipeline_events table + NOTIFY trigger + function"
        )


async def down(pool) -> None:
    """Recreate via the original DDL from migration 0059.

    Importing 0059 directly keeps the schema definition in one place;
    if 0059 changes (it shouldn't), the down-path tracks.
    """
    import importlib.util
    from pathlib import Path

    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "_mig_0059", here / "0059_pipeline_events_bus.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load migration 0059 to reverse 0156")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    await mod.up(pool)
    logger.info("Migration 0156 down: re-applied 0059 to recreate pipeline_events")
