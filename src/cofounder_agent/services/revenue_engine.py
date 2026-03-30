"""
Revenue Engine — closed-loop content optimization based on performance data.

Tracks which content generates revenue (views, clicks, engagement) and
feeds that data back into the content generator to produce more of what works.

The loop:
  Content → Traffic → Revenue signals → Analysis → Better content → More traffic

Revenue signals:
  - Page views (from web_analytics or Google Analytics)
  - Affiliate link clicks (from affiliate_links.clicks)
  - Newsletter signups driven by specific posts
  - Time on page / scroll depth (future)
  - Social shares (future)

Usage:
    from services.revenue_engine import RevenueEngine
    engine = RevenueEngine(pool)
    insights = await engine.analyze_content_performance()
    topics = await engine.suggest_topics_from_performance()
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


class RevenueEngine:
    """Analyzes content performance and suggests revenue-optimizing actions."""

    def __init__(self, pool=None, settings_service=None):
        self.pool = pool
        self.settings = settings_service

    async def get_top_performing_posts(self, limit: int = 10) -> List[dict]:
        """Get posts ranked by performance signals (views, affiliate clicks)."""
        if not self.pool:
            return []
        try:
            rows = await self.pool.fetch("""
                SELECT
                    p.title,
                    p.slug,
                    p.view_count,
                    p.category_id,
                    p.seo_keywords,
                    p.published_at,
                    COALESCE(p.view_count, 0) as views,
                    LENGTH(p.content) as content_length
                FROM posts p
                WHERE p.status = 'published'
                ORDER BY COALESCE(p.view_count, 0) DESC, p.published_at DESC
                LIMIT $1
            """, limit)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("[REVENUE] Failed to get top posts: %s", e)
            return []

    async def get_category_performance(self) -> List[dict]:
        """Get aggregate performance by category."""
        if not self.pool:
            return []
        try:
            rows = await self.pool.fetch("""
                SELECT
                    c.name as category,
                    COUNT(p.id) as post_count,
                    COALESCE(SUM(p.view_count), 0) as total_views,
                    COALESCE(AVG(p.view_count), 0) as avg_views
                FROM posts p
                LEFT JOIN categories c ON p.category_id = c.id
                WHERE p.status = 'published'
                GROUP BY c.name
                ORDER BY total_views DESC
            """)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("[REVENUE] Failed to get category performance: %s", e)
            return []

    async def get_keyword_performance(self) -> List[dict]:
        """Analyze which keywords correlate with higher views."""
        if not self.pool:
            return []
        try:
            rows = await self.pool.fetch("""
                SELECT
                    seo_keywords,
                    view_count,
                    title
                FROM posts
                WHERE status = 'published' AND seo_keywords IS NOT NULL
                ORDER BY COALESCE(view_count, 0) DESC
                LIMIT 30
            """)

            # Extract keyword frequency from top performers
            keyword_scores: dict = {}
            for row in rows:
                keywords = (row["seo_keywords"] or "").split(",")
                views = row["view_count"] or 0
                for kw in keywords:
                    kw = kw.strip().lower()
                    if kw and len(kw) > 2:
                        if kw not in keyword_scores:
                            keyword_scores[kw] = {"keyword": kw, "total_views": 0, "post_count": 0}
                        keyword_scores[kw]["total_views"] += views
                        keyword_scores[kw]["post_count"] += 1

            # Sort by total views
            return sorted(keyword_scores.values(), key=lambda x: x["total_views"], reverse=True)[:20]
        except Exception as e:
            logger.error("[REVENUE] Failed to get keyword performance: %s", e)
            return []

    async def suggest_topics_from_performance(self, count: int = 5) -> List[str]:
        """Suggest new content topics based on what performs well.

        Analyzes top-performing posts and keywords to generate topics
        that are likely to perform well.
        """
        top_posts = await self.get_top_performing_posts(20)
        top_keywords = await self.get_keyword_performance()

        if not top_posts:
            return ["AI development trends in 2026",
                    "Building production systems with Python",
                    "The solo developer tech stack"]

        # Extract themes from top performers
        themes = set()
        for post in top_posts[:10]:
            keywords = (post.get("seo_keywords") or "").split(",")
            for kw in keywords:
                kw = kw.strip().lower()
                if kw and len(kw) > 3:
                    themes.add(kw)

        # Generate topic suggestions from themes
        suggestions = []
        theme_list = list(themes)[:10]
        templates = [
            "The Complete Guide to {theme}",
            "Why {theme} Matters More Than Ever in 2026",
            "{theme}: What Most Developers Get Wrong",
            "Advanced {theme} Patterns for Production Systems",
            "How to Optimize {theme} for Maximum Impact",
        ]

        import random
        for _ in range(count):
            if theme_list:
                theme = random.choice(theme_list).title()
                template = random.choice(templates)
                suggestions.append(template.format(theme=theme))

        return suggestions

    async def analyze_content_performance(self) -> dict:
        """Generate a comprehensive content performance analysis."""
        top_posts = await self.get_top_performing_posts(10)
        categories = await self.get_category_performance()
        keywords = await self.get_keyword_performance()
        suggestions = await self.suggest_topics_from_performance()

        total_views = sum(p.get("views", 0) for p in top_posts)
        avg_content_length = (
            sum(p.get("content_length", 0) for p in top_posts) / len(top_posts)
            if top_posts else 0
        )

        return {
            "summary": {
                "total_views": total_views,
                "avg_content_length": int(avg_content_length),
                "top_category": categories[0]["category"] if categories else "none",
                "top_keyword": keywords[0]["keyword"] if keywords else "none",
            },
            "top_posts": [
                {"title": p["title"][:60], "views": p.get("views", 0), "slug": p["slug"]}
                for p in top_posts[:5]
            ],
            "category_breakdown": categories,
            "top_keywords": keywords[:10],
            "suggested_topics": suggestions,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def generate_performance_report(self) -> str:
        """Generate a human-readable performance report."""
        analysis = await self.analyze_content_performance()
        s = analysis["summary"]

        lines = [
            "=== CONTENT PERFORMANCE REPORT ===",
            f"Total views: {s['total_views']}",
            f"Avg content length: {s['avg_content_length']} chars",
            f"Top category: {s['top_category']}",
            f"Top keyword: {s['top_keyword']}",
            "",
            "Top posts:",
        ]
        for p in analysis["top_posts"]:
            lines.append(f"  {p['views']} views — {p['title']}")

        lines.append("")
        lines.append("Suggested next topics:")
        for t in analysis["suggested_topics"]:
            lines.append(f"  - {t}")

        return "\n".join(lines)
