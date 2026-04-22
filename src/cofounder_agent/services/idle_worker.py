"""Idle Worker — background maintenance tasks that run when the pipeline has no active content generation."""

import time
from contextlib import suppress

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


class IdleWorker:
    """Background maintenance tasks for when the pipeline is idle."""

    def __init__(self, pool):
        self.pool = pool
        self._last_run: dict[str, float] = {}
        self._schedules_loaded = False

    async def _load_persisted_schedules(self):
        """Load last-run timestamps from app_settings so restarts don't cause a thundering herd."""
        if self._schedules_loaded:
            return
        try:
            rows = await self.pool.fetch(
                "SELECT key, value FROM app_settings WHERE key LIKE 'idle_last_run_%'"
            )
            for row in rows:
                task_name = row["key"].replace("idle_last_run_", "")
                with suppress(ValueError, TypeError):
                    self._last_run[task_name] = float(row["value"])
            self._schedules_loaded = True
            if rows:
                logger.debug("[IDLE] Loaded %d persisted schedule timestamps", len(rows))
        except Exception as e:
            logger.warning("[IDLE] Failed to load persisted schedules: %s", e)
            self._schedules_loaded = True  # Don't retry on every cycle

    async def _persist_mark_run(self, task_name: str):
        """Mark a task as run in-memory and persist to DB."""
        now = time.time()
        self._last_run[task_name] = now
        try:
            await self.pool.execute(
                "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
                f"idle_last_run_{task_name}", str(now),
            )
        except Exception as e:
            logger.debug("[IDLE] Failed to persist schedule for %s: %s", task_name, e)

    async def _create_gitea_issue(self, title: str, body: str) -> bool:
        """Delegate to the shared dedup-aware utility.

        Historically this was inline here; extracted to utils.gitea_issues
        so services/jobs/* can share the same dedup logic.
        """
        from utils.gitea_issues import create_gitea_issue
        return await create_gitea_issue(title, body)

    def _is_due(self, task_name: str, interval_minutes: int) -> bool:
        """Check if a task is due. Uses 4x cooldown if task previously completed all work."""
        last = self._last_run.get(task_name, 0)
        # If task completed all work last run, use extended cooldown
        cooldown_key = f"{task_name}_completed"
        if self._last_run.get(cooldown_key, 0) > last:
            # Task finished all work — use 4x interval before rechecking
            return (time.time() - last) >= (interval_minutes * 60 * 4)
        return (time.time() - last) >= (interval_minutes * 60)

    def _mark_completed(self, task_name: str):
        """Mark a task as having completed all available work (extended cooldown)."""
        self._last_run[f"{task_name}_completed"] = time.time()

    async def run_cycle(self) -> dict:
        """Run one cycle of all due idle tasks. Returns summary.

        **This cycle is the shrinking residue of the pre-plugin scheduler.**
        PluginScheduler (main.py lifespan) now owns every job that has a
        services/jobs/ counterpart — sync_page_views, audit_published_quality,
        crosspost_to_devto, etc. are all registered via entry_points and run
        on apscheduler. The tasks that still live here are the ones that
        either (a) have no Job counterpart yet (image regen, podcast/video
        backfill, anomaly detection, memory-staleness, embedding refresh)
        or (b) are event-driven rather than scheduled (topic discovery,
        scheduled-post publishing).

        When you port one of the remaining methods to services/jobs/, delete
        its call here too so the pipeline doesn't double-dispatch.
        """
        await self._load_persisted_schedules()
        results = {}

        # Publish scheduled posts whose publish_at time has arrived.
        # Event-driven (publish_at is a per-task timestamp), no Job counterpart.
        results["scheduled_publishes"] = await self._publish_scheduled_posts()

        # Topic discovery — event-driven (issue #229). Fires on signals
        # (queue_low, stale_content, rejection_streak, manual). 24h safety-net
        # kept as fallback so the system never stalls completely if signal
        # evaluation breaks.
        should_discover, reason = await self._should_trigger_discovery()
        if should_discover:
            logger.info("[IDLE] Topic discovery triggered by signal: %s", reason)
            results["topic_discovery"] = await self._discover_and_queue_topics()
            results["topic_discovery"]["trigger"] = reason
            await self._persist_mark_run("topic_discovery")
        elif self._is_due("topic_discovery", 1440):
            logger.warning("[IDLE] Topic discovery: 24h safety-net triggered (signals not firing?)")
            results["topic_discovery"] = await self._discover_and_queue_topics()
            results["topic_discovery"]["trigger"] = "safety_net_24h"
            await self._persist_mark_run("topic_discovery")

        # --- GPU/heavy tasks: skip when pipeline is actively generating ---
        pending = await self.pool.fetchrow(
            "SELECT COUNT(*) as c FROM content_tasks WHERE status IN ('pending', 'in_progress')"
        )
        if pending and pending["c"] > 0:
            logger.debug("[IDLE] %d active tasks — skipping GPU-heavy idle work", pending["c"])
            if results:
                return results
            return {"skipped": True, "reason": f"{pending['c']} active tasks"}

        if results:
            logger.info("[IDLE] Completed %d background tasks: %s",
                        len(results), ", ".join(results.keys()))

        return results

    async def _publish_scheduled_posts(self) -> dict:
        """Publish approved tasks whose scheduled_at has arrived."""
        if not self.pool:
            return {"published": 0}
        try:
            rows = await self.pool.fetch(
                "SELECT task_id::text, id FROM pipeline_tasks "
                "WHERE status = 'approved' AND scheduled_at IS NOT NULL AND scheduled_at <= NOW()"
            )
            if not rows:
                return {"published": 0}

            published = 0
            for row in rows:
                task_id = row["task_id"]
                numeric_id = row["id"]
                try:
                    from services.database_service import DatabaseService
                    from services.publish_service import publish_post_from_task

                    db = DatabaseService()
                    db._pool = self.pool
                    task = await db.get_task(str(task_id))
                    if not task:
                        continue
                    result = await publish_post_from_task(
                        db, task, str(task_id),
                        publisher="scheduled",
                        trigger_revalidation=True,
                        queue_social=True,
                        draft_mode=False,
                        honor_pacing=False,
                    )
                    if result.success:
                        await self.pool.execute(
                            "UPDATE pipeline_tasks SET scheduled_at = NULL WHERE task_id = $1",
                            task_id,
                        )
                        published += 1
                        logger.info("[SCHEDULED] Published task %s (id %s)", task_id, numeric_id)
                    else:
                        logger.warning("[SCHEDULED] Publish failed for %s: %s", task_id, result.error)
                except Exception as e:
                    logger.exception("[SCHEDULED] Error publishing %s: %s", task_id, e)
            return {"published": published, "checked": len(rows)}
        except Exception as e:
            logger.exception("[SCHEDULED] Error checking scheduled posts: %s", e)
            return {"published": 0, "error": str(e)}

    async def _should_trigger_discovery(self) -> tuple[bool, str]:
        """Evaluate whether topic discovery should fire now (issue #229).

        Returns (should_fire, reason).  Signals considered:
        - Throttle gate: approval queue full → suppress (GH-89 AC#3)
        - Manual trigger: app_settings.topic_discovery_manual_trigger = true
        - Queue low: pending_tasks < queue_low_threshold (default 2)
        - Stale content: last published > stale_hours (default 6)
        - Rejection streak: 3+ consecutive rejections
        - Cooldown: min 30 min between runs (configurable)

        All thresholds are app_settings knobs so operators can tune per niche.
        """
        if not self.pool:
            return False, "no_pool"

        # 0. Throttle gate — if the approval queue is at or above
        # ``max_approval_queue``, suppress discovery. Without this,
        # auto-generated topics pile up behind the throttle wall and the
        # operator has to shovel out a mountain of stale pending work
        # before the pipeline can move. Runs BEFORE cooldown/manual
        # checks so even a manual_trigger can't stuff more topics
        # into a full queue. See GH-89.
        try:
            from services.pipeline_throttle import is_queue_full

            full, queue_size, queue_limit = await is_queue_full(self.pool)
            if full:
                return False, f"queue_full({queue_size}>={queue_limit})"
        except Exception as e:
            logger.debug("[IDLE] Throttle-gate check failed: %s", e)

        # 1. Manual trigger (runs BEFORE cooldown so an operator who
        # explicitly asks for "discover now" isn't silently ignored for up
        # to 30 min — they already saw the state and asked to override.
        # Closes gitea#277. Queue-full from step 0 still applies above:
        # manual can't stuff more topics onto a wall.
        try:
            manual = (await self._get_setting(
                "topic_discovery_manual_trigger", "false"
            )).strip().lower()
            if manual == "true":
                # Clear the flag so it doesn't re-fire every cycle
                await self.pool.execute(
                    "UPDATE app_settings SET value = 'false' "
                    "WHERE key = 'topic_discovery_manual_trigger'"
                )
                return True, "manual_trigger"
        except Exception as e:
            logger.debug("[IDLE] Manual trigger check failed: %s", e)

        # 2. Cooldown check (after manual so operator override wins)
        try:
            cooldown_s = int(await self._get_setting(
                "topic_discovery_min_cooldown_seconds", "1800"
            ))
        except (ValueError, TypeError):
            cooldown_s = 1800  # 30 min default

        try:
            last_raw = await self._get_setting("idle_last_run_topic_discovery", "0")
            last_ts = float(last_raw or 0)
        except (ValueError, TypeError):
            last_ts = 0.0

        import time
        now_ts = time.time()
        if now_ts - last_ts < cooldown_s:
            return False, "cooldown"

        # 3. Queue-low signal
        try:
            low_threshold = int(await self._get_setting(
                "topic_discovery_queue_low_threshold", "2"
            ))
        except (ValueError, TypeError):
            low_threshold = 2

        try:
            pending = await self.pool.fetchval(
                "SELECT COUNT(*) FROM content_tasks WHERE status = 'pending'"
            )
            if (pending or 0) < low_threshold:
                return True, f"queue_low({pending}<{low_threshold})"
        except Exception as e:
            logger.debug("[IDLE] Queue-low check failed: %s", e)

        # 4. Stale content signal
        try:
            stale_hours = int(await self._get_setting(
                "topic_discovery_stale_hours", "6"
            ))
        except (ValueError, TypeError):
            stale_hours = 6

        try:
            last_pub = await self.pool.fetchval(
                "SELECT MAX(published_at) FROM posts WHERE status = 'published'"
            )
            if last_pub:
                from datetime import datetime, timezone
                if last_pub.tzinfo is None:
                    last_pub = last_pub.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - last_pub).total_seconds() / 3600
                if age_hours > stale_hours:
                    return True, f"stale_content({age_hours:.1f}h>{stale_hours}h)"
        except Exception as e:
            logger.debug("[IDLE] Stale-content check failed: %s", e)

        # 5. Rejection streak signal
        try:
            streak_threshold = int(await self._get_setting(
                "topic_discovery_rejection_streak", "3"
            ))
        except (ValueError, TypeError):
            streak_threshold = 3

        try:
            _streak_h = site_config.get_int("topic_discovery_streak_window_hours", 6)
            recent = await self.pool.fetch(
                "SELECT status FROM content_tasks "
                f"WHERE updated_at > NOW() - INTERVAL '{_streak_h} hours' "
                "ORDER BY updated_at DESC LIMIT $1",
                streak_threshold,
            )
            if len(recent) >= streak_threshold and all(
                r["status"] in ("rejected", "rejected_final") for r in recent
            ):
                return True, f"rejection_streak({streak_threshold})"
        except Exception as e:
            logger.debug("[IDLE] Rejection-streak check failed: %s", e)

        return False, "no_signal"

    async def _discover_and_queue_topics(self) -> dict:
        """Discover trending topics and queue them as content tasks."""
        try:
            from services.topic_discovery import TopicDiscovery
            discovery = TopicDiscovery(self.pool)
            topics = await discovery.discover(max_topics=5)

            if not topics:
                return {"discovered": 0, "queued": 0, "note": "no fresh topics found"}

            queued = await discovery.queue_topics(topics)
            logger.info("[IDLE] Topic discovery: %d discovered, %d queued", len(topics), queued)
            return {
                "discovered": len(topics),
                "queued": queued,
                "topics": [t.title[:50] for t in topics],
            }

        except Exception as e:
            logger.warning("[IDLE] Topic discovery failed: %s", e)
            return {"error": str(e)}

    async def _get_setting(self, key: str, default: str) -> str:
        """Read an app_setting with fallback. Used by _should_trigger_discovery."""
        try:
            row = await self.pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", key,
            )
            if row and row["value"]:
                return row["value"]
        except Exception:
            pass
        return default



