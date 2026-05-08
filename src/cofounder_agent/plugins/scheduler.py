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

    def __init__(self, pool: Any, *, site_config: Any = None):
        """Create the scheduler bound to a DB pool.

        ``pool`` is passed into each Job.run() call so Jobs can read/write
        to the same Postgres the rest of Poindexter uses.

        ``site_config`` (Phase H DI seam, GH#95) is stored for jobs that
        need DB-backed config at scheduling time. Optional for back-compat.
        """
        self._pool = pool
        self._site_config = site_config
        self._scheduler = AsyncIOScheduler()
        self._registered: list[str] = []
        # In-process run counters for /api/metrics/operational. Not persistent
        # — they reset on worker restart, which is fine for "is the scheduler
        # firing?" health signals. See ``get_stats``.
        self._jobs_run: int = 0
        self._jobs_succeeded: int = 0
        self._jobs_failed: int = 0
        self._last_tick_epoch: float | None = None

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

        async def _runner():
            # Each fire: load fresh config (enables live-toggle without restart)
            import time as _time
            live_cfg = await PluginConfig.load(self._pool, "job", job.name)
            if not live_cfg.enabled:
                logger.debug("scheduler: job %r disabled at fire-time; skip", job.name)
                return
            self._jobs_run += 1
            self._last_tick_epoch = _time.time()
            # Seed the dispatcher-injected `_site_config` reserved key so jobs
            # can read it via `config.get("_site_config")` instead of reaching
            # for the legacy singleton import (glad-labs-stack#330). Mirrors
            # the seam image_provider / tts_provider / audio_gen_provider
            # plugins already use.
            if self._site_config is not None and "_site_config" not in live_cfg.config:
                live_cfg.config["_site_config"] = self._site_config
            try:
                result = await job.run(self._pool, live_cfg.config)
                logger.info(
                    "scheduler: job %r ran ok=%s detail=%r changes=%d",
                    job.name, result.ok, result.detail, result.changes_made,
                )
                if bool(result.ok):
                    self._jobs_succeeded += 1
                else:
                    self._jobs_failed += 1
                await self._record_last_run(job.name, ok=bool(result.ok))
            except Exception as e:
                logger.exception("scheduler: job %r raised: %s", job.name, e)
                self._jobs_failed += 1
                await self._record_last_run(job.name, ok=False)

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

    def get_stats(self) -> dict[str, Any]:
        """Return live in-process scheduler health stats.

        Used by ``/api/metrics/operational`` (poindexter#395) to replace
        the legacy ``TaskExecutor`` block. Counters are process-local —
        they reset on restart. That's intentional: the endpoint exists to
        answer "is the scheduler firing right now?", which is exactly the
        cycle these counters describe.

        Returns:
            dict with keys:
                ``is_running`` — True iff apscheduler's loop is up
                ``registered_job_count`` — number of accepted Jobs
                ``jobs_run`` — total fires since last restart
                ``jobs_succeeded`` — fires that returned ok=True
                ``jobs_failed`` — fires that raised or returned ok=False
                ``last_tick_epoch`` — Unix epoch (float) of the most
                    recent fire, or None if the scheduler hasn't fired
                ``next_run_epoch`` — Unix epoch (float) of the next
                    scheduled fire across all jobs, or None if nothing
                    is queued
        """
        next_run_epoch: float | None = None
        try:
            jobs = self._scheduler.get_jobs()
            next_runs = [
                j.next_run_time.timestamp()
                for j in jobs
                if getattr(j, "next_run_time", None) is not None
            ]
            if next_runs:
                next_run_epoch = min(next_runs)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("scheduler.get_stats: next_run probe failed: %s", e)

        return {
            "is_running": bool(self._scheduler.running),
            "registered_job_count": len(self._registered),
            "jobs_run": self._jobs_run,
            "jobs_succeeded": self._jobs_succeeded,
            "jobs_failed": self._jobs_failed,
            "last_tick_epoch": self._last_tick_epoch,
            "next_run_epoch": next_run_epoch,
        }

    async def _record_last_run(self, name: str, ok: bool) -> None:
        """Stamp ``app_settings`` with this job's last-run epoch + status.

        Two keys per job, written every fire:

        - ``plugin_job_last_run_<name>`` — Unix epoch seconds (string).
        - ``plugin_job_last_status_<name>`` — ``"ok"`` or ``"err"``.

        Dashboards that surface "minutes since last run" should read these
        instead of the legacy ``idle_last_run_*`` keys (which only the
        retired ``services/idle_worker.py`` wrote and were stuck once that
        loop was decomposed into plugin Jobs).

        Failure here is swallowed: telemetry must not crash the scheduler.
        """
        import time
        epoch = str(int(time.time()))
        status = "ok" if ok else "err"
        sql = (
            "INSERT INTO app_settings (key, value, category, description, updated_at) "
            "VALUES ($1, $2, 'plugin_telemetry', $3, NOW()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    sql,
                    f"plugin_job_last_run_{name}",
                    epoch,
                    f"Unix epoch of last fire for plugin job {name!r} (auto-written by PluginScheduler)",
                )
                await conn.execute(
                    sql,
                    f"plugin_job_last_status_{name}",
                    status,
                    f"Outcome of last fire for plugin job {name!r}: 'ok' or 'err'",
                )
        except Exception as e:
            logger.warning("scheduler: last-run telemetry write failed for %r: %s", name, e)
