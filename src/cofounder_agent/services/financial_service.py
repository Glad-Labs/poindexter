"""
Financial Service - Unified service for financial analysis and ROI tracking

Consolidates FinancialAgent functionality (agents/financial_agent/) into a
composable, flat service module that integrates with the workflow engine.

This service provides:
- Cost tracking and ROI analysis
- Budget allocation
- Financial forecasting
- Cost optimization recommendations
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class FinancialService:
    """
    Financial analysis and cost tracking service.

    Provides methods for:
    - Tracking AI model costs
    - Calculating ROI for content generation
    - Budget forecasting
    - Cost optimization recommendations
    """

    def __init__(
        self,
        database_service: Optional[Any] = None,
        model_router: Optional[Any] = None,
    ):
        """
        Initialize financial service.

        Args:
            database_service: PostgreSQL database service
            model_router: Model router for cost tracking
        """
        self.database_service = database_service
        self.model_router = model_router
        logger.info("FinancialService initialized")

    async def analyze_content_cost(
        self,
        content_id: str,
        topic: str,
        model_selections: Optional[Dict[str, str]] = None,
        word_count: int = 1500,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Analyze financial cost of content generation.

        Args:
            content_id: ID of generated content
            topic: Content topic
            model_selections: Models used per phase
            word_count: Content word count
            **kwargs: Additional parameters

        Returns:
            Dictionary with cost analysis, ROI metrics, recommendations
        """
        try:
            from agents.financial_agent.agents.financial_agent import FinancialAgent

            financial_agent = FinancialAgent()
            analysis = await financial_agent.run(
                content_id=content_id,
                topic=topic,
                word_count=word_count,
                model_selections=model_selections or {},
            )

            logger.info(f"Financial analysis completed for content: {content_id}")

            return {
                "phase": "financial_analysis",
                "content_id": content_id,
                "analysis": analysis,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "financial_agent",
            }

        except Exception as e:
            logger.error(f"Financial analysis failed: {e}", exc_info=True)
            return {
                "phase": "financial_analysis",
                "error": str(e),
                "content_id": content_id,
            }

    async def calculate_roi(
        self,
        content_id: str,
        generation_cost: float,
        estimated_reach: int = 1000,
        conversion_rate: float = 0.02,
        revenue_per_conversion: float = 10.0,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Calculate ROI for content generation.

        Args:
            content_id: Content ID
            generation_cost: USD cost of generation
            estimated_reach: Estimated audience reach
            conversion_rate: Expected conversion rate (0-1)
            revenue_per_conversion: Revenue per conversion
            **kwargs: Additional parameters

        Returns:
            Dictionary with ROI calculations and metrics
        """
        try:
            expected_conversions = estimated_reach * conversion_rate
            expected_revenue = expected_conversions * revenue_per_conversion
            net_profit = expected_revenue - generation_cost
            roi = (net_profit / generation_cost * 100) if generation_cost > 0 else 0

            logger.info(f"ROI calculated for content {content_id}: {roi:.1f}%")

            return {
                "content_id": content_id,
                "generation_cost": generation_cost,
                "estimated_reach": estimated_reach,
                "expected_conversions": expected_conversions,
                "expected_revenue": expected_revenue,
                "net_profit": net_profit,
                "roi_percentage": roi,
                "payback_period_days": (generation_cost / (expected_revenue / 365)) if expected_revenue > 0 else float("inf"),
            }

        except Exception as e:
            logger.error(f"ROI calculation failed: {e}", exc_info=True)
            return {"error": str(e), "content_id": content_id}

    async def forecast_budget(
        self,
        monthly_content_target: int,
        avg_cost_per_piece: float,
        growth_rate: float = 0.1,
        months_ahead: int = 12,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Forecast budget requirements.

        Args:
            monthly_content_target: Target pieces per month
            avg_cost_per_piece: Average generation cost per piece
            growth_rate: Expected monthly growth rate
            months_ahead: Number of months to forecast
            **kwargs: Additional parameters

        Returns:
            Dictionary with budget forecast and projections
        """
        try:
            forecast = {}
            cumulative_cost = 0

            for month in range(1, months_ahead + 1):
                monthly_target = monthly_content_target * ((1 + growth_rate) ** (month - 1))
                monthly_cost = monthly_target * avg_cost_per_piece
                cumulative_cost += monthly_cost

                forecast[f"month_{month}"] = {
                    "projected_pieces": int(monthly_target),
                    "monthly_cost": monthly_cost,
                    "cumulative_cost": cumulative_cost,
                }

            logger.info(f"Budget forecast completed for {months_ahead} months")

            return {
                "forecast_months": months_ahead,
                "monthly_content_target": monthly_content_target,
                "avg_cost_per_piece": avg_cost_per_piece,
                "growth_rate": growth_rate,
                "total_projected_cost": cumulative_cost,
                "monthly_forecasts": forecast,
            }

        except Exception as e:
            logger.error(f"Budget forecast failed: {e}", exc_info=True)
            return {"error": str(e)}

    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata for discovery"""
        return {
            "name": "financial_service",
            "category": "financial",
            "description": "Financial analysis, cost tracking, and ROI calculation service",
            "capabilities": [
                "cost_analysis",
                "roi_calculation",
                "budget_forecasting",
                "cost_optimization",
                "financial_reporting",
            ],
            "version": "1.0",
        }
