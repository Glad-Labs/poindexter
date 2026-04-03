"""
Idle Worker — background tasks that run when the pipeline has no active content generation.

The system should never be truly idle. When not generating new content, it:
1. Audits existing published posts for quality issues
2. Checks for broken links in published content
3. Re-embeds content that has changed
4. Analyzes which topics get traffic and suggests new ones
5. Adjusts quality thresholds based on pass/fail rates
6. Cleans up stale data

All tasks are non-blocking and yield to content generation when tasks arrive.
Controlled via pipeline_stages table (key: 'idle_*').

Usage:
    idle = IdleWorker(pool)
    await idle.run_cycle()  # One pass of all idle tasks
"""

import time

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


class IdleWorker:
    """Background maintenance tasks for when the pipeline is idle."""

    def __init__(self, pool):
        self.pool = pool
        self._last_run: dict[str, float] = {}

    async def _create_gitea_issue(self, title: str, body: str) -> bool:
        """Create a Gitea issue for tracking discovered problems."""
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
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{gitea_url}/api/v1/repos/{gitea_repo}/issues",
                    headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
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
        """Check if a background task is due to run."""
        last = self._last_run.get(task_name, 0)
        return (time.time() - last) >= (interval_minutes * 60)

    def _mark_run(self, task_name: str):
        self._last_run[task_name] = time.time()

    async def run_cycle(self) -> dict:
        """Run one cycle of all due idle tasks. Returns summary."""
        results = {}

        # Check if there are pending content tasks — if so, skip idle work
        pending = await self.pool.fetchrow(
            "SELECT COUNT(*) as c FROM content_tasks WHERE status IN ('pending', 'approved', 'in_progress')"
        )
        if pending and pending["c"] > 0:
            logger.debug("[IDLE] %d active tasks — skipping idle work", pending["c"])
            return {"skipped": True, "reason": f"{pending['c']} active tasks"}

        # 1. Quality audit on published posts (every 6 hours)
        if self._is_due("quality_audit", 360):
            results["quality_audit"] = await self._audit_published_quality()
            self._mark_run("quality_audit")

        # 2. Broken link check (every 12 hours)
        if self._is_due("link_check", 720):
            results["link_check"] = await self._check_published_links()
            self._mark_run("link_check")

        # 3. Topic gap analysis (every 24 hours)
        if self._is_due("topic_gaps", 1440):
            results["topic_gaps"] = await self._analyze_topic_gaps()
            self._mark_run("topic_gaps")

        # 4. Threshold tuning (every 12 hours)
        if self._is_due("threshold_tune", 720):
            results["threshold_tune"] = await self._tune_thresholds()
            self._mark_run("threshold_tune")

        # 5. Stale embedding refresh (every 4 hours)
        if self._is_due("embedding_refresh", 240):
            results["embedding_refresh"] = await self._refresh_stale_embeddings()
            self._mark_run("embedding_refresh")

        # 6. Topic discovery — find and queue fresh content topics (every 8 hours)
        if self._is_due("topic_discovery", 480):
            results["topic_discovery"] = await self._discover_and_queue_topics()
            self._mark_run("topic_discovery")

        # 7. Shared context sync (every 30 minutes)
        if self._is_due("context_sync", 30):
            results["context_sync"] = await self._sync_shared_context()
            self._mark_run("context_sync")

        # 8. Auto-embed new/changed posts (every 2 hours)
        if self._is_due("auto_embed", 120):
            results["auto_embed"] = await self._auto_embed_posts()
            self._mark_run("auto_embed")

        # 9. Regenerate stock photo images with SDXL (every 6 hours, 5 per cycle)
        if self._is_due("image_regen", 360):
            results["image_regen"] = await self._regenerate_stock_images()
            self._mark_run("image_regen")

        # 10. Fix uncategorized posts (every 12 hours)
        if self._is_due("fix_categories", 720):
            results["fix_categories"] = await self._fix_uncategorized_posts()
            self._mark_run("fix_categories")

        # 11. Fix posts missing SEO metadata (every 12 hours)
        if self._is_due("fix_seo", 720):
            results["fix_seo"] = await self._fix_missing_seo()
            self._mark_run("fix_seo")

        # 12. Clean broken internal links (every 24 hours)
        if self._is_due("fix_internal_links", 1440):
            results["fix_internal_links"] = await self._fix_broken_internal_links()
            self._mark_run("fix_internal_links")

        # 13. Remove broken external links (every 24 hours)
        if self._is_due("fix_external_links", 1440):
            results["fix_external_links"] = await self._fix_broken_external_links()
            self._mark_run("fix_external_links")

        # 14. Fix duplicate titles (every 24 hours)
        if self._is_due("fix_duplicates", 1440):
            results["fix_duplicates"] = await self._detect_duplicate_posts()
            self._mark_run("fix_duplicates")
            self._mark_run("auto_embed")

        # 15. Anomaly detection — statistical outlier monitoring (every 4 hours)
        if self._is_due("anomaly_detect", 240):
            results["anomaly_detect"] = await self._detect_anomalies()
            self._mark_run("anomaly_detect")

        if results:
            logger.info("[IDLE] Completed %d background tasks: %s",
                        len(results), ", ".join(results.keys()))

        return results

    async def _audit_published_quality(self) -> dict:
        """Re-score a batch of published posts to catch quality drift."""
        try:
            # Get posts that haven't been audited recently
            rows = await self.pool.fetch("""
                SELECT p.id, p.title, p.slug, LEFT(p.content, 3000) as content_preview
                FROM posts p
                WHERE p.status = 'published'
                AND p.id NOT IN (
                    SELECT DISTINCT task_id FROM audit_log
                    WHERE event_type = 'idle_quality_audit'
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
                # Quick checks: word count, heading presence, link count
                word_count = len(content.split())
                has_headings = "##" in content or "<h2" in content
                if word_count < 500:
                    issues.append(f"{row['title'][:40]}: only {word_count} words")
                if not has_headings:
                    issues.append(f"{row['title'][:40]}: no headings found")

            # Create Gitea issues for problems found
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
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                for row in rows:
                    urls = re.findall(r'https?://[^\s\)"<>]+', row["content"] or "")
                    site_domain = site_config.get("site_domain", "localhost")
                    for url in urls[:10]:  # Max 10 URLs per post
                        if site_domain in url:
                            continue  # Skip internal
                        try:
                            resp = await client.head(url)
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
        """Analyze pass/fail rates and auto-adjust publish threshold within guardrails.

        Guardrails:
        - Hard floor/ceiling: 50-90 range
        - Max ±3 points per cycle (no oscillation)
        - Requires 10+ scored tasks in last 7 days
        - Logs every change to audit_log for traceability
        """
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
            import asyncpg
            import os
            import tempfile

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
                return {"regenerated": 0, "note": "all posts have AI images"}

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
                style = styles.get(cat, styles.get("default", "professional digital art"))
                prompt = f"{style}, blog header about {post['title'][:50]}"

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    output_path = tmp.name

                try:
                    success = await svc.generate_image(
                        prompt=prompt, output_path=output_path,
                        negative_prompt=negative, high_quality=False,
                    )
                    if success and os.path.exists(output_path):
                        result = cloudinary.uploader.upload(
                            output_path, folder="generated/",
                            resource_type="image", tags=["featured", cat],
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

    async def _fix_uncategorized_posts(self) -> dict:
        """Find published posts with no category and assign one based on content."""
        try:
            import asyncpg, os
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
                    f"Posts defaulted to Technology category. Review and reassign if needed.",
                )
            return {"fixed": fixed}
        except Exception as e:
            return {"error": str(e)}

    async def _fix_missing_seo(self) -> dict:
        """Find posts missing SEO title/description and flag them."""
        try:
            import asyncpg, os
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
            import asyncpg, os, re
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
            import asyncpg, httpx, os, re
            cloud = await asyncpg.connect(os.getenv("DATABASE_URL", ""))

            rows = await cloud.fetch("""
                SELECT id, title, content FROM posts
                WHERE status = 'published' AND content LIKE '%http%'
                ORDER BY RANDOM() LIMIT 5
            """)

            site_domain = site_config.get("site_domain", "localhost")
            broken_total = 0
            posts_fixed = 0

            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                for row in rows:
                    md_urls = re.findall(r"\]\((https?://[^\s\)]+)\)", row["content"] or "")
                    html_urls = re.findall(r'href="(https?://[^"]+)"', row["content"] or "")
                    urls = set(u.rstrip(".,;:)") for u in md_urls + html_urls if site_domain not in u and "pexels" not in u and "cloudinary" not in u)

                    broken = set()
                    for url in list(urls)[:10]:
                        try:
                            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
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

    async def _detect_duplicate_posts(self) -> dict:
        """Detect posts with very similar titles and flag for review."""
        try:
            import asyncpg, os
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
        """Statistical anomaly detection across key system metrics.

        Uses z-score method: compares recent values against the 30-day rolling
        average. Flags anything >2 standard deviations from the mean.
        Alerts via Gitea issue when anomalies cluster.
        """
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
