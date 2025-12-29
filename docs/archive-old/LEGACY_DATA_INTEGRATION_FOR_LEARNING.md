# Legacy Data Integration for Intelligent Orchestrator Learning

## Overview

Your intelligent orchestrator can learn exponentially faster by ingesting your **existing legacy data** alongside real-time execution data. This creates a powerful feedback loop where the LLM trains on:

- **Real execution patterns** (what actually worked in your business)
- **Financial metrics** (revenue, spending, customer acquisition costs)
- **Content performance** (existing posts, engagement metrics, conversions)
- **Web analytics** (user behavior, traffic sources, conversion funnels)
- **Task history** (everything you've done before)
- **Quality evaluations** (what made content successful)

---

## Architecture: Data Integration Pipeline

```
LEGACY DATA SOURCES (Existing System)
├── Tasks Database (PostgreSQL)
│   ├── 89+ historical tasks
│   ├── Task status & outcomes
│   ├── Task metadata & parameters
│   └── Task results & outputs
│
├── Posts/Content Database
│   ├── Published articles & posts
│   ├── Content metadata (keywords, categories, etc.)
│   ├── Social media variants
│   └── Publishing history
│
├── Social Media Analytics
│   ├── LinkedIn engagement (views, likes, shares, comments)
│   ├── Twitter/X engagement
│   ├── Facebook engagement
│   ├── Posting timestamps
│   └── Audience demographics
│
├── Web Analytics (Google Analytics, etc.)
│   ├── Traffic sources
│   ├── User behavior (bounce rate, time on page, etc.)
│   ├── Conversion funnels
│   ├── Device/browser breakdown
│   └── Geographic data
│
├── Financial Metrics
│   ├── Monthly revenue
│   ├── Customer acquisition costs
│   ├── Lifetime customer value
│   ├── Marketing spend
│   ├── ROI by content/campaign
│   └── Growth rates
│
└── Quality Evaluations
    ├── Historical quality scores
    ├── Which criteria correlated with success
    ├── Refinement patterns
    └── User feedback & approvals
                          ↓
DATA ENRICHMENT & NORMALIZATION LAYER
┌──────────────────────────────────────────────────────────────────┐
│ New Service: LegacyDataIntegrationService                        │
│                                                                   │
│ Responsibilities:                                                │
│ 1. Query legacy data sources (tasks, posts, analytics, metrics) │
│ 2. Normalize/standardize data formats                           │
│ 3. Match tasks to posts, posts to analytics                     │
│ 4. Calculate derived metrics (ROI, engagement/dollar, etc.)     │
│ 5. Create enriched training examples                             │
│ 6. Handle missing/incomplete data gracefully                    │
└──────────────────────────────────────────────────────────────────┘
                          ↓
ENHANCED TRAINING DATA GENERATION
┌──────────────────────────────────────────────────────────────────┐
│ Extended Training Example Structure:                             │
│                                                                   │
│ {                                                                │
│   // EXECUTION CONTEXT                                          │
│   "user_request": "Create LinkedIn post about Q4 growth",       │
│   "intent": "create_social_content",                            │
│   "timestamp": "2025-12-09T10:30:00Z",                         │
│   "is_legacy": false,  // This is a new execution             │
│                                                                   │
│   // BUSINESS CONTEXT (NEW - from legacy data)                 │
│   "business_state": {                                           │
│     "previous_month_revenue": 125000,                          │
│     "this_month_revenue": 150000,                              │
│     "growth_rate": 0.20,  // 20% growth                       │
│     "customer_count": 320,                                     │
│     "monthly_recurring_revenue": 45000,                        │
│     "website_monthly_traffic": 250000,                         │
│     "conversion_rate": 0.045  // 4.5%                        │
│   },                                                            │
│                                                                   │
│   // SIMILAR PAST CONTENT (NEW - from legacy data)            │
│   "similar_historical_content": [                              │
│     {                                                           │
│       "task_id": "task-001",                                  │
│       "title": "Q3 Growth Metrics Post",                      │
│       "topic": "Growth metrics",                               │
│       "created_at": "2025-09-15",                             │
│       "quality_score": 0.92,                                  │
│       "publication_metrics": {                                │
│         "platform": "linkedin",                               │
│         "views": 3421,                                        │
│         "clicks": 87,                                         │
│         "shares": 12,                                         │
│         "engagement_rate": 0.029,  // 2.9%                   │
│         "estimated_traffic": 45,   // clicks from LinkedIn  │
│         "estimated_conversions": 2  // estimated            │
│       },                                                      │
│       "post_metrics_after_publish": {                        │
│         "website_traffic_increase": 0.08,  // 8% increase  │
│         "conversion_impact": 0.002  // 0.2% improvement    │
│       }                                                      │
│     }                                                        │
│   ],                                                          │
│                                                                │
│   // EXECUTION PLAN                                           │
│   "execution_plan": {                                         │
│     "steps": 6,                                              │
│     "workflow_source": "user_request",                       │
│     "agents_involved": ["financial", "content", "linkedin"]│
│   },                                                         │
│                                                                │
│   // EXECUTION RESULT                                        │
│   "execution_result": {                                      │
│     "duration": 47,                                          │
│     "cost": 0.12,                                            │
│     "quality_score": 0.906,                                 │
│     "successful": true                                      │
│   },                                                         │
│                                                                │
│   // POST-PUBLICATION METRICS (NEW)                         │
│   "post_publication_metrics": {                             │
│     "linkedin_views": 3421,                                 │
│     "linkedin_clicks": 87,                                  │
│     "linkedin_shares": 12,                                  │
│     "linkedin_engagement_rate": 0.029,                      │
│     "website_traffic_attributed": 45,                       │
│     "sales_attributed": 2,                                  │
│     "revenue_attributed": 3400,  // avg order value × sales│
│     "roi_on_execution_cost": 28.33  // revenue / cost    │
│   },                                                        │
│                                                                │
│   // LEARNED PATTERNS (NEW)                                │
│   "patterns_discovered": [                                 │
│     "Growth + metrics posts perform 35% better",           │
│     "Financial context → stronger engagement",             │
│     "Q metrics posts → 2.9% engagement on LinkedIn",       │
│     "Publishing at 10 AM → 20% higher reach",            │
│     "Posts mentioning growth metrics → 3x shares",        │
│     "Professional tone → better quality scores"           │
│   ],                                                       │
│                                                                │
│   // CORRELATIONS WITH HISTORICAL DATA (NEW)              │
│   "business_correlations": {                               │
│     "growth_rate_correlation": 0.85,                       │
│     "engagement_rate_correlation": 0.78,                   │
│     "revenue_impact_correlation": 0.72,                    │
│     "customer_acquisition_impact": 0.65                    │
│   }                                                        │
│ }                                                          │
└──────────────────────────────────────────────────────────────────┘
                          ↓
HYBRID TRAINING DATASET (Legacy + Real-Time)
├── Legacy examples: 89+ historical tasks with outcomes
├── Enhanced with: Business context, post performance, ROI
├── Real-time examples: New executions with immediate feedback
├── Combined: 200+ rich training examples ready for fine-tuning
                          ↓
FINE-TUNED REASONING LLM
├── Understands business metrics & their impact
├── Learns what content performs well (historically proven)
├── Makes better decisions based on business context
├── Predicts engagement & ROI before publishing
└── Continuously improves as more data flows in
```

---

## Implementation: LegacyDataIntegrationService

### 1. Service Architecture

```python
# src/cofounder_agent/services/legacy_data_integration.py

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from enum import Enum
import asyncpg
from dataclasses import dataclass, asdict
import logging

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
    task_metadata: Dict[str, Any]  # Original metadata
    result: Optional[Dict[str, Any]]  # Task result/output


@dataclass
class PublishedPost:
    """Post published from a task"""
    post_id: str
    task_id: Optional[str]  # Link to creating task
    title: str
    topic: str
    content: str
    platforms: List[str]  # linkedin, twitter, etc.
    published_at: str
    featured_image_url: Optional[str]
    quality_score: Optional[float]


@dataclass
class SocialAnalyticsData:
    """Social media performance data"""
    post_id: str
    platform: str  # linkedin, twitter, facebook
    views: int
    clicks: int
    shares: int
    comments: int
    engagement_rate: float  # calculated: (clicks + shares + comments) / views
    engagement_score: float  # platform-weighted score
    published_at: str
    measurement_date: str


@dataclass
class WebAnalyticsData:
    """Web traffic & conversion data"""
    post_id: str  # Which post drove this traffic
    source: str  # "linkedin", "twitter", "direct", etc.
    traffic: int  # visitors from this source
    conversions: int  # sales/signups attributed
    revenue: float  # USD revenue attributed
    bounce_rate: float  # %
    avg_time_on_page: float  # seconds
    measurement_period: str  # date range


@dataclass
class FinancialMetrics:
    """Business financial metrics"""
    measurement_date: str
    revenue_monthly: float
    customers: int
    acquisition_cost: float
    lifetime_value: float
    growth_rate: float  # month-over-month
    marketing_spend: float


class LegacyDataIntegrationService:
    """
    Integrates legacy data sources into orchestrator learning system.

    Fetches historical data and enriches real-time executions with context.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    # ========================================================================
    # DATA RETRIEVAL METHODS
    # ========================================================================

    async def get_historical_tasks(
        self,
        limit: int = 100,
        status_filter: Optional[str] = None,
        topic_filter: Optional[str] = None
    ) -> List[HistoricalTask]:
        """
        Fetch historical tasks from legacy system.

        Args:
            limit: Max number of tasks to fetch
            status_filter: Optional status (completed, failed, etc.)
            topic_filter: Optional topic keyword filter

        Returns:
            List of historical tasks with metadata
        """
        query = """
            SELECT
                id as task_id,
                task_name,
                topic,
                status,
                created_at,
                completed_at,
                category,
                primary_keyword,
                target_audience,
                quality_score,
                task_metadata,
                result
            FROM tasks
            WHERE 1=1
        """
        params = []
        param_count = 1

        if status_filter:
            query += f" AND status = ${param_count}"
            params.append(status_filter)
            param_count += 1

        if topic_filter:
            query += f" AND (topic ILIKE ${param_count} OR primary_keyword ILIKE ${param_count})"
            params.append(f"%{topic_filter}%")
            param_count += 1

        query += f" ORDER BY created_at DESC LIMIT ${param_count}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [HistoricalTask(**dict(row)) for row in rows]

    async def get_published_posts(
        self,
        limit: int = 100,
        topic_filter: Optional[str] = None
    ) -> List[PublishedPost]:
        """
        Fetch published posts with task linkage.

        Returns posts published from orchestrator tasks or manual creation.
        """
        query = """
            SELECT
                id as post_id,
                task_id,
                title,
                topic,
                content,
                platforms,
                published_at,
                featured_image_url,
                quality_score
            FROM posts
            WHERE published_at IS NOT NULL
            ORDER BY published_at DESC
            LIMIT $1
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, limit)

        return [PublishedPost(**dict(row)) for row in rows]

    async def get_social_analytics(
        self,
        post_id: Optional[str] = None,
        platform: Optional[str] = None,
        days: int = 90
    ) -> List[SocialAnalyticsData]:
        """
        Fetch social media engagement data.

        Args:
            post_id: Optional filter by post
            platform: Optional filter by platform (linkedin, twitter, etc.)
            days: Look back this many days

        Returns:
            Social engagement metrics
        """
        cutoff_date = datetime.now() - timedelta(days=days)

        query = """
            SELECT
                post_id,
                platform,
                views,
                clicks,
                shares,
                comments,
                engagement_rate,
                engagement_score,
                published_at,
                measurement_date
            FROM social_post_analytics
            WHERE measurement_date >= $1
        """
        params = [cutoff_date]
        param_count = 2

        if post_id:
            query += f" AND post_id = ${param_count}"
            params.append(post_id)
            param_count += 1

        if platform:
            query += f" AND platform = ${param_count}"
            params.append(platform)
            param_count += 1

        query += " ORDER BY measurement_date DESC"

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [SocialAnalyticsData(**dict(row)) for row in rows]

    async def get_web_analytics(
        self,
        days: int = 90
    ) -> List[WebAnalyticsData]:
        """
        Fetch web traffic and conversion attribution data.

        Returns traffic and conversions attributed to posts/content.
        """
        cutoff_date = datetime.now() - timedelta(days=days)

        query = """
            SELECT
                post_id,
                source,
                traffic,
                conversions,
                revenue,
                bounce_rate,
                avg_time_on_page,
                measurement_period
            FROM web_analytics
            WHERE measurement_period >= $1
            ORDER BY measurement_period DESC
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, cutoff_date)

        return [WebAnalyticsData(**dict(row)) for row in rows]

    async def get_financial_metrics(
        self,
        limit: int = 12  # Last 12 months
    ) -> List[FinancialMetrics]:
        """
        Fetch historical financial metrics.

        Returns monthly financial snapshots.
        """
        query = """
            SELECT
                measurement_date,
                revenue_monthly,
                customers,
                acquisition_cost,
                lifetime_value,
                growth_rate,
                marketing_spend
            FROM financial_metrics
            ORDER BY measurement_date DESC
            LIMIT $1
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, limit)

        return [FinancialMetrics(**dict(row)) for row in rows]

    # ========================================================================
    # ENRICHMENT METHODS
    # ========================================================================

    async def find_similar_historical_tasks(
        self,
        topic: str,
        keyword: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find similar historical tasks for context.

        Used to show the orchestrator what worked before for similar requests.

        Args:
            topic: Current topic to match against
            keyword: Optional keyword to match
            limit: Number of similar tasks to return

        Returns:
            Similar historical tasks with their performance
        """
        historical = await self.get_historical_tasks(
            limit=100,
            status_filter="completed",
            topic_filter=topic
        )

        # Rank by quality score and recency
        ranked = sorted(
            historical,
            key=lambda x: (x.quality_score or 0, x.completed_at or ""),
            reverse=True
        )

        # Fetch engagement data for top matches
        enriched = []
        for task in ranked[:limit]:
            # Get associated post if exists
            post_analytics = None
            if task.task_metadata.get("post_id"):
                post_analytics = await self.get_social_analytics(
                    post_id=task.task_metadata["post_id"]
                )

            enriched.append({
                "task_id": task.task_id,
                "topic": task.topic,
                "task_name": task.task_name,
                "quality_score": task.quality_score,
                "completed_at": task.completed_at,
                "category": task.category,
                "primary_keyword": task.primary_keyword,
                "post_metrics": post_analytics[0] if post_analytics else None,
                "metadata": task.task_metadata
            })

        return enriched

    async def enrich_execution_with_context(
        self,
        execution_id: str,
        topic: str,
        business_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enrich a current execution with historical context.

        This prepares data to be added to the execution for learning.

        Returns:
            Enriched context object to include in training data
        """
        # Get similar historical tasks
        similar_tasks = await self.find_similar_historical_tasks(
            topic=topic,
            limit=5
        )

        # Get current financial metrics
        financial = await self.get_financial_metrics(limit=1)
        current_metrics = financial[0] if financial else None

        # Get recent social performance
        recent_posts = await self.get_published_posts(limit=20)
        platform_performance = await self._aggregate_platform_performance(
            recent_posts
        )

        return {
            "similar_historical_content": similar_tasks,
            "business_state": {
                "revenue_monthly": current_metrics.revenue_monthly if current_metrics else None,
                "customers": current_metrics.customers if current_metrics else None,
                "acquisition_cost": current_metrics.acquisition_cost if current_metrics else None,
                "lifetime_value": current_metrics.lifetime_value if current_metrics else None,
                "growth_rate": current_metrics.growth_rate if current_metrics else None,
                "marketing_spend": current_metrics.marketing_spend if current_metrics else None,
                **business_context
            },
            "platform_performance_baseline": platform_performance
        }

    # ========================================================================
    # ANALYTICS AGGREGATION
    # ========================================================================

    async def _aggregate_platform_performance(
        self,
        posts: List[PublishedPost],
        days: int = 90
    ) -> Dict[str, Any]:
        """
        Aggregate performance by platform across recent posts.

        Shows what engagement rates are typical by platform.
        """
        analytics = await self.get_social_analytics(days=days)

        # Group by platform
        by_platform = {}
        for analytic in analytics:
            if analytic.platform not in by_platform:
                by_platform[analytic.platform] = {
                    "count": 0,
                    "total_views": 0,
                    "total_engagement": 0,
                    "avg_engagement_rate": 0.0
                }

            platform_data = by_platform[analytic.platform]
            platform_data["count"] += 1
            platform_data["total_views"] += analytic.views
            platform_data["total_engagement"] += (
                analytic.clicks + analytic.shares + analytic.comments
            )

        # Calculate averages
        for platform in by_platform:
            count = by_platform[platform]["count"]
            if count > 0:
                total_eng = by_platform[platform]["total_engagement"]
                total_views = by_platform[platform]["total_views"]
                by_platform[platform]["avg_engagement_rate"] = (
                    total_eng / total_views if total_views > 0 else 0
                )

        return by_platform

    async def get_topic_effectiveness(
        self,
        topic: str,
        days: int = 180
    ) -> Dict[str, Any]:
        """
        Analyze how effective a topic has been historically.

        Returns: engagement rates, conversion rates, ROI by topic.
        """
        # Find all posts about this topic
        historical = await self.get_historical_tasks(
            limit=50,
            topic_filter=topic,
            status_filter="completed"
        )

        if not historical:
            return {}

        total_views = 0
        total_clicks = 0
        total_shares = 0
        total_conversions = 0
        total_revenue = 0
        count = 0

        for task in historical:
            post_id = task.task_metadata.get("post_id")
            if not post_id:
                continue

            analytics = await self.get_social_analytics(post_id=post_id)
            web = await self.get_web_analytics(days=days)

            for analytic in analytics:
                total_views += analytic.views
                total_clicks += analytic.clicks
                total_shares += analytic.shares
                count += 1

            for web_data in web:
                if web_data.post_id == post_id:
                    total_conversions += web_data.conversions
                    total_revenue += web_data.revenue

        if count == 0:
            return {}

        return {
            "topic": topic,
            "sample_size": count,
            "total_views": total_views,
            "avg_views_per_post": total_views / count,
            "total_engagement": total_clicks + total_shares,
            "avg_engagement_rate": (total_clicks + total_shares) / total_views if total_views > 0 else 0,
            "total_conversions": total_conversions,
            "conversion_rate": total_conversions / total_views if total_views > 0 else 0,
            "total_revenue": total_revenue,
            "revenue_per_view": total_revenue / total_views if total_views > 0 else 0,
            "effectiveness_score": (total_revenue / total_views * 100) if total_views > 0 else 0
        }
```

### 2. Integration with Orchestrator

```python
# In intelligent_orchestrator.py, add:

async def _enrich_execution_plan_with_legacy_data(
    self,
    execution_plan: ExecutionPlan,
    legacy_integration: LegacyDataIntegrationService
) -> ExecutionPlan:
    """
    Enrich execution plan with historical context before running.

    Shows the orchestrator what worked before for similar requests.
    """
    # Get similar historical tasks
    similar_tasks = await legacy_integration.find_similar_historical_tasks(
        topic=execution_plan.user_request,
        limit=5
    )

    # Get topic effectiveness
    effectiveness = await legacy_integration.get_topic_effectiveness(
        topic=execution_plan.user_request
    )

    # Add to execution plan metadata
    execution_plan.metadata["similar_historical_tasks"] = similar_tasks
    execution_plan.metadata["topic_effectiveness"] = effectiveness

    return execution_plan


async def extract_learning_data_with_legacy_context(
    self,
    execution_result: ExecutionResult,
    legacy_integration: LegacyDataIntegrationService
) -> Dict[str, Any]:
    """
    Create training example enriched with historical context.
    """
    # Get enriched context
    context = await legacy_integration.enrich_execution_with_context(
        execution_id=execution_result.result_id,
        topic=execution_result.outputs.get("topic", ""),
        business_context=execution_result.outputs.get("business_context", {})
    )

    # Create training example
    training_example = {
        # Execution info
        "execution_id": execution_result.result_id,
        "timestamp": execution_result.created_at.isoformat(),
        "user_request": execution_result.user_request,
        "intent": execution_result.intent,

        # LEGACY DATA INTEGRATION
        "business_state": context.get("business_state"),
        "similar_historical_content": context.get("similar_historical_content"),
        "platform_performance_baseline": context.get("platform_performance_baseline"),

        # Execution details
        "execution_plan": asdict(execution_result.plan),
        "execution_result": asdict(execution_result),
        "quality_assessment": asdict(execution_result.quality_assessment),

        # Post-execution metrics
        "post_publication_metrics": execution_result.final_formatting.get("metrics", {})
        if execution_result.final_formatting else {},

        # Patterns learned
        "patterns_discovered": await self._discover_patterns(execution_result),

        # Business impact correlations
        "business_correlations": await self._calculate_correlations(
            execution_result,
            context.get("business_state", {})
        )
    }

    return training_example
```

### 3. Database Schema Updates

Add these tables to store legacy data references and integrations:

```sql
-- Table: social_post_analytics
-- Stores social media engagement data for published posts
CREATE TABLE IF NOT EXISTS social_post_analytics (
    id SERIAL PRIMARY KEY,
    post_id VARCHAR(255) NOT NULL,
    task_id VARCHAR(255),  -- Link to creating task
    platform VARCHAR(50) NOT NULL,  -- linkedin, twitter, facebook
    views INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    engagement_rate DECIMAL(5,3) DEFAULT 0,  -- Calculated: engagement/views
    engagement_score DECIMAL(5,2) DEFAULT 0,  -- Platform-weighted score
    published_at TIMESTAMP,
    measurement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_data JSONB DEFAULT '{}',  -- Platform-specific data

    CONSTRAINT fk_post FOREIGN KEY (post_id) REFERENCES posts(id),
    CONSTRAINT fk_task FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX idx_social_analytics_post_id ON social_post_analytics(post_id);
CREATE INDEX idx_social_analytics_task_id ON social_post_analytics(task_id);
CREATE INDEX idx_social_analytics_platform ON social_post_analytics(platform);
CREATE INDEX idx_social_analytics_measurement_date ON social_post_analytics(measurement_date DESC);


-- Table: web_analytics
-- Stores traffic and conversion attribution data
CREATE TABLE IF NOT EXISTS web_analytics (
    id SERIAL PRIMARY KEY,
    post_id VARCHAR(255),  -- Which post drove traffic (nullable for non-post sources)
    source VARCHAR(100) NOT NULL,  -- linkedin, twitter, organic, direct, etc.
    traffic INTEGER DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    revenue DECIMAL(10,2) DEFAULT 0,
    bounce_rate DECIMAL(5,2) DEFAULT 0,  -- Percentage
    avg_time_on_page INTEGER DEFAULT 0,  -- Seconds
    measurement_period DATE NOT NULL,
    raw_data JSONB DEFAULT '{}',  -- GA or other tool raw data

    CONSTRAINT fk_post FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE INDEX idx_web_analytics_post_id ON web_analytics(post_id);
CREATE INDEX idx_web_analytics_source ON web_analytics(source);
CREATE INDEX idx_web_analytics_period ON web_analytics(measurement_period DESC);


-- Table: financial_metrics
-- Snapshots of business financial metrics over time
CREATE TABLE IF NOT EXISTS financial_metrics (
    id SERIAL PRIMARY KEY,
    measurement_date DATE NOT NULL UNIQUE,
    revenue_monthly DECIMAL(12,2),  -- Monthly revenue
    customers INTEGER,  -- Total customer count
    acquisition_cost DECIMAL(8,2),  -- CAC
    lifetime_value DECIMAL(8,2),  -- LTV
    growth_rate DECIMAL(5,3),  -- Month-over-month growth
    marketing_spend DECIMAL(10,2),  -- Monthly marketing budget
    custom_metrics JSONB DEFAULT '{}',  -- Any additional metrics

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_financial_metrics_date ON financial_metrics(measurement_date DESC);


-- Table: orchestrator_training_data
-- Stores all training examples (legacy + real-time) for fine-tuning
CREATE TABLE IF NOT EXISTS orchestrator_training_data (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(255) UNIQUE,
    user_request TEXT NOT NULL,
    intent VARCHAR(100),

    -- Business context
    business_state JSONB,  -- Revenue, customers, metrics at time of execution
    similar_historical_tasks JSONB,  -- What worked before for similar requests
    platform_baseline JSONB,  -- Typical engagement rates

    -- Execution & results
    execution_plan JSONB,  -- The workflow designed
    execution_result JSONB,  -- What actually happened
    quality_score DECIMAL(3,1),

    -- Outcomes
    post_publication_metrics JSONB,  -- Views, clicks, shares, conversions
    business_impact JSONB,  -- Revenue impact, CAC impact, etc.
    success BOOLEAN,

    -- Patterns & learning
    patterns_discovered JSONB,  -- JSON array of discovered patterns
    business_correlations JSONB,  -- How metrics affected success

    -- Metadata
    is_legacy BOOLEAN DEFAULT FALSE,  -- Is this from historical data?
    is_high_quality BOOLEAN DEFAULT FALSE,  -- Quality score >= 0.85?
    training_ready BOOLEAN DEFAULT FALSE,  -- Ready for fine-tuning?

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_training_data_success ON orchestrator_training_data(success);
CREATE INDEX idx_training_data_quality ON orchestrator_training_data(quality_score DESC);
CREATE INDEX idx_training_data_training_ready ON orchestrator_training_data(training_ready);
CREATE INDEX idx_training_data_intent ON orchestrator_training_data(intent);
```

---

## Data Flow: Real-Time Execution + Legacy Context

```
USER REQUEST (Chat)
"Create a LinkedIn post about Q4 growth metrics"
                ↓
ORCHESTRATOR.process_request()
                ↓
LEGACY DATA ENRICHMENT
┌─────────────────────────────────────────────┐
│ 1. Find similar historical tasks             │
│    → Found 5 posts about "Q4 metrics"       │
│    → Q3 metrics post: 0.92 quality, 2.9% eng│
│    → Q2 metrics post: 0.88 quality, 2.5% eng│
│                                              │
│ 2. Get current business metrics              │
│    → Revenue: $150K (↑20% vs prev month)    │
│    → Customers: 320                         │
│    → Growth rate: 0.20 (20%)                │
│                                              │
│ 3. Get platform baseline engagement         │
│    → LinkedIn avg engagement: 2.8% this mo  │
│    → LinkedIn avg engagement: 2.2% avg      │
│    → Twitter avg engagement: 1.5% this mo   │
│                                              │
│ 4. Get topic effectiveness metrics          │
│    → "Q4 metrics" effectiveness score: 4.2  │
│    → Avg views per post: 2,850              │
│    → Avg engagement rate: 2.8%              │
│    → Conversion rate: 0.025 (2.5%)          │
│    → Revenue per view: $0.47                │
└─────────────────────────────────────────────┘
                ↓
ORCHESTRATION (with enriched context)
├─ Analyze metrics
├─ Generate content (with knowledge of what worked)
├─ Format for LinkedIn (knowing 2.9% baseline)
├─ Quality check (knowing 0.92 is target)
└─ Generate variants
                ↓
EXECUTION RESULT
├─ Generated post (1000 chars)
├─ Quality score: 0.906 ✅ PASSES
├─ Publishing recommendations
└─ Engagement prediction: ~2.9% (based on historical avg)
                ↓
TRAINING EXAMPLE CREATED
┌─────────────────────────────────────────────┐
│ {                                           │
│   "user_request": "Create LinkedIn post...",│
│   "business_state": {                      │
│     "revenue": 150000,                     │
│     "growth": 0.20,                        │
│     "customers": 320                       │
│   },                                       │
│   "similar_historical_content": [          │
│     {                                      │
│       "topic": "Q3 metrics",              │
│       "quality": 0.92,                    │
│       "engagement": 0.029,                │
│       "views": 3200,                      │
│       "revenue_attributed": 3200          │
│     }                                      │
│   ],                                       │
│   "execution_result": {...},              │
│   "post_metrics": {                        │
│     "views": 3421,                        │
│     "clicks": 87,                         │
│     "shares": 12,                         │
│     "conversions": 2                      │
│   },                                       │
│   "patterns": [                            │
│     "Growth context → higher engagement",  │
│     "Q metrics posts → 2.9% engagement",  │
│     "Revenue growth → metric interest"     │
│   ]                                        │
│ }                                          │
└─────────────────────────────────────────────┘
                ↓
TRAINING DATASET STORED
├─ Saved to orchestrator_training_data table
├─ Marked as high-quality if score >= 0.85
├─ Flagged as training-ready
└─ Ready for fine-tuning pipeline
```

---

## Benefits of Legacy Data Integration

### 1. **Informed Decision Making**

- Orchestrator understands historical context before deciding
- Knows what topics work well in your business
- Adapts based on current financial situation

### 2. **Better Quality Output**

- Training examples show what successful content looks like
- LLM learns your specific quality standards
- Patterns from past successes inform new decisions

### 3. **Predictive Capabilities**

- Can estimate engagement before publishing (based on historical data)
- Predicts likely ROI based on similar past content
- Suggests optimal timing based on what worked before

### 4. **Continuous Improvement Loop**

```
Historical Data (89+ tasks)
  → Training examples (enriched with outcomes)
    → Fine-tuned LLM (understands your business)
      → Better orchestration decisions
        → Better quality content
          → Better business results
            → More training data
              → Even better LLM
```

### 5. **Business Context Awareness**

- LLM understands how business metrics affect engagement
- Makes decisions aligned with revenue impact, not just content quality
- Learns what customers care about during different business phases

---

## Implementation Roadmap

### Phase 1: Data Source Integration (1 week)

1. Create LegacyDataIntegrationService
2. Implement data retrieval methods for:
   - Historical tasks
   - Published posts
   - Social analytics
   - Financial metrics
3. Add database tables for analytics
4. Test data queries

### Phase 2: Orchestrator Integration (1 week)

1. Integrate legacy service into orchestrator
2. Enrich execution plans with historical context
3. Enhance training data generation
4. Add business context to execution decisions

### Phase 3: Correlation Analysis (1-2 weeks)

1. Build topic effectiveness calculator
2. Implement business impact correlation analysis
3. Create platform baseline aggregations
4. Track engagement patterns by context

### Phase 4: Training Data Export (1 week)

1. Implement JSONL export for fine-tuning
2. Filter training data by quality
3. Prepare for custom LLM training
4. Create training pipeline documentation

### Phase 5: Fine-Tuning & Deployment (2-3 weeks)

1. Export accumulated training data
2. Fine-tune proprietary LLM on your data
3. Deploy fine-tuned model
4. Monitor improvement vs baseline

---

## Example Training Data Structure

```json
{
  "user_request": "Create a LinkedIn post about our Q4 growth metrics",
  "intent": "create_social_content_with_metrics",
  "is_legacy": false,
  "timestamp": "2025-12-09T10:30:00Z",

  "business_state": {
    "revenue_monthly": 150000,
    "previous_revenue": 125000,
    "growth_rate": 0.2,
    "customers": 320,
    "customer_acquisition_cost": 450,
    "customer_lifetime_value": 8500,
    "marketing_spend": 12000,
    "monthly_website_traffic": 250000,
    "conversion_rate": 0.045
  },

  "similar_historical_content": [
    {
      "task_id": "task-001",
      "topic": "Q3 Growth Metrics",
      "quality_score": 0.92,
      "publication_metrics": {
        "platform": "linkedin",
        "views": 3200,
        "clicks": 81,
        "shares": 11,
        "comments": 5,
        "engagement_rate": 0.0289
      },
      "post_metrics_after_publish": {
        "website_traffic_attributed": 42,
        "conversions_attributed": 2,
        "revenue_attributed": 3400,
        "roi": 28.33
      }
    }
  ],

  "platform_baseline": {
    "linkedin": {
      "avg_engagement_rate": 0.028,
      "avg_views_per_post": 2850,
      "avg_conversion_rate": 0.025,
      "avg_revenue_per_view": 0.47
    }
  },

  "execution_plan": {
    "steps": 6,
    "agents": ["financial", "content", "linkedin"],
    "estimated_duration": 45,
    "estimated_cost": 0.12,
    "workflow_source": "user_request"
  },

  "execution_result": {
    "actual_duration": 47,
    "actual_cost": 0.12,
    "final_quality_score": 0.906,
    "refinements_needed": 0,
    "successful": true
  },

  "post_publication_metrics": {
    "linkedin": {
      "views": 3421,
      "clicks": 87,
      "shares": 12,
      "comments": 8,
      "engagement_rate": 0.0295
    },
    "website_traffic_attributed": 45,
    "conversions_attributed": 2,
    "revenue_attributed": 3400,
    "roi_on_cost": 28.33
  },

  "patterns_discovered": [
    "Q-metrics posts perform 35% better than average",
    "Business growth context increases engagement 40%",
    "Professional tone + financial data = higher quality",
    "Growth rates >15% trigger higher audience interest",
    "Posts mentioning customer count → 2x shares",
    "Revenue growth announcements → 3x engagement"
  ],

  "business_correlations": {
    "growth_rate_vs_engagement": 0.85,
    "revenue_growth_vs_social_reach": 0.78,
    "financial_context_vs_quality_score": 0.72,
    "customer_growth_vs_conversions": 0.68
  }
}
```

This structure gives your fine-tuned LLM the context to understand:

- **What worked historically** (similar_historical_content)
- **What baseline performance looks like** (platform_baseline)
- **What your business state is** (business_state)
- **What the actual impact was** (post_publication_metrics)
- **What patterns emerged** (patterns_discovered)
- **How business metrics correlate with success** (business_correlations)

The result: An LLM that understands your specific business, your audience, and what drives results for your organization.
