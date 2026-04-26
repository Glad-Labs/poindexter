"""Cost Guard — pre-call budget enforcement for LLM provider plugins.

Reinstated for the OpenAICompatProvider plugin (Glad-Labs/poindexter#132).
Earlier deleted in commit 5eb26b51 along with a 22K-line dead-code sweep
because no live call site invoked it; the new ``OpenAICompatProvider``
gives us a real consumer again — every cloud-OAI call routes through
``CostGuard.preflight()`` before the SDK fires, and ``record()`` after.

Design:

- **Local backends** (``localhost``, ``127.0.0.1``, ``host.docker.internal``)
  short-circuit to "allowed, $0" — the guard contract is uniform across
  providers but the budget impact for self-hosted vLLM / llama.cpp /
  LocalAI is zero dollars.
- **Cloud backends** estimate cost from a per-model rate table (or the
  caller's pre-computed estimate) and raise :class:`CostGuardExhausted`
  if a request would push the running daily/monthly spend over the limit.
- **Post-call** :meth:`record` writes the actual cost to ``cost_logs``
  using the existing schema. The dispatcher can also feed cost back in
  if the provider returned a billable value in its response.

The guard MUST NOT silently fall back to a different provider on
exhaustion — see ``feedback_no_silent_defaults`` and the LLM-provider
policy. Callers catch :class:`CostGuardExhausted` and decide what to do
(usually: surface the error and stop).

Settings (read from app_settings via ``SiteConfig``):

- ``daily_spend_limit_usd`` (default ``2.00``)
- ``monthly_spend_limit_usd`` (default ``100.00``)
- ``cost_alert_threshold_pct`` (default ``80.0``)

Per-model rate table lives in ``services.model_constants.MODEL_COSTS``
already; this module looks up by model name and falls back to a
conservative default (GPT-4o-mini-style $0.0005 input / $0.0015 output
per 1K tokens) if the model is unknown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Conservative cost-per-1K-tokens fallback for cloud models we don't have
# a rate row for. Pinned to GPT-4o-mini-equivalent so an unrecognized
# OpenRouter / Together model doesn't slip through under-budgeted.
_FALLBACK_RATE_PER_1K = {"input": 0.0005, "output": 0.0015}


# Local backends report zero dollars regardless of model. Match by host
# substring rather than full equality so ``http://host.docker.internal:8080/v1``
# and ``http://127.0.0.1:11434`` both resolve correctly.
_LOCAL_HOST_HINTS = (
    "localhost",
    "127.0.0.1",
    "host.docker.internal",
    "0.0.0.0",
)


class CostGuardExhausted(RuntimeError):
    """Raised when a pre-call budget check fails.

    Carries the reason + the budget snapshot so log/alert sites can
    surface useful context without re-querying. Callers MUST NOT catch
    this and silently retry against a different paid provider — fall
    back to a free local provider explicitly via the router policy, or
    surface the error to the operator.
    """

    def __init__(
        self,
        reason: str,
        *,
        scope: str = "daily",
        spent_usd: float = 0.0,
        limit_usd: float = 0.0,
    ):
        super().__init__(reason)
        self.reason = reason
        self.scope = scope
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd


@dataclass
class CostEstimate:
    """Pre-call cost estimate. ``is_local=True`` skips budget enforcement."""

    estimated_usd: float
    is_local: bool
    model: str
    provider: str


def is_local_base_url(base_url: str | None) -> bool:
    """Return True when ``base_url`` points at a self-hosted backend.

    Self-hosted = zero dollar cost; the guard still wraps the call so
    the contract is uniform but never blocks.
    """
    if not base_url:
        return False
    lower = base_url.lower()
    return any(hint in lower for hint in _LOCAL_HOST_HINTS)


class CostGuard:
    """Daily/monthly spend enforcement for LLM provider plugins.

    Constructed once per worker (or once per request — both are fine,
    state lives in the DB). The guard owns no in-memory budget state of
    its own; every preflight() does a fresh DB read so multiple workers
    can't race past the cap by holding stale local counters.
    """

    def __init__(
        self,
        *,
        site_config: Any | None = None,
        pool: Any | None = None,
    ):
        self._site_config = site_config
        self._pool = pool

    # ------------------------------------------------------------------
    # Limit lookups
    # ------------------------------------------------------------------

    def _limit(self, key: str, default: float) -> float:
        """Read a numeric limit from site_config; fall back to ``default``.

        Numeric coercion is lenient — a malformed app_settings row falls
        through to the default rather than crashing the call path.
        """
        if self._site_config is None:
            return default
        try:
            raw = self._site_config.get(key, str(default))
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("[COST_GUARD] non-numeric setting %r — using default %s", key, default)
            return default

    # ------------------------------------------------------------------
    # Spend lookups
    # ------------------------------------------------------------------

    async def _sum_cost(self, window_sql: str) -> float:
        """Sum ``cost_usd`` from cost_logs over the given SQL window.

        ``window_sql`` is a fragment such as ``"date_trunc('day', NOW())"``
        — substituted directly into the WHERE clause. Filters out the
        ``electricity`` and ``ollama`` providers because those entries
        track home-power draw, not cloud spend, and would falsely trip
        the guard.
        """
        if self._pool is None:
            return 0.0
        try:
            row = await self._pool.fetchrow(
                f"""
                SELECT COALESCE(SUM(cost_usd), 0.0) AS total
                FROM cost_logs
                WHERE created_at >= {window_sql}
                  AND provider NOT IN ('electricity', 'ollama')
                """,
            )
            return float(row["total"]) if row else 0.0
        except Exception as e:
            logger.warning("[COST_GUARD] cost_logs query failed: %s", e)
            return 0.0

    async def get_daily_spend(self) -> float:
        return await self._sum_cost("date_trunc('day', NOW())")

    async def get_monthly_spend(self) -> float:
        return await self._sum_cost("date_trunc('month', NOW())")

    # ------------------------------------------------------------------
    # Estimation
    # ------------------------------------------------------------------

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        base_url: str | None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        rate_table: dict[str, dict[str, float]] | None = None,
    ) -> CostEstimate:
        """Estimate the dollar cost of an upcoming call.

        Local backends always cost $0 regardless of model. Cloud backends
        look up the model in ``rate_table`` (caller-supplied, falls back
        to ``_FALLBACK_RATE_PER_1K`` if missing) and multiply by the
        token estimates. Embedding calls usually pass
        ``completion_tokens=0`` — the input-side rate covers the
        embedding cost.
        """
        if is_local_base_url(base_url):
            return CostEstimate(
                estimated_usd=0.0, is_local=True, model=model, provider=provider,
            )

        rates = (rate_table or {}).get(model) or _FALLBACK_RATE_PER_1K
        in_cost = (prompt_tokens / 1000.0) * float(rates.get("input", 0.0))
        out_cost = (completion_tokens / 1000.0) * float(rates.get("output", 0.0))
        return CostEstimate(
            estimated_usd=in_cost + out_cost,
            is_local=False,
            model=model,
            provider=provider,
        )

    # ------------------------------------------------------------------
    # Preflight + record
    # ------------------------------------------------------------------

    async def preflight(self, estimate: CostEstimate) -> None:
        """Raise :class:`CostGuardExhausted` if the call would blow the budget.

        Local-backend estimates short-circuit immediately. Cloud
        estimates compare ``current_spend + estimate.estimated_usd``
        against the daily and monthly limits.
        """
        if estimate.is_local:
            return

        daily_limit = self._limit("daily_spend_limit_usd", 2.0)
        monthly_limit = self._limit("monthly_spend_limit_usd", 100.0)

        daily = await self.get_daily_spend()
        monthly = await self.get_monthly_spend()

        if monthly + estimate.estimated_usd > monthly_limit:
            raise CostGuardExhausted(
                f"Monthly spend cap reached: ${monthly:.4f} + "
                f"${estimate.estimated_usd:.4f} > ${monthly_limit:.2f}",
                scope="monthly",
                spent_usd=monthly,
                limit_usd=monthly_limit,
            )

        if daily + estimate.estimated_usd > daily_limit:
            raise CostGuardExhausted(
                f"Daily spend cap reached: ${daily:.4f} + "
                f"${estimate.estimated_usd:.4f} > ${daily_limit:.2f}",
                scope="daily",
                spent_usd=daily,
                limit_usd=daily_limit,
            )

        # Soft alert path — log only, don't block.
        alert_pct = self._limit("cost_alert_threshold_pct", 80.0) / 100.0
        if daily_limit > 0 and (daily + estimate.estimated_usd) >= daily_limit * alert_pct:
            logger.warning(
                "[COST_GUARD] approaching daily cap (%.1f%%): "
                "$%.4f + $%.4f vs $%.2f",
                100.0 * (daily + estimate.estimated_usd) / daily_limit,
                daily, estimate.estimated_usd, daily_limit,
            )

    async def record(
        self,
        *,
        provider: str,
        model: str,
        cost_usd: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        phase: str = "llm_call",
        task_id: str | None = None,
        success: bool = True,
        duration_ms: int | None = None,
    ) -> None:
        """Persist the actual cost of a completed LLM call.

        Best-effort — a failed insert never bubbles out of the call path.
        The pipeline already logs to cost_logs via admin_db.log_cost from
        higher layers; this method is the cheap-path for plugins that
        want to record cost without taking a dependency on the admin
        service.
        """
        if self._pool is None:
            return
        try:
            await self._pool.execute(
                """
                INSERT INTO cost_logs (
                    task_id, phase, model, provider,
                    input_tokens, output_tokens, total_tokens,
                    cost_usd, duration_ms, success,
                    created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW()
                )
                """,
                task_id, phase, model, provider,
                int(prompt_tokens), int(completion_tokens),
                int(prompt_tokens + completion_tokens),
                float(cost_usd), duration_ms, success,
            )
        except Exception as e:
            logger.warning("[COST_GUARD] cost_logs insert failed: %s", e)
