"""Idle Worker — background maintenance tasks that run when the pipeline has no active content generation."""

import json
import time

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
                try:
                    self._last_run[task_name] = float(row["value"])
                except (ValueError, TypeError):
                    pass
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
        """Create a deduplicated Gitea issue for tracking discovered problems."""
        import base64

        import httpx

        gitea_url = site_config.get("gitea_url", "http://localhost:3001")
        gitea_user = site_config.get("gitea_user", "gladlabs")
        gitea_pass = site_config.get("gitea_password", "")
        gitea_repo = site_config.get("gitea_repo", "gladlabs/glad-labs-codebase")

        if not gitea_pass:
            logger.debug("[IDLE] No Gitea password configured — skipping issue creation")
            return False

        try:
            auth = base64.b64encode(f"{gitea_user}:{gitea_pass}".encode()).decode()
            headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=10) as client:
                # Dedup: check for existing open issues with same prefix
                title_prefix = title.split(":")[0].strip() if ":" in title else title[:30]
                search_resp = await client.get(
                    f"{gitea_url}/api/v1/repos/{gitea_repo}/issues",
                    headers=headers,
                    params={"state": "open", "limit": 10},
                )
                if search_resp.status_code == 200:
                    existing = search_resp.json()
                    for issue in existing:
                        existing_prefix = issue["title"].split(":")[0].strip() if ":" in issue["title"] else issue["title"][:30]
                        if existing_prefix == title_prefix:
                            logger.debug("[IDLE] Gitea issue already exists: #%d %s", issue["number"], title_prefix)
                            return False

                resp = await client.post(
                    f"{gitea_url}/api/v1/repos/{gitea_repo}/issues",
                    headers=headers,
                    json={"title": title, "body": body},
                )
                if resp.status_code < 300:
                    issue = resp.json()
                    logger.info("[IDLE] Created Gitea issue #%d: %s", issue["number"], title[:50])
                    return True
        except Exception as e:
            logger.debug("[IDLE] Gitea issue creation failed: %s", e)
        return False

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
        """Run one cycle of all due idle tasks. Returns summary."""
        await self._load_persisted_schedules()
        results = {}

        # Lightweight syncs run regardless of pipeline activity
        if self._is_due("sync_page_views", 30):
            results["sync_page_views"] = await self._sync_page_views()
            await self._persist_mark_run("sync_page_views")

        if self._is_due("sync_newsletter_subscribers", 60):
            results["sync_newsletter_subscribers"] = await self._sync_newsletter_subscribers()
            await self._persist_mark_run("sync_newsletter_subscribers")

        # Expire stale approval queue items (every 6 hours)
        if self._is_due("expire_stale_approvals", 360):
            results["expire_stale_approvals"] = await self._expire_stale_approvals()
            await self._persist_mark_run("expire_stale_approvals")

        # --- Non-GPU tasks: run even when pipeline is busy ---
        # Topic discovery (web scraping + DB insert, no GPU)
        if self._is_due("topic_discovery", 480):
            results["topic_discovery"] = await self._discover_and_queue_topics()
            await self._persist_mark_run("topic_discovery")

        # Topic gap analysis (DB query only, no GPU)
        if self._is_due("topic_gaps", 1440):
            results["topic_gaps"] = await self._analyze_topic_gaps()
            await self._persist_mark_run("topic_gaps")

        # Shared context sync (filesystem + DB, no GPU)
        if self._is_due("context_sync", 30):
            results["context_sync"] = await self._sync_shared_context()
            await self._persist_mark_run("context_sync")

        # --- GPU/heavy tasks: skip when pipeline is actively generating ---
        pending = await self.pool.fetchrow(
            "SELECT COUNT(*) as c FROM content_tasks WHERE status IN ('pending', 'in_progress')"
        )
        if pending and pending["c"] > 0:
            logger.debug("[IDLE] %d active tasks — skipping GPU-heavy idle work", pending["c"])
            if results:
                return results
            return {"skipped": True, "reason": f"{pending['c']} active tasks"}

        # 1. Quality audit on published posts (every 6 hours)
        if self._is_due("quality_audit", 360):
            results["quality_audit"] = await self._audit_published_quality()
            await self._persist_mark_run("quality_audit")

        # 2. Broken link check (every 12 hours)
        if self._is_due("link_check", 720):
            results["link_check"] = await self._check_published_links()
            await self._persist_mark_run("link_check")

        # 3. Threshold tuning (every 12 hours)
        if self._is_due("threshold_tune", 720):
            results["threshold_tune"] = await self._tune_thresholds()
            await self._persist_mark_run("threshold_tune")

        # 4. Stale embedding refresh (every 4 hours)
        if self._is_due("embedding_refresh", 240):
            results["embedding_refresh"] = await self._refresh_stale_embeddings()
            await self._persist_mark_run("embedding_refresh")

        # 8. Auto-embed new/changed posts (every 2 hours)
        if self._is_due("auto_embed", 120):
            results["auto_embed"] = await self._auto_embed_posts()
            await self._persist_mark_run("auto_embed")

        # 9. Regenerate stock photo images with SDXL (every 6 hours, 5 per cycle)
        if self._is_due("image_regen", 360):
            results["image_regen"] = await self._regenerate_stock_images()
            await self._persist_mark_run("image_regen")

        # 10. Backfill podcast episodes for posts missing them (every 4 hours, 2 per cycle)
        if self._is_due("podcast_backfill", 240):
            results["podcast_backfill"] = await self._backfill_podcasts()
            await self._persist_mark_run("podcast_backfill")

        # 11. Backfill videos for posts missing them (every 6 hours, 1 per cycle)
        if self._is_due("video_backfill", 360):
            results["video_backfill"] = await self._backfill_videos()
            await self._persist_mark_run("video_backfill")

        # 12. Fix uncategorized posts (every 12 hours)
        if self._is_due("fix_categories", 720):
            results["fix_categories"] = await self._fix_uncategorized_posts()
            await self._persist_mark_run("fix_categories")

        # 11. Fix posts missing SEO metadata (every 12 hours)
        if self._is_due("fix_seo", 720):
            results["fix_seo"] = await self._fix_missing_seo()
            await self._persist_mark_run("fix_seo")

        # 12. Clean broken internal links (every 24 hours)
        if self._is_due("fix_internal_links", 1440):
            results["fix_internal_links"] = await self._fix_broken_internal_links()
            await self._persist_mark_run("fix_internal_links")

        # 13. Remove broken external links (every 24 hours)
        if self._is_due("fix_external_links", 1440):
            results["fix_external_links"] = await self._fix_broken_external_links()
            await self._persist_mark_run("fix_external_links")

        # 14. Fix duplicate titles (every 24 hours)
        if self._is_due("fix_duplicates", 1440):
            results["fix_duplicates"] = await self._detect_duplicate_posts()
            await self._persist_mark_run("fix_duplicates")
            await self._persist_mark_run("auto_embed")

        # 15. Anomaly detection — statistical outlier monitoring (every 4 hours)
        if self._is_due("anomaly_detect", 240):
            results["anomaly_detect"] = await self._detect_anomalies()
            await self._persist_mark_run("anomaly_detect")

        # 16. Auto-update electricity rate + GPU power from public data (every 30 days)
        if self._is_due("utility_rates", 43200):
            results["utility_rates"] = await self._update_utility_rates()
            await self._persist_mark_run("utility_rates")

        # 17. Memory staleness check — alert when any pgvector writer goes stale
        # (every 30 min; internal cooldown prevents Discord spam).
        if self._is_due("memory_stale_check", 30):
            results["memory_stale_check"] = await self._check_memory_staleness()
            await self._persist_mark_run("memory_stale_check")

        # 17. Post-publish verification — check recently published posts are accessible (every 2 hours)
        if self._is_due("publish_verify", 120):
            results["publish_verify"] = await self._verify_published_posts()
            await self._persist_mark_run("publish_verify")

        # 18. Dev.to cross-posting — cross-post published posts not yet on Dev.to (every 6 hours)
        if self._is_due("devto_crosspost", 360):
            results["devto_crosspost"] = await self._crosspost_to_devto()
            await self._persist_mark_run("devto_crosspost")

        # 19. Database backup — export key tables as JSON to local disk (every 24 hours)
        if self._is_due("db_backup", 720):
            results["db_backup"] = await self._backup_database()
            await self._persist_mark_run("db_backup")

        if results:
            logger.info("[IDLE] Completed %d background tasks: %s",
                        len(results), ", ".join(results.keys()))

        return results

    async def _expire_stale_approvals(self) -> dict:
        """Auto-expire tasks stuck in awaiting_approval beyond the configurable TTL (default 7 days)."""
        try:
            ttl_days = 7
            try:
                row = await self.pool.fetchrow(
                    "SELECT value FROM app_settings WHERE key = 'approval_ttl_days'"
                )
                if row and row["value"]:
                    ttl_days = int(row["value"])
            except Exception:
                pass

            expired = await self.pool.fetch("""
                UPDATE content_tasks
                SET status = 'expired',
                    result = jsonb_build_object('reason', 'Auto-expired: exceeded approval TTL of ' || $1 || ' days')
                WHERE status = 'awaiting_approval'
                  AND updated_at < NOW() - make_interval(days => $1)
                RETURNING task_id, topic
            """, ttl_days)

            if expired:
                logger.info(
                    "[IDLE] Expired %d stale approval tasks (TTL: %d days)",
                    len(expired), ttl_days,
                )
                for row in expired[:5]:
                    logger.info("  - %s: %s", row["task_id"][:8], (row["topic"] or "")[:50])
            return {"expired_count": len(expired), "ttl_days": ttl_days}
        except Exception as e:
            logger.warning("[IDLE] Approval expiry failed: %s", e)
            return {"error": str(e)}

    async def _audit_published_quality(self) -> dict:
        """Re-score a batch of published posts to catch quality drift."""
        try:
            # posts.id is UUID, audit_log.task_id is VARCHAR — cast on comparison.
            rows = await self.pool.fetch("""
                SELECT p.id, p.title, p.slug, LEFT(p.content, 3000) as content_preview
                FROM posts p
                WHERE p.status = 'published'
                AND p.id::text NOT IN (
                    SELECT DISTINCT task_id FROM audit_log
                    WHERE event_type = 'idle_quality_audit'
                    AND task_id IS NOT NULL
                    AND timestamp > NOW() - INTERVAL '7 days'
                )
                ORDER BY p.published_at ASC
                LIMIT 5
            """)

            if not rows:
                return {"audited": 0, "note": "all posts recently audited"}

            issues = []
            for row in rows:
                content = row["content_preview"] or ""
                word_count = len(content.split())
                has_headings = "##" in content or "<h2" in content
                if word_count < 500:
                    issues.append(f"{row['title'][:40]}: only {word_count} words")
                if not has_headings:
                    issues.append(f"{row['title'][:40]}: no headings found")

                # Record the audit so this post is skipped on the next cycle.
                try:
                    await self.pool.execute(
                        "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
                        "VALUES ($1, $2, $3, $4::jsonb, $5)",
                        "idle_quality_audit",
                        "idle_worker",
                        str(row["id"]),
                        json.dumps({"title": row["title"], "word_count": word_count}),
                        "info",
                    )
                except Exception as _e:
                    logger.debug("[IDLE] audit_log insert failed: %s", _e)

            if issues:
                body = "## Quality Audit Findings\n\n" + "\n".join(f"- {i}" for i in issues)
                await self._create_gitea_issue(
                    f"quality: {len(issues)} issues in {len(rows)} audited posts",
                    body,
                )

            logger.info("[IDLE] Quality audit: checked %d posts, %d issues", len(rows), len(issues))
            return {"audited": len(rows), "issues": issues[:10]}

        except Exception as e:
            logger.warning("[IDLE] Quality audit failed: %s", e)
            return {"error": str(e)}

    async def _check_published_links(self) -> dict:
        """Check a batch of published posts for broken links."""
        try:
            import httpx

            rows = await self.pool.fetch("""
                SELECT p.id, p.title, p.content
                FROM posts p
                WHERE p.status = 'published'
                ORDER BY RANDOM()
                LIMIT 3
            """)

            import re
            broken = []
            checked = 0
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0),
                follow_redirects=True,
            ) as client:
                for row in rows:
                    urls = re.findall(r'https?://[^\s\)"<>]+', row["content"] or "")
                    site_domain = site_config.get("site_domain", "localhost")
                    for url in urls[:10]:  # Max 10 URLs per post
                        if site_domain in url:
                            continue  # Skip internal
                        try:
                            resp = await client.head(url, timeout=8)
                            checked += 1
                            if resp.status_code >= 400:
                                broken.append({"post": row["title"][:40], "url": url, "status": resp.status_code})
                        except Exception:
                            broken.append({"post": row["title"][:40], "url": url, "status": "unreachable"})
                            checked += 1

            if broken:
                body = "## Broken Links Found\n\n" + "\n".join(
                    f"- [{b['post']}] {b['url']} → {b['status']}" for b in broken[:10]
                )
                await self._create_gitea_issue(
                    f"links: {len(broken)} broken URLs in published posts",
                    body,
                )

            logger.info("[IDLE] Link check: %d checked, %d broken", checked, len(broken))
            return {"checked": checked, "broken": len(broken), "details": broken[:5]}

        except Exception as e:
            logger.warning("[IDLE] Link check failed: %s", e)
            return {"error": str(e)}

    async def _analyze_topic_gaps(self) -> dict:
        """Analyze published content to find topic gaps and suggest new topics."""
        try:
            # Get category distribution
            categories = await self.pool.fetch("""
                SELECT c.name, COUNT(p.id) as posts
                FROM categories c
                LEFT JOIN posts p ON c.id = p.category_id AND p.status = 'published'
                GROUP BY c.name
                ORDER BY posts ASC
            """)

            # Find underserved categories
            empty = [r["name"] for r in categories if r["posts"] == 0]
            low = [f"{r['name']} ({r['posts']})" for r in categories if 0 < r["posts"] < 5]

            # Check for topic freshness — how old is the newest post per category?
            stale = await self.pool.fetch("""
                SELECT c.name, MAX(p.published_at) as latest
                FROM categories c
                JOIN posts p ON c.id = p.category_id AND p.status = 'published'
                GROUP BY c.name
                HAVING MAX(p.published_at) < NOW() - INTERVAL '14 days'
            """)

            suggestions = []
            if empty:
                suggestions.append(f"Empty categories need posts: {', '.join(empty)}")
            if low:
                suggestions.append(f"Low coverage: {', '.join(low)}")
            if stale:
                suggestions.append(f"Stale categories (no post in 14d): {', '.join(r['name'] for r in stale)}")

            if suggestions:
                body = "## Topic Gap Analysis\n\n" + "\n".join(f"- {s}" for s in suggestions)
                await self._create_gitea_issue(
                    f"content: topic gaps — {len(empty)} empty categories, {len(low)} low coverage",
                    body,
                )

            logger.info("[IDLE] Topic gaps: %d suggestions", len(suggestions))
            return {"empty_categories": empty, "low_coverage": low, "suggestions": suggestions}

        except Exception as e:
            logger.warning("[IDLE] Topic gap analysis failed: %s", e)
            return {"error": str(e)}

    # Guardrails for auto-tuning
    THRESHOLD_MIN = 50
    THRESHOLD_MAX = 90
    THRESHOLD_STEP = 3  # max adjustment per cycle
    THRESHOLD_MIN_SAMPLES = 10  # need this many tasks before tuning

    async def _tune_thresholds(self) -> dict:
        """Auto-adjust publish threshold based on 7-day pass/fail rates (50-90 range, max +/-3/cycle)."""
        try:
            stats = await self.pool.fetchrow("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) as published,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                    AVG(quality_score) as avg_score,
                    STDDEV(quality_score) as stddev_score
                FROM content_tasks
                WHERE created_at > NOW() - INTERVAL '7 days'
                AND quality_score IS NOT NULL
            """)

            if not stats or stats["total"] < self.THRESHOLD_MIN_SAMPLES:
                return {"note": f"insufficient data ({stats['total'] if stats else 0} tasks, need {self.THRESHOLD_MIN_SAMPLES})"}

            total = stats["total"]
            pass_rate = (stats["published"] / total * 100) if total else 0
            fail_rate = (stats["failed"] / total * 100) if total else 0
            avg_score = float(stats["avg_score"] or 0)

            # Read current threshold
            current = await self.pool.fetchval(
                "SELECT value FROM app_settings WHERE key = 'auto_publish_threshold'"
            )
            current_threshold = int(current) if current else 75

            # Calculate adjustment
            adjustment = 0
            reason = "no change needed"

            if fail_rate > 50:
                # Too many failures — lower threshold to let more through
                adjustment = -self.THRESHOLD_STEP
                reason = f"high failure rate ({fail_rate:.0f}%) — lowering threshold"
            elif pass_rate > 90 and avg_score < current_threshold - 5:
                # Passing too much low-quality content — raise threshold
                adjustment = self.THRESHOLD_STEP
                reason = f"high pass rate ({pass_rate:.0f}%) with low avg score ({avg_score:.0f}) — raising threshold"
            elif fail_rate > 30:
                # Moderate failures — small decrease
                adjustment = -1
                reason = f"moderate failure rate ({fail_rate:.0f}%) — small decrease"
            elif pass_rate > 95 and avg_score > 85:
                # Everything passing with high scores — can afford to raise slightly
                adjustment = 1
                reason = f"excellent quality (avg {avg_score:.0f}) — raising slightly"

            if adjustment != 0:
                new_threshold = max(self.THRESHOLD_MIN, min(self.THRESHOLD_MAX, current_threshold + adjustment))
                if new_threshold != current_threshold:
                    await self.pool.execute(
                        "UPDATE app_settings SET value = $1, updated_at = NOW() WHERE key = 'auto_publish_threshold'",
                        str(new_threshold),
                    )
                    # Log to audit_log for traceability
                    try:
                        import json
                        await self.pool.execute(
                            "INSERT INTO audit_log (event_type, source, details, severity) VALUES ($1, $2, $3, $4)",
                            "threshold_auto_tuned", "idle_worker",
                            json.dumps({
                                "old": current_threshold, "new": new_threshold,
                                "adjustment": adjustment, "reason": reason,
                                "stats": {"total": total, "pass_rate": round(pass_rate, 1),
                                          "fail_rate": round(fail_rate, 1), "avg_score": round(avg_score, 1)},
                            }),
                            "info",
                        )
                    except Exception:
                        pass  # audit log is best-effort
                    logger.info("[IDLE] Threshold auto-tuned: %d → %d (%s)", current_threshold, new_threshold, reason)
                else:
                    adjustment = 0
                    reason = f"at boundary ({new_threshold}), no change"

            logger.info("[IDLE] Threshold analysis: %d tasks, %.0f%% pass, avg %.0f, threshold %d",
                        total, pass_rate, avg_score, current_threshold + adjustment)
            return {
                "total_tasks": total,
                "pass_rate": round(pass_rate, 1),
                "fail_rate": round(fail_rate, 1),
                "avg_score": round(avg_score, 1),
                "current_threshold": current_threshold,
                "new_threshold": current_threshold + adjustment if adjustment else current_threshold,
                "adjustment": adjustment,
                "reason": reason,
            }

        except Exception as e:
            logger.warning("[IDLE] Threshold tuning failed: %s", e)
            return {"error": str(e)}

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

    async def _sync_shared_context(self) -> dict:
        """Run the shared context sync script."""
        try:
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "python", "scripts/sync-shared-context.py",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=site_config.get("repo_root", "/app"),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode().strip()
            logger.info("[IDLE] Shared context synced: %s", output[:80])
            return {"ok": proc.returncode == 0, "output": output[:100]}
        except Exception as e:
            return {"error": str(e)}

    async def _auto_embed_posts(self) -> dict:
        """Embed new/changed posts into pgvector."""
        try:
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "python", "scripts/auto-embed.py", "--phase", "posts",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=site_config.get("repo_root", "/app"),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode().strip()
            logger.info("[IDLE] Auto-embed: %s", output[-80:] if output else "done")
            return {"ok": proc.returncode == 0, "output": output[-100:] if output else "done"}
        except Exception as e:
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
                            negative_prompt=negative, high_quality=False,
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
                        try:
                            os.remove(output_path)
                        except Exception:
                            pass
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
        """Generate podcast episodes for published posts that don't have them yet."""
        try:
            import os

            import asyncpg

            from services.podcast_service import PODCAST_DIR, PodcastService

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
                    if generated >= 2:  # Max 2 per cycle
                        break
                except Exception as e:
                    logger.warning("[IDLE] Podcast backfill failed for %s: %s", post["title"][:30], e)

            await cloud.close()
            if generated == 0:
                self._mark_completed("podcast_backfill")
            return {"generated": generated}
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

    async def _fix_uncategorized_posts(self) -> dict:
        """Find published posts with no category and assign one based on content."""
        try:
            import os

            import asyncpg
            cloud = await asyncpg.connect(os.getenv("DATABASE_URL", ""))
            posts = await cloud.fetch(
                "SELECT id, title FROM posts WHERE status = 'published' AND category_id IS NULL LIMIT 5"
            )
            if not posts:
                await cloud.close()
                return {"fixed": 0, "note": "all posts categorized"}

            # Default to Technology if we can't determine
            default_cat = await cloud.fetchval("SELECT id FROM categories WHERE slug = 'technology'")
            fixed = 0
            for post in posts:
                await cloud.execute("UPDATE posts SET category_id = $1 WHERE id = $2", default_cat, post["id"])
                fixed += 1

            await cloud.close()
            if fixed:
                await self._create_gitea_issue(
                    f"content: assigned category to {fixed} uncategorized posts",
                    "Posts defaulted to Technology category. Review and reassign if needed.",
                )
            return {"fixed": fixed}
        except Exception as e:
            return {"error": str(e)}

    async def _fix_missing_seo(self) -> dict:
        """Find posts missing SEO title/description and flag them."""
        try:
            import os

            import asyncpg
            cloud = await asyncpg.connect(os.getenv("DATABASE_URL", ""))
            missing = await cloud.fetch("""
                SELECT id, title FROM posts
                WHERE status = 'published' AND (seo_title IS NULL OR seo_title = '' OR seo_description IS NULL OR seo_description = '')
                LIMIT 10
            """)
            await cloud.close()

            if not missing:
                return {"missing": 0, "note": "all posts have SEO metadata"}

            titles = [p["title"][:40] for p in missing]
            await self._create_gitea_issue(
                f"seo: {len(missing)} posts missing SEO title or description",
                "## Posts Missing SEO\n\n" + "\n".join(f"- {t}" for t in titles),
            )
            return {"missing": len(missing), "posts": titles}
        except Exception as e:
            return {"error": str(e)}

    async def _fix_broken_internal_links(self) -> dict:
        """Remove internal links that point to unpublished/deleted posts."""
        try:
            import os
            import re

            import asyncpg
            cloud = await asyncpg.connect(os.getenv("DATABASE_URL", ""))

            pub_rows = await cloud.fetch("SELECT slug FROM posts WHERE status = 'published'")
            published = {r["slug"] for r in pub_rows}

            rows = await cloud.fetch("SELECT id, title, content FROM posts WHERE status = 'published' AND content LIKE '%/posts/%'")
            fixed = 0
            for row in rows:
                content = row["content"]
                new_content = content
                linked = re.findall(r"/posts/([a-z0-9-]+)", content)
                for slug in set(linked):
                    if slug not in published:
                        new_content = re.sub(r"\[([^\]]+)\]\(/posts/" + re.escape(slug) + r"\)", r"\1", new_content)
                        new_content = re.sub(r"<a[^>]*href=\"/posts/" + re.escape(slug) + r"\"[^>]*>([^<]*)</a>", r"\1", new_content)
                        new_content = re.sub(r"<li[^>]*>.*?/posts/" + re.escape(slug) + r"[^<]*</a></li>", "", new_content)
                if new_content != content:
                    await cloud.execute("UPDATE posts SET content = $1, updated_at = NOW() WHERE id = $2", new_content, row["id"])
                    fixed += 1

            await cloud.close()
            if fixed:
                await self._create_gitea_issue(
                    f"links: removed broken internal links from {fixed} posts",
                    "Auto-cleaned links to unpublished/deleted posts.",
                )
            return {"fixed": fixed}
        except Exception as e:
            return {"error": str(e)}

    async def _fix_broken_external_links(self) -> dict:
        """Check and remove broken external URLs from published posts (5 posts per cycle)."""
        try:
            import os
            import re

            import asyncpg
            import httpx
            cloud = await asyncpg.connect(os.getenv("DATABASE_URL", ""))

            rows = await cloud.fetch("""
                SELECT id, title, content FROM posts
                WHERE status = 'published' AND content LIKE '%http%'
                ORDER BY RANDOM() LIMIT 5
            """)

            site_domain = site_config.get("site_domain", "localhost")
            broken_total = 0
            posts_fixed = 0

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0),
                follow_redirects=True,
            ) as client:
                for row in rows:
                    md_urls = re.findall(r"\]\((https?://[^\s\)]+)\)", row["content"] or "")
                    html_urls = re.findall(r'href="(https?://[^"]+)"', row["content"] or "")
                    urls = set(u.rstrip(".,;:)") for u in md_urls + html_urls if site_domain not in u and "pexels" not in u and "cloudinary" not in u)

                    broken = set()
                    for url in list(urls)[:10]:
                        try:
                            resp = await client.get(
                                url,
                                headers={"User-Agent": "Mozilla/5.0"},
                                timeout=8,
                            )
                            if resp.status_code == 404:
                                broken.add(url)
                        except Exception:
                            broken.add(url)

                    if broken:
                        content = row["content"]
                        for url in broken:
                            escaped = re.escape(url)
                            content = re.sub(r"\[([^\]]+)\]\(" + escaped + r"\)", r"\1", content)
                            content = re.sub(r'<a[^>]*href="' + escaped + r'"[^>]*>([^<]*)</a>', r"\1", content)
                        await cloud.execute("UPDATE posts SET content = $1, updated_at = NOW() WHERE id = $2", content, row["id"])
                        broken_total += len(broken)
                        posts_fixed += 1

            await cloud.close()
            if posts_fixed:
                await self._create_gitea_issue(
                    f"links: removed {broken_total} broken external URLs from {posts_fixed} posts",
                    "Auto-cleaned 404 external links.",
                )
            return {"checked": len(rows), "posts_fixed": posts_fixed, "links_removed": broken_total}
        except Exception as e:
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
        try:
            global_threshold = int(
                await self._get_setting("memory_stale_threshold_seconds", "21600")
            )
        except (ValueError, TypeError):
            pass

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

    async def _detect_duplicate_posts(self) -> dict:
        """Detect posts with very similar titles and flag for review."""
        try:
            import os

            import asyncpg
            cloud = await asyncpg.connect(os.getenv("DATABASE_URL", ""))
            posts = await cloud.fetch("SELECT id, title FROM posts WHERE status = 'published' ORDER BY title")
            await cloud.close()

            # Simple word-overlap duplicate detection
            duplicates = []
            titles = [(p["id"], p["title"].lower().split()) for p in posts]
            for i, (id1, words1) in enumerate(titles):
                for id2, words2 in titles[i+1:]:
                    if len(words1) < 4 or len(words2) < 4:
                        continue
                    overlap = len(set(words1) & set(words2)) / max(len(set(words1)), len(set(words2)))
                    if overlap > 0.7:
                        duplicates.append((posts[i]["title"][:50], [p for p in posts if p["id"] == id2][0]["title"][:50]))

            if duplicates:
                body = "## Potential Duplicate Posts\n\n" + "\n".join(f"- \"{a}\" vs \"{b}\"" for a, b in duplicates[:10])
                await self._create_gitea_issue(f"content: {len(duplicates)} potential duplicate post pairs", body)

            return {"duplicates": len(duplicates), "pairs": duplicates[:5]}
        except Exception as e:
            return {"error": str(e)}

    async def _detect_anomalies(self) -> dict:
        """Z-score anomaly detection across system metrics (>2 stddev from 30-day mean)."""
        try:
            import json
            import math

            anomalies = []

            # Define metrics to monitor: (name, query for recent value, query for historical stats)
            metrics = [
                (
                    "task_failure_rate",
                    """SELECT CASE WHEN COUNT(*) = 0 THEN 0
                        ELSE SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::float / COUNT(*)
                        END as val
                    FROM content_tasks WHERE created_at > NOW() - INTERVAL '24 hours'""",
                    """SELECT AVG(daily_rate) as mean, STDDEV(daily_rate) as stddev FROM (
                        SELECT date_trunc('day', created_at) as day,
                            CASE WHEN COUNT(*) = 0 THEN 0
                            ELSE SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::float / COUNT(*)
                            END as daily_rate
                        FROM content_tasks
                        WHERE created_at > NOW() - INTERVAL '30 days'
                        GROUP BY day
                    ) t""",
                ),
                (
                    "avg_quality_score",
                    "SELECT AVG(quality_score) as val FROM content_tasks WHERE created_at > NOW() - INTERVAL '24 hours' AND quality_score IS NOT NULL",
                    """SELECT AVG(daily_avg) as mean, STDDEV(daily_avg) as stddev FROM (
                        SELECT date_trunc('day', created_at) as day, AVG(quality_score) as daily_avg
                        FROM content_tasks
                        WHERE created_at > NOW() - INTERVAL '30 days' AND quality_score IS NOT NULL
                        GROUP BY day
                    ) t""",
                ),
                (
                    "cost_per_day",
                    "SELECT COALESCE(SUM(cost_usd), 0) as val FROM cost_logs WHERE created_at > NOW() - INTERVAL '24 hours'",
                    """SELECT AVG(daily_cost) as mean, STDDEV(daily_cost) as stddev FROM (
                        SELECT date_trunc('day', created_at) as day, COALESCE(SUM(cost_usd), 0) as daily_cost
                        FROM cost_logs
                        WHERE created_at > NOW() - INTERVAL '30 days'
                        GROUP BY day
                    ) t""",
                ),
                (
                    "error_log_rate",
                    "SELECT COUNT(*) as val FROM audit_log WHERE severity = 'error' AND timestamp > NOW() - INTERVAL '24 hours'",
                    """SELECT AVG(daily_errors) as mean, STDDEV(daily_errors) as stddev FROM (
                        SELECT date_trunc('day', timestamp) as day, COUNT(*) as daily_errors
                        FROM audit_log WHERE severity = 'error'
                        AND timestamp > NOW() - INTERVAL '30 days'
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

    async def _sync_page_views(self) -> dict:
        """Pull new page_views from cloud DB into local brain DB for Grafana dashboards."""
        try:
            import os

            import asyncpg

            cloud_url = os.getenv("DATABASE_URL", "")
            if not cloud_url:
                return {"note": "no DATABASE_URL — skipping page_views sync"}

            # Ensure local table exists
            await self.pool.execute("""
                CREATE TABLE IF NOT EXISTS page_views (
                    id SERIAL PRIMARY KEY,
                    path TEXT,
                    slug TEXT,
                    referrer TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Watermark: latest created_at in local DB
            row = await self.pool.fetchrow("SELECT MAX(created_at) AS max_ts FROM page_views")
            last_ts = row["max_ts"] if row else None

            cloud = await asyncpg.connect(cloud_url)
            try:
                if last_ts:
                    rows = await cloud.fetch(
                        "SELECT id, path, slug, referrer, user_agent, created_at "
                        "FROM page_views WHERE created_at > $1 ORDER BY created_at LIMIT 5000",
                        last_ts,
                    )
                else:
                    rows = await cloud.fetch(
                        "SELECT id, path, slug, referrer, user_agent, created_at "
                        "FROM page_views ORDER BY created_at LIMIT 5000",
                    )
            finally:
                await cloud.close()

            if not rows:
                logger.debug("[IDLE] page_views sync: 0 new rows")
                return {"rows_synced": 0}

            # Batch insert into local DB
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    for r in rows:
                        await conn.execute(
                            """
                            INSERT INTO page_views (id, path, slug, referrer, user_agent, created_at)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            r["id"], r.get("path"), r.get("slug"),
                            r.get("referrer"), r.get("user_agent"), r["created_at"],
                        )

            logger.info("[IDLE] page_views sync: pulled %d new rows", len(rows))
            return {"rows_synced": len(rows)}

        except Exception as e:
            logger.warning("[IDLE] page_views sync failed: %s", e)
            return {"error": str(e)}

    async def _sync_newsletter_subscribers(self) -> dict:
        """Pull new/updated newsletter_subscribers from cloud DB into local brain DB."""
        try:
            import os

            import asyncpg

            cloud_url = os.getenv("DATABASE_URL", "")
            if not cloud_url:
                return {"note": "no DATABASE_URL — skipping newsletter_subscribers sync"}

            # Ensure local table exists
            await self.pool.execute("""
                CREATE TABLE IF NOT EXISTS newsletter_subscribers (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    first_name VARCHAR(100),
                    last_name VARCHAR(100),
                    company VARCHAR(255),
                    interest_categories JSONB,
                    subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    ip_address VARCHAR(45),
                    user_agent TEXT,
                    verified BOOLEAN DEFAULT FALSE,
                    verification_token VARCHAR(255),
                    verified_at TIMESTAMP WITH TIME ZONE,
                    unsubscribed_at TIMESTAMP WITH TIME ZONE,
                    unsubscribe_reason TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    marketing_consent BOOLEAN DEFAULT FALSE
                )
            """)

            # Watermark: latest updated_at in local DB
            row = await self.pool.fetchrow("SELECT MAX(updated_at) AS max_ts FROM newsletter_subscribers")
            last_ts = row["max_ts"] if row else None

            cloud = await asyncpg.connect(cloud_url)
            try:
                if last_ts:
                    rows = await cloud.fetch(
                        "SELECT * FROM newsletter_subscribers "
                        "WHERE updated_at > $1 ORDER BY updated_at LIMIT 1000",
                        last_ts,
                    )
                else:
                    rows = await cloud.fetch(
                        "SELECT * FROM newsletter_subscribers "
                        "ORDER BY updated_at LIMIT 1000",
                    )
            finally:
                await cloud.close()

            if not rows:
                logger.debug("[IDLE] newsletter_subscribers sync: 0 new rows")
                return {"rows_synced": 0}

            # Batch upsert into local DB
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    for r in rows:
                        await conn.execute(
                            """
                            INSERT INTO newsletter_subscribers (
                                id, email, first_name, last_name, company,
                                interest_categories, subscribed_at, ip_address, user_agent,
                                verified, verification_token, verified_at,
                                unsubscribed_at, unsubscribe_reason,
                                created_at, updated_at, marketing_consent
                            )
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                            ON CONFLICT (id) DO UPDATE SET
                                email              = EXCLUDED.email,
                                first_name         = EXCLUDED.first_name,
                                last_name          = EXCLUDED.last_name,
                                company            = EXCLUDED.company,
                                interest_categories = EXCLUDED.interest_categories,
                                subscribed_at      = EXCLUDED.subscribed_at,
                                verified           = EXCLUDED.verified,
                                verified_at        = EXCLUDED.verified_at,
                                unsubscribed_at    = EXCLUDED.unsubscribed_at,
                                unsubscribe_reason = EXCLUDED.unsubscribe_reason,
                                updated_at         = EXCLUDED.updated_at,
                                marketing_consent  = EXCLUDED.marketing_consent
                            """,
                            r["id"], r["email"], r.get("first_name"), r.get("last_name"),
                            r.get("company"), r.get("interest_categories"),
                            r.get("subscribed_at"), r.get("ip_address"), r.get("user_agent"),
                            r.get("verified", False), r.get("verification_token"),
                            r.get("verified_at"), r.get("unsubscribed_at"),
                            r.get("unsubscribe_reason"),
                            r["created_at"], r["updated_at"],
                            r.get("marketing_consent", False),
                        )

            logger.info("[IDLE] newsletter_subscribers sync: pulled %d rows", len(rows))
            return {"rows_synced": len(rows)}

        except Exception as e:
            logger.warning("[IDLE] newsletter_subscribers sync failed: %s", e)
            return {"error": str(e)}

    # Known GPU TDPs (watts) — add more as needed
    GPU_TDP_MAP = {
        "RTX 5090": 575,
        "RTX 5080": 360,
        "RTX 5070 Ti": 300,
        "RTX 5070": 250,
        "RTX 4090": 450,
        "RTX 4080": 320,
        "RTX 4070 Ti": 285,
        "RTX 4070": 200,
        "RTX 3090": 350,
        "RTX 3080": 320,
    }

    async def _update_utility_rates(self) -> dict:
        """Fetch EIA electricity rate and detect GPU TDP via nvidia-smi. Updates app_settings."""
        import json

        changes = {}

        # --- Part 1: Electricity rate from EIA API ---
        try:
            import httpx

            eia_url = (
                "https://api.eia.gov/v2/electricity/retail-sales/data/"
                "?api_key=DEMO_KEY"
                "&frequency=monthly"
                "&data[0]=price"
                "&facets[sectorid][]=RES"
                "&sort[0][column]=period"
                "&sort[0][direction]=desc"
                "&length=1"
            )

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0)
            ) as client:
                resp = await client.get(eia_url, timeout=15)
                resp.raise_for_status()
                data = resp.json()

            # EIA response: {"response": {"data": [{"price": 16.11, "period": "2025-12", ...}]}}
            records = data.get("response", {}).get("data", [])
            if records:
                cents_kwh = float(records[0]["price"])
                dollars_kwh = round(cents_kwh / 100, 4)
                period = records[0].get("period", "unknown")

                # Compare with current setting
                current_raw = await self.pool.fetchval(
                    "SELECT value FROM app_settings WHERE key = 'electricity_rate_kwh'"
                )
                current_rate = float(current_raw) if current_raw else 0.0

                if current_rate == 0.0 or abs(dollars_kwh - current_rate) / max(current_rate, 0.001) > 0.10:
                    # Upsert the setting
                    await self.pool.execute("""
                        INSERT INTO app_settings (key, value, updated_at)
                        VALUES ('electricity_rate_kwh', $1, NOW())
                        ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()
                    """, str(dollars_kwh))

                    changes["electricity_rate_kwh"] = {
                        "old": current_rate, "new": dollars_kwh, "period": period,
                    }
                    logger.info(
                        "[IDLE] Electricity rate updated: $%.4f/kWh → $%.4f/kWh (EIA %s)",
                        current_rate, dollars_kwh, period,
                    )
                else:
                    logger.info(
                        "[IDLE] Electricity rate unchanged ($%.4f/kWh, EIA %s, <10%% diff)",
                        dollars_kwh, period,
                    )
            else:
                logger.warning("[IDLE] EIA API returned no data records")

        except Exception as e:
            logger.warning("[IDLE] EIA electricity rate fetch failed: %s", e)

        # --- Part 2: GPU model detection via nvidia-smi ---
        try:
            import asyncio

            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            gpu_name = stdout.decode().strip().split("\n")[0].strip()

            if gpu_name:
                # Match against known TDP map
                tdp = None
                for model, watts in self.GPU_TDP_MAP.items():
                    if model in gpu_name:
                        tdp = watts
                        break

                if tdp:
                    current_watts = await self.pool.fetchval(
                        "SELECT value FROM app_settings WHERE key = 'gpu_power_watts'"
                    )
                    current_int = int(current_watts) if current_watts else 0

                    if current_int != tdp:
                        await self.pool.execute("""
                            INSERT INTO app_settings (key, value, updated_at)
                            VALUES ('gpu_power_watts', $1, NOW())
                            ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()
                        """, str(tdp))

                        changes["gpu_power_watts"] = {
                            "old": current_int, "new": tdp, "gpu": gpu_name,
                        }
                        logger.info("[IDLE] GPU TDP updated: %dW → %dW (%s)", current_int, tdp, gpu_name)
                    else:
                        logger.info("[IDLE] GPU TDP unchanged (%dW, %s)", tdp, gpu_name)
                else:
                    logger.info("[IDLE] GPU '%s' not in TDP map — skipping", gpu_name)

        except Exception as e:
            logger.debug("[IDLE] nvidia-smi detection failed (expected on cloud): %s", e)

        # --- Log changes to audit_log ---
        if changes:
            try:
                await self.pool.execute(
                    "INSERT INTO audit_log (event_type, source, details, severity) VALUES ($1, $2, $3, $4)",
                    "utility_rates_updated", "idle_worker", json.dumps(changes), "info",
                )
            except Exception:
                pass  # audit log is best-effort

        return {"changes": changes} if changes else {"note": "all utility rates current"}

    async def _verify_published_posts(self) -> dict:
        """Verify recently published posts return HTTP 200 on the live site."""
        try:
            import json
            import os

            import asyncpg
            import httpx

            cloud_url = os.getenv("DATABASE_URL", "")
            if not cloud_url:
                return {"note": "no DATABASE_URL — skipping publish verification"}

            cloud = await asyncpg.connect(cloud_url)
            try:
                rows = await cloud.fetch("""
                    SELECT id, title, slug FROM posts
                    WHERE status = 'published'
                    AND published_at > NOW() - INTERVAL '24 hours'
                    ORDER BY published_at DESC
                    LIMIT 20
                """)
            finally:
                await cloud.close()

            if not rows:
                return {"checked": 0, "note": "no posts published in last 24h"}

            site_url = site_config.require("site_url")
            verified = 0
            failures = []

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0),
                follow_redirects=True,
            ) as client:
                for row in rows:
                    url = f"{site_url}/posts/{row['slug']}"
                    try:
                        resp = await client.get(url, timeout=10)
                        if resp.status_code == 200:
                            verified += 1
                        else:
                            failures.append({
                                "slug": row["slug"],
                                "title": row["title"][:50],
                                "status": resp.status_code,
                            })
                    except Exception as e:
                        failures.append({
                            "slug": row["slug"],
                            "title": row["title"][:50],
                            "status": f"error: {e}",
                        })

            if failures:
                # Log each failure to audit_log
                for f in failures:
                    try:
                        await self.pool.execute(
                            "INSERT INTO audit_log (event_type, source, details, severity) "
                            "VALUES ($1, $2, $3, $4)",
                            "publish_verify_failed", "idle_worker",
                            json.dumps(f), "warning",
                        )
                    except Exception:
                        pass

                logger.warning(
                    "[IDLE] Publish verification: %d/%d failed — %s",
                    len(failures), len(rows),
                    ", ".join(f["slug"] for f in failures[:5]),
                )
            else:
                logger.info("[IDLE] Publish verification: all %d recent posts accessible", verified)

            return {"checked": len(rows), "verified": verified, "failures": failures[:10]}

        except Exception as e:
            logger.warning("[IDLE] Publish verification failed: %s", e)
            return {"error": str(e)}

    async def _crosspost_to_devto(self) -> dict:
        """Cross-post published posts that haven't been sent to Dev.to yet."""
        try:
            from services.devto_service import DevToCrossPostService

            svc = DevToCrossPostService(self.pool)

            # Check if API key is configured before doing any work
            api_key = await svc._get_api_key()
            if not api_key:
                return {"skipped": True, "reason": "devto_api_key not configured"}

            # Find published posts without a devto_url in metadata (limit 3 per cycle)
            rows = await self.pool.fetch("""
                SELECT id, title, slug
                FROM posts
                WHERE status = 'published'
                  AND (metadata IS NULL
                       OR metadata->>'devto_url' IS NULL
                       OR metadata->>'devto_url' = '')
                ORDER BY published_at DESC
                LIMIT 3
            """)

            if not rows:
                return {"crossposted": 0, "note": "all published posts already on Dev.to"}

            crossposted = 0
            errors = []
            for row in rows:
                post_id = str(row["id"])
                try:
                    devto_url = await svc.cross_post_by_post_id(post_id)
                    if devto_url:
                        crossposted += 1
                        logger.info(
                            "[IDLE] Cross-posted to Dev.to: %s -> %s",
                            row["slug"], devto_url,
                        )
                    else:
                        errors.append(f"{row['slug']}: no URL returned")
                except Exception as e:
                    errors.append(f"{row['slug']}: {e}")

            result = {"crossposted": crossposted, "checked": len(rows)}
            if errors:
                result["errors"] = errors[:5]
            return result

        except Exception as e:
            logger.warning("[IDLE] Dev.to cross-posting failed: %s", e)
            return {"error": str(e)}

    async def _backup_database(self) -> dict:
        """Full database backup: pg_dump + R2 upload + JSON table export.

        Three layers of backup:
        1. pg_dump (custom format, compressed) — full schema + data, restorable
        2. R2 upload — off-site copy of the pg_dump to Cloudflare R2
        3. JSON export — human-readable table dumps for quick inspection

        Local retention: 14 days. R2 retention: 90 days (managed by R2 lifecycle rules).
        """
        import json
        import os
        from datetime import datetime, timezone

        backup_dir = os.path.join(os.path.expanduser("~"), ".poindexter", "backups")
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result: dict = {"timestamp": timestamp}

        # ── Layer 1: pg_dump ──────────────────────────────────────────
        dump_file = os.path.join(backup_dir, f"poindexter-db-{timestamp}.dump")
        try:
            import asyncio
            # Parse DATABASE_URL for pg_dump connection params.
            # The pool connects via Docker networking (postgres-local:5432)
            # and we need the same host, not localhost.
            from urllib.parse import urlparse
            db_url = os.environ.get("DATABASE_URL", "")
            if db_url:
                parsed = urlparse(db_url)
                db_host = parsed.hostname or "postgres-local"
                db_port = str(parsed.port or 5432)
                db_name = (parsed.path or "/poindexter_brain").lstrip("/")
                db_user = parsed.username or "poindexter"
                db_pass = parsed.password or ""
            else:
                db_host = os.environ.get("PGHOST", "postgres-local")
                db_port = os.environ.get("PGPORT", "5432")
                db_name = os.environ.get("PGDATABASE", "poindexter_brain")
                db_user = os.environ.get("PGUSER", "poindexter")
                db_pass = os.environ.get("PGPASSWORD", "")

            env = os.environ.copy()
            env["PGPASSWORD"] = db_pass

            proc = await asyncio.create_subprocess_exec(
                "pg_dump",
                f"--host={db_host}",
                f"--port={db_port}",
                f"--username={db_user}",
                f"--dbname={db_name}",
                "--format=custom",
                "--compress=6",
                "--no-password",
                f"--file={dump_file}",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode == 0 and os.path.isfile(dump_file) and os.path.getsize(dump_file) > 0:
                size_mb = round(os.path.getsize(dump_file) / (1024 * 1024), 2)
                result["pg_dump"] = f"OK ({size_mb} MB)"
                logger.info("[BACKUP] pg_dump OK: %s (%s MB)", dump_file, size_mb)
            else:
                err_msg = stderr.decode(errors="replace")[:200] if stderr else "unknown error"
                result["pg_dump"] = f"FAILED: {err_msg}"
                logger.warning("[BACKUP] pg_dump failed: %s", err_msg)
                if os.path.isfile(dump_file):
                    os.remove(dump_file)
                dump_file = None
        except FileNotFoundError:
            result["pg_dump"] = "SKIPPED: pg_dump not installed"
            logger.info("[BACKUP] pg_dump not available — skipping binary backup")
            dump_file = None
        except Exception as e:
            result["pg_dump"] = f"ERROR: {e}"
            logger.warning("[BACKUP] pg_dump error: %s", e)
            dump_file = None

        # ── Layer 2: R2 upload ────────────────────────────────────────
        if dump_file and os.path.isfile(dump_file):
            try:
                from services.r2_upload_service import upload_to_r2
                r2_key = f"backups/db/poindexter-db-{timestamp}.dump"
                url = await upload_to_r2(dump_file, r2_key, content_type="application/octet-stream")
                if url:
                    result["r2_upload"] = f"OK ({r2_key})"
                    logger.info("[BACKUP] R2 upload OK: %s", r2_key)
                else:
                    result["r2_upload"] = "FAILED: upload_to_r2 returned None (check R2 credentials)"
                    logger.warning("[BACKUP] R2 upload returned None — check cloudflare_r2_access_key in app_settings")
            except Exception as e:
                result["r2_upload"] = f"FAILED: {e}"
                logger.warning("[BACKUP] R2 upload failed: %s", e)
        else:
            result["r2_upload"] = "SKIPPED (no dump file)"

        # ── Layer 3: JSON table export (lightweight, human-readable) ──
        tables = [
            "posts", "app_settings", "brain_knowledge", "brain_decisions",
            "affiliate_links", "content_tasks", "page_views", "cost_logs",
        ]
        backed_up = []
        errors = []

        for table in tables:
            try:
                rows = await self.pool.fetch(f"SELECT * FROM {table}")  # noqa: S608 — trusted table names
                if not rows:
                    continue
                records = [dict(r) for r in rows]
                for rec in records:
                    for k, v in rec.items():
                        if hasattr(v, "isoformat"):
                            rec[k] = v.isoformat()
                        elif isinstance(v, (bytes, memoryview)):
                            rec[k] = None
                        elif not isinstance(v, (str, int, float, bool, list, dict, type(None))):
                            rec[k] = str(v)

                filepath = os.path.join(backup_dir, f"{table}_{timestamp}.json")
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(records, f, indent=2, default=str)
                backed_up.append(f"{table}: {len(records)} rows")
            except Exception as e:
                errors.append(f"{table}: {e}")

        result["json_export"] = backed_up

        # ── Prune old local backups — keep last 14 days ───────────────
        try:
            cutoff = time.time() - (14 * 86400)
            pruned = 0
            for fname in os.listdir(backup_dir):
                fpath = os.path.join(backup_dir, fname)
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    pruned += 1
            if pruned:
                result["pruned"] = pruned
        except Exception as e:
            logger.debug("[BACKUP] Pruning failed: %s", e)

        # ── Alert on failure ──────────────────────────────────────────
        pg_ok = result.get("pg_dump", "").startswith("OK")
        r2_ok = result.get("r2_upload", "").startswith("OK")

        if not pg_ok or not r2_ok:
            try:
                await self._create_gitea_issue(
                    title=f"[AUTO] Database backup issue — {timestamp}",
                    body=(
                        f"Automated backup had issues:\n\n"
                        f"- pg_dump: {result.get('pg_dump', 'N/A')}\n"
                        f"- R2 upload: {result.get('r2_upload', 'N/A')}\n"
                        f"- JSON export: {len(backed_up)} tables\n"
                    ),
                )
            except Exception:
                pass  # Don't fail the backup over a Gitea issue

        logger.info(
            "[BACKUP] Complete — pg_dump=%s, R2=%s, JSON=%d tables, dir=%s",
            result.get("pg_dump", "N/A")[:30],
            result.get("r2_upload", "N/A")[:30],
            len(backed_up),
            backup_dir,
        )
        if errors:
            result["errors"] = errors[:5]
        return result
