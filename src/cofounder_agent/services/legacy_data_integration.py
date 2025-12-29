"""
Legacy data integration service for enriching orchestrator with historical context.

Integrates data from:
- Historical tasks (89+ records)
- Published posts and content
- Social media analytics
- Web analytics
- Financial metrics
- Quality evaluations
"""

import asyncpg
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DataSource(str, Enum):
    """Available legacy data sources"""

    TASKS = "tasks"
    POSTS = "posts"
    SOCIAL_ANALYTICS = "social_analytics"
    WEB_ANALYTICS = "web_analytics"
    FINANCIAL_METRICS = "financial_metrics"
    QUALITY_EVALUATIONS = "quality_evaluations"


@dataclass
class HistoricalTask:
    """Historical task from legacy system"""

    task_id: str
    task_name: str
    topic: str
    status: str
    created_at: str
    completed_at: Optional[str]
    category: str
    primary_keyword: Optional[str]
    target_audience: Optional[str]
    quality_score: Optional[float]
    task_metadata: Dict[str, Any]
    result: Optional[Dict[str, Any]]


@dataclass
class PublishedPost:
    """Post published from a task"""

    post_id: str
    task_id: Optional[str]
    title: str
    topic: str
    content: str
    platforms: List[str]
    published_at: str
    featured_image_url: Optional[str]
    quality_score: Optional[float]


@dataclass
class SocialAnalyticsData:
    """Social media performance data"""

    post_id: str
    platform: str
    views: int
    clicks: int
    shares: int
    comments: int
    engagement_rate: float
    engagement_score: float
    published_at: str
    measurement_date: str


@dataclass
class WebAnalyticsData:
    """Web traffic & conversion data"""

    post_id: Optional[str]
    source: str
    traffic: int
    conversions: int
    revenue: float
    bounce_rate: float
    avg_time_on_page: float
    measurement_period: str


@dataclass
class FinancialMetrics:
    """Business financial metrics"""

    measurement_date: str
    revenue_monthly: float
    customers: int
    acquisition_cost: float
    lifetime_value: float
    growth_rate: float
    marketing_spend: float


class LegacyDataIntegrationService:
    """
    Integrates legacy data sources into orchestrator learning system.

    Fetches historical data and enriches real-time executions with context.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self.db_pool = db_pool

    # ========================================================================
    # DATA RETRIEVAL METHODS
    # ========================================================================

    async def get_historical_tasks(
        self,
        limit: int = 50,
        status_filter: Optional[str] = None,
        topic_filter: Optional[str] = None,
    ) -> List[HistoricalTask]:
        """Get historical tasks from database"""
        query = "SELECT * FROM tasks WHERE 1=1"
        params = []
        param_count = 1

        if status_filter:
            query += f" AND status = ${param_count}"
            params.append(status_filter)
            param_count += 1

        if topic_filter:
            query += f" AND topic ILIKE ${param_count}"
            params.append(f"%{topic_filter}%")
            param_count += 1

        query += f" ORDER BY created_at DESC LIMIT ${param_count}"
        params.append(limit)

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            HistoricalTask(
                task_id=str(row["id"]),
                task_name=row.get("task_name", ""),
                topic=row.get("topic", ""),
                status=row.get("status", ""),
                created_at=row["created_at"].isoformat() if row["created_at"] else None,
                completed_at=row["completed_at"].isoformat() if row.get("completed_at") else None,
                category=row.get("category", ""),
                primary_keyword=row.get("primary_keyword"),
                target_audience=row.get("target_audience"),
                quality_score=(
                    float(row.get("quality_score", 0)) if row.get("quality_score") else None
                ),
                task_metadata=row.get("task_metadata") or {},
                result=row.get("result"),
            )
            for row in rows
        ]

    async def get_published_posts(
        self, limit: int = 50, topic_filter: Optional[str] = None
    ) -> List[PublishedPost]:
        """Get published posts from database"""
        query = "SELECT * FROM posts WHERE status = 'published'"
        params = []
        param_count = 1

        if topic_filter:
            query += f" AND topic ILIKE ${param_count}"
            params.append(f"%{topic_filter}%")
            param_count += 1

        query += f" ORDER BY published_at DESC LIMIT ${param_count}"
        params.append(limit)

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            PublishedPost(
                post_id=str(row["id"]),
                task_id=row.get("task_id"),
                title=row.get("title", ""),
                topic=row.get("topic", ""),
                content=row.get("content", ""),
                platforms=row.get("platforms", []),
                published_at=row["published_at"].isoformat() if row.get("published_at") else None,
                featured_image_url=row.get("featured_image_url"),
                quality_score=(
                    float(row.get("quality_score", 0)) if row.get("quality_score") else None
                ),
            )
            for row in rows
        ]

    async def get_social_analytics(self, days: int = 90) -> List[SocialAnalyticsData]:
        """Get social media analytics from last N days"""
        query = """
            SELECT * FROM social_post_analytics
            WHERE measurement_date >= NOW() - INTERVAL '1 day' * $1
            ORDER BY measurement_date DESC
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, days)

        return [
            SocialAnalyticsData(
                post_id=str(row["post_id"]),
                platform=row["platform"],
                views=row.get("views", 0),
                clicks=row.get("clicks", 0),
                shares=row.get("shares", 0),
                comments=row.get("comments", 0),
                engagement_rate=float(row.get("engagement_rate", 0)),
                engagement_score=float(row.get("engagement_score", 0)),
                published_at=(
                    row.get("published_at").isoformat() if row.get("published_at") else None
                ),
                measurement_date=(
                    row["measurement_date"].isoformat() if row["measurement_date"] else None
                ),
            )
            for row in rows
        ]

    async def get_web_analytics(self, days: int = 90) -> List[WebAnalyticsData]:
        """Get web analytics data from last N days"""
        query = """
            SELECT * FROM web_analytics
            WHERE measurement_period >= NOW() - INTERVAL '1 day' * $1
            ORDER BY measurement_period DESC
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, days)

        return [
            WebAnalyticsData(
                post_id=row.get("post_id"),
                source=row.get("source", ""),
                traffic=row.get("traffic", 0),
                conversions=row.get("conversions", 0),
                revenue=float(row.get("revenue", 0)),
                bounce_rate=float(row.get("bounce_rate", 0)),
                avg_time_on_page=float(row.get("avg_time_on_page", 0)),
                measurement_period=(
                    row["measurement_period"].isoformat() if row.get("measurement_period") else None
                ),
            )
            for row in rows
        ]

    async def get_financial_metrics(self, limit: int = 12) -> List[FinancialMetrics]:
        """Get business financial metrics (last N months)"""
        query = """
            SELECT * FROM financial_metrics
            ORDER BY measurement_date DESC
            LIMIT $1
        """

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, limit)

        return [
            FinancialMetrics(
                measurement_date=(
                    row["measurement_date"].isoformat() if row["measurement_date"] else None
                ),
                revenue_monthly=float(row.get("revenue_monthly", 0)),
                customers=row.get("customers", 0),
                acquisition_cost=float(row.get("acquisition_cost", 0)),
                lifetime_value=float(row.get("lifetime_value", 0)),
                growth_rate=float(row.get("growth_rate", 0)),
                marketing_spend=float(row.get("marketing_spend", 0)),
            )
            for row in rows
        ]

    # ========================================================================
    # ENRICHMENT METHODS
    # ========================================================================

    async def find_similar_historical_tasks(
        self, topic: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find similar historical tasks by topic.

        Used to show what worked before for similar requests.
        """
        tasks = await self.get_historical_tasks(limit=limit, topic_filter=topic)

        enriched = []
        for task in tasks:
            # Get analytics for this task's posts if any
            if task.task_id:
                query = """
                    SELECT SUM(views) as total_views, SUM(clicks) as total_clicks,
                           SUM(shares) as total_shares, AVG(engagement_rate) as avg_engagement
                    FROM social_post_analytics
                    WHERE post_id IN (SELECT id FROM posts WHERE task_id = $1)
                """
                async with self.db_pool.acquire() as conn:
                    analytics = await conn.fetchrow(query, task.task_id)

                enriched.append(
                    {
                        "task_id": task.task_id,
                        "topic": task.topic,
                        "created_at": task.created_at,
                        "quality_score": task.quality_score,
                        "analytics": {
                            "total_views": analytics.get("total_views", 0) if analytics else 0,
                            "total_clicks": analytics.get("total_clicks", 0) if analytics else 0,
                            "total_shares": analytics.get("total_shares", 0) if analytics else 0,
                            "avg_engagement_rate": (
                                float(analytics.get("avg_engagement", 0))
                                if analytics and analytics.get("avg_engagement")
                                else 0
                            ),
                        },
                    }
                )

        return enriched

    async def enrich_execution_with_context(
        self, execution_id: str, business_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enrich execution with historical context before running.

        Shows the orchestrator what worked before.
        """
        # Get similar historical tasks
        topic = business_context.get("topic", "general")
        similar_tasks = await self.find_similar_historical_tasks(topic=topic, limit=5)

        # Get current financial metrics
        financial = await self.get_financial_metrics(limit=1)
        current_metrics = financial[0] if financial else None

        # Get platform baseline engagement
        social_data = await self.get_social_analytics(days=30)
        platform_baseline = self._calculate_platform_baseline(social_data)

        # Get topic effectiveness
        topic_effectiveness = await self.get_topic_effectiveness(topic=topic, days=180)

        return {
            "execution_id": execution_id,
            "similar_historical_tasks": similar_tasks,
            "current_business_metrics": {
                "revenue": current_metrics.revenue_monthly if current_metrics else None,
                "customers": current_metrics.customers if current_metrics else None,
                "growth_rate": current_metrics.growth_rate if current_metrics else None,
                "acquisition_cost": current_metrics.acquisition_cost if current_metrics else None,
            },
            "platform_baseline": platform_baseline,
            "topic_effectiveness": topic_effectiveness,
            "enrichment_timestamp": datetime.now().isoformat(),
        }

    async def get_topic_effectiveness(self, topic: str, days: int = 180) -> Dict[str, Any]:
        """
        Calculate effectiveness metrics for a topic.

        Shows performance patterns for similar content.
        """
        posts = await self.get_published_posts(limit=50, topic_filter=topic)

        if not posts:
            return {"topic": topic, "sample_size": 0, "avg_quality": 0, "effectiveness_score": 0}

        # Get analytics for these posts
        query = """
            SELECT 
                AVG(engagement_rate) as avg_engagement,
                AVG(views) as avg_views,
                AVG(clicks) as avg_clicks,
                AVG(shares) as avg_shares
            FROM social_post_analytics
            WHERE post_id = ANY($1::text[])
        """

        post_ids = [p.post_id for p in posts]

        async with self.db_pool.acquire() as conn:
            analytics = await conn.fetchrow(query, post_ids)

        avg_quality = (
            sum(p.quality_score for p in posts if p.quality_score)
            / len([p for p in posts if p.quality_score])
            if posts
            else 0
        )

        effectiveness_score = (
            ((float(analytics["avg_engagement"]) or 0) * 0.4 + (avg_quality * 0.6))
            if analytics
            else 0
        )

        return {
            "topic": topic,
            "sample_size": len(posts),
            "avg_quality": avg_quality,
            "avg_engagement_rate": (
                float(analytics["avg_engagement"])
                if analytics and analytics["avg_engagement"]
                else 0
            ),
            "avg_views": int(analytics["avg_views"]) if analytics and analytics["avg_views"] else 0,
            "avg_clicks": (
                int(analytics["avg_clicks"]) if analytics and analytics["avg_clicks"] else 0
            ),
            "avg_shares": (
                int(analytics["avg_shares"]) if analytics and analytics["avg_shares"] else 0
            ),
            "effectiveness_score": effectiveness_score,
        }

    # ========================================================================
    # ANALYTICS HELPERS
    # ========================================================================

    def _calculate_platform_baseline(
        self, social_data: List[SocialAnalyticsData]
    ) -> Dict[str, Any]:
        """Calculate average engagement by platform"""
        by_platform = {}

        for data in social_data:
            if data.platform not in by_platform:
                by_platform[data.platform] = {
                    "total_views": 0,
                    "total_clicks": 0,
                    "total_shares": 0,
                    "count": 0,
                    "engagement_rates": [],
                }

            by_platform[data.platform]["total_views"] += data.views
            by_platform[data.platform]["total_clicks"] += data.clicks
            by_platform[data.platform]["total_shares"] += data.shares
            by_platform[data.platform]["count"] += 1
            by_platform[data.platform]["engagement_rates"].append(data.engagement_rate)

        # Calculate averages
        baseline = {}
        for platform, data in by_platform.items():
            if data["count"] > 0:
                baseline[platform] = {
                    "avg_views": data["total_views"] / data["count"],
                    "avg_clicks": data["total_clicks"] / data["count"],
                    "avg_shares": data["total_shares"] / data["count"],
                    "avg_engagement_rate": sum(data["engagement_rates"])
                    / len(data["engagement_rates"]),
                    "sample_size": data["count"],
                }

        return baseline

    async def get_correlation_analysis(self) -> Dict[str, Any]:
        """
        Analyze correlations between business metrics and content performance.

        Shows relationships between financial growth and engagement.
        """
        financial = await self.get_financial_metrics(limit=12)
        social = await self.get_social_analytics(days=365)

        if not financial or not social:
            return {"error": "Insufficient data for correlation analysis"}

        # This is a simplified correlation analysis
        # In production, would use numpy/scipy for proper correlation
        return {
            "financial_metrics_count": len(financial),
            "engagement_records_count": len(social),
            "analysis_note": "Correlation analysis requires paired time-series data",
        }
