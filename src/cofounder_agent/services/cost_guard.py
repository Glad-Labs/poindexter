"""
Cost Guard — hard spending limits for cloud API calls.

Checks daily and monthly budgets before allowing any cloud LLM call.
Ollama (local) calls always pass. Cloud calls are blocked when limits
are reached, with fallback to Ollama.

Settings (from app_settings):
  daily_spend_limit = 2.00 (USD)
  monthly_spend_limit = 10.00 (USD)
  max_tokens_per_request = 4000
  max_tokens_per_task = 16000
  cost_alert_threshold_pct = 80

Usage:
    from services.cost_guard import CostGuard
    guard = CostGuard(pool)
    allowed, reason = await guard.check_budget("anthropic")
    if not allowed:
        # Fall back to Ollama or skip
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Approximate cost per 1K tokens by provider (USD)
# These are conservative estimates — actual costs vary by model
TOKEN_COSTS_PER_1K = {
    "anthropic": {"input": 0.00025, "output": 0.00125},   # Haiku
    "anthropic_sonnet": {"input": 0.003, "output": 0.015}, # Sonnet
    "anthropic_opus": {"input": 0.015, "output": 0.075},   # Opus
    "openai": {"input": 0.0005, "output": 0.0015},         # GPT-4o-mini
    "google": {"input": 0.0001, "output": 0.0004},           # Gemini Flash
    "ollama": {"input": 0.0, "output": 0.0},               # Local — always free
}


class CostGuard:
    """Enforces spending limits on cloud API calls."""

    def __init__(self, pool=None, settings_service=None):
        self.pool = pool
        self.settings = settings_service

    async def _get_limit(self, key: str, default: float) -> float:
        """Get a limit from settings or return default."""
        if self.settings:
            val = await self.settings.get(key)
            if val:
                try:
                    return float(val)
                except ValueError:
                    pass
        return default

    async def get_daily_spend(self) -> float:
        """Get today's cloud API spend from cost_logs."""
        if not self.pool:
            return 0.0
        try:
            row = await self.pool.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total "
                "FROM cost_logs "
                "WHERE created_at >= date_trunc('day', NOW())"
            )
            return float(row["total"]) if row else 0.0
        except Exception as e:
            logger.warning("[COST_GUARD] Failed to query daily spend: %s", e)
            return 0.0

    async def get_monthly_spend(self) -> float:
        """Get this month's cloud API spend from cost_logs."""
        if not self.pool:
            return 0.0
        try:
            row = await self.pool.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total "
                "FROM cost_logs "
                "WHERE created_at >= date_trunc('month', NOW())"
            )
            return float(row["total"]) if row else 0.0
        except Exception as e:
            logger.warning("[COST_GUARD] Failed to query monthly spend: %s", e)
            return 0.0

    async def check_budget(self, provider: str) -> Tuple[bool, str]:
        """
        Check if a cloud API call is within budget.

        Args:
            provider: The LLM provider ("anthropic", "openai", "google", "ollama")

        Returns:
            (allowed, reason) — True if call is allowed, False with explanation if blocked
        """
        # Ollama is always free — never block
        if provider == "ollama" or provider.startswith("ollama"):
            return True, "local"

        # Google: only Gemini Flash is free — paid models must be budget-checked
        # The $300 Gemini incident was caused by this blanket "free_tier" bypass
        if provider == "google":
            # Still check budget limits for Google — flash is cheap but not always free
            pass  # Fall through to budget checks below

        daily_limit = await self._get_limit("daily_spend_limit", 2.0)
        monthly_limit = await self._get_limit("monthly_spend_limit", 10.0)
        alert_pct = await self._get_limit("cost_alert_threshold_pct", 80.0)

        daily_spend = await self.get_daily_spend()
        monthly_spend = await self.get_monthly_spend()

        # Hard block: monthly limit
        if monthly_spend >= monthly_limit:
            logger.critical(
                "[COST_GUARD] Monthly limit reached: $%.2f / $%.2f — blocking cloud calls",
                monthly_spend, monthly_limit,
            )
            return False, f"Monthly spend limit reached (${monthly_spend:.2f} / ${monthly_limit:.2f})"

        # Hard block: daily limit
        if daily_spend >= daily_limit:
            logger.warning(
                "[COST_GUARD] Daily limit reached: $%.2f / $%.2f — blocking cloud calls",
                daily_spend, daily_limit,
            )
            return False, f"Daily spend limit reached (${daily_spend:.2f} / ${daily_limit:.2f})"

        # Alert: approaching daily limit
        if daily_spend >= daily_limit * (alert_pct / 100):
            logger.warning(
                "[COST_GUARD] Approaching daily limit: $%.2f / $%.2f (%.0f%%)",
                daily_spend, daily_limit, (daily_spend / daily_limit * 100),
            )

        return True, "within_budget"

    async def estimate_cost(
        self, provider: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate cost for a request before making it."""
        costs = TOKEN_COSTS_PER_1K.get(provider, TOKEN_COSTS_PER_1K.get("anthropic", {}))
        input_cost = (input_tokens / 1000) * costs.get("input", 0)
        output_cost = (output_tokens / 1000) * costs.get("output", 0)
        return input_cost + output_cost

    async def get_budget_status(self) -> dict:
        """Get current budget status for dashboard/API."""
        daily_limit = await self._get_limit("daily_spend_limit", 2.0)
        monthly_limit = await self._get_limit("monthly_spend_limit", 10.0)
        daily_spend = await self.get_daily_spend()
        monthly_spend = await self.get_monthly_spend()

        return {
            "daily": {
                "spent": round(daily_spend, 4),
                "limit": daily_limit,
                "remaining": round(max(0, daily_limit - daily_spend), 4),
                "pct_used": round(daily_spend / daily_limit * 100, 1) if daily_limit > 0 else 0,
            },
            "monthly": {
                "spent": round(monthly_spend, 4),
                "limit": monthly_limit,
                "remaining": round(max(0, monthly_limit - monthly_spend), 4),
                "pct_used": round(monthly_spend / monthly_limit * 100, 1) if monthly_limit > 0 else 0,
            },
        }
