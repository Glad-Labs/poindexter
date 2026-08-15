"""One-off: regenerate a task's frozen media scripts + shot lists in place.

Thin CLI over ``modules.content.media_regen.regen_video_scripts`` — the core
was promoted there (2026-08-15, the poindexter#982 promotion) so the
scheduled ``backfill_media_scripts`` job and this operator command share one
implementation. See the core's docstring for exactly what is regenerated
(video keys only), what is deliberately untouched (podcast script + audio),
and what ``--apply`` heals (assets, approvals, dispatch marker).

Run INSIDE the worker container (needs the app env, DB, and Ollama):

    docker exec poindexter-worker python scripts/regen_media_scripts.py \
        <task_id> [<task_id> ...] [--apply]

Run tasks SEQUENTIALLY (this script already does) — parallel regens
GPU-busy-skip each other's calls; and pause ``media_pipeline_trigger_enabled``
around multi-task regens, or the first cleared marker starts a render that
steals the GPU from the next regen.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("regen_media_scripts")


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_ids", nargs="+")
    parser.add_argument("--apply", action="store_true", help="write back + heal (default: dry run)")
    args = parser.parse_args()

    from modules.content.media_regen import regen_video_scripts
    from services.di_wiring import (
        build_and_wire_subprocess_with_container,
        build_platform_for_subprocess,
    )
    from services.flows.content_generation import _build_default_database_service

    # A REAL DatabaseService (not a bare pool): its initialize() also sets the
    # global AuditLogger, without which build_platform_for_subprocess returns
    # None and generate_video_shot_list SKIPS ("no Platform handle").
    database_service = await _build_default_database_service()
    pool = database_service.pool
    try:
        site_config, _container = await build_and_wire_subprocess_with_container(pool)
        platform = build_platform_for_subprocess(pool, site_config)
        if platform is None:
            logger.error("platform handle unavailable — the director would skip; aborting")
            return 1
        ok = True
        for task_id in args.task_ids:
            outcome = await regen_video_scripts(
                task_id, pool=pool, site_config=site_config,
                platform=platform, database_service=database_service,
                apply=args.apply,
            )
            if outcome.ok:
                logger.info("task %s: %s", task_id, outcome.detail)
                if not args.apply:
                    print(f"\n===== {task_id} — LONG (dry run) =====\n{outcome.long_script}\n")
                    print(
                        f"===== {task_id} — SHORT (dry run) =====\n"
                        f"{outcome.short_script or '(no short)'}\n"
                    )
            else:
                logger.error("task %s: %s — skipping", task_id, outcome.detail)
                ok = False
        return 0 if ok else 1
    finally:
        close = getattr(database_service, "close", None)
        if close is not None:
            await close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
