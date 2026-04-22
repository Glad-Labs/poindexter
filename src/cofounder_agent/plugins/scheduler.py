"""PluginScheduler — wrap apscheduler for registered Jobs.

Phase C will rip out ``services/idle_worker.py``'s DIY scheduling loop
and replace it with this adapter. For Phase A we just ship the contract
+ unit test coverage so downstream phases can plug in.

Design:

- Uses ``apscheduler.AsyncIOScheduler`` (async-native) with the default
  ``MemoryJobStore``. Jobs are re-registered from ``entry_points`` on
  each worker boot, so we don't need cross-restart persistence for the
  *schedule* — the list of jobs is authoritative in code + entry_points.
- Jobs' ``last_run_at`` and ``next_run_time`` are tracked in
  ``app_settings`` (via :class:`PluginConfig`) for operator visibility.
  This is not required by apscheduler; it's for humans.
- The ``schedule`` attribute on each Job can be:

  - ``"every N seconds"`` / ``"every N minutes"`` / ``"every N hours"``
    / ``"every N days"`` — interval triggers
  - ``"0 */6 * * *"`` — five-field cron
  - ``"0 9 * * 1-5"`` — also cron

  Anything else: the scheduler logs an error and skips the job.
"""

from __future__ import annotations

import inspect
import logging
import re
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import PluginConfig

logger = logging.getLogger(__name__)

# Matches "every 5 minutes", "every 1 hour", "every 30 seconds", etc.
_INTERVAL_RE = re.compile(
    r"^\s*every\s+(\d+)\s+(seconds?|minutes?|hours?|days?)\s*$",
    re.IGNORECASE,
)


def _parse_schedule(schedule: str):
    """Convert a Job.schedule string to an apscheduler trigger.

    Returns ``None`` if the schedule string is unrecognized — caller
    should log + skip that Job.
    """
    m = _INTERVAL_RE.match(schedule)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower().rstrip("s")  # "second" / "minute" / "hour" / "day"
        kwargs = {f"{unit}s": n}
        return IntervalTrigger(**kwargs)

    # Cron fallback — let apscheduler parse the 5-field form.
    try:
        return CronTrigger.from_crontab(schedule)
    except Exception:
        return None


class PluginScheduler:
    """Thin wrapper around apscheduler that understands our Job Protocol."""

    def __init__(self, pool: Any, site_config: Any = None):
        """Create the scheduler bound to a DB pool + optional SiteConfig.

        Args:
            pool: Shared asyncpg pool — passed into each Job.run() call
                so Jobs can read/write to the same Postgres as the rest
                of Poindexter.
            site_config: SiteConfig instance (Phase H GH#95 — optional
                during the incremental migration). When provided, it's
                forwarded into each ``Job.run(..., site_config=...)``
                call if and only if the job's signature declares the
                ``site_config`` parameter. Jobs that haven't migrated
                yet don't see the kwarg, avoiding TypeError while the
                rollout is in flight.
        """
        self._pool = pool
        self._site_config = site_config
        self._scheduler = AsyncIOScheduler()
        self._registered: list[str] = []

    async def register_job(self, job: Any) -> bool:
        """Add a single Job to the schedule.

        Returns ``True`` if the Job was scheduled, ``False`` if skipped
        (disabled via PluginConfig, malformed schedule, or Job already
        registered).
        """
        if job.name in self._registered:
            logger.warning("scheduler: job %r already registered; skipping", job.name)
            return False

        cfg = await PluginConfig.load(self._pool, "job", job.name)
        if not cfg.enabled:
            logger.info("scheduler: job %r disabled in app_settings; skipping", job.name)
            return False

        # Per-install overrides win over the Job's default schedule.
        schedule = cfg.get("schedule", job.schedule)
        trigger = _parse_schedule(schedule)
        if trigger is None:
            logger.error(
                "scheduler: job %r has unrecognized schedule %r; skipping",
                job.name, schedule,
            )
            return False

        # Incremental Phase H migration (#95): forward site_config only
        # to jobs that declare it. Inspect once at registration time —
        # signature doesn't change between runs.
        run_params = inspect.signature(job.run).parameters
        pass_site_config = "site_config" in run_params

        async def _runner(
            _pass_sc: bool = pass_site_config,
            _job: Any = job,
        ) -> None:
            # Each fire: load fresh config (enables live-toggle without restart)
            live_cfg = await PluginConfig.load(self._pool, "job", _job.name)
            if not live_cfg.enabled:
                logger.debug("scheduler: job %r disabled at fire-time; skip", _job.name)
                return
            try:
                extra: dict[str, Any] = {}
                if _pass_sc:
                    extra["site_config"] = self._site_config
                result = await _job.run(self._pool, live_cfg.config, **extra)
                logger.info(
                    "scheduler: job %r ran ok=%s detail=%r changes=%d",
                    _job.name, result.ok, result.detail, result.changes_made,
                )
            except Exception as e:
                logger.exception("scheduler: job %r raised: %s", _job.name, e)

        self._scheduler.add_job(
            _runner,
            trigger=trigger,
            id=job.name,
            name=job.name,
            replace_existing=True,
            coalesce=True,
            max_instances=1 if not getattr(job, "idempotent", False) else 3,
        )
        self._registered.append(job.name)
        return True

    async def register_all(self, jobs: list[Any]) -> list[str]:
        """Register a list of Jobs in one pass. Returns names that were accepted."""
        accepted = []
        for job in jobs:
            if await self.register_job(job):
                accepted.append(job.name)
        return accepted

    def start(self) -> None:
        """Start the async scheduler. Idempotent."""
        if not self._scheduler.running:
            self._scheduler.start()

    async def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler. Pass ``wait=False`` to cancel in-flight jobs."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    def jobs(self) -> list[str]:
        """Return registered Job names."""
        return list(self._registered)
