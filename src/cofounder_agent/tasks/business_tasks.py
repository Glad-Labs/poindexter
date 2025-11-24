"""Business intelligence tasks - analytics and insights."""

from typing import Dict, Any
from src.cofounder_agent.tasks.base import PureTask, ExecutionContext
import logging

logger = logging.getLogger(__name__)


class FinancialAnalysisTask(PureTask):
    """
    Financial analysis: Calculate costs, ROI, and projections.
    
    Input:
        - workflow_type: str - Type of workflow analyzed
        - content_created: int - Number of pieces created
        - platforms: list - Social platforms used
        - time_period: str - Analysis period (monthly, quarterly, yearly)
    
    Output:
        - total_cost: float - Total cost for workflow
        - cost_breakdown: dict - Cost per component
        - roi: float - Return on investment estimate
        - projections: dict - Future cost projections
    """

    def __init__(self):
        super().__init__(
            name="financial_analysis",
            description="Analyze costs and calculate ROI",
            required_inputs=["workflow_type"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute financial analysis task."""
        from src.cofounder_agent.services.model_router import model_router
        
        workflow_type = input_data["workflow_type"]
        content_created = input_data.get("content_created", 1)
        platforms = input_data.get("platforms", [])
        time_period = input_data.get("time_period", "monthly")
        
        # Cost estimation
        cost_breakdown = {
            "llm_calls": content_created * 0.50,  # ~$0.50 per blog post
            "image_search": content_created * 0.10,  # ~$0.10 per content piece
            "social_distribution": len(platforms) * 0.05,  # ~$0.05 per platform
            "storage": 0.10,  # Monthly storage
            "api_calls": content_created * 0.15,  # ~$0.15 misc API calls
        }
        
        total_cost = sum(cost_breakdown.values())
        
        # ROI estimation (assumes traffic monetization)
        estimated_traffic = content_created * 500  # ~500 visitors per content
        estimated_revenue = estimated_traffic * 0.02  # 2% conversion at $10 avg
        roi = ((estimated_revenue - total_cost) / total_cost * 100) if total_cost > 0 else 0
        
        prompt = f"""Analyze financial impact of this content workflow:

Workflow Type: {workflow_type}
Content Pieces: {content_created}
Social Platforms: {len(platforms)}
Time Period: {time_period}

Costs:
- LLM Calls: ${cost_breakdown['llm_calls']:.2f}
- Images: ${cost_breakdown['image_search']:.2f}
- Social Distribution: ${cost_breakdown['social_distribution']:.2f}
- Storage: ${cost_breakdown['storage']:.2f}
- Other APIs: ${cost_breakdown['api_calls']:.2f}

Total Cost: ${total_cost:.2f}
Estimated Revenue: ${estimated_revenue:.2f}
Estimated ROI: {roi:.1f}%

Provide:
1. Cost optimization recommendations
2. Revenue increase opportunities
3. Break-even analysis
4. 3-month projection

Format as JSON with keys: recommendations, opportunities, breakeven_units, projection"""
        
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.3,
            max_tokens=800,
        )
        
        try:
            import json
            analysis = json.loads(response)
        except:
            analysis = {
                "recommendations": [],
                "opportunities": [],
                "projection": {},
            }
        
        return {
            "workflow_type": workflow_type,
            "total_cost": round(total_cost, 2),
            "cost_breakdown": {k: round(v, 2) for k, v in cost_breakdown.items()},
            "estimated_revenue": round(estimated_revenue, 2),
            "roi_percentage": round(roi, 1),
            "content_count": content_created,
            "recommendations": analysis.get("recommendations", []),
            "opportunities": analysis.get("opportunities", []),
            "breakeven_units": analysis.get("breakeven_units", 0),
        }


class MarketAnalysisTask(PureTask):
    """
    Market analysis: Analyze market trends and opportunities.
    
    Input:
        - topic: str - Market/topic to analyze
        - competitors: list - Competitor URLs (optional)
        - target_audience: str - Target market segment
    
    Output:
        - market_size: str - Estimated market size
        - trends: list - Current trends
        - gaps: list - Market gaps/opportunities
        - recommendations: list - Strategic recommendations
    """

    def __init__(self):
        super().__init__(
            name="market_analysis",
            description="Analyze market trends and competitive landscape",
            required_inputs=["topic"],
            timeout_seconds=120,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute market analysis task."""
        from src.cofounder_agent.services.model_router import model_router
        
        topic = input_data["topic"]
        competitors = input_data.get("competitors", [])
        target_audience = input_data.get("target_audience", "general")
        
        prompt = f"""Perform detailed market analysis for: {topic}

Target Audience: {target_audience}
Competitors: {', '.join(competitors) if competitors else 'None specified'}

Analyze:
1. Market size and growth rate
2. Current trends (last 12 months)
3. Competitor strategies and positioning
4. Market gaps and opportunities
5. Customer pain points
6. Recommended positioning strategy

Provide specific, data-driven insights.

Format as JSON with keys: market_size, growth_rate, trends, gaps, customer_insights, positioning"""
        
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.4,
            max_tokens=1200,
        )
        
        try:
            import json
            analysis = json.loads(response)
        except:
            analysis = {
                "market_size": "Unknown",
                "trends": [],
                "gaps": [],
            }
        
        return {
            "topic": topic,
            "market_size": analysis.get("market_size", "Unknown"),
            "growth_rate": analysis.get("growth_rate", "Unknown"),
            "trends": analysis.get("trends", []),
            "market_gaps": analysis.get("gaps", []),
            "customer_insights": analysis.get("customer_insights", {}),
            "positioning": analysis.get("positioning", ""),
            "competitors_analyzed": len(competitors),
            "target_audience": target_audience,
        }


class PerformanceReviewTask(PureTask):
    """
    Performance review: Analyze content and campaign performance.
    
    Input:
        - period: str - Review period (weekly, monthly, quarterly)
        - metrics: dict - Performance metrics (views, clicks, conversions)
    
    Output:
        - summary: str - Performance summary
        - insights: list - Key insights
        - improvements: list - Suggested improvements
        - trend: str - Performance trend (up, down, flat)
    """

    def __init__(self):
        super().__init__(
            name="performance_review",
            description="Review and analyze campaign performance metrics",
            required_inputs=["period"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute performance review task."""
        from src.cofounder_agent.services.model_router import model_router
        
        period = input_data["period"]
        metrics = input_data.get("metrics", {})
        
        # Format metrics for analysis
        metrics_str = "\n".join(
            f"- {k}: {v}" for k, v in metrics.items()
        ) if metrics else "No metrics provided"
        
        prompt = f"""Analyze performance for {period} period:

Metrics:
{metrics_str}

Provide:
1. Overall performance summary
2. Key insights (what worked, what didn't)
3. Recommended improvements
4. Performance trend analysis
5. Action items for next period

Format as JSON with keys: summary, insights, improvements, trend, trend_percentage, action_items"""
        
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.4,
            max_tokens=1000,
        )
        
        try:
            import json
            analysis = json.loads(response)
        except:
            analysis = {
                "summary": response,
                "insights": [],
                "improvements": [],
                "trend": "stable",
            }
        
        return {
            "period": period,
            "summary": analysis.get("summary", ""),
            "insights": analysis.get("insights", []),
            "improvements": analysis.get("improvements", []),
            "trend": analysis.get("trend", "stable"),
            "trend_percentage": analysis.get("trend_percentage", 0),
            "action_items": analysis.get("action_items", []),
            "metrics_reviewed": len(metrics),
        }
