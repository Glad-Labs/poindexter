"""
Financial Agent Cost Tracking Service

Monitors AI API costs, analyzes spending trends, triggers budget alerts,
and provides cost optimization recommendations.

Features:
- Real-time cost monitoring from /metrics/costs endpoint
- Budget threshold alerts (75%, 90%, 100%)
- Trend analysis and forecasting
- Cost optimization recommendations
- Monthly budget tracking ($100/month limit)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)


class BudgetAlertLevel(str, Enum):
    """Budget alert severity levels."""

    INFO = "info"  # < 75% of budget
    WARNING = "warning"  # 75-90% of budget
    URGENT = "urgent"  # 90-100% of budget
    CRITICAL = "critical"  # > 100% of budget


@dataclass
class BudgetAlert:
    """Budget alert data structure."""

    level: BudgetAlertLevel
    percentage: float
    amount_spent: float
    amount_remaining: float
    threshold: float
    message: str
    timestamp: datetime
    recommendations: List[str]


class CostTrackingService:
    """
    Autonomous cost tracking service for Financial Agent.

    Monitors AI API spending, analyzes trends, triggers alerts,
    and provides optimization recommendations.
    """

    # Monthly budget limit
    MONTHLY_BUDGET = 100.0  # $100/month

    # Alert thresholds
    ALERT_THRESHOLDS = {
        BudgetAlertLevel.WARNING: 0.75,  # 75% of budget
        BudgetAlertLevel.URGENT: 0.90,  # 90% of budget
        BudgetAlertLevel.CRITICAL: 1.00,  # 100% of budget
    }

    def __init__(
        self,
        cofounder_api_url: str = "http://localhost:8000",
        pubsub_client=None,
        enable_notifications: bool = True,
    ):
        """
        Initialize cost tracking service.

        Args:
            cofounder_api_url: Base URL for Co-Founder Agent API
            pubsub_client: Optional Pub/Sub client for alerts
            enable_notifications: Whether to send Pub/Sub notifications
        """
        self.api_url = cofounder_api_url
        self.pubsub_client = pubsub_client
        self.enable_notifications = enable_notifications

        # Track monthly spending
        self.monthly_spent = 0.0
        self.current_month = datetime.now().month
        self.current_year = datetime.now().year

        # Alert history (prevent duplicate alerts)
        self.alert_history: List[BudgetAlert] = []
        self.last_alert_level: Optional[BudgetAlertLevel] = None

        logger.info(
            "Cost tracking service initialized",
            monthly_budget=self.MONTHLY_BUDGET,
            api_url=self.api_url,
        )

    async def fetch_cost_metrics(self) -> Optional[Dict[str, Any]]:
        """
        Fetch current cost metrics from Co-Founder Agent.

        Returns:
            Cost metrics dictionary or None if fetch fails
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.api_url}/metrics/costs", timeout=10.0)
                response.raise_for_status()
                data = response.json()

                logger.debug("Cost metrics fetched", status_code=response.status_code)

                return data.get("costs", {})

        except httpx.HTTPError as e:
            logger.error("Failed to fetch cost metrics", error=str(e), api_url=self.api_url)
            return None
        except Exception as e:
            logger.error("Unexpected error fetching cost metrics", error=str(e))
            return None

    def check_monthly_reset(self):
        """Check if we've entered a new month and reset counters."""
        now = datetime.now()

        if now.month != self.current_month or now.year != self.current_year:
            logger.info(
                "New billing period started, resetting monthly counters",
                previous_month=self.current_month,
                previous_year=self.current_year,
                previous_spent=self.monthly_spent,
                new_month=now.month,
                new_year=now.year,
            )

            # Reset counters
            self.monthly_spent = 0.0
            self.current_month = now.month
            self.current_year = now.year
            self.alert_history.clear()
            self.last_alert_level = None

    async def analyze_costs(self) -> Dict[str, Any]:
        """
        Analyze current costs and generate recommendations.

        Returns:
            Analysis report with metrics, alerts, and recommendations
        """
        self.check_monthly_reset()

        metrics = await self.fetch_cost_metrics()

        if not metrics:
            return {
                "status": "error",
                "message": "Failed to fetch cost metrics",
                "timestamp": datetime.now().isoformat(),
            }

        # Extract key metrics
        budget_data = metrics.get("budget", {})
        ai_cache = metrics.get("ai_cache", {})
        model_router = metrics.get("model_router", {})
        summary = metrics.get("summary", {})

        # Calculate monthly spending (accumulate from budget.current_spent)
        current_spent = budget_data.get("current_spent", 0.0)
        self.monthly_spent += current_spent

        # Calculate budget status
        budget_percentage = (self.monthly_spent / self.MONTHLY_BUDGET) * 100
        remaining = self.MONTHLY_BUDGET - self.monthly_spent

        # Check for budget alerts
        alert = self._check_budget_thresholds(
            spent=self.monthly_spent, budget=self.MONTHLY_BUDGET, percentage=budget_percentage
        )

        # Generate recommendations
        recommendations = self._generate_recommendations(
            metrics=metrics,
            budget_percentage=budget_percentage,
            alert_level=alert.level if alert else BudgetAlertLevel.INFO,
        )

        # Publish alert if needed
        if alert and self.enable_notifications:
            await self._publish_alert(alert)

        analysis = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "monthly_budget": {
                "limit": self.MONTHLY_BUDGET,
                "spent": round(self.monthly_spent, 2),
                "remaining": round(remaining, 2),
                "percentage_used": round(budget_percentage, 1),
                "period": f"{self.current_year}-{self.current_month:02d}",
            },
            "optimization_performance": {
                "ai_cache_hit_rate": ai_cache.get("hit_rate_percentage", 0) if ai_cache else 0,
                "ai_cache_savings": ai_cache.get("estimated_savings_usd", 0) if ai_cache else 0,
                "model_router_savings": (
                    model_router.get("estimated_savings_usd", 0) if model_router else 0
                ),
                "budget_model_usage": (
                    model_router.get("budget_model_percentage", 0) if model_router else 0
                ),
                "total_savings": summary.get("total_estimated_savings_usd", 0),
            },
            "alert": (
                {
                    "level": alert.level.value if alert else "info",
                    "message": alert.message if alert else "Budget healthy",
                    "recommendations": alert.recommendations if alert else recommendations,
                }
                if alert
                else None
            ),
            "recommendations": recommendations,
            "projections": self._calculate_projections(self.monthly_spent),
        }

        logger.info(
            "Cost analysis complete",
            monthly_spent=round(self.monthly_spent, 2),
            budget_percentage=round(budget_percentage, 1),
            alert_level=alert.level.value if alert else "none",
        )

        return analysis

    def _check_budget_thresholds(
        self, spent: float, budget: float, percentage: float
    ) -> Optional[BudgetAlert]:
        """
        Check if budget thresholds exceeded and create alert.

        Args:
            spent: Amount spent
            budget: Budget limit
            percentage: Percentage of budget used

        Returns:
            BudgetAlert if threshold exceeded, None otherwise
        """
        # Determine alert level
        alert_level = None
        threshold = 0.0

        if percentage >= 100:
            alert_level = BudgetAlertLevel.CRITICAL
            threshold = self.ALERT_THRESHOLDS[BudgetAlertLevel.CRITICAL]
        elif percentage >= 90:
            alert_level = BudgetAlertLevel.URGENT
            threshold = self.ALERT_THRESHOLDS[BudgetAlertLevel.URGENT]
        elif percentage >= 75:
            alert_level = BudgetAlertLevel.WARNING
            threshold = self.ALERT_THRESHOLDS[BudgetAlertLevel.WARNING]

        # Don't create alert if no threshold exceeded
        if not alert_level:
            return None

        # Don't create alert if already notified at this level or higher
        if self.last_alert_level and alert_level.value <= self.last_alert_level.value:
            return None

        # Create alert
        remaining = budget - spent

        recommendations = []
        if alert_level == BudgetAlertLevel.CRITICAL:
            recommendations = [
                "🚨 IMMEDIATE ACTION REQUIRED: Monthly budget exceeded",
                "Disable non-critical AI features immediately",
                "Review all pending tasks and defer non-urgent work",
                "Consider increasing monthly budget or reducing usage",
            ]
        elif alert_level == BudgetAlertLevel.URGENT:
            recommendations = [
                "⚠️ URGENT: Approaching monthly budget limit",
                "Prioritize only critical AI tasks",
                "Increase AI cache hit rate by reusing queries",
                "Use budget models (GPT-3.5) for all non-critical tasks",
            ]
        elif alert_level == BudgetAlertLevel.WARNING:
            recommendations = [
                "⚡ WARNING: 75% of monthly budget consumed",
                "Monitor spending closely for remainder of month",
                "Optimize query patterns to maximize cache hits",
                "Review task complexity routing decisions",
            ]

        alert = BudgetAlert(
            level=alert_level,
            percentage=percentage,
            amount_spent=spent,
            amount_remaining=remaining,
            threshold=threshold,
            message=f"{alert_level.value.upper()}: {percentage:.1f}% of monthly budget used (${spent:.2f}/${budget:.2f})",
            timestamp=datetime.now(),
            recommendations=recommendations,
        )

        # Update tracking
        self.last_alert_level = alert_level
        self.alert_history.append(alert)

        return alert

    def _generate_recommendations(
        self, metrics: Dict[str, Any], budget_percentage: float, alert_level: BudgetAlertLevel
    ) -> List[str]:
        """
        Generate cost optimization recommendations based on metrics.

        Args:
            metrics: Current cost metrics
            budget_percentage: Percentage of budget used
            alert_level: Current alert level

        Returns:
            List of recommendation strings
        """
        recommendations = []

        ai_cache = metrics.get("ai_cache", {})
        model_router = metrics.get("model_router", {})

        # AI Cache recommendations
        if ai_cache:
            hit_rate = ai_cache.get("hit_rate_percentage", 0)
            if hit_rate < 15:
                recommendations.append(
                    f"💡 AI Cache hit rate is low ({hit_rate:.1f}%). "
                    "Consider enabling longer TTL or pre-warming common queries."
                )
            elif hit_rate > 30:
                recommendations.append(
                    f"✅ AI Cache performing well ({hit_rate:.1f}% hit rate). "
                    f"Saving ${ai_cache.get('estimated_savings_usd', 0):.2f}."
                )

        # Model Router recommendations
        if model_router:
            budget_usage = model_router.get("budget_model_percentage", 0)
            if budget_usage < 50 and budget_percentage > 50:
                recommendations.append(
                    f"💡 Only {budget_usage:.1f}% of requests using budget models. "
                    "Review task complexity routing to use cheaper models."
                )
            elif budget_usage > 70:
                recommendations.append(
                    f"✅ Smart routing optimized ({budget_usage:.1f}% budget models). "
                    f"Saving ${model_router.get('estimated_savings_usd', 0):.2f}."
                )

        # Budget-specific recommendations
        if budget_percentage > 50:
            days_in_month = 30
            current_day = datetime.now().day
            daily_rate = self.monthly_spent / current_day if current_day > 0 else 0
            projected = daily_rate * days_in_month

            if projected > self.MONTHLY_BUDGET:
                recommendations.append(
                    f"📊 Current spending rate projects ${projected:.2f} by month end. "
                    "Consider reducing usage to stay within budget."
                )

        return recommendations

    def _calculate_projections(self, current_spent: float) -> Dict[str, float]:
        """
        Calculate end-of-month spending projections.

        Args:
            current_spent: Amount spent so far this month

        Returns:
            Projection dictionary
        """
        now = datetime.now()
        days_elapsed = now.day

        # Avoid division by zero
        if days_elapsed == 0:
            return {"projected_monthly_total": 0.0, "projected_overage": 0.0, "daily_rate": 0.0}

        # Calculate daily burn rate
        daily_rate = current_spent / days_elapsed

        # Project to end of month (assume 30 days)
        days_in_month = 30
        projected_total = daily_rate * days_in_month

        # Calculate potential overage
        projected_overage = max(0, projected_total - self.MONTHLY_BUDGET)

        return {
            "projected_monthly_total": round(projected_total, 2),
            "projected_overage": round(projected_overage, 2),
            "daily_rate": round(daily_rate, 2),
            "days_elapsed": days_elapsed,
            "days_remaining": days_in_month - days_elapsed,
        }

    async def _publish_alert(self, alert: BudgetAlert):
        """
        Publish budget alert to Pub/Sub topic.

        Args:
            alert: Budget alert to publish
        """
        if not self.pubsub_client:
            logger.warning("No Pub/Sub client available, skipping alert notification")
            return

        try:
            message = {
                "type": "budget_alert",
                "level": alert.level.value,
                "percentage": alert.percentage,
                "amount_spent": alert.amount_spent,
                "amount_remaining": alert.amount_remaining,
                "message": alert.message,
                "recommendations": alert.recommendations,
                "timestamp": alert.timestamp.isoformat(),
            }

            # Publish to 'financial-alerts' topic
            await self.pubsub_client.publish(topic="financial-alerts", message=message)

            logger.info(
                "Budget alert published", level=alert.level.value, percentage=alert.percentage
            )

        except Exception as e:
            logger.error(
                "Failed to publish budget alert", error=str(e), alert_level=alert.level.value
            )

    def get_monthly_summary(self) -> Dict[str, Any]:
        """
        Get summary of monthly spending and alerts.

        Returns:
            Monthly summary dictionary
        """
        return {
            "period": f"{self.current_year}-{self.current_month:02d}",
            "budget": self.MONTHLY_BUDGET,
            "spent": round(self.monthly_spent, 2),
            "remaining": round(self.MONTHLY_BUDGET - self.monthly_spent, 2),
            "percentage_used": round((self.monthly_spent / self.MONTHLY_BUDGET) * 100, 1),
            "alerts_triggered": len(self.alert_history),
            "last_alert_level": self.last_alert_level.value if self.last_alert_level else None,
            "projections": self._calculate_projections(self.monthly_spent),
        }


# Initialize function for easy integration
def initialize_cost_tracking(
    cofounder_api_url: str = "http://localhost:8000",
    pubsub_client=None,
    enable_notifications: bool = True,
) -> CostTrackingService:
    """
    Initialize cost tracking service.

    Args:
        cofounder_api_url: Base URL for Co-Founder Agent API
        pubsub_client: Optional Pub/Sub client
        enable_notifications: Whether to enable alerts

    Returns:
        Initialized CostTrackingService
    """
    return CostTrackingService(
        cofounder_api_url=cofounder_api_url,
        pubsub_client=pubsub_client,
        enable_notifications=enable_notifications,
    )
