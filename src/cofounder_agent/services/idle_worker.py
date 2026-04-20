"""Idle Worker — background maintenance tasks that run when the pipeline has no active content generation."""

import json
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

        # Stale embedding refresh — not the same as auto_embed_posts (which
        # is covered by services/jobs/auto_embed_posts). This one detects
        # rows whose embedding is old and refreshes them.
        if self._is_due("embedding_refresh", 240):
            results["embedding_refresh"] = await self._refresh_stale_embeddings()
            await self._persist_mark_run("embedding_refresh")

        # Regenerate stock photo images with SDXL (every 6 hours, 5 per cycle).
        # GPU-heavy, no Job counterpart.
        if self._is_due("image_regen", 360):
            results["image_regen"] = await self._regenerate_stock_images()
            await self._persist_mark_run("image_regen")

        # Backfill podcast episodes for posts missing them. No Job counterpart.
        if self._is_due("podcast_backfill", 240):
            results["podcast_backfill"] = await self._backfill_podcasts()
            await self._persist_mark_run("podcast_backfill")

        # Backfill videos for posts missing them. No Job counterpart.
        if self._is_due("video_backfill", 360):
            results["video_backfill"] = await self._backfill_videos()
            await self._persist_mark_run("video_backfill")

        # Anomaly detection — statistical outlier monitoring. No Job counterpart.
        if self._is_due("anomaly_detect", 240):
            results["anomaly_detect"] = await self._detect_anomalies()
            await self._persist_mark_run("anomaly_detect")

        # Memory staleness check — alert when any pgvector writer goes stale
        # (every 30 min; internal cooldown prevents Discord spam).
        if self._is_due("memory_stale_check", 30):
            results["memory_stale_check"] = await self._check_memory_staleness()
            await self._persist_mark_run("memory_stale_check")

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

    async def _refresh_stale_embeddings(self) -> dict:
        """Check if any published posts need re-embedding."""
        try:
            # This would typically call the embedding service
            # For now, just count posts without embeddings
            try:
                from services.site_config import site_config
                local_db_url = site_config.get("local_database_url")
                if not local_db_url:
                    return {"note": "no local DB configured for embeddings"}
            except Exception:
                return {"note": "site_config not available"}

            return {"note": "embedding refresh deferred to auto-embed cron"}

        except Exception as e:
            return {"error": str(e)}

    async def _should_trigger_discovery(self) -> tuple[bool, str]:
        """Evaluate whether topic discovery should fire now (issue #229).

        Returns (should_fire, reason).  Signals considered:
        - Manual trigger: app_settings.topic_discovery_manual_trigger = true
        - Queue low: pending_tasks < queue_low_threshold (default 2)
        - Stale content: last published > stale_hours (default 6)
        - Rejection streak: 3+ consecutive rejections
        - Cooldown: min 30 min between runs (configurable)

        All thresholds are app_settings knobs so operators can tune per niche.
        """
        if not self.pool:
            return False, "no_pool"

        # 1. Cooldown check
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

        # 2. Manual trigger (highest priority after cooldown)
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

    async def _regenerate_stock_images(self) -> dict:
        """Find posts with Pexels stock photos and replace with SDXL-generated images."""
        try:
            import os
            import tempfile

            import asyncpg

            # Find posts with pexels URLs (stock photos)
            cloud_url = os.getenv("DATABASE_URL", "")
            if not cloud_url:
                return {"note": "no cloud DB for posts"}

            cloud = await asyncpg.connect(cloud_url)
            posts = await cloud.fetch("""
                SELECT p.id, p.title, c.name as category
                FROM posts p LEFT JOIN categories c ON p.category_id = c.id
                WHERE p.status = 'published'
                AND p.featured_image_url LIKE '%pexels%'
                LIMIT 5
            """)

            if not posts:
                await cloud.close()
                self._mark_completed("image_regen")
                return {"regenerated": 0, "note": "all posts have AI images"}

            try:
                # Get styles from local settings
                styles = {}
                rows = await self.pool.fetch("SELECT key, value FROM app_settings WHERE key LIKE 'image_style_%'")
                for r in rows:
                    styles[r["key"].replace("image_style_", "")] = r["value"]
                negative = await self.pool.fetchval("SELECT value FROM app_settings WHERE key = 'image_negative_prompt'") or ""

                from services.image_service import get_image_service
                svc = get_image_service()

                import cloudinary
                import cloudinary.uploader
                cloudinary.config(
                    cloud_name=site_config.get("cloudinary_cloud_name"),
                    api_key=site_config.get("cloudinary_api_key"),
                    api_secret=site_config.get("cloudinary_api_secret"),
                )

                regenerated = 0
                for post in posts:
                    cat = (post["category"] or "technology").lower()
                    # Use Ollama to generate a proper SDXL prompt
                    prompt = f"photorealistic scene related to {post['title'][:50]}, cinematic lighting, 4k, detailed, no people, no text"
                    try:
                        import httpx
                        _ollama = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
                        async with httpx.AsyncClient(timeout=30) as _c:
                            _r = await _c.post(f"{_ollama}/api/generate", json={
                                "model": "llama3:latest",
                                "prompt": f"Write a Stable Diffusion XL prompt for a blog featured image about: {post['title'][:80]}\nRequirements: photorealistic scene, cinematic lighting, no people, no text. 1 sentence only. Output ONLY the prompt.",
                                "stream": False, "options": {"num_predict": 100, "temperature": 0.7},
                            })
                            _r.raise_for_status()
                            _gen = _r.json().get("response", "").strip().strip('"')
                            if len(_gen) > 20:
                                prompt = _gen
                    except Exception:
                        pass  # Use fallback prompt

                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        output_path = tmp.name

                    try:
                        success = await svc.generate_image(
                            prompt=prompt, output_path=output_path,
                            negative_prompt=negative,
                        )
                        if success and os.path.exists(output_path):
                            import asyncio
                            result = await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda p=output_path, c=cat: cloudinary.uploader.upload(
                                    p, folder="generated/",
                                    resource_type="image", tags=["featured", c],
                                ),
                            )
                            image_url = result.get("secure_url", "")
                            if image_url:
                                await cloud.execute(
                                    "UPDATE posts SET featured_image_url = $1, updated_at = NOW() WHERE id = $2",
                                    image_url, post["id"],
                                )
                                regenerated += 1
                                logger.info("[IDLE] Regenerated image for: %s", post["title"][:40])
                            os.remove(output_path)
                    except Exception as e:
                        logger.warning("[IDLE] Image regen failed for %s: %s", post["title"][:30], e)
                        with suppress(OSError):
                            os.remove(output_path)
            finally:
                await cloud.close()

            if regenerated:
                await self._create_gitea_issue(
                    f"images: regenerated {regenerated} stock photos with SDXL",
                    f"Replaced Pexels stock photos with AI-generated category art for {regenerated} posts.",
                )

            return {"regenerated": regenerated, "remaining": len(posts) - regenerated}

        except Exception as e:
            logger.warning("[IDLE] Image regeneration failed: %s", e)
            return {"error": str(e)}

    async def _backfill_podcasts(self) -> dict:
        """Generate podcast episodes for published posts that don't have them yet.

        Also uploads generated episodes to R2 CDN and rebuilds the podcast
        RSS feed — previously only the publish flow did this (#208).
        """
        try:
            import os

            import asyncpg

            from services.podcast_service import PodcastService

            cloud_url = os.getenv("DATABASE_URL", "")
            if not cloud_url:
                return {"note": "no cloud DB"}

            cloud = await asyncpg.connect(cloud_url)
            posts = await cloud.fetch("""
                SELECT id::text, title, content
                FROM posts WHERE status = 'published'
                ORDER BY published_at DESC LIMIT 20
            """)

            svc = PodcastService()
            generated = 0
            uploaded = 0

            # First pass: sync existing local episodes to R2 (runs until all caught up)
            try:
                from services.r2_upload_service import upload_podcast_episode
                sync_count = 0
                for post in posts:
                    if svc.episode_exists(post["id"]) and sync_count < 5:
                        try:
                            r2_url = await upload_podcast_episode(post["id"])
                            if r2_url:
                                sync_count += 1
                        except Exception:
                            pass
                if sync_count > 0:
                    uploaded += sync_count
                    logger.info("[IDLE] Synced %d podcast episodes to R2", sync_count)
            except ImportError:
                pass

            # Second pass: generate new episodes for posts that don't have them
            for post in posts:
                if svc.episode_exists(post["id"]):
                    continue
                try:
                    result = await svc.generate_episode(
                        post_id=post["id"],
                        title=post["title"],
                        content=post["content"] or "",
                    )
                    if result.success:
                        generated += 1
                        logger.info("[IDLE] Generated podcast for: %s", post["title"][:40])
                        # Upload to R2 CDN (#208)
                        try:
                            from services.r2_upload_service import upload_podcast_episode
                            r2_url = await upload_podcast_episode(post["id"])
                            if r2_url:
                                uploaded += 1
                                logger.info("[IDLE] Uploaded podcast to R2: %s", post["id"][:8])
                        except Exception as r2_err:
                            logger.warning("[IDLE] R2 upload failed for %s: %s", post["id"][:8], r2_err)
                    if generated >= 2:  # Max 2 per cycle
                        break
                except Exception as e:
                    logger.warning("[IDLE] Podcast backfill failed for %s: %s", post["title"][:30], e)

            # Rebuild podcast RSS feed on R2 if we uploaded anything
            if uploaded > 0:
                try:
                    import httpx as _hx

                    from services.r2_upload_service import upload_to_r2
                    from services.site_config import site_config as _scfg
                    _api_base = _scfg.get("internal_api_base_url", "http://localhost:8002")
                    async with _hx.AsyncClient(timeout=_hx.Timeout(30.0, connect=5.0)) as _client:
                        _feed = await _client.get(f"{_api_base}/api/podcast/feed.xml", timeout=30)
                        _feed_path = os.path.join(os.path.expanduser("~"), ".poindexter", "podcast-feed.xml")
                        with open(_feed_path, "w") as _f:
                            _f.write(_feed.text)
                        await upload_to_r2(_feed_path, "podcast/feed.xml", "application/rss+xml")
                        logger.info("[IDLE] Podcast RSS feed rebuilt on R2")
                except Exception as feed_err:
                    logger.warning("[IDLE] Podcast feed rebuild failed (non-fatal): %s", feed_err)

            await cloud.close()
            if generated == 0:
                self._mark_completed("podcast_backfill")
            return {"generated": generated, "uploaded": uploaded}
        except Exception as e:
            logger.warning("[IDLE] Podcast backfill failed: %s", e)
            return {"error": str(e)}

    async def _backfill_videos(self) -> dict:
        """Generate videos for published posts that have podcasts but no video."""
        try:
            import os

            import asyncpg

            from services.podcast_service import PODCAST_DIR
            from services.video_service import VIDEO_DIR, generate_video_for_post

            cloud_url = os.getenv("DATABASE_URL", "")
            if not cloud_url:
                return {"note": "no cloud DB"}

            cloud = await asyncpg.connect(cloud_url)
            posts = await cloud.fetch("""
                SELECT id::text, title, content
                FROM posts WHERE status = 'published'
                ORDER BY published_at DESC LIMIT 20
            """)

            generated = 0
            for post in posts:
                post_id = post["id"]
                podcast_path = PODCAST_DIR / f"{post_id}.mp3"
                video_path = VIDEO_DIR / f"{post_id}.mp4"

                # Only generate video if podcast exists but video doesn't
                if not podcast_path.exists() or video_path.exists():
                    continue

                try:
                    result = await generate_video_for_post(
                        post_id=post_id,
                        title=post["title"],
                        content=post["content"] or "",
                    )
                    if result.success:
                        generated += 1
                        logger.info("[IDLE] Generated video for: %s", post["title"][:40])
                    if generated >= 1:  # Max 1 per cycle (GPU-heavy)
                        break
                except Exception as e:
                    logger.warning("[IDLE] Video backfill failed for %s: %s", post["title"][:30], e)

            await cloud.close()
            if generated == 0:
                self._mark_completed("video_backfill")
            return {"generated": generated}
        except Exception as e:
            logger.warning("[IDLE] Video backfill failed: %s", e)
            return {"error": str(e)}

    async def _check_memory_staleness(self) -> dict:
        """Scan the shared-memory writers for stale syncs and alert.

        For each writer row in `MemoryClient.stats()['by_writer']`, compare
        age against a per-writer threshold (app_settings key
        `memory_stale_threshold_seconds_<writer>`, global fallback
        `memory_stale_threshold_seconds`, default 6h). If age > threshold AND
        we haven't alerted on this writer in the last
        `memory_stale_alert_cooldown_seconds` (default 6h), fire:
            1. A `memory_sync_stale` audit_log event (visible on /pipeline)
            2. A Discord ops-channel message via the existing webhook

        Dedup state lives in app_settings under `memory_stale_last_alerts`
        as a JSON blob mapping writer → ISO timestamp of last alert.
        """
        from datetime import datetime, timezone

        stats = None
        try:
            from poindexter.memory import MemoryClient

            async with MemoryClient() as mem:
                stats = await mem.stats()
        except Exception as e:
            return {"error": f"stats fetch failed: {e}"}

        if not stats or "by_writer" not in stats:
            return {"error": "no stats data"}

        now = datetime.now(timezone.utc)

        # Load cooldown state.
        raw_last = await self._get_setting("memory_stale_last_alerts", "{}")
        try:
            last_alerts: dict[str, str] = json.loads(raw_last) if raw_last else {}
            if not isinstance(last_alerts, dict):
                last_alerts = {}
        except (json.JSONDecodeError, TypeError):
            last_alerts = {}

        cooldown = 6 * 3600
        try:
            cooldown_raw = await self._get_setting(
                "memory_stale_alert_cooldown_seconds", "21600"
            )
            cooldown = int(cooldown_raw)
        except (ValueError, TypeError):
            pass

        global_threshold = 6 * 3600
        with suppress(ValueError, TypeError):
            global_threshold = int(
                await self._get_setting("memory_stale_threshold_seconds", "21600")
            )

        stale_writers: list[dict] = []
        alerts_fired: list[str] = []
        new_last_alerts = dict(last_alerts)

        for writer, data in stats["by_writer"].items():
            newest = data.get("newest")
            if newest is None:
                continue
            # asyncpg returns datetime; normalize to aware UTC.
            if hasattr(newest, "tzinfo") and newest.tzinfo is None:
                newest = newest.replace(tzinfo=timezone.utc)
            age_seconds = int((now - newest).total_seconds())

            # Per-writer threshold overrides the global one.
            per_key = f"memory_stale_threshold_seconds_{writer}"
            try:
                threshold = int(await self._get_setting(per_key, str(global_threshold)))
            except (ValueError, TypeError):
                threshold = global_threshold

            if age_seconds <= threshold:
                continue

            # Past threshold — decide if we should alert.
            stale_writers.append(
                {
                    "writer": writer,
                    "age_seconds": age_seconds,
                    "threshold": threshold,
                    "count": int(data.get("count") or 0),
                    "newest": newest.isoformat(),
                }
            )

            last_iso = new_last_alerts.get(writer)
            should_alert = True
            if last_iso:
                try:
                    last_dt = datetime.fromisoformat(last_iso)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if (now - last_dt).total_seconds() < cooldown:
                        should_alert = False
                except ValueError:
                    pass

            if not should_alert:
                continue

            # Fire audit event (visible on /pipeline dashboard + Grafana)
            try:
                from services.audit_log import audit_log_bg
                audit_log_bg(
                    "memory_sync_stale", "idle_worker",
                    {
                        "writer": writer,
                        "age_seconds": age_seconds,
                        "threshold_seconds": threshold,
                        "count": int(data.get("count") or 0),
                        "newest": newest.isoformat(),
                    },
                    severity="warning",
                )
            except Exception as e:
                logger.debug("[MEMORY_STALE] audit event failed: %s", e)

            # Discord ops-channel notification
            try:
                from services.task_executor import _notify_openclaw
                age_hours = age_seconds / 3600
                msg = (
                    f"[MEMORY STALE] writer `{writer}` hasn't been embedded in "
                    f"{age_hours:.1f}h (threshold {threshold // 3600}h). "
                    f"{data.get('count', 0)} rows in pgvector, newest={newest.isoformat()}. "
                    f"Check /memory dashboard."
                )
                await _notify_openclaw(msg, critical=False)
                alerts_fired.append(writer)
            except Exception as e:
                logger.debug("[MEMORY_STALE] discord notify failed: %s", e)

            new_last_alerts[writer] = now.isoformat()

        # Persist updated cooldown state
        if new_last_alerts != last_alerts:
            try:
                await self.pool.execute(
                    "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()",
                    "memory_stale_last_alerts",
                    json.dumps(new_last_alerts),
                )
            except Exception as e:
                logger.debug("[MEMORY_STALE] state persist failed: %s", e)

        return {
            "stale_count": len(stale_writers),
            "alerts_fired": len(alerts_fired),
            "stale": stale_writers,
        }

    async def _get_setting(self, key: str, default: str) -> str:
        """Read an app_setting with fallback. Used by the memory staleness check."""
        try:
            row = await self.pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
            if row and row["value"]:
                return row["value"]
        except Exception:
            pass
        return default

    async def _detect_anomalies(self) -> dict:
        """Z-score anomaly detection across system metrics (>2 stddev from 30-day mean)."""
        try:
            import json
            import math

            anomalies = []

            # Anomaly detection windows — tunable for per-customer
            # sensitivity and A/B testing (#198). `current_hours` defines
            # "recent" (default 24h); `baseline_days` defines the
            # historical mean+stddev window (default 30d).
            _current_h = site_config.get_int("brain_anomaly_current_window_hours", 24)
            _baseline_d = site_config.get_int("brain_anomaly_baseline_window_days", 30)

            # Define metrics to monitor: (name, query for recent value, query for historical stats)
            metrics = [
                (
                    "task_failure_rate",
                    f"""SELECT CASE WHEN COUNT(*) = 0 THEN 0
                        ELSE SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::float / COUNT(*)
                        END as val
                    FROM content_tasks WHERE created_at > NOW() - INTERVAL '{_current_h} hours'""",
                    f"""SELECT AVG(daily_rate) as mean, STDDEV(daily_rate) as stddev FROM (
                        SELECT date_trunc('day', created_at) as day,
                            CASE WHEN COUNT(*) = 0 THEN 0
                            ELSE SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::float / COUNT(*)
                            END as daily_rate
                        FROM content_tasks
                        WHERE created_at > NOW() - INTERVAL '{_baseline_d} days'
                        GROUP BY day
                    ) t""",
                ),
                (
                    "avg_quality_score",
                    f"SELECT AVG(quality_score) as val FROM content_tasks WHERE created_at > NOW() - INTERVAL '{_current_h} hours' AND quality_score IS NOT NULL",
                    f"""SELECT AVG(daily_avg) as mean, STDDEV(daily_avg) as stddev FROM (
                        SELECT date_trunc('day', created_at) as day, AVG(quality_score) as daily_avg
                        FROM content_tasks
                        WHERE created_at > NOW() - INTERVAL '{_baseline_d} days' AND quality_score IS NOT NULL
                        GROUP BY day
                    ) t""",
                ),
                (
                    "cost_per_day",
                    f"SELECT COALESCE(SUM(cost_usd), 0) as val FROM cost_logs WHERE created_at > NOW() - INTERVAL '{_current_h} hours'",
                    f"""SELECT AVG(daily_cost) as mean, STDDEV(daily_cost) as stddev FROM (
                        SELECT date_trunc('day', created_at) as day, COALESCE(SUM(cost_usd), 0) as daily_cost
                        FROM cost_logs
                        WHERE created_at > NOW() - INTERVAL '{_baseline_d} days'
                        GROUP BY day
                    ) t""",
                ),
                (
                    "error_log_rate",
                    f"SELECT COUNT(*) as val FROM audit_log WHERE severity = 'error' AND timestamp > NOW() - INTERVAL '{_current_h} hours'",
                    f"""SELECT AVG(daily_errors) as mean, STDDEV(daily_errors) as stddev FROM (
                        SELECT date_trunc('day', timestamp) as day, COUNT(*) as daily_errors
                        FROM audit_log WHERE severity = 'error'
                        AND timestamp > NOW() - INTERVAL '{_baseline_d} days'
                        GROUP BY day
                    ) t""",
                ),
            ]

            for name, recent_q, hist_q in metrics:
                try:
                    recent = await self.pool.fetchval(recent_q)
                    hist = await self.pool.fetchrow(hist_q)

                    if recent is None or hist is None:
                        continue
                    mean = float(hist["mean"] or 0)
                    stddev = float(hist["stddev"] or 0)
                    value = float(recent)

                    if stddev == 0 or math.isnan(stddev):
                        continue  # not enough variance to detect anomalies

                    z_score = (value - mean) / stddev

                    if abs(z_score) > 2.0:
                        direction = "spike" if z_score > 0 else "drop"
                        anomalies.append({
                            "metric": name,
                            "value": round(value, 4),
                            "mean": round(mean, 4),
                            "stddev": round(stddev, 4),
                            "z_score": round(z_score, 2),
                            "direction": direction,
                        })
                except Exception as e:
                    logger.debug("[IDLE] Anomaly check failed for %s: %s", name, e)

            if anomalies:
                # Log to audit_log
                await self.pool.execute(
                    "INSERT INTO audit_log (event_type, source, details, severity) VALUES ($1, $2, $3, $4)",
                    "anomaly_detected", "idle_worker", json.dumps(anomalies), "warning",
                )
                # Create Gitea issue if 2+ anomalies (avoid noise from single metric blips)
                if len(anomalies) >= 2:
                    body = "## Anomalies Detected\n\n" + "\n".join(
                        f"- **{a['metric']}**: {a['value']} ({a['direction']}, z={a['z_score']}, mean={a['mean']}±{a['stddev']})"
                        for a in anomalies
                    )
                    await self._create_gitea_issue(
                        f"anomaly: {len(anomalies)} metrics outside normal range", body,
                    )
                logger.warning("[IDLE] Anomalies detected: %s",
                               ", ".join(f"{a['metric']}={a['value']} (z={a['z_score']})" for a in anomalies))

            else:
                logger.info("[IDLE] Anomaly detection: all metrics within normal range")

            return {"anomalies": len(anomalies), "details": anomalies}

        except Exception as e:
            logger.warning("[IDLE] Anomaly detection failed: %s", e)
            return {"error": str(e)}
