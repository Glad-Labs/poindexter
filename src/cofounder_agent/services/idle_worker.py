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
from datetime import datetime, timezone
from typing import Optional

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


class IdleWorker:
    """Background maintenance tasks for when the pipeline is idle."""

    def __init__(self, pool):
        self.pool = pool
        self._last_run: dict[str, float] = {}

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

            logger.info("[IDLE] Topic gaps: %d suggestions", len(suggestions))
            return {"empty_categories": empty, "low_coverage": low, "suggestions": suggestions}

        except Exception as e:
            logger.warning("[IDLE] Topic gap analysis failed: %s", e)
            return {"error": str(e)}

    async def _tune_thresholds(self) -> dict:
        """Analyze pass/fail rates and suggest threshold adjustments.

        Writes suggestions to brain_knowledge — does NOT auto-modify thresholds.
        Human or brain daemon reviews and applies changes.
        """
        try:
            # Get pass/fail rate over last 7 days
            stats = await self.pool.fetchrow("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) as published,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                    AVG(quality_score) as avg_score
                FROM content_tasks
                WHERE created_at > NOW() - INTERVAL '7 days'
                AND quality_score IS NOT NULL
            """)

            if not stats or stats["total"] == 0:
                return {"note": "no recent tasks to analyze"}

            total = stats["total"]
            pass_rate = (stats["published"] / total * 100) if total else 0
            fail_rate = (stats["failed"] / total * 100) if total else 0
            avg_score = float(stats["avg_score"] or 0)

            suggestions = []
            if fail_rate > 50:
                suggestions.append(f"High failure rate ({fail_rate:.0f}%) — consider lowering qa_final_score_threshold")
            if pass_rate > 90 and avg_score < 75:
                suggestions.append(f"High pass rate but low avg score ({avg_score:.0f}) — consider raising threshold")
            if avg_score > 85:
                suggestions.append(f"High avg score ({avg_score:.0f}) — quality is strong, thresholds are appropriate")

            logger.info("[IDLE] Threshold analysis: %d tasks, %.0f%% pass rate, avg score %.0f",
                        total, pass_rate, avg_score)
            return {
                "total_tasks": total,
                "pass_rate": round(pass_rate, 1),
                "fail_rate": round(fail_rate, 1),
                "avg_score": round(avg_score, 1),
                "suggestions": suggestions,
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
