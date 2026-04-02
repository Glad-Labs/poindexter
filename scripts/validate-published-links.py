#!/usr/bin/env python3
"""
Validate Published Links — Scan all published posts for broken URLs.

Connects to the Railway cloud DB, fetches every published post,
extracts and validates all external URLs, and writes a health report.

Usage:
    python scripts/validate-published-links.py

Environment:
    DATABASE_URL — Railway PostgreSQL connection string
    (Falls back to ~/.openclaw/workspace/.env if not set)

Output:
    ~/.gladlabs/link-health-report.json
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add backend to sys.path so we can import services
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cofounder_agent"))

import asyncpg

from services.url_validator import URLValidator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("validate-published-links")

# ---------------------------------------------------------------------------
# Database URL resolution
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    _env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            if _line.startswith("DATABASE_URL="):
                DATABASE_URL = _line.split("=", 1)[1].strip()

REPORT_DIR = Path.home() / ".gladlabs"
REPORT_PATH = REPORT_DIR / "link-health-report.json"


async def fetch_published_posts(pool: asyncpg.Pool):
    """Fetch all published posts with their content."""
    rows = await pool.fetch(
        """
        SELECT id, title, slug, content, published_at
        FROM posts
        WHERE status = 'published'
        ORDER BY published_at DESC
        """
    )
    return rows


async def main():
    if not DATABASE_URL:
        logger.error("No DATABASE_URL configured. Set it or add to ~/.openclaw/workspace/.env")
        sys.exit(1)

    logger.info("Connecting to database...")
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)

    try:
        posts = await fetch_published_posts(pool)
        logger.info("Found %d published posts", len(posts))

        if not posts:
            logger.info("No published posts to validate.")
            return

        validator = URLValidator(timeout=5.0)
        start_time = time.time()

        total_urls = 0
        total_valid = 0
        total_invalid = 0
        per_post_results = []

        for post in posts:
            post_id = str(post["id"])
            title = post["title"] or "(untitled)"
            content = post["content"] or ""

            urls = validator.extract_urls(content)
            if not urls:
                per_post_results.append({
                    "post_id": post_id,
                    "title": title,
                    "slug": post["slug"],
                    "url_count": 0,
                    "valid": 0,
                    "invalid": 0,
                    "broken_urls": [],
                })
                continue

            results = await validator.validate_urls(urls)
            broken = [u for u, s in results.items() if s == "invalid"]
            valid_count = len(urls) - len(broken)

            total_urls += len(urls)
            total_valid += valid_count
            total_invalid += len(broken)

            per_post_results.append({
                "post_id": post_id,
                "title": title,
                "slug": post["slug"],
                "url_count": len(urls),
                "valid": valid_count,
                "invalid": len(broken),
                "broken_urls": broken,
            })

            status = "OK" if not broken else f"{len(broken)} BROKEN"
            logger.info(
                "  [%s] %s — %d URLs (%s)",
                post_id[:8], title[:60], len(urls), status,
            )
            if broken:
                for bu in broken:
                    logger.warning("    BROKEN: %s", bu)

        elapsed = time.time() - start_time

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "summary": {
                "total_posts": len(posts),
                "posts_with_links": sum(1 for p in per_post_results if p["url_count"] > 0),
                "total_urls": total_urls,
                "valid": total_valid,
                "invalid": total_invalid,
            },
            "posts": per_post_results,
        }

        # Write report
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

        logger.info("")
        logger.info("=" * 60)
        logger.info("LINK HEALTH REPORT")
        logger.info("=" * 60)
        logger.info("  Posts scanned:   %d", len(posts))
        logger.info("  Total URLs:      %d", total_urls)
        logger.info("  Valid:           %d", total_valid)
        logger.info("  Broken:          %d", total_invalid)
        logger.info("  Scan time:       %.1fs", elapsed)
        logger.info("  Report saved:    %s", REPORT_PATH)
        logger.info("=" * 60)

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
