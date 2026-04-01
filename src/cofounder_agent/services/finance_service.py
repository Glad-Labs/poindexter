"""
Finance Service — Mercury banking integration + business metrics.

Connects to Mercury API to:
- Track transactions and categorize spend
- Generate P&L reports
- Alert on unusual spending
- Calculate true business costs (infrastructure + electricity + cloud APIs)

Mercury API docs: https://docs.mercury.com/reference/api-overview

Usage:
    from services.finance_service import FinanceService
    fs = FinanceService(pool, settings_service)
    report = await fs.generate_monthly_pnl()
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Expense categories for auto-classification
EXPENSE_CATEGORIES = {
    "railway": "infrastructure",
    "vercel": "infrastructure",
    "anthropic": "ai_services",
    "openai": "ai_services",
    "google cloud": "ai_services",
    "grafana": "monitoring",
    "github": "development",
    "namecheap": "domain",
    "cloudflare": "domain",
    "electricity": "infrastructure",
    "ollama": "infrastructure",
}


class FinanceService:
    """Business finance tracking and reporting."""

    def __init__(self, pool=None, settings_service=None):
        self.pool = pool
        self.settings = settings_service

    async def _get_mercury_key(self) -> Optional[str]:
        """Get Mercury API key from settings."""
        if self.settings:
            return await self.settings.get("mercury_api_key")
        return None

    async def get_infrastructure_costs(self) -> dict:
        """Calculate current infrastructure costs from known sources."""
        costs = {
            "electricity_monthly": 0.0,
            "cloud_api_monthly": 0.0,
            "railway_monthly": 0.0,
            "total_monthly": 0.0,
        }

        # Electricity cost from GPU power tracking
        if self.settings:
            rate = float(await self.settings.get("electricity_rate_kwh") or "0.29")
            gpu_idle_watts = float(await self.settings.get("gpu_idle_watts") or "45")
            system_idle_watts = float(await self.settings.get("system_idle_watts") or "120")
            # Estimate: system runs 24/7 + GPU active ~4h/day at higher wattage
            gpu_active_watts = float(await self.settings.get("gpu_inference_watts") or "400")
            daily_kwh = (system_idle_watts * 24 + (gpu_active_watts - gpu_idle_watts) * 4) / 1000
            costs["electricity_monthly"] = round(daily_kwh * rate * 30, 2)

        # Cloud API spend from cost_logs
        if self.pool:
            try:
                row = await self.pool.fetchrow(
                    "SELECT COALESCE(SUM(cost_usd), 0) as total FROM cost_logs "
                    "WHERE created_at >= date_trunc('month', NOW())"
                )
                costs["cloud_api_monthly"] = round(float(row["total"]), 2) if row else 0.0
            except Exception as e:
                logger.warning("[FINANCE] Failed to query cloud API costs: %s", e)

        # Railway estimated (from plan)
        costs["railway_monthly"] = 5.0  # Hobby plan estimate

        costs["total_monthly"] = round(
            costs["electricity_monthly"] + costs["cloud_api_monthly"] + costs["railway_monthly"],
            2,
        )
        return costs

    async def generate_monthly_pnl(self) -> str:
        """Generate a monthly P&L summary."""
        costs = await self.get_infrastructure_costs()

        # Content metrics
        post_count = 0
        if self.pool:
            try:
                row = await self.pool.fetchrow(
                    "SELECT COUNT(*) as count FROM posts WHERE status = 'published' "
                    "AND published_at >= date_trunc('month', NOW())"
                )
                post_count = int(row["count"]) if row else 0
            except Exception as e:
                logger.warning("[FINANCE] Failed to query post count: %s", e)

        report = [
            f"=== Monthly P&L — {datetime.now(timezone.utc).strftime('%B %Y')} ===",
            "",
            "REVENUE:",
            "  AdSense: $0.00 (not yet configured)",
            "  Affiliates: $0.00 (not yet configured)",
            "  Subscriptions: $0.00 (not yet launched)",
            f"  Total Revenue: $0.00",
            "",
            "EXPENSES:",
            f"  Electricity (GPU+system): ${costs['electricity_monthly']:.2f}",
            f"  Cloud APIs: ${costs['cloud_api_monthly']:.2f}",
            f"  Railway hosting: ${costs['railway_monthly']:.2f}",
            f"  Total Expenses: ${costs['total_monthly']:.2f}",
            "",
            f"NET: -${costs['total_monthly']:.2f}",
            "",
            "PRODUCTION:",
            f"  Posts published this month: {post_count}",
            f"  Cost per post: ${costs['total_monthly'] / max(post_count, 1):.3f}",
        ]

        return "\n".join(report)

    async def categorize_transaction(self, description: str, amount: float) -> str:
        """Auto-categorize a transaction based on description keywords."""
        desc_lower = description.lower()
        for keyword, category in EXPENSE_CATEGORIES.items():
            if keyword in desc_lower:
                return category
        return "uncategorized"
