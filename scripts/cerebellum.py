"""
Cerebellum — always-on trend analysis worker.

Continuously observes ALL system metrics and writes trend observations
to brain_knowledge. Pure facts, no recommendations — the decision system
reads these to make informed choices.

Tracks: costs, content quality, traffic, infrastructure, revenue.
Runs on: PostgreSQL only. No GPU, no API calls, no cost.

Usage:
    pythonw scripts/cerebellum.py          # Background (windowless)
    python scripts/cerebellum.py           # With console output
    python scripts/cerebellum.py --once    # Run one cycle and exit
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# pythonw.exe compatibility
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

LOG_FILE = os.path.join(os.path.expanduser("~"), ".gladlabs", "cerebellum.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("cerebellum")
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)
if sys.stdout is not None and getattr(sys.stdout, "name", "") != os.devnull:
    logger.addHandler(logging.StreamHandler(sys.stdout))

# Database connection — use public Railway URL for local, internal for Railway
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    # Try reading from OpenClaw env
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("DATABASE_URL="):
                DATABASE_URL = _line.split("=", 1)[1].strip()

CYCLE_INTERVAL = 900  # 15 minutes


async def analyze_cost_trends(pool):
    """Analyze spending trends across time periods."""
    observations = []
    try:
        # Daily spend comparison: today vs 7-day average
        row = await pool.fetchrow("""
            WITH today AS (
                SELECT COALESCE(SUM(cost_usd), 0) AS spend
                FROM cost_logs WHERE created_at >= CURRENT_DATE
            ),
            avg_7d AS (
                SELECT COALESCE(AVG(daily_spend), 0) AS avg_spend FROM (
                    SELECT date_trunc('day', created_at) AS day, SUM(cost_usd) AS daily_spend
                    FROM cost_logs
                    WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < CURRENT_DATE
                    GROUP BY 1
                ) sub
            )
            SELECT today.spend, avg_7d.avg_spend FROM today, avg_7d
        """)
        if row:
            today_spend = float(row["spend"])
            avg_spend = float(row["avg_spend"])
            if avg_spend > 0:
                pct_change = ((today_spend - avg_spend) / avg_spend) * 100
                direction = "up" if pct_change > 10 else "down" if pct_change < -10 else "flat"
                observations.append({
                    "entity": "trend.costs.daily_spend",
                    "attribute": "vs_7d_avg",
                    "value": direction,
                    "confidence": min(0.9, 0.5 + abs(pct_change) / 100),
                    "source": "cerebellum",
                    "metadata": json.dumps({
                        "today": round(today_spend, 4), "avg_7d": round(avg_spend, 4),
                        "pct_change": round(pct_change, 1),
                    }),
                })

        # Spend by provider
        rows = await pool.fetch("""
            SELECT provider, SUM(cost_usd) AS total, COUNT(*) AS calls
            FROM cost_logs WHERE created_at >= CURRENT_DATE
            GROUP BY provider ORDER BY total DESC
        """)
        for r in rows:
            observations.append({
                "entity": f"trend.costs.provider.{r['provider']}",
                "attribute": "daily_spend",
                "value": str(round(float(r["total"]), 4)),
                "confidence": 0.95,
                "source": "cerebellum",
                "metadata": json.dumps({"calls": r["calls"]}),
            })
    except Exception as e:
        logger.debug("Cost trend analysis failed: %s", e)

    return observations


async def analyze_content_trends(pool):
    """Analyze content quality and production trends."""
    observations = []
    try:
        # Quality score trend: last 7 days vs previous 7 days
        row = await pool.fetchrow("""
            WITH recent AS (
                SELECT AVG(quality_score) AS avg_score, COUNT(*) AS count
                FROM content_tasks
                WHERE quality_score IS NOT NULL
                AND created_at >= NOW() - INTERVAL '7 days'
            ),
            previous AS (
                SELECT AVG(quality_score) AS avg_score, COUNT(*) AS count
                FROM content_tasks
                WHERE quality_score IS NOT NULL
                AND created_at >= NOW() - INTERVAL '14 days'
                AND created_at < NOW() - INTERVAL '7 days'
            )
            SELECT recent.avg_score AS recent_avg, recent.count AS recent_count,
                   previous.avg_score AS prev_avg, previous.count AS prev_count
            FROM recent, previous
        """)
        if row and row["recent_avg"] and row["prev_avg"]:
            diff = float(row["recent_avg"]) - float(row["prev_avg"])
            direction = "up" if diff > 2 else "down" if diff < -2 else "flat"
            observations.append({
                "entity": "trend.content.quality_score",
                "attribute": "7d_vs_prev_7d",
                "value": direction,
                "confidence": 0.8,
                "source": "cerebellum",
                "metadata": json.dumps({
                    "recent_avg": round(float(row["recent_avg"]), 1),
                    "prev_avg": round(float(row["prev_avg"]), 1),
                    "diff": round(diff, 1),
                    "recent_count": row["recent_count"],
                }),
            })

        # Task status distribution
        rows = await pool.fetch("""
            SELECT status, COUNT(*) AS count
            FROM content_tasks
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY status
        """)
        status_counts = {r["status"]: r["count"] for r in rows}
        total = sum(status_counts.values())
        if total > 0:
            reject_rate = status_counts.get("rejected", 0) / total * 100
            observations.append({
                "entity": "trend.content.rejection_rate",
                "attribute": "last_24h",
                "value": str(round(reject_rate, 1)),
                "confidence": 0.9,
                "source": "cerebellum",
                "metadata": json.dumps(status_counts),
            })

        # Top scoring model
        row = await pool.fetchrow("""
            SELECT model_used, AVG(quality_score) AS avg_score, COUNT(*) AS count
            FROM content_tasks
            WHERE quality_score IS NOT NULL AND model_used IS NOT NULL
            AND created_at >= NOW() - INTERVAL '7 days'
            GROUP BY model_used
            ORDER BY avg_score DESC
            LIMIT 1
        """)
        if row:
            observations.append({
                "entity": "trend.content.best_model",
                "attribute": "by_quality_7d",
                "value": row["model_used"],
                "confidence": 0.7 if row["count"] >= 5 else 0.4,
                "source": "cerebellum",
                "metadata": json.dumps({
                    "avg_score": round(float(row["avg_score"]), 1),
                    "sample_size": row["count"],
                }),
            })
    except Exception as e:
        logger.debug("Content trend analysis failed: %s", e)

    return observations


async def analyze_traffic_trends(pool):
    """Analyze page view and traffic trends."""
    observations = []
    try:
        # Daily views: today vs 7-day average
        row = await pool.fetchrow("""
            WITH today AS (
                SELECT COUNT(*) AS views FROM page_views WHERE created_at >= CURRENT_DATE
            ),
            avg_7d AS (
                SELECT COALESCE(AVG(daily_views), 0) AS avg_views FROM (
                    SELECT date_trunc('day', created_at) AS day, COUNT(*) AS daily_views
                    FROM page_views
                    WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < CURRENT_DATE
                    GROUP BY 1
                ) sub
            )
            SELECT today.views, avg_7d.avg_views FROM today, avg_7d
        """)
        if row:
            today_views = int(row["views"])
            avg_views = float(row["avg_views"])
            if avg_views > 0:
                pct_change = ((today_views - avg_views) / avg_views) * 100
                direction = "up" if pct_change > 15 else "down" if pct_change < -15 else "flat"
                observations.append({
                    "entity": "trend.traffic.daily_views",
                    "attribute": "vs_7d_avg",
                    "value": direction,
                    "confidence": 0.8,
                    "source": "cerebellum",
                    "metadata": json.dumps({
                        "today": today_views, "avg_7d": round(avg_views, 1),
                        "pct_change": round(pct_change, 1),
                    }),
                })

        # Top referrer today
        row = await pool.fetchrow("""
            SELECT COALESCE(NULLIF(referrer, ''), '(direct)') AS referrer, COUNT(*) AS views
            FROM page_views WHERE created_at >= CURRENT_DATE
            GROUP BY 1 ORDER BY 2 DESC LIMIT 1
        """)
        if row and row["views"] > 0:
            observations.append({
                "entity": "trend.traffic.top_referrer",
                "attribute": "today",
                "value": row["referrer"][:200],
                "confidence": 0.9,
                "source": "cerebellum",
                "metadata": json.dumps({"views": row["views"]}),
            })

        # Total published posts
        row = await pool.fetchrow("SELECT COUNT(*) AS total FROM posts WHERE status = 'published'")
        if row:
            observations.append({
                "entity": "trend.content.published_total",
                "attribute": "count",
                "value": str(row["total"]),
                "confidence": 1.0,
                "source": "cerebellum",
            })
    except Exception as e:
        logger.debug("Traffic trend analysis failed: %s", e)

    return observations


async def analyze_infrastructure_trends(pool):
    """Analyze infrastructure health patterns."""
    observations = []
    try:
        # Task queue depth
        row = await pool.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
                COUNT(*) FILTER (WHERE status = 'awaiting_approval') AS awaiting,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM content_tasks
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        if row:
            observations.append({
                "entity": "trend.infra.task_queue",
                "attribute": "last_24h",
                "value": "healthy" if row["failed"] == 0 else "degraded",
                "confidence": 0.9,
                "source": "cerebellum",
                "metadata": json.dumps({
                    "pending": row["pending"], "in_progress": row["in_progress"],
                    "awaiting": row["awaiting"], "failed": row["failed"],
                }),
            })

        # Average task processing time
        row = await pool.fetchrow("""
            SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) AS avg_seconds
            FROM content_tasks
            WHERE status IN ('awaiting_approval', 'published')
            AND created_at >= NOW() - INTERVAL '24 hours'
            AND updated_at IS NOT NULL
        """)
        if row and row["avg_seconds"]:
            avg_min = float(row["avg_seconds"]) / 60
            observations.append({
                "entity": "trend.infra.avg_task_time",
                "attribute": "last_24h",
                "value": str(round(avg_min, 1)),
                "confidence": 0.85,
                "source": "cerebellum",
                "metadata": json.dumps({"avg_minutes": round(avg_min, 1)}),
            })
    except Exception as e:
        logger.debug("Infrastructure trend analysis failed: %s", e)

    return observations


async def write_observations(pool, observations):
    """Write trend observations to brain_knowledge table.

    Schema: id, entity, attribute, value (text), confidence, source,
    source_session, tags, created_at, updated_at, expires_at.
    Unique on (entity, attribute).
    Metadata is embedded in the value field as JSON string.
    """
    if not observations:
        return 0

    written = 0
    for obs in observations:
        try:
            # Pack metadata into value if present
            metadata = obs.get("metadata")
            value = str(obs["value"])
            if metadata:
                value = json.dumps({"value": obs["value"], "details": json.loads(metadata) if isinstance(metadata, str) else metadata})

            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (entity, attribute)
                DO UPDATE SET value = $3, confidence = $4, source = $5, updated_at = NOW()
            """,
                obs["entity"], obs["attribute"], value,
                obs.get("confidence", 0.5), obs.get("source", "cerebellum"),
            )
            written += 1
        except Exception as e:
            logger.debug("Failed to write observation %s: %s", obs.get("entity"), e)

    return written


async def run_cycle(pool):
    """Run one analysis cycle across all systems."""
    all_observations = []

    all_observations.extend(await analyze_cost_trends(pool))
    all_observations.extend(await analyze_content_trends(pool))
    all_observations.extend(await analyze_traffic_trends(pool))
    all_observations.extend(await analyze_infrastructure_trends(pool))

    written = await write_observations(pool, all_observations)
    logger.info("Cerebellum cycle: %d observations, %d written to brain_knowledge",
                len(all_observations), written)
    return len(all_observations)


async def main_async():
    """Main async entry point."""
    import asyncpg

    if not DATABASE_URL:
        logger.error("No DATABASE_URL configured")
        return

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    logger.info("Cerebellum connected to database")

    one_shot = "--once" in sys.argv

    while True:
        try:
            await run_cycle(pool)
        except Exception as e:
            logger.error("Cerebellum cycle error: %s", e)

        for h in logger.handlers:
            h.flush()

        if one_shot:
            break

        await _async_sleep(CYCLE_INTERVAL)

    await pool.close()


async def _async_sleep(seconds):
    """Async sleep."""
    import asyncio
    await asyncio.sleep(seconds)


if __name__ == "__main__":
    import asyncio
    logger.info("Cerebellum starting (once=%s)", "--once" in sys.argv)
    asyncio.run(main_async())
