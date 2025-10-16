"""
Advanced Business Intelligence Dashboard
Comprehensive analytics, metrics, and insights for GLAD Labs
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
import json
import statistics

# Set up logging
logger = logging.getLogger(__name__)

@dataclass
class MetricData:
    """Individual metric data point"""
    name: str
    value: float
    unit: str
    timestamp: datetime
    trend: Optional[str] = None  # "up", "down", "stable"
    change_percentage: Optional[float] = None
    target_value: Optional[float] = None
    status: str = "normal"  # "normal", "warning", "critical"

@dataclass
class KPICard:
    """Key Performance Indicator card for dashboard"""
    title: str
    current_value: float
    previous_value: Optional[float]
    unit: str
    trend: str
    change_percentage: float
    status: str
    target: Optional[float] = None
    description: str = ""
    
class AdvancedBusinessDashboard:
    """Comprehensive business intelligence dashboard"""
    
    def __init__(self):
        self.metrics_history: Dict[str, List[MetricData]] = {}
        self.kpis: Dict[str, KPICard] = {}
        self.insights: List[Dict[str, Any]] = []
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
    async def collect_comprehensive_metrics(self) -> Dict[str, Any]:
        """Collect all business metrics from various sources"""
        try:
            # Simulate comprehensive data collection
            # In production, this would integrate with real data sources
            
            current_time = datetime.now()
            
            # Task Management Metrics
            task_metrics = await self._collect_task_metrics()
            
            # Content Performance Metrics  
            content_metrics = await self._collect_content_metrics()
            
            # Financial Metrics
            financial_metrics = await self._collect_financial_metrics()
            
            # System Performance Metrics
            system_metrics = await self._collect_system_metrics()
            
            # Customer & Market Metrics
            market_metrics = await self._collect_market_metrics()
            
            # Operational Efficiency Metrics
            operational_metrics = await self._collect_operational_metrics()
            
            comprehensive_metrics = {
                "timestamp": current_time.isoformat(),
                "task_management": task_metrics,
                "content_performance": content_metrics,
                "financial": financial_metrics,
                "system_performance": system_metrics,
                "market_data": market_metrics,
                "operational_efficiency": operational_metrics,
                "summary": await self._generate_executive_summary(
                    task_metrics, content_metrics, financial_metrics,
                    system_metrics, market_metrics, operational_metrics
                )
            }
            
            # Store metrics in history
            await self._store_metrics_history(comprehensive_metrics)
            
            # Update KPIs
            await self._update_kpis(comprehensive_metrics)
            
            # Generate insights
            await self._generate_business_insights(comprehensive_metrics)
            
            return comprehensive_metrics
            
        except Exception as e:
            self.logger.error(f"Error collecting comprehensive metrics: {e}")
            return {"error": str(e)}
    
    async def _collect_task_metrics(self) -> Dict[str, Any]:
        """Collect task management metrics"""
        # Simulate task data collection
        return {
            "total_tasks": 47,
            "completed_tasks": 32,
            "in_progress": 8,
            "pending": 7,
            "completion_rate": 0.68,
            "average_completion_time": 2.3,  # days
            "overdue_tasks": 3,
            "task_velocity": 12.5,  # tasks per week
            "quality_score": 4.2,   # out of 5
            "by_category": {
                "content_creation": 25,
                "business_analysis": 8,
                "system_maintenance": 6,
                "strategic_planning": 4,
                "marketing": 4
            },
            "by_priority": {
                "high": 12,
                "medium": 23, 
                "low": 12
            }
        }
    
    async def _collect_content_metrics(self) -> Dict[str, Any]:
        """Collect content performance metrics"""
        return {
            "total_content_pieces": 156,
            "published_this_month": 23,
            "average_engagement_rate": 0.045,
            "top_performing_topics": [
                {"topic": "AI Automation", "engagement": 0.078},
                {"topic": "Business Strategy", "engagement": 0.065},
                {"topic": "Content Marketing", "engagement": 0.052}
            ],
            "content_quality_score": 4.1,
            "seo_performance": {
                "average_rank": 15.2,
                "organic_traffic": 8540,
                "click_through_rate": 0.032,
                "conversion_rate": 0.028
            },
            "content_production_rate": 5.8,  # pieces per week
            "content_costs": {
                "total_monthly": 1200,
                "cost_per_piece": 52.17,
                "roi": 3.4
            }
        }
    
    async def _collect_financial_metrics(self) -> Dict[str, Any]:
        """Collect financial performance metrics"""
        return {
            "revenue": {
                "monthly_recurring": 8450,
                "one_time": 2300,
                "total_monthly": 10750,
                "growth_rate": 0.125,
                "churn_rate": 0.035
            },
            "costs": {
                "ai_services": 1200,
                "infrastructure": 450,
                "content_creation": 800,
                "marketing": 600,
                "total_monthly": 3050
            },
            "profitability": {
                "gross_margin": 0.716,
                "net_profit": 7700,
                "profit_margin": 0.716,
                "cash_flow": 6500
            },
            "customer_metrics": {
                "total_customers": 145,
                "new_customers": 12,
                "customer_ltv": 2400,
                "customer_acquisition_cost": 180,
                "ltv_cac_ratio": 13.33
            }
        }
    
    async def _collect_system_metrics(self) -> Dict[str, Any]:
        """Collect system performance metrics"""
        return {
            "uptime": 0.997,
            "response_time": 145,  # milliseconds
            "error_rate": 0.002,
            "throughput": 1250,  # requests per hour
            "ai_model_performance": {
                "accuracy": 0.94,
                "latency": 890,  # milliseconds
                "cost_per_request": 0.003,
                "success_rate": 0.982
            },
            "resource_utilization": {
                "cpu": 0.65,
                "memory": 0.73,
                "storage": 0.42,
                "bandwidth": 0.38
            },
            "security": {
                "incidents": 0,
                "threats_blocked": 23,
                "security_score": 0.96
            }
        }
    
    async def _collect_market_metrics(self) -> Dict[str, Any]:
        """Collect market and competitive data"""
        return {
            "market_position": {
                "market_share": 0.028,
                "competitive_rank": 15,
                "brand_awareness": 0.12
            },
            "market_trends": {
                "ai_automation_growth": 0.45,
                "content_marketing_demand": 0.28,
                "small_business_adoption": 0.35
            },
            "opportunities": {
                "enterprise_market": 0.8,
                "international_expansion": 0.6,
                "new_product_categories": 0.7
            },
            "competitive_analysis": {
                "pricing_advantage": 0.15,
                "feature_completeness": 0.82,
                "customer_satisfaction": 4.3
            }
        }
    
    async def _collect_operational_metrics(self) -> Dict[str, Any]:
        """Collect operational efficiency metrics"""
        return {
            "automation_rate": 0.78,
            "manual_intervention_required": 0.22,
            "process_efficiency": 0.85,
            "team_productivity": 0.91,
            "resource_optimization": {
                "ai_usage_efficiency": 0.88,
                "cost_per_output": 2.45,
                "time_to_value": 1.2  # days
            },
            "quality_metrics": {
                "output_quality": 4.2,
                "error_frequency": 0.015,
                "customer_satisfaction": 4.4
            }
        }
    
    async def _generate_executive_summary(self, *metrics) -> Dict[str, Any]:
        """Generate executive summary from all metrics"""
        task_metrics, content_metrics, financial_metrics, system_metrics, market_metrics, operational_metrics = metrics
        
        # Calculate key indicators
        revenue_growth = financial_metrics["revenue"]["growth_rate"]
        completion_rate = task_metrics["completion_rate"]
        system_health = system_metrics["uptime"]
        profit_margin = financial_metrics["profitability"]["profit_margin"]
        
        # Determine overall business health
        health_score = (completion_rate + system_health + profit_margin + operational_metrics["process_efficiency"]) / 4
        
        if health_score >= 0.8:
            health_status = "Excellent"
        elif health_score >= 0.7:
            health_status = "Good"
        elif health_score >= 0.6:
            health_status = "Fair"
        else:
            health_status = "Needs Attention"
        
        return {
            "business_health": health_status,
            "health_score": health_score,
            "key_highlights": [
                f"Revenue growing at {revenue_growth:.1%} monthly",
                f"Task completion rate: {completion_rate:.1%}",
                f"System uptime: {system_health:.1%}",
                f"Profit margin: {profit_margin:.1%}",
                f"Automation rate: {operational_metrics['automation_rate']:.1%}"
            ],
            "priority_areas": await self._identify_priority_areas(metrics),
            "growth_opportunities": [
                "Enterprise market expansion",
                "International scaling",
                "AI automation enhancement",
                "Content strategy optimization"
            ],
            "risk_factors": await self._identify_risk_factors(metrics)
        }
    
    async def _identify_priority_areas(self, metrics: Tuple) -> List[str]:
        """Identify areas requiring immediate attention"""
        task_metrics, content_metrics, financial_metrics, system_metrics, market_metrics, operational_metrics = metrics
        
        priority_areas = []
        
        # Check task completion rate
        if task_metrics["completion_rate"] < 0.7:
            priority_areas.append("Improve task completion rate")
        
        # Check overdue tasks
        if task_metrics["overdue_tasks"] > 5:
            priority_areas.append("Address overdue tasks")
        
        # Check profit margins
        if financial_metrics["profitability"]["profit_margin"] < 0.6:
            priority_areas.append("Optimize cost structure")
        
        # Check system performance
        if system_metrics["error_rate"] > 0.005:
            priority_areas.append("Improve system reliability")
        
        # Check content performance
        if content_metrics["average_engagement_rate"] < 0.04:
            priority_areas.append("Enhance content strategy")
        
        return priority_areas
    
    async def _identify_risk_factors(self, metrics: Tuple) -> List[str]:
        """Identify potential business risks"""
        task_metrics, content_metrics, financial_metrics, system_metrics, market_metrics, operational_metrics = metrics
        
        risk_factors = []
        
        # Financial risks
        if financial_metrics["revenue"]["churn_rate"] > 0.05:
            risk_factors.append("High customer churn rate")
        
        # Operational risks
        if operational_metrics["manual_intervention_required"] > 0.3:
            risk_factors.append("High manual intervention dependency")
        
        # System risks
        if system_metrics["uptime"] < 0.99:
            risk_factors.append("System reliability concerns")
        
        # Market risks
        if market_metrics["competitive_analysis"]["customer_satisfaction"] < 4.0:
            risk_factors.append("Customer satisfaction below target")
        
        return risk_factors
    
    async def _store_metrics_history(self, metrics: Dict[str, Any]):
        """Store metrics in historical data"""
        timestamp = datetime.fromisoformat(metrics["timestamp"])
        
        # Store key metrics for trend analysis
        key_metrics = {
            "revenue": metrics["financial"]["revenue"]["total_monthly"],
            "profit_margin": metrics["financial"]["profitability"]["profit_margin"],
            "task_completion_rate": metrics["task_management"]["completion_rate"],
            "system_uptime": metrics["system_performance"]["uptime"],
            "content_engagement": metrics["content_performance"]["average_engagement_rate"]
        }
        
        for metric_name, value in key_metrics.items():
            if metric_name not in self.metrics_history:
                self.metrics_history[metric_name] = []
            
            metric_data = MetricData(
                name=metric_name,
                value=value,
                unit=self._get_metric_unit(metric_name),
                timestamp=timestamp
            )
            
            self.metrics_history[metric_name].append(metric_data)
            
            # Keep only last 30 days of data
            cutoff_date = timestamp - timedelta(days=30)
            self.metrics_history[metric_name] = [
                m for m in self.metrics_history[metric_name] 
                if m.timestamp > cutoff_date
            ]
    
    def _get_metric_unit(self, metric_name: str) -> str:
        """Get unit for metric"""
        units = {
            "revenue": "$",
            "profit_margin": "%",
            "task_completion_rate": "%", 
            "system_uptime": "%",
            "content_engagement": "%"
        }
        return units.get(metric_name, "")
    
    async def _update_kpis(self, metrics: Dict[str, Any]):
        """Update KPI cards with latest data"""
        
        # Revenue KPI
        current_revenue = metrics["financial"]["revenue"]["total_monthly"]
        previous_revenue = current_revenue / (1 + metrics["financial"]["revenue"]["growth_rate"])
        
        self.kpis["revenue"] = KPICard(
            title="Monthly Revenue",
            current_value=current_revenue,
            previous_value=previous_revenue,
            unit="$",
            trend="up" if current_revenue > previous_revenue else "down",
            change_percentage=metrics["financial"]["revenue"]["growth_rate"] * 100,
            status="normal" if metrics["financial"]["revenue"]["growth_rate"] > 0 else "warning",
            target=12000,
            description="Total monthly recurring and one-time revenue"
        )
        
        # Task Completion KPI
        completion_rate = metrics["task_management"]["completion_rate"]
        self.kpis["task_completion"] = KPICard(
            title="Task Completion Rate",
            current_value=completion_rate * 100,
            previous_value=65.0,  # Simulated previous value
            unit="%",
            trend="up" if completion_rate > 0.65 else "down",
            change_percentage=((completion_rate - 0.65) / 0.65) * 100,
            status="normal" if completion_rate >= 0.75 else "warning",
            target=80,
            description="Percentage of tasks completed on time"
        )
        
        # System Uptime KPI
        uptime = metrics["system_performance"]["uptime"]
        self.kpis["system_uptime"] = KPICard(
            title="System Uptime",
            current_value=uptime * 100,
            previous_value=99.2,
            unit="%", 
            trend="up" if uptime > 0.992 else "down",
            change_percentage=((uptime - 0.992) / 0.992) * 100,
            status="normal" if uptime >= 0.99 else "critical",
            target=99.5,
            description="System availability and reliability"
        )
        
        # Content Engagement KPI  
        engagement = metrics["content_performance"]["average_engagement_rate"]
        self.kpis["content_engagement"] = KPICard(
            title="Content Engagement",
            current_value=engagement * 100,
            previous_value=4.2,
            unit="%",
            trend="up" if engagement > 0.042 else "down", 
            change_percentage=((engagement - 0.042) / 0.042) * 100,
            status="normal" if engagement >= 0.04 else "warning",
            target=5.0,
            description="Average engagement rate across all content"
        )
    
    async def _generate_business_insights(self, metrics: Dict[str, Any]):
        """Generate AI-powered business insights"""
        
        insights = []
        
        # Revenue insights
        revenue_growth = metrics["financial"]["revenue"]["growth_rate"]
        if revenue_growth > 0.1:
            insights.append({
                "type": "positive",
                "category": "Revenue",
                "title": "Strong Revenue Growth",
                "message": f"Revenue growing at {revenue_growth:.1%} monthly, exceeding industry average of 8%",
                "recommendation": "Consider scaling successful initiatives and exploring new market segments",
                "impact": "high",
                "confidence": 0.9
            })
        
        # Task management insights
        completion_rate = metrics["task_management"]["completion_rate"]
        if completion_rate < 0.7:
            insights.append({
                "type": "warning",
                "category": "Operations",
                "title": "Task Completion Below Target",
                "message": f"Current completion rate of {completion_rate:.1%} is below target of 75%",
                "recommendation": "Review task prioritization and resource allocation. Consider automating routine tasks",
                "impact": "medium",
                "confidence": 0.85
            })
        
        # Cost optimization insights
        profit_margin = metrics["financial"]["profitability"]["profit_margin"]
        if profit_margin > 0.7:
            insights.append({
                "type": "positive", 
                "category": "Profitability",
                "title": "Excellent Profit Margins",
                "message": f"Profit margin of {profit_margin:.1%} demonstrates strong cost control",
                "recommendation": "Maintain current efficiency while exploring growth investments",
                "impact": "high",
                "confidence": 0.95
            })
        
        # Content performance insights
        engagement = metrics["content_performance"]["average_engagement_rate"]
        if engagement > 0.04:
            insights.append({
                "type": "positive",
                "category": "Content",
                "title": "Above-Average Content Performance", 
                "message": f"Content engagement of {engagement:.1%} exceeds industry benchmark of 3.5%",
                "recommendation": "Scale high-performing content types and topics",
                "impact": "medium",
                "confidence": 0.8
            })
        
        self.insights = insights
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get complete dashboard data"""
        
        # Get latest metrics
        latest_metrics = await self.collect_comprehensive_metrics()
        
        # Get trend data
        trend_data = await self._get_trend_data()
        
        # Get predictions
        predictions = await self._generate_predictions()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "kpis": {name: asdict(kpi) for name, kpi in self.kpis.items()},
            "metrics": latest_metrics,
            "trends": trend_data,
            "insights": self.insights,
            "predictions": predictions,
            "alerts": await self._get_active_alerts()
        }
    
    async def _get_trend_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get trend data for charts"""
        trend_data = {}
        
        for metric_name, history in self.metrics_history.items():
            trend_data[metric_name] = [
                {
                    "timestamp": metric.timestamp.isoformat(),
                    "value": metric.value,
                    "unit": metric.unit
                }
                for metric in history[-30:]  # Last 30 data points
            ]
        
        return trend_data
    
    async def _generate_predictions(self) -> Dict[str, Any]:
        """Generate business predictions using trend analysis"""
        predictions = {}
        
        for metric_name, history in self.metrics_history.items():
            if len(history) >= 7:  # Need at least 7 data points
                values = [m.value for m in history[-7:]]
                
                # Simple linear trend prediction
                if len(values) > 1:
                    trend = (values[-1] - values[0]) / len(values)
                    predicted_value = values[-1] + trend
                    
                    predictions[metric_name] = {
                        "predicted_value": predicted_value,
                        "trend_direction": "up" if trend > 0 else "down",
                        "confidence": min(0.9, len(values) / 30),  # Higher confidence with more data
                        "horizon": "next_period"
                    }
        
        return predictions
    
    async def _get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get active business alerts"""
        # This would integrate with the notification system
        return [
            {
                "type": "warning",
                "message": "Task completion rate below target",
                "severity": "medium",
                "timestamp": datetime.now().isoformat()
            }
        ]