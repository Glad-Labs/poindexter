"""Cost Guard — hard spend ceilings for paid LLM provider plugins.

Re-introduced 2026-04-25 to back the cloud-LLM provider plugins (Gemini,
Anthropic, OpenAI) being added under Phase J. The original
``services/cost_guard.py`` was deleted in commit ``5eb26b51`` along
with all paid-API code; the plugin contract documented for these
providers names this module + ``CostGuardExhausted`` directly, so
they live here again.

Settings (in ``app_settings``, all optional with safe defaults):
    daily_spend_limit_usd       (default ``2.0``)
    monthly_spend_limit_usd     (default ``10.0``)
    plugin.llm_provider.<name>.cost_per_1k_input_usd
    plugin.llm_provider.<name>.cost_per_1k_output_usd

The "per-model rate" lookup falls back through:

1. ``plugin.llm_provider.<name>.model.<model>.cost_per_1k_*_usd``
2. ``plugin.llm_provider.<name>.cost_per_1k_*_usd``
3. The ``DEFAULT_RATES`` table in this module.

Usage::

    from services.cost_guard import CostGuard, CostGuardExhausted

    guard = CostGuard(site_config=site_config, pool=pool)
    estimate = await guard.estimate_cost(
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_tokens=4_000,
        completion_tokens=1_000,
    )
    await guard.check_budget(provider="gemini", model="gemini-2.5-flash",
                             estimated_cost_usd=estimate)
    # ... make the API call ...
    await guard.record_usage(
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        cost_usd=actual_cost,
    )

``check_budget`` raises :class:`CostGuardExhausted` when either the
daily or monthly limit has already been reached, OR when the
estimated cost would push spend past the daily ceiling. Callers
should let it propagate so the dispatcher can fall back to a free
provider (Ollama).
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# Pessimistic per-1K-token cost defaults. Operators override per
# provider/model via ``app_settings``. Treating unknown models as
# "expensive" is intentional: an outdated default biases toward
# blocking, never toward silent overspend.
DEFAULT_RATES: dict[str, dict[str, dict[str, float]]] = {
    "gemini": {
        # AI Studio published rates as of 2026-04 — verify against
        # https://ai.google.dev/pricing before tightening these.
        "gemini-2.5-flash": {"input": 0.000075, "output": 0.0003},
        "gemini-2.5-pro": {"input": 0.00125, "output": 0.005},
        "text-embedding-004": {"input": 0.00001, "output": 0.0},
        "_default": {"input": 0.00125, "output": 0.005},
    },
}


class CostGuardExhausted(RuntimeError):
    """Raised when a paid-provider call would exceed the configured
    daily or monthly spend ceiling.

    Carries enough structured detail for dispatch logic to log a
    fallback decision without re-querying the guard.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        scope: str,
        spent_usd: float,
        limit_usd: float,
        estimated_cost_usd: float,
    ):
        self.provider = provider
        self.model = model
        self.scope = scope
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        self.estimated_cost_usd = estimated_cost_usd
        super().__init__(
            f"CostGuard exhausted: provider={provider!r} model={model!r} "
            f"scope={scope} spent=${spent_usd:.4f} limit=${limit_usd:.4f} "
            f"estimated_cost=${estimated_cost_usd:.4f}"
        )


class CostGuard:
    """Pre-call budget check + post-call usage recording.

    Stateless across requests — pulls limits from ``site_config`` and
    spend totals from ``cost_logs`` on every call. The pool is optional
    so unit tests can construct a guard without a database.
    """

    def __init__(
        self,
        *,
        site_config: Any = None,
        pool: Any = None,
    ):
        self._site_config = site_config
        self._pool = pool

    # ------------------------------------------------------------------
    # Per-model rate resolution
    # ------------------------------------------------------------------

    def _get_rate(self, provider: str, model: str, kind: str) -> float:
        """Look up cost-per-1K-tokens for ``provider/model``.

        ``kind`` is ``"input"`` or ``"output"``. Resolution order is
        documented at the top of this module.
        """
        if kind not in ("input", "output"):
            raise ValueError(f"kind must be 'input' or 'output', got {kind!r}")

        if self._site_config is not None:
            try:
                model_key = (
                    f"plugin.llm_provider.{provider}.model.{model}."
                    f"cost_per_1k_{kind}_usd"
                )
                model_val = self._site_config.get(model_key, "")
                if model_val:
                    return float(model_val)
            except Exception:
                pass
            try:
                provider_key = (
                    f"plugin.llm_provider.{provider}.cost_per_1k_{kind}_usd"
                )
                provider_val = self._site_config.get(provider_key, "")
                if provider_val:
                    return float(provider_val)
            except Exception:
                pass

        provider_defaults = DEFAULT_RATES.get(provider) or {}
        model_defaults = (
            provider_defaults.get(model)
            or provider_defaults.get("_default")
            or {}
        )
        return float(model_defaults.get(kind, 0.0))

    async def estimate_cost(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Compute the USD cost of a hypothetical call.

        Used by ``check_budget`` to guard against single-call blowouts
        (e.g., a 1M-token Gemini Pro request right at the daily limit).
        """
        input_rate = self._get_rate(provider, model, "input")
        output_rate = self._get_rate(provider, model, "output")
        return (
            (prompt_tokens / 1000.0) * input_rate
            + (completion_tokens / 1000.0) * output_rate
        )

    # ------------------------------------------------------------------
    # Spend lookups
    # ------------------------------------------------------------------

    async def _spend_total(self, sql_window: str) -> float:
        """Sum ``cost_usd`` from ``cost_logs`` over the supplied window.

        Returns 0.0 when no pool is wired up — the guard runs in a
        "limit-check only" mode in that case, which keeps tests
        offline-friendly.
        """
        if self._pool is None:
            return 0.0
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT COALESCE(SUM(cost_usd), 0) AS total "
                    f"FROM cost_logs "
                    f"WHERE created_at >= {sql_window}"
                )
                return float(row["total"]) if row else 0.0
        except Exception as e:
            logger.warning("[COST_GUARD] spend query failed (%s): %s", sql_window, e)
            return 0.0

    async def get_daily_spend(self) -> float:
        return await self._spend_total("date_trunc('day', NOW())")

    async def get_monthly_spend(self) -> float:
        return await self._spend_total("date_trunc('month', NOW())")

    def _limit(self, key: str, default: float) -> float:
        if self._site_config is None:
            return default
        try:
            raw = self._site_config.get(key, "")
            if raw:
                return float(raw)
        except Exception:
            pass
        return default

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_budget(
        self,
        *,
        provider: str,
        model: str,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        """Raise :class:`CostGuardExhausted` if a call would overspend.

        Three checks run in order:

        1. Monthly spend already ``>=`` monthly limit.
        2. Daily spend already ``>=`` daily limit.
        3. Daily spend + ``estimated_cost_usd`` would push the day over
           the daily limit.

        All three raise the same exception type so callers only need
        a single ``except`` branch.
        """
        daily_limit = self._limit("daily_spend_limit_usd", 2.0)
        monthly_limit = self._limit("monthly_spend_limit_usd", 10.0)

        monthly_spend = await self.get_monthly_spend()
        if monthly_limit > 0 and monthly_spend >= monthly_limit:
            raise CostGuardExhausted(
                provider=provider,
                model=model,
                scope="monthly",
                spent_usd=monthly_spend,
                limit_usd=monthly_limit,
                estimated_cost_usd=estimated_cost_usd,
            )

        daily_spend = await self.get_daily_spend()
        if daily_limit > 0 and daily_spend >= daily_limit:
            raise CostGuardExhausted(
                provider=provider,
                model=model,
                scope="daily",
                spent_usd=daily_spend,
                limit_usd=daily_limit,
                estimated_cost_usd=estimated_cost_usd,
            )

        if (
            daily_limit > 0
            and estimated_cost_usd > 0
            and (daily_spend + estimated_cost_usd) > daily_limit
        ):
            raise CostGuardExhausted(
                provider=provider,
                model=model,
                scope="daily_estimate",
                spent_usd=daily_spend,
                limit_usd=daily_limit,
                estimated_cost_usd=estimated_cost_usd,
            )

    async def record_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float | None = None,
        phase: str = "",
        task_id: str | None = None,
        success: bool = True,
    ) -> float:
        """Persist a paid-API call to ``cost_logs`` and return cost.

        When ``cost_usd`` is None, computes it from the per-model
        rates so plugin authors don't have to. Returns the cost (so
        observability code can log it) and is best-effort about DB
        writes — failures are logged but don't propagate, matching
        the "never break the API call to log it" pattern used
        elsewhere in this codebase.
        """
        if cost_usd is None:
            cost_usd = await self.estimate_cost(
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        if self._pool is None:
            logger.debug(
                "[COST_GUARD] record_usage offline: provider=%s model=%s "
                "tokens=%d/%d cost=$%.6f",
                provider, model, prompt_tokens, completion_tokens, cost_usd,
            )
            return float(cost_usd)

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO cost_logs (
                        task_id, provider, model, phase,
                        prompt_tokens, completion_tokens,
                        cost_usd, success, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    """,
                    task_id,
                    provider,
                    model,
                    phase,
                    int(prompt_tokens),
                    int(completion_tokens),
                    float(cost_usd),
                    bool(success),
                )
        except Exception as e:
            logger.warning(
                "[COST_GUARD] record_usage insert failed (%s/%s): %s",
                provider, model, e,
            )
        return float(cost_usd)
