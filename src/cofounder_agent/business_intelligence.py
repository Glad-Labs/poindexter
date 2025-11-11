"""
Business Intelligence System for Glad Labs AI Co-Founder

This module provides comprehensive business intelligence gathering and analysis
capabilities, including data collection from multiple sources, trend analysis,
performance monitoring, and strategic insights generation.

Note: Uses PostgreSQL (glad_labs_dev) for all data storage.
"""

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

import aiohttp


class DataSource(str, Enum):
    """Available data sources for business intelligence"""
    CMS_ANALYTICS = "cms_analytics"
    SOCIAL_MEDIA = "social_media"
    WEBSITE_ANALYTICS = "website_analytics"
    FINANCIAL_DATA = "financial_data"
    CONTENT_PERFORMANCE = "content_performance"
    AI_USAGE_METRICS = "ai_usage_metrics"
    SYSTEM_HEALTH = "system_health"
    MARKET_DATA = "market_data"


@dataclass
class BusinessMetric:
    """A single business metric with metadata"""
    name: str
    value: float
    unit: str
    category: str
    source: DataSource
    timestamp: datetime
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class TrendAnalysis:
    """Analysis of metric trends over time"""
    metric: str
    direction: str  # "up", "down", "stable"
    magnitude: float  # percentage change
    period: str  # "daily", "weekly", "monthly"
    confidence: float
    significant: bool
    forecast: Optional[Dict[str, Any]] = None


@dataclass
class CompetitorInsight:
    """Competitive intelligence data"""
    competitor_name: str
    domain: str
    content_frequency: float
    social_engagement: float
    estimated_traffic: Optional[int] = None
    key_topics: List[str] = None
    strengths: List[str] = None
    opportunities: List[str] = None


class BusinessIntelligenceSystem:
    """
    Comprehensive business intelligence system that:
    1. Collects data from multiple sources
    2. Analyzes trends and patterns
    3. Generates insights and predictions
    4. Monitors competitive landscape
    5. Tracks business performance
    """
    
    def __init__(self, data_dir: str = "business_intelligence_data"):
        self.logger = logging.getLogger("business_intelligence")
        
        # Database: PostgreSQL only (glad_labs_dev)
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
        )
        
        # Data collectors
        self.data_collectors = {}
        self._register_data_collectors()
        
        # Analysis engines
        self.trend_analyzer = TrendAnalyzer()
        self.performance_analyzer = PerformanceAnalyzer()
        self.competitive_analyzer = CompetitiveAnalyzer()
        
        # Cache for frequently accessed data
        self.metrics_cache = {}
        self.cache_timestamp = None
        self.cache_duration = timedelta(minutes=15)
    
    async def initialize(self):
        """Initialize PostgreSQL connection for business intelligence"""
        self.logger.info(f"✅ Initializing Business Intelligence System with PostgreSQL")
        # Connection will be established on-demand via database service
    
    def _register_data_collectors(self):
        """Register data collection methods for different sources"""
        self.data_collectors = {
            DataSource.CMS_ANALYTICS: self._collect_cms_analytics,
            DataSource.CONTENT_PERFORMANCE: self._collect_content_performance,
            DataSource.AI_USAGE_METRICS: self._collect_ai_usage_metrics,
            DataSource.SYSTEM_HEALTH: self._collect_system_health,
            DataSource.WEBSITE_ANALYTICS: self._collect_website_analytics,
            DataSource.SOCIAL_MEDIA: self._collect_social_media_metrics,
            DataSource.FINANCIAL_DATA: self._collect_financial_data,
            DataSource.MARKET_DATA: self._collect_market_data
        }
    
    async def collect_all_metrics(self) -> Dict[str, List[BusinessMetric]]:
        """Collect metrics from all available data sources"""
        all_metrics = {}
        
        for source, collector in self.data_collectors.items():
            try:
                self.logger.info(f"Collecting metrics from {source}")
                metrics = await collector()
                all_metrics[source.value] = metrics
                
                # Store metrics in database
                await self._store_metrics(metrics)
                
            except Exception as e:
                self.logger.error(f"Error collecting from {source}: {e}")
                all_metrics[source.value] = []
        
        # Update cache
        self.metrics_cache = all_metrics
        self.cache_timestamp = datetime.now()
        
        return all_metrics
    
    async def _collect_cms_analytics(self) -> List[BusinessMetric]:
        """Collect analytics from CMS (Strapi)"""
        metrics = []
        
        try:
            # This would integrate with actual Strapi analytics API
            # For now, simulate data collection
            
            metrics.extend([
                BusinessMetric(
                    name="total_content_pieces",
                    value=47.0,
                    unit="count",
                    category="content",
                    source=DataSource.CMS_ANALYTICS,
                    timestamp=datetime.now(),
                    metadata={"last_update": "2024-01-15"}
                ),
                BusinessMetric(
                    name="content_creation_rate",
                    value=2.3,
                    unit="pieces_per_day",
                    category="content",
                    source=DataSource.CMS_ANALYTICS,
                    timestamp=datetime.now(),
                    confidence=0.85
                ),
                BusinessMetric(
                    name="average_content_length",
                    value=1250.0,
                    unit="words",
                    category="content",
                    source=DataSource.CMS_ANALYTICS,
                    timestamp=datetime.now()
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting CMS analytics: {e}")
        
        return metrics
    
    async def _collect_content_performance(self) -> List[BusinessMetric]:
        """Collect content performance metrics"""
        metrics = []
        
        try:
            # Simulate content performance data
            metrics.extend([
                BusinessMetric(
                    name="average_engagement_rate",
                    value=4.2,
                    unit="percentage",
                    category="performance",
                    source=DataSource.CONTENT_PERFORMANCE,
                    timestamp=datetime.now(),
                    confidence=0.8
                ),
                BusinessMetric(
                    name="top_performing_content_ctr",
                    value=12.5,
                    unit="percentage",
                    category="performance",
                    source=DataSource.CONTENT_PERFORMANCE,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="content_virality_score",
                    value=3.7,
                    unit="score",
                    category="performance",
                    source=DataSource.CONTENT_PERFORMANCE,
                    timestamp=datetime.now(),
                    metadata={"scale": "1-10"}
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting content performance: {e}")
        
        return metrics
    
    async def _collect_ai_usage_metrics(self) -> List[BusinessMetric]:
        """Collect AI usage and cost metrics"""
        metrics = []
        
        try:
            # Simulate AI usage data
            metrics.extend([
                BusinessMetric(
                    name="daily_ai_requests",
                    value=245.0,
                    unit="requests",
                    category="ai_usage",
                    source=DataSource.AI_USAGE_METRICS,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="average_daily_ai_cost",
                    value=12.50,
                    unit="usd",
                    category="ai_usage",
                    source=DataSource.AI_USAGE_METRICS,
                    timestamp=datetime.now(),
                    confidence=0.95
                ),
                BusinessMetric(
                    name="cost_per_content_piece",
                    value=0.83,
                    unit="usd",
                    category="ai_usage",
                    source=DataSource.AI_USAGE_METRICS,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="local_model_usage_ratio",
                    value=35.0,
                    unit="percentage",
                    category="ai_usage",
                    source=DataSource.AI_USAGE_METRICS,
                    timestamp=datetime.now(),
                    metadata={"optimization_target": 50.0}
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting AI usage metrics: {e}")
        
        return metrics
    
    async def _collect_system_health(self) -> List[BusinessMetric]:
        """Collect system health and performance metrics"""
        metrics = []
        
        try:
            # Simulate system health data
            metrics.extend([
                BusinessMetric(
                    name="system_uptime",
                    value=99.2,
                    unit="percentage",
                    category="system",
                    source=DataSource.SYSTEM_HEALTH,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="average_response_time",
                    value=250.0,
                    unit="milliseconds",
                    category="system",
                    source=DataSource.SYSTEM_HEALTH,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="error_rate",
                    value=0.8,
                    unit="percentage",
                    category="system",
                    source=DataSource.SYSTEM_HEALTH,
                    timestamp=datetime.now(),
                    confidence=0.9
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting system health: {e}")
        
        return metrics
    
    async def _collect_website_analytics(self) -> List[BusinessMetric]:
        """Collect website analytics (would integrate with Google Analytics)"""
        metrics = []
        
        try:
            # Simulate website analytics
            metrics.extend([
                BusinessMetric(
                    name="monthly_visitors",
                    value=1250.0,
                    unit="visitors",
                    category="website",
                    source=DataSource.WEBSITE_ANALYTICS,
                    timestamp=datetime.now(),
                    confidence=0.85
                ),
                BusinessMetric(
                    name="bounce_rate",
                    value=42.3,
                    unit="percentage",
                    category="website",
                    source=DataSource.WEBSITE_ANALYTICS,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="average_session_duration",
                    value=3.2,
                    unit="minutes",
                    category="website",
                    source=DataSource.WEBSITE_ANALYTICS,
                    timestamp=datetime.now()
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting website analytics: {e}")
        
        return metrics
    
    async def _collect_social_media_metrics(self) -> List[BusinessMetric]:
        """Collect social media performance metrics"""
        metrics = []
        
        try:
            # Simulate social media data
            metrics.extend([
                BusinessMetric(
                    name="total_followers",
                    value=3400.0,
                    unit="followers",
                    category="social",
                    source=DataSource.SOCIAL_MEDIA,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="weekly_engagement_rate",
                    value=6.8,
                    unit="percentage",
                    category="social",
                    source=DataSource.SOCIAL_MEDIA,
                    timestamp=datetime.now(),
                    confidence=0.8
                ),
                BusinessMetric(
                    name="content_reach",
                    value=15000.0,
                    unit="impressions",
                    category="social",
                    source=DataSource.SOCIAL_MEDIA,
                    timestamp=datetime.now()
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting social media metrics: {e}")
        
        return metrics
    
    async def _collect_financial_data(self) -> List[BusinessMetric]:
        """Collect financial performance data"""
        metrics = []
        
        try:
            # Simulate financial data
            metrics.extend([
                BusinessMetric(
                    name="monthly_revenue",
                    value=850.0,
                    unit="usd",
                    category="finance",
                    source=DataSource.FINANCIAL_DATA,
                    timestamp=datetime.now(),
                    confidence=0.95
                ),
                BusinessMetric(
                    name="monthly_expenses",
                    value=320.0,
                    unit="usd",
                    category="finance",
                    source=DataSource.FINANCIAL_DATA,
                    timestamp=datetime.now()
                ),
                BusinessMetric(
                    name="profit_margin",
                    value=62.4,
                    unit="percentage",
                    category="finance",
                    source=DataSource.FINANCIAL_DATA,
                    timestamp=datetime.now()
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting financial data: {e}")
        
        return metrics
    
    async def _collect_market_data(self) -> List[BusinessMetric]:
        """Collect market intelligence and trends"""
        metrics = []
        
        try:
            # Simulate market data
            metrics.extend([
                BusinessMetric(
                    name="market_sentiment_score",
                    value=7.2,
                    unit="score",
                    category="market",
                    source=DataSource.MARKET_DATA,
                    timestamp=datetime.now(),
                    metadata={"scale": "1-10", "topics": ["AI", "content marketing", "automation"]}
                ),
                BusinessMetric(
                    name="competitor_content_volume",
                    value=23.0,
                    unit="posts_per_week",
                    category="market",
                    source=DataSource.MARKET_DATA,
                    timestamp=datetime.now(),
                    confidence=0.7
                ),
                BusinessMetric(
                    name="industry_growth_rate",
                    value=15.2,
                    unit="percentage_yearly",
                    category="market",
                    source=DataSource.MARKET_DATA,
                    timestamp=datetime.now()
                )
            ])
            
        except Exception as e:
            self.logger.error(f"Error collecting market data: {e}")
        
        return metrics
    
    async def _store_metrics(self, metrics: List[BusinessMetric]):
        """Store metrics in PostgreSQL database"""
        # TODO: Implement PostgreSQL storage via DatabaseService
        # For now, store in cache only
        self.logger.debug(f"Storing {len(metrics)} metrics in cache (PostgreSQL integration pending)")
    
    async def analyze_trends(self, metric_name: str, period: str = "weekly") -> Optional[TrendAnalysis]:
        """Analyze trends for a specific metric"""
        try:
            return await self.trend_analyzer.analyze_metric_trend(metric_name, period)
        except Exception as e:
            self.logger.error(f"Error analyzing trends for {metric_name}: {e}")
            return None
    
    async def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary"""
        try:
            return await self.performance_analyzer.generate_summary()
        except Exception as e:
            self.logger.error(f"Error generating performance summary: {e}")
            return {}
    
    async def analyze_competitive_landscape(self) -> List[CompetitorInsight]:
        """Analyze competitive landscape"""
        try:
            return await self.competitive_analyzer.analyze_competitors()
        except Exception as e:
            self.logger.error(f"Error analyzing competitive landscape: {e}")
            return []
    
    async def generate_strategic_insights(self) -> List[Dict[str, Any]]:
        """Generate strategic business insights based on all collected data"""
        insights = []
        
        try:
            # Get recent metrics
            if not self.metrics_cache or not self._cache_is_valid():
                await self.collect_all_metrics()
            
            # Analyze AI cost efficiency
            ai_metrics = self.metrics_cache.get(DataSource.AI_USAGE_METRICS.value, [])
            cost_per_content = next((m.value for m in ai_metrics if m.name == "cost_per_content_piece"), None)
            local_usage = next((m.value for m in ai_metrics if m.name == "local_model_usage_ratio"), None)
            
            if cost_per_content and local_usage:
                if cost_per_content > 1.0:
                    insights.append({
                        "type": "opportunity",
                        "area": "cost_optimization",
                        "title": "AI Cost Optimization Opportunity",
                        "description": f"Current cost per content piece is ${cost_per_content:.2f}. Increasing local model usage from {local_usage}% could reduce costs significantly.",
                        "confidence": 0.85,
                        "priority": 4,
                        "recommendations": [
                            "Increase local model usage for development and testing",
                            "Use premium models only for final production content",
                            "Implement content quality scoring to optimize model selection"
                        ]
                    })
            
            # Analyze content production trends
            content_metrics = self.metrics_cache.get(DataSource.CMS_ANALYTICS.value, [])
            creation_rate = next((m.value for m in content_metrics if m.name == "content_creation_rate"), None)
            
            if creation_rate and creation_rate < 3.0:
                insights.append({
                    "type": "recommendation",
                    "area": "content_strategy",
                    "title": "Scale Content Production",
                    "description": f"Current creation rate of {creation_rate} pieces per day is below optimal. Market opportunity exists for increased production.",
                    "confidence": 0.9,
                    "priority": 3,
                    "recommendations": [
                        "Implement automated content scheduling",
                        "Create topic clusters for batch content generation",
                        "Develop content templates for faster production"
                    ]
                })
            
            # Analyze market position
            market_metrics = self.metrics_cache.get(DataSource.MARKET_DATA.value, [])
            sentiment = next((m.value for m in market_metrics if m.name == "market_sentiment_score"), None)
            
            if sentiment and sentiment > 7.0:
                insights.append({
                    "type": "opportunity",
                    "area": "growth",
                    "title": "Favorable Market Conditions",
                    "description": f"Market sentiment score of {sentiment}/10 indicates strong opportunity for AI content tools. Time to accelerate growth initiatives.",
                    "confidence": 0.8,
                    "priority": 5,
                    "recommendations": [
                        "Launch targeted marketing campaigns",
                        "Develop strategic partnerships",
                        "Expand feature set based on market demand",
                        "Consider raising prices based on strong demand"
                    ]
                })
            
        except Exception as e:
            self.logger.error(f"Error generating strategic insights: {e}")
        
        return insights
    
    def _cache_is_valid(self) -> bool:
        """Check if metrics cache is still valid"""
        if not self.cache_timestamp:
            return False
        return datetime.now() - self.cache_timestamp < self.cache_duration
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive dashboard data for business intelligence"""
        # Ensure we have fresh data
        if not self._cache_is_valid():
            await self.collect_all_metrics()
        
        # Get performance summary
        performance = await self.get_performance_summary()
        
        # Get strategic insights
        insights = await self.generate_strategic_insights()
        
        # Get competitive analysis
        competitors = await self.analyze_competitive_landscape()
        
        return {
            "metrics": self.metrics_cache,
            "performance_summary": performance,
            "strategic_insights": insights,
            "competitive_analysis": [asdict(c) for c in competitors],
            "last_updated": self.cache_timestamp.isoformat() if self.cache_timestamp else None,
            "data_quality": {
                "total_metrics": sum(len(metrics) for metrics in self.metrics_cache.values()),
                "data_sources": len(self.metrics_cache),
                "confidence_score": self._calculate_overall_confidence()
            }
        }
    
    def _calculate_overall_confidence(self) -> float:
        """Calculate overall confidence in business intelligence data"""
        all_confidences = []
        
        for metrics in self.metrics_cache.values():
            for metric in metrics:
                all_confidences.append(metric.confidence)
        
        return sum(all_confidences) / len(all_confidences) if all_confidences else 0.0


class TrendAnalyzer:
    """Analyzes trends in business metrics over time"""
    
    async def analyze_metric_trend(self, metric_name: str, period: str) -> TrendAnalysis:
        """Analyze trend for a specific metric (PostgreSQL integration pending)"""
        # TODO: Implement with PostgreSQL queries via DatabaseService
        # For now, return placeholder trend analysis
        return TrendAnalysis(
            metric=metric_name,
            direction="unknown",
            magnitude=0.0,
            period=period,
            confidence=0.0,
            significant=False
        )


class PerformanceAnalyzer:
    """Analyzes overall business performance"""
    
    async def generate_summary(self) -> Dict[str, Any]:
        """Generate comprehensive performance summary (PostgreSQL integration pending)"""
        # TODO: Query metrics from PostgreSQL via DatabaseService
        # For now, return placeholder summary
        return {
            "categories": {},
            "total_metrics": 0,
            "performance_score": 0.0,
            "generated_at": datetime.now().isoformat()
        }
    
    def _calculate_performance_score(self, categories: Dict[str, List[Dict]]) -> float:
        """Calculate overall performance score (0-100)"""
        # TODO: Implement scoring logic with PostgreSQL-sourced data
        return 0.0


class CompetitiveAnalyzer:
    """Analyzes competitive landscape"""
    
    async def analyze_competitors(self) -> List[CompetitorInsight]:
        """Analyze competitive landscape (PostgreSQL integration pending)"""
        # TODO: Implement with actual competitive intelligence API calls
        # Store results in PostgreSQL via DatabaseService
        # For now, return empty list (placeholder)
        return []


# Example usage
async def main():
    """Test the business intelligence system"""
    logging.basicConfig(level=logging.INFO)
    
    bi_system = BusinessIntelligenceSystem()
    
    print("🔍 Collecting business intelligence data...")
    metrics = await bi_system.collect_all_metrics()
    
    print(f"✅ Collected {sum(len(m) for m in metrics.values())} metrics from {len(metrics)} sources")
    
    # Get dashboard data
    dashboard = await bi_system.get_dashboard_data()
    
    print("\n📊 BUSINESS INTELLIGENCE DASHBOARD")
    print("=" * 50)
    
    print(f"Data Quality Score: {dashboard['data_quality']['confidence_score']:.1%}")
    print(f"Performance Score: {dashboard['performance_summary'].get('performance_score', 'N/A')}")
    
    print(f"\n📈 Strategic Insights ({len(dashboard['strategic_insights'])} found):")
    for insight in dashboard['strategic_insights']:
        print(f"• {insight['title']}: {insight['description'][:100]}...")
    
    print(f"\n⚔️ Competitive Analysis ({len(dashboard['competitive_analysis'])} competitors):")
    for competitor in dashboard['competitive_analysis']:
        print(f"• {competitor['competitor_name']}: {competitor['content_frequency']} posts/week")
    
    print(f"\n📊 Metrics by Category:")
    for category, metrics_list in dashboard['metrics'].items():
        print(f"• {category}: {len(metrics_list)} metrics")


if __name__ == "__main__":
    asyncio.run(main())